from packastack.apt.packages import BinaryPackage, PackageIndex
from packastack.debpkg.control import ParsedDependency
from packastack.planning.dependency_satisfaction import evaluate_dependencies


def _index(entries: list[tuple[str, str, str]]) -> PackageIndex:
    idx = PackageIndex()
    for name, version, component in entries:
        pkg = BinaryPackage(name=name, version=version, architecture="amd64")
        idx.add_package(pkg, component, "release")
    return idx


def test_version_check_and_cloud_archive_flag() -> None:
    dev = _index([
        ("python3-foo", "2.0", "main"),
    ])
    prev = _index([
        ("python3-foo", "1.0", "main"),
    ])

    deps = [
        ParsedDependency(name="python3-foo", relation=">=", version="2.0"),
        ParsedDependency(name="python3-bar"),
    ]

    results, summary = evaluate_dependencies(deps, dev_index=dev, prev_index=prev, kind="build")

    assert summary.total == 2
    assert summary.dev_satisfied == 1
    assert summary.prev_lts_satisfied == 0
    assert summary.cloud_archive_required == 2

    first = results[0]
    assert first.dev.satisfied is True
    assert first.prev_lts.satisfied is False
    assert first.prev_lts.reason == "version_too_low"
    assert first.cloud_archive_required is True

    second = results[1]
    assert second.dev.found is False
    assert second.prev_lts.found is False


def test_mir_warning_detected() -> None:
    dev = _index([
        ("python3-baz", "1.0", "universe"),
    ])
    prev = _index([
        ("python3-baz", "1.0", "universe"),
    ])

    deps = [ParsedDependency(name="python3-baz")]

    results, summary = evaluate_dependencies(deps, dev_index=dev, prev_index=prev, kind="runtime")

    assert summary.mir_warnings == 1
    assert results[0].mir_warning is True
    assert results[0].cloud_archive_required is False


def test_alternative_dep_returns_first_found() -> None:
    dev = _index([
        ("python3-alt", "1.0", "main"),
    ])

    dep = ParsedDependency(
        name="python3-alt",
        relation=">=",
        version="2.0",
        alternatives=[ParsedDependency(name="python3-missing")],
    )

    results, summary = evaluate_dependencies([dep], dev_index=dev, prev_index=None, kind="build")

    assert summary.total == 1
    assert summary.dev_satisfied == 0
    assert summary.prev_lts_satisfied == 0
    assert summary.cloud_archive_required == 1

    result = results[0]
    assert result.dev.found is True
    assert result.dev.satisfied is False
    assert result.dev.reason == "version_too_low"
    assert result.prev_lts.found is False


def test_missing_version_counts_as_found() -> None:
    dev = _index([
        ("python3-noversion", "", "main"),
    ])

    dep = ParsedDependency(name="python3-noversion")

    results, summary = evaluate_dependencies([dep], dev_index=dev, prev_index=None, kind="runtime")

    assert summary.total == 1
    assert summary.dev_satisfied == 1
    assert summary.prev_lts_satisfied == 0
    assert summary.cloud_archive_required == 1

    status = results[0]
    assert status.dev.found is True
    assert status.dev.satisfied is True
    assert status.dev.reason == "ok"
