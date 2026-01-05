# Refactor Plan: build.py

This document captures the refactoring plan for `src/packastack/commands/build.py` (3,873 lines).

## Phase Map

The `_run_build` function (lines 2173-3873) contains 12 major phases executed sequentially:

| # | Phase | Lines | Description |
|---|-------|-------|-------------|
| 1 | **resolve** | 2173-2272 | Parse CLI build type, resolve auto type from releases repo, resolve series |
| 2 | **planning** | 2273-2319 | Call `run_plan_for_package()`, handle plan-only modes, check exit codes |
| 3 | **retirement** | 2320-2397 | Check if upstream project is retired via RetirementChecker |
| 4 | **registry** | 2400-2480 | Load UpstreamsRegistry, resolve upstream config, initialize provenance |
| 5 | **policy** | 2483-2505 | Check snapshot eligibility, enforce policy blocks |
| 6 | **plan** (indexes) | 2508-2606 | Load Ubuntu/CA/local indexes, validate tools, ensure schroot |
| 7 | **fetch** | 2609-2705 | Clone packaging repo, protect files, check watch mismatch |
| 8 | **prepare** | 2708-2800 | Update launchpad.yaml, select upstream source, apply signature policy, fetch tarball |
| 9 | **validate-deps** | 2953-3107 | Extract/validate upstream deps, identify buildable missing deps |
| 10 | **auto-build** | 3110-3215 | Recursively build missing dependencies via subprocess |
| 11 | **import-orig** | 3349-3466 | Import upstream tarball, merge upstream branch |
| 12 | **patches** | 3469-3598 | gbp pq import/export, refresh patches |
| 13 | **build** | 3601-3756 | Build source package, optionally binary with sbuild/dpkg |
| 14 | **verify** | 3759-3823 | Publish artifacts to local repo, regenerate indexes |
| 15 | **provenance** | 3826-3844 | Write provenance record |
| 16 | **report** | 3847-3873 | Write summary, show upload commands |

### Build-All Functions (lines 371-1460)

| Function | Lines | Description |
|----------|-------|-------------|
| `_build_dependency_graph` | 371-397 | Wrapper for graph_builder |
| `_build_upstream_versions_from_packaging` | 400-416 | Extract versions from changelogs |
| `_filter_retired_packages` | 419-451 | Filter retired packages |
| `_get_parallel_batches` | 454-505 | Compute parallel build batches |
| `_run_single_build` | 508-607 | Run subprocess for single package |
| `_generate_reports` | 611-768 | Generate JSON/Markdown reports |
| `run_build_all` | 771-828 | Entry point (creates RunContext) |
| `_run_build_all` | 831-1199 | Main build-all implementation |
| `_run_sequential_builds` | 1228-1333 | Sequential executor |
| `_run_parallel_builds` | 1336-1460 | Parallel executor |

### Helper Functions (lines 180-369)

| Function | Lines | Description |
|----------|-------|-------------|
| `_maybe_enable_sphinxdoc` | 180-212 | Add sphinxdoc addon to rules |
| `_no_gpg_sign_enabled` | 214-217 | Check env for GPG sign disable |
| `_maybe_disable_gpg_sign` | 220-226 | Inject --no-gpg-sign into git commit |
| `_get_git_author_env` | 228-262 | Get git author from DEBFULLNAME/DEBEMAIL |
| `_shorten_path` | 264-281 | Shorten paths for commit messages |
| `_staged_changes` | 284-307 | List staged git files |
| `_compose_commit_message` | 310-340 | Compose commit with file list |

---

## Duplication Inventory

### 1. Activity + Log Event Pattern (~50 occurrences)

Every phase logs both to UI and structured events:

```python
activity("phase", f"Message: {value}")
run.log_event({
    "event": "phase.action",
    "key": value,
})
```

**Proposal**: Create `log_phase_event(run, phase, message, event_key, **kwargs)` helper.

### 2. Error Exit Pattern (~15 occurrences)

