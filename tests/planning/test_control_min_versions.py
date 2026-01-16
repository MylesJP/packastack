from packastack.debpkg.control import ParsedDependency
from packastack.planning.control_min_versions import (
    _cmp,
    apply_min_version_policy,
    decide_min_version,
    decisions_to_report,
)


def test_chooses_previous_lts_when_compatible():
    deps = [ParsedDependency(name="python3-foo")]
    updated, decisions = apply_min_version_policy(
        existing=deps,
        upstream_mins={"python3-foo": "2.0"},
        prev_lts_versions={"python3-foo": "2.1"},
    )

    assert updated[0].version == "2.1"
    assert decisions[0].action == "added"
    assert decisions[0].cloud_archive_required is False


def test_does_not_reduce_without_normalize():
    deps = [ParsedDependency(name="python3-bar", relation=">=", version="3.0")]
    updated, decisions = apply_min_version_policy(
        existing=deps,
        upstream_mins={"python3-bar": "2.0"},
        prev_lts_versions={"python3-bar": "2.1"},
        normalize=False,
    )

    assert updated[0].version == "3.0"
    assert decisions[0].action == "kept"


def test_normalize_allows_lowering_to_prev_lts():
    deps = [ParsedDependency(name="python3-baz", relation=">=", version="3.0")]
    updated, decisions = apply_min_version_policy(
        existing=deps,
        upstream_mins={"python3-baz": "2.0"},
        prev_lts_versions={"python3-baz": "2.1"},
        normalize=True,
    )

    assert updated[0].version == "2.1"
    assert decisions[0].action == "lowered"


def test_cloud_archive_required_when_prev_lts_too_low():
    deps = [ParsedDependency(name="python3-qux")]
    updated, decisions = apply_min_version_policy(
        existing=deps,
        upstream_mins={"python3-qux": "2.1"},
        prev_lts_versions={"python3-qux": "2.0"},
    )

    assert updated[0].version == "2.1"
    assert decisions[0].cloud_archive_required is True


def test_report_summarises_actions_and_counts():
    decisions = [
        decide_min_version("python3-a", None, "1.0", "1.2"),
        decide_min_version("python3-b", "2.0", "2.0", "1.9"),
    ]
    report = decisions_to_report(decisions)

    assert report["raised"] == 0
    assert report["cloud_archive_required"] == 1
    assert report["unchanged"] >= 1


def test_apply_preserves_alphabetical_ordering():
    deps = [
        ParsedDependency(name="python3-zeta", relation=">=", version="1.0"),
        ParsedDependency(name="python3-alpha", relation=">=", version="1.0"),
    ]
    updated, _ = apply_min_version_policy(
        existing=deps,
        upstream_mins={},
        prev_lts_versions={},
    )

    names = [dep.name for dep in updated]
    assert names == sorted(names)


def test_cmp_handles_none_values():
    assert _cmp(None, None) == 0
    assert _cmp(None, "1.0") == -1
    assert _cmp("1.0", None) == 1


def test_no_upstream_min_keeps_existing():
    deps = [ParsedDependency(name="python3-keep", relation=">=", version="2.0")]
    updated, decisions = apply_min_version_policy(
        existing=deps,
        upstream_mins={},
        prev_lts_versions={},
    )

    assert updated[0].version == "2.0"
    assert decisions[0].reason_code == "no_upstream_min"


def test_dry_run_does_not_modify_dependencies():
    deps = [ParsedDependency(name="python3-dry", relation=">=", version="1.0")]
    updated, decisions = apply_min_version_policy(
        existing=deps,
        upstream_mins={"python3-dry": "2.0"},
        prev_lts_versions={"python3-dry": "2.1"},
        dry_run=True,
    )

    assert updated[0].version == "1.0"
    assert decisions[0].action == "added" or decisions[0].action == "raised"
