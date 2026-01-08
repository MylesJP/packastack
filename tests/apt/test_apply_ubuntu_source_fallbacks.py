from packastack.apt.packages import PackageIndex, apply_ubuntu_source_fallbacks
from packastack.commands.plan import ResolvedTarget


def test_fallback_prefers_plain_base():
    idx = PackageIndex()
    idx.sources = {"stevedore": ["python-stevedore"]}

    t = ResolvedTarget(source_package="python-stevedore", upstream_project="stevedore", resolution_source="registry")
    apply_ubuntu_source_fallbacks(idx, [t], run=None)

    assert t.source_package == "stevedore"
    assert "+ub-fallback" in t.resolution_source


def test_fallback_chooses_python3_when_only_python3_exists():
    idx = PackageIndex()
    idx.sources = {"python3-stevedore": ["python3-stevedore"]}

    t = ResolvedTarget(source_package="stevedore", upstream_project="stevedore", resolution_source="registry")
    apply_ubuntu_source_fallbacks(idx, [t], run=None)

    assert t.source_package == "python3-stevedore"
    assert "+ub-fallback" in t.resolution_source


def test_fallback_no_match_leaves_target_alone():
    idx = PackageIndex()
    idx.sources = {"otherpkg": ["otherpkg"]}

    t = ResolvedTarget(source_package="does-not-exist", upstream_project="does-not-exist", resolution_source="registry")
    apply_ubuntu_source_fallbacks(idx, [t], run=None)

    assert t.source_package == "does-not-exist"
    assert "+ub-fallback" not in t.resolution_source


def test_normalize_upstream_without_index():
    # When no index is provided, upstream_project should be normalized
    # from a python- prefixed source package to the base deliverable.
    t = ResolvedTarget(source_package="python-stevedore", upstream_project="python-stevedore", resolution_source="registry")
    apply_ubuntu_source_fallbacks(None, [t], run=None)
    assert t.upstream_project == "stevedore"


def test_source_to_deliverable_helper():
    from packastack.commands.plan import _source_package_to_deliverable

    assert _source_package_to_deliverable("python-oslo.log") == "oslo.log"
    assert _source_package_to_deliverable("python-keystoneclient") == "keystoneclient"
    assert _source_package_to_deliverable("nova") == "nova"