```python
if <error_condition>:
    activity("phase", f"Error: {message}")
    run.write_summary(
        status="failed",
        error="...",
        exit_code=EXIT_*,
    )
    return EXIT_*
```

**Proposal**: Create `phase_error(run, phase, message, exit_code) -> int` helper that:
- Logs activity with error prefix
- Logs event with `phase.error` key
- Calls `run.write_summary()`
- Returns the exit code

### 3. Package Index Loading (3 occurrences)

- Lines 993-1018 (build-all): Load Ubuntu + CA + local repo indexes
- Lines 2507-2536 (_run_build): Same pattern
- Lines 2537-2560 (_run_build): Load CA index

**Proposal**: Extract `load_merged_package_indexes(paths, ubuntu_series, cloud_archive) -> PackageIndex`.

### 4. Local Repo Refresh (4 occurrences)

- Line 1305 (sequential builds): After each success
- Line 1414 (parallel builds): After each batch
- Line 3656 (binary build): Before sbuild
- Line 3790 (verify): After publishing

**Proposal**: Already extracted as `_refresh_local_repo_indexes()`. Keep as-is.

### 5. Tarball Caching Pattern (5 occurrences)

Lines 1955, 1988, 2032, 2063, 2136 all call:
```python
cache_tarball(
    tarball_path=path,
    entry=TarballCacheEntry(...),
    cache_base=cache_base,
)
```

**Proposal**: This is in `_fetch_release_tarball()` which handles multiple methods. Keep as-is but ensure consistent entry fields.

### 6. Git Command Execution Pattern (~10 occurrences)

```python
cmd = _maybe_disable_gpg_sign(["git", "commit", "-m", message])
run_command(cmd, cwd=pkg_repo, env=_get_git_author_env())
```

**Proposal**: Create `git_commit(pkg_repo, message, extra_lines=None) -> bool` helper.

---

## Single Build vs Build-All Differences

| Aspect | Single Build | Build-All |
|--------|--------------|-----------|
| Entry point | `_build_single_mode()` | `_build_all_mode()` |
| Package source | CLI argument | `discover_packages()` |
| Execution | Inline `_run_build()` | Subprocess via `_run_single_build()` |
| Progress | Phase-based activity | Rich progress bars |
| Failure handling | Exit immediately | Continue/track failures |
| Repo regeneration | After each phase | After each batch |
| State persistence | Provenance only | BuildAllState + JSON |

**Key insight**: Build-all already uses subprocess isolation (`_run_single_build`). We should NOT change this—keep subprocess isolation for all build-all builds.

---

## Proposed Module Boundaries

### New Directory Structure

```
src/packastack/commands/build/
├── __init__.py          # Re-export build() CLI entry point
├── types.py             # Dataclasses for state objects
├── errors.py            # Typed exceptions, phase_error() helper
├── git_helpers.py       # Git-related utilities
├── tarball.py           # Tarball acquisition logic
├── phases.py            # Phase functions for _run_build
├── executor.py          # Build-all executor logic
└── reports.py           # Report generation
```

### What Stays in build.py

- `build()` - Typer CLI entry point (lines 1566-1674)
- `_build_single_mode()` - Router to `_run_build()`
- `_build_all_mode()` - Router to `run_build_all()`
- Exit code constants

### types.py Contents

```python
@dataclass
class ResolvedTargets:
    """Resolved target series information."""
    openstack_series: str
    ubuntu_series: str
    prev_series: str | None
    is_development: bool

@dataclass
class WorkspacePaths:
    """Paths used during build."""
    workspace: Path
    pkg_repo: Path
    build_output: Path
    upstream_work_dir: Path | None

@dataclass
class BuildInputs:
    """Consolidated inputs for a build."""
    request: BuildRequest
    run: RunContext
    cfg: dict
    paths: dict[str, Path]
    targets: ResolvedTargets

@dataclass
class PhaseResult:
    """Result of a phase execution."""
    success: bool
    exit_code: int
    message: str
    data: dict[str, Any] | None = None

@dataclass
class BuildOutcome:
    """Final outcome of a build."""
    success: bool
    exit_code: int
    package: str
    version: str
    build_type: str
    artifacts: list[Path]
    provenance: BuildProvenance | None
    error: str | None = None
```

