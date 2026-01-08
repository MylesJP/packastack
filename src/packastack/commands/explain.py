# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
"""Implementation of `packastack explain` command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer

from packastack.apt.packages import load_package_index, apply_ubuntu_source_fallbacks
from packastack.commands.plan import _fetch_packaging_repos
from packastack.core.config import load_config
from packastack.core.paths import resolve_paths
from packastack.core.run import RunContext, activity
from packastack.debpkg.control import parse_control
from packastack.planning.dependency_satisfaction import evaluate_dependencies
from packastack.reports.explain import write_explain_reports
from packastack.target.distro_info import get_current_lts
from packastack.target.resolution import TargetResolver, parse_target_expr
from packastack.target.series import resolve_series
from packastack.upstream.registry import UpstreamsRegistry
from packastack.upstream.releases import get_current_development_series, is_snapshot_eligible

EXIT_CONFIG_ERROR = 1


def _format_dependency(name: str, relation: str, version: str) -> str:
    if relation and version:
        return f"{name} ({relation} {version})"
    return name


def _render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    target = report.get("target", {})
    lines.append(f"[explain] Target: {target.get('source_package', '')}")
    lines.append("[explain] Resolved as:")
    lines.append(
        f"          source={target.get('source_package','')} "
        f"canonical={target.get('upstream_project','')} "
        f"resolution={target.get('resolution_source','')}"
    )
    ts = report.get("type_selection", {})
    lines.append(
        f"[explain] Build type: {ts.get('selected','snapshot')} "
        f"({ts.get('mode','auto')}) reason={ts.get('reason','') or 'n/a'}"
    )
    summary = report.get("summary", {})
    lines.append("[explain] Dependency satisfaction:")
    lines.append(
        f"          ubuntu-series ({report.get('ubuntu_series')}):  "
        f"{summary.get('build_deps_dev_satisfied',0)}/{summary.get('build_deps_total',0)} satisfied"
    )
    lines.append(
        f"          current-lts ({report.get('current_lts','unknown')}):   "
        f"{summary.get('build_deps_current_lts_satisfied',0)}/{summary.get('build_deps_total',0)} satisfied"
    )
    lines.append(f"          cloud-archive required:    {summary.get('cloud_archive_required_count',0)} deps")
    lines.append(f"          MIR warnings:              {summary.get('mir_warning_count',0)} deps in universe")

    cloud_list = report.get("cloud_archive_deps", [])
    if cloud_list:
        lines.append("[explain] Cloud-archive required deps:")
        for dep in cloud_list:
            lines.append(
                "          - "
                f"{_format_dependency(dep.get('name',''), dep.get('relation',''), dep.get('version',''))}"
            )

    mir_list = report.get("mir_warning_deps", [])
    if mir_list:
        lines.append("[explain] MIR warnings (universe):")
        for dep in mir_list:
            lines.append(
                "          - "
                f"{_format_dependency(dep.get('name',''), dep.get('relation',''), dep.get('version',''))}"
            )

    return "\n".join(lines)



def explain(
    package: str = typer.Argument(..., help="Package name or OpenStack project to explain"),
    target: str = typer.Option("devel", "-t", "--target", help="OpenStack series target"),
    ubuntu_series: str = typer.Option("devel", "-u", "--ubuntu-series", help="Ubuntu series target"),
    output_format: str = typer.Option("text", "-f", "--format", help="Output format: text|json|html"),
    show_deps: str = typer.Option("both", "--show-deps", help="Which dependencies to show: runtime|build|both"),
    show_satisfied: bool = typer.Option(True, "--show-satisfied/--hide-satisfied", help="Show satisfied dependencies"),
    show_unsatisfied: bool = typer.Option(True, "--show-unsatisfied/--hide-unsatisfied", help="Show unsatisfied dependencies"),
    include_universe: bool = typer.Option(True, "--include-universe/--no-include-universe", help="Include universe deps (still warn about MIR)"),
    include_retired: bool = typer.Option(False, "--include-retired", help="Include retired upstream projects"),
    offline: bool = typer.Option(False, "-o", "--offline", help="Run in offline mode"),
    force: bool = typer.Option(False, "-F", "--force", help="Proceed on multiple matches"),
) -> None:
    """Explain target resolution, build type, and dependency satisfaction."""

    with RunContext("explain") as run:
        cfg = load_config()
        paths = resolve_paths(cfg)

        if output_format not in {"text", "json", "html"}:
            activity("error", "--format must be one of: text, json, html")
            sys.exit(EXIT_CONFIG_ERROR)
        if show_deps not in {"runtime", "build", "both"}:
            activity("error", "--show-deps must be one of: runtime, build, both")
            sys.exit(EXIT_CONFIG_ERROR)

        resolved_ubuntu = resolve_series(ubuntu_series)
        activity("resolve", f"Ubuntu series: {resolved_ubuntu}")
        run.log_event({"event": "series.ubuntu_resolved", "series": resolved_ubuntu})

        current_lts = get_current_lts()
        current_lts_series = current_lts.series if current_lts else ""
        if current_lts_series:
            activity("resolve", f"Current LTS series: {current_lts_series}")
        run.log_event({"event": "series.current_lts", "series": current_lts_series})

        releases_repo = paths["openstack_releases_repo"]
        if target == "devel":
            openstack_target = get_current_development_series(releases_repo) or target
        else:
            openstack_target = target
        activity("resolve", f"OpenStack target: {openstack_target}")
        cloud_archive = None
        if current_lts_series and openstack_target:
            cloud_archive = f"{current_lts_series}-{openstack_target}"
            activity("resolve", f"Cloud Archive: {cloud_archive}")
        run.log_event({"event": "series.openstack_resolved", "target": openstack_target})

        # Load registry
        registry: UpstreamsRegistry | None = None
        try:
            registry = UpstreamsRegistry()
        except Exception as e:
            activity("warning", f"Failed to load registry: {e}")

        # Parse target expression and resolve
        local_repo = paths["local_apt_repo"]
        try:
            expr = parse_target_expr(package)
        except ValueError as e:
            activity("error", f"Invalid target expression: {e}")
            sys.exit(EXIT_CONFIG_ERROR)

        resolver = TargetResolver(
            registry=registry,
            local_repo=local_repo,
            releases_repo=releases_repo,
            openstack_target=openstack_target,
        )

        # Resolve with all_matches to handle prefix/contains
        result = resolver.resolve(expr, all_matches=True)

        # Collect candidates
        candidates = result.candidates if result.candidates else []
        if result.identity:
            candidates = [result.identity]

        if not candidates:
            activity("error", f"No packages found matching: {package}")
            run.write_summary(status="failed", error="no matches", exit_code=EXIT_CONFIG_ERROR)
            sys.exit(EXIT_CONFIG_ERROR)

        if len(candidates) > 1 and not force:
            matches = ", ".join(identity.source_package for identity in candidates)
            activity("error", f"Multiple matches: {matches} (use --force to proceed)")
            run.write_summary(status="failed", error="multiple matches", exit_code=EXIT_CONFIG_ERROR)
            sys.exit(EXIT_CONFIG_ERROR)

        target_identity = candidates[0]
        activity("resolve", f"Target: {target_identity.source_package} (from {target_identity.origin.value})")

        pockets = cfg.get("defaults", {}).get("ubuntu_pockets", ["release", "updates", "security"])
        components = cfg.get("defaults", {}).get("ubuntu_components", ["main", "universe"])
        ubuntu_cache = paths["ubuntu_archive_cache"]

        activity("plan", "Loading package indexes")
        dev_index = load_package_index(ubuntu_cache, resolved_ubuntu, pockets, components)
        prev_index = None
        if current_lts_series:
            prev_index = load_package_index(ubuntu_cache, current_lts_series, pockets, components)

        # Apply Ubuntu source-name fallbacks to the resolved target identity
        try:
            wrapper = type("_RT", (), {})()
            wrapper.source_package = target_identity.source_package
            wrapper.upstream_project = getattr(target_identity, "canonical_upstream", None)
            wrapper.resolution_source = target_identity.origin.value
            apply_ubuntu_source_fallbacks(dev_index, [wrapper], run)
            # Reflect back any substitution
            if wrapper.source_package != target_identity.source_package:
                target_identity.source_package = wrapper.source_package
        except Exception:
            pass

        reports_dir = run.run_path / "reports"

        # Fetch packaging repo
        packaging_cache = paths.get("build_root", paths["cache_root"] / "build") / "packaging-cache"
        packaging_paths = _fetch_packaging_repos(
            packages=[target_identity.source_package],
            dest_dir=packaging_cache,
            ubuntu_series=resolved_ubuntu,
            openstack_series=openstack_target,
            offline=offline,
            workers=1,
        )
        packaging_path = packaging_paths.get(target_identity.source_package)
        if not packaging_path:
            activity("error", f"Packaging repo missing for {target_identity.source_package}")
            run.write_summary(status="failed", error="packaging repo missing", exit_code=EXIT_CONFIG_ERROR)
            sys.exit(EXIT_CONFIG_ERROR)

        control_path = packaging_path / "debian" / "control"
        if not control_path.exists():
            activity("error", f"debian/control not found for {target_identity.source_package}")
            run.write_summary(status="failed", error="control missing", exit_code=EXIT_CONFIG_ERROR)
            sys.exit(EXIT_CONFIG_ERROR)

        source_pkg = parse_control(control_path)

        build_deps: list[ParsedDependency] = []
        runtime_deps: list[ParsedDependency] = []
        if show_deps in ("build", "both"):
            build_deps = list(source_pkg.build_depends) + list(source_pkg.build_depends_indep)
        if show_deps in ("runtime", "both"):
            runtime_deps = source_pkg.get_runtime_depends()

        build_results, build_summary = evaluate_dependencies(build_deps, dev_index, prev_index, kind="build")
        runtime_results, runtime_summary = evaluate_dependencies(runtime_deps, dev_index, prev_index, kind="runtime")

        # Filter satisfied/unsatisfied
        def _filter(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            filtered = []
            for item in items:
                sat = item.get("dev", {}).get("satisfied") and item.get("prev_lts", {}).get("satisfied")
                if sat and not show_satisfied:
                    continue
                if not sat and not show_unsatisfied:
                    continue
                filtered.append(item)
            return filtered

        build_payload_full = [r.to_dict() for r in build_results]
        runtime_payload_full = [r.to_dict() for r in runtime_results]

        def _is_main(entry: dict[str, Any]) -> bool:
            dev_comp = entry.get("dev", {}).get("component")
            prev_comp = entry.get("prev_lts", {}).get("component")
            return (dev_comp in ("main", None, "")) and (prev_comp in ("main", None, ""))

        build_payload_display = _filter(build_payload_full)
        runtime_payload_display = _filter(runtime_payload_full)

        if not include_universe:
            build_payload_display = [d for d in build_payload_display if _is_main(d)]
            runtime_payload_display = [d for d in runtime_payload_display if _is_main(d)]

        summary = {
            "build_deps_total": build_summary.total,
            "build_deps_dev_satisfied": build_summary.dev_satisfied,
            "build_deps_current_lts_satisfied": build_summary.prev_lts_satisfied,
            "runtime_deps_total": runtime_summary.total,
            "runtime_deps_dev_satisfied": runtime_summary.dev_satisfied,
            "runtime_deps_current_lts_satisfied": runtime_summary.prev_lts_satisfied,
            "cloud_archive_required_count": build_summary.cloud_archive_required + runtime_summary.cloud_archive_required,
            "mir_warning_count": build_summary.mir_warnings + runtime_summary.mir_warnings,
        }

        # Build type reasoning (simple snapshot eligibility check)
        eligible, reason, preferred_version = is_snapshot_eligible(
            releases_repo,
            openstack_target,
            target_identity.source_package,
        )
        selected_type = "snapshot" if eligible else "release"

        report = {
            "run_id": run.run_id,
            "target": {
                "source_package": target_identity.source_package,
                "upstream_project": target_identity.canonical_upstream,
                "resolution_source": target_identity.origin.value,
            },
            "openstack_target": openstack_target,
            "ubuntu_series": resolved_ubuntu,
            "current_lts": current_lts_series,
            "cloud_archive": cloud_archive,
            "type_selection": {
                "mode": "auto",
                "selected": selected_type,
                "reason": reason or "auto",
                "preferred_version": preferred_version,
            },
            "dependencies": {
                "build": build_payload_display,
                "runtime": runtime_payload_display,
            },
            "cloud_archive_deps": [d for d in (build_payload_full + runtime_payload_full) if d.get("cloud_archive_required")],
            "mir_warning_deps": [d for d in (build_payload_full + runtime_payload_full) if d.get("mir_warning")],
            "summary": summary,
        }

        saved = write_explain_reports(report, reports_dir)
        run.write_summary(
            status="success",
            exit_code=0,
            reports={"explain_json": str(saved["json"]), "explain_html": str(saved["html"])}
        )

        if output_format == "json":
            print(saved["json"].read_text(), file=sys.__stdout__)
        elif output_format == "html":
            print(saved["html"].read_text(), file=sys.__stdout__)
        else:
            print(_render_text(report), file=sys.__stdout__)
