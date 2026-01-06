import pytest

from packastack.planning.targets import (
    MatchMode,
    TargetIdentity,
    TargetExpr,
    TargetParseError,
    TargetScope,
    detect_shell_expansion,
    parse_target_expr,
    resolve_targets,
)


@pytest.fixture()
def sample_identities() -> list[TargetIdentity]:
    return [
        TargetIdentity(
            source_package="nova",
            canonical_upstream="openstack/nova",
            deliverable_name="nova",
            governed_by_openstack=True,
            kind="service",
            aliases=("python-nova",),
            origin="registry",
        ),
        TargetIdentity(
            source_package="python-novaclient",
            canonical_upstream="openstack/python-novaclient",
            deliverable_name="python-novaclient",
            governed_by_openstack=True,
            kind="client",
            aliases=("novaclient",),
            origin="registry",
        ),
        TargetIdentity(
            source_package="gnocchi",
            canonical_upstream="gnocchixyz/gnocchi",
            deliverable_name=None,
            governed_by_openstack=False,
            kind="service",
            aliases=(),
            origin="registry",
        ),
    ]


def test_parse_modes_and_scopes():
    assert parse_target_expr("nova") == TargetExpr(
        raw="nova", scope=None, body="nova", mode=MatchMode.EXACT
    )

    parsed_prefix = parse_target_expr("^nova")
    assert parsed_prefix.mode is MatchMode.PREFIX
    assert parsed_prefix.body == "nova"

    parsed_contains = parse_target_expr("~nova")
    assert parsed_contains.mode is MatchMode.CONTAINS
    assert parsed_contains.body == "nova"

    parsed_glob = parse_target_expr("nova*")
    assert parsed_glob.mode is MatchMode.GLOB
    assert parsed_glob.body == "nova"

    scoped = parse_target_expr("deliverable:python-novaclient")
    assert scoped.scope is TargetScope.DELIVERABLE
    assert scoped.body == "python-novaclient"

    with pytest.raises(TargetParseError):
        parse_target_expr("")
    with pytest.raises(TargetParseError):
        parse_target_expr("unknownscope:nova")
    with pytest.raises(TargetParseError):
        parse_target_expr("nova with space")


def test_resolve_exact_and_scope(sample_identities):
    expr = parse_target_expr("nova")
    matches, mode = resolve_targets(expr, sample_identities)
    assert [m.source_package for m in matches] == ["nova"]
    assert mode is MatchMode.EXACT

    scoped_expr = parse_target_expr("deliverable:python-novaclient")
    matches, mode = resolve_targets(scoped_expr, sample_identities)
    assert [m.source_package for m in matches] == ["python-novaclient"]
    assert mode is MatchMode.EXACT


def test_resolve_contains_all_matches(sample_identities):
    extra = TargetIdentity(
        source_package="nova-api",
        canonical_upstream="openstack/nova-api",
        deliverable_name="nova-api",
        governed_by_openstack=True,
        kind="service",
        aliases=(),
        origin="registry",
    )

    expr = parse_target_expr("~nov")
    matches, mode = resolve_targets(
        expr, sample_identities + [extra], allow_all_matches=True
    )
    assert [m.source_package for m in matches] == [
        "nova",
        "nova-api",
        "python-novaclient",
    ]
    assert mode is MatchMode.CONTAINS


@pytest.mark.parametrize(
    "args,expected",
    [
        (["nova", "novaclient"], True),
        (["nova"], False),
        (["^nova", "python-novaclient"], False),
    ],
)
def test_detect_shell_expansion(args, expected):
    assert detect_shell_expansion(args) is expected