### git_helpers.py Contents

Move:
- `_maybe_enable_sphinxdoc()`
- `_no_gpg_sign_enabled()`
- `_maybe_disable_gpg_sign()`
- `_get_git_author_env()`
- `_shorten_path()`
- `_staged_changes()`
- `_compose_commit_message()`

Add:
- `git_commit(repo: Path, message: str, extra_lines: list[str] | None = None) -> bool`

### tarball.py Contents

Move:
- `_run_uscan()`
- `_fetch_release_tarball()`
- `_download_pypi_tarball()`
- `_download_github_release_tarball()`

### phases.py Contents

Extract from `_run_build()`:
- `resolve_build_type(inputs) -> PhaseResult`
- `check_retirement(inputs) -> PhaseResult`
- `resolve_registry(inputs) -> PhaseResult`
- `check_policy(inputs) -> PhaseResult`
- `fetch_packaging(inputs) -> PhaseResult`
- `prepare_upstream(inputs) -> PhaseResult`
- `validate_dependencies(inputs) -> PhaseResult`
- `auto_build_deps(inputs) -> PhaseResult`
- `import_upstream(inputs) -> PhaseResult`
- `apply_patches(inputs) -> PhaseResult`
- `build_packages(inputs) -> PhaseResult`
- `verify_and_publish(inputs) -> PhaseResult`
- `write_provenance(inputs) -> PhaseResult`

### executor.py Contents

Move:
- `_run_single_build()`
- `_run_sequential_builds()`
- `_run_parallel_builds()`
- `_get_parallel_batches()`

### reports.py Contents

Move:
- `_generate_reports()`

---

## Golden Behavior Notes

### Exit Codes (MUST preserve)

| Code | Constant | Meaning |
|------|----------|---------|
| 0 | EXIT_SUCCESS | Success |
| 1 | EXIT_CONFIG_ERROR | Configuration error |
| 2 | EXIT_TOOL_MISSING | Required tools missing |
| 3 | EXIT_FETCH_FAILED | Fetch/clone failed |
| 4 | EXIT_PATCH_FAILED | Patch application failed |
| 5 | EXIT_MISSING_PACKAGES | Missing dependencies |
| 6 | EXIT_CYCLE_DETECTED | Dependency cycle |
| 7 | EXIT_BUILD_FAILED | Build failed |
| 8 | EXIT_POLICY_BLOCKED | Policy blocked |
| 9 | EXIT_REGISTRY_ERROR | Registry error |
| 10 | EXIT_RETIRED_PROJECT | Retired project |
| 11 | EXIT_DISCOVERY_FAILED | Build-all: discovery failed |
| 12 | EXIT_GRAPH_ERROR | Build-all: graph error |
| 13 | EXIT_ALL_BUILD_FAILED | Build-all: some builds failed |
| 14 | EXIT_RESUME_ERROR | Build-all: resume error |

### Log Event Keys (MUST preserve)

The following event keys are used and must remain compatible:
- `resolve.package`, `resolve.auto_type_selected`, `resolve.build_type`, `resolve.prev_series`
- `registry.loaded`, `registry.resolved`
- `policy.retired_project`, `policy.possibly_retired`, `policy.snapshot`, `policy.tarball_verification`, `policy.watch_mismatch`
- `plan.ubuntu_index`, `plan.cloud_archive_index`, `plan.local_repo_index`, `plan.build_order`
- `schroot.ready`
- `fetch.complete`
- `prepare.*` (launchpad_yaml, snapshot, version, manpages, fix_priority_extra, doctree_cleanup, pgp_watch_fix, misc_pre_depends)
- `import-orig.branch`, `import-orig.complete`, `import-orig.failed`
- `patches.upstreamed`, `patches.complete`
- `build.source_complete`, `build.binary_complete`, `build.binary_failed`, `build.sbuild_command`
- `verify.publish`, `verify.publish_failed`, `verify.index`, `verify.source_index`, `verify.index_mismatch`
- `provenance.written`, `provenance.write_failed`
- `validate-deps.*`
- `auto-build.*`
- `discovery.complete`, `build_all.*`

