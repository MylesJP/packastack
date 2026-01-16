from packastack.apt.packages import BinaryPackage, PackageIndex
from packastack.planning.graph_builder import build_graph_from_index


def make_index_with_deps():
    idx = PackageIndex()
    # Source 'a' provides binary 'a-bin' and depends on 'b-bin'
    a = BinaryPackage(name="a-bin", version="1.0", architecture="all", source="a", depends=["b-bin (>= 1.0)"])
    b = BinaryPackage(name="b-bin", version="1.0", architecture="all", source="b", depends=[])
    idx.add_package(a, component="main", pocket="release")
    idx.add_package(b, component="main", pocket="release")
    return idx


def test_build_graph_from_index_simple():
    idx = make_index_with_deps()
    res = build_graph_from_index(packages=["a"], package_index=idx, openstack_packages=None)
    # Graph should contain node 'a' and an edge to 'b' via the dependency
    assert "a" in res.graph.nodes
    # Since openstack_packages defaults to the provided packages set,
    # 'b' will not be treated as an OpenStack package and therefore
    # will not be added as a rebuild node; ensure the graph reflects that.
    assert "b" not in res.graph.nodes
    # No missing deps should be reported because dependency resolution
    # found a binary providing the dependency.
    assert res.missing_deps == {}
