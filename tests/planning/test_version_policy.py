import json
from pathlib import Path

from packastack.planning.validated_plan import resolve_dependency_with_spec
from packastack.reports.dep_sync import (
    DependencySatisfactionSummary,
    render_satisfaction_text,
    save_satisfaction_report,
)


class _StubIndex:
    def __init__(self, versions: dict[str, str]) -> None:
        self._versions = versions

    def get_version(self, name: str) -> str | None:  # pragma: no cover - tiny helper
        return self._versions.get(name)


def test_resolve_dependency_with_spec_respects_policy() -> None:
    ubuntu_index = _StubIndex({"python3-foo": "1.0-0ubuntu1"})

    _, _, satisfied_enforced = resolve_dependency_with_spec(
        "python3-foo",
        ">=2.0",
        None,
        None,
        ubuntu_index,
        enforce_min_versions=True,
    )
    assert satisfied_enforced is False

    version, source, satisfied_ignored = resolve_dependency_with_spec(
        "python3-foo",
        ">=2.0",
        None,
        None,
        ubuntu_index,
        enforce_min_versions=False,
    )
    assert version == "1.0-0ubuntu1"
    assert source == "ubuntu"
    assert satisfied_ignored is True


def test_dependency_satisfaction_report_render_and_save(tmp_path: Path) -> None:
    summary = DependencySatisfactionSummary(
        package="nova",
        policy="ignore",
        total=3,
        satisfied=2,
        outdated=1,
        missing=0,
        overridden=1,
        by_source={"ubuntu": 2, "local": 1},
        missing_deps=[],
        outdated_deps=["python3-bar"],
    )

    text = render_satisfaction_text(summary)
    assert "Policy: ignore" in text
    assert "Outdated" in text
    assert "python3-bar" in text

    paths = save_satisfaction_report(summary, tmp_path)
    assert len(paths) == 2
    for path in paths:
        assert path.exists()

    data = json.loads(paths[1].read_text())
    assert data["policy"] == "ignore"
    assert data["outdated_deps"] == ["python3-bar"]