### CLI Flags (MUST preserve)

All Typer options in `build()` function (lines 1566-1674) must remain unchanged:
- Positional: `package`
- Options: `--target`, `--ubuntu-series`, `--cloud-archive`, `--type`, `--milestone`, `--force`, `--offline`, `--validate-plan`, `--plan-upload`, `--upload`, `--binary/--no-binary`, `--builder`, `--build-deps/--no-build-deps`, `--no-cleanup`, `--no-spinner`, `--yes`, `--use-gbp-dch/--no-gbp-dch`, `--include-retired`, `--skip-repo-regen` (hidden)
- Build-all options: `--all`, `--keep-going/--fail-fast`, `--max-failures`, `--resume`, `--resume-run-id`, `--retry-failed`, `--skip-failed/--no-skip-failed`, `--parallel`, `--packages-file`, `--dry-run`

---

## Staged Commit Checklist

### Commit 1: REFACTOR_PLAN.md (this document)
- [x] Read build.py end-to-end
- [x] Create phase map
- [x] Create duplication inventory
- [x] Document golden behaviors
- [ ] Commit REFACTOR_PLAN.md

### Commit 2: Introduce types.py with dataclasses
- [ ] Create `src/packastack/commands/build/` directory
- [ ] Create `types.py` with dataclasses
- [ ] Create `errors.py` with `phase_error()` helper
- [ ] Update imports in build.py (no behavior change)
- [ ] Add tests for dataclass construction
- [ ] Verify: `uv run pytest tests/commands/test_build.py -q`

### Commit 3: Extract git_helpers.py
- [ ] Move git helper functions
- [ ] Update imports in build.py
- [ ] Add tests for `_shorten_path`, `_compose_commit_message`
- [ ] Verify tests pass

### Commit 4: Extract tarball.py
- [ ] Move tarball acquisition functions
- [ ] Update imports in build.py
- [ ] Add tests with mocked subprocess
- [ ] Verify tests pass

### Commit 5: Split _run_build into phase functions
- [ ] Create `phases.py` with phase functions
- [ ] Create coordinator in build.py that calls phases
- [ ] Ensure all event keys preserved
- [ ] Verify tests pass

### Commit 6: Extract executor.py for build-all
- [ ] Move executor functions
- [ ] Update `_run_build_all` to use executor
- [ ] Add tests for retry/resume semantics
- [ ] Verify tests pass

### Commit 7: Cleanup pass
- [ ] Reduce deref churn (consistent aliasing)
- [ ] Flatten nesting with guard clauses
- [ ] Collapse repeated guard patterns
- [ ] Final test verification

---

## Verification Commands

```bash
# After each commit:
uv run pytest tests/commands/test_build.py tests/commands/test_build_all.py -q
uv run python -m packastack build --help
uv run ruff check src/packastack/commands/

# Smoke test (if workspace available):
uv run python -m packastack build oslo.config --type snapshot --validate-plan
```

---

## Behavior Risks

1. **Phase function extraction**: Each phase currently has access to all local variables. Converting to explicit inputs/outputs may miss subtle dependencies. **Mitigation**: Extract one phase at a time, run tests after each.

2. **Error handling changes**: The `phase_error()` helper must emit identical log keys. **Mitigation**: Wrapper preserves exact format, tested with characterization tests.

3. **Build-all subprocess behavior**: Already uses subprocess isolation. **Mitigation**: No changes to subprocess logic.

4. **Import order dependencies**: Some phases depend on state from earlier phases (e.g., `provenance` object). **Mitigation**: Pass provenance through `BuildInputs` or as explicit parameter.
