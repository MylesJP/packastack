"""Tests for wave computation in dependency graphs."""

from packastack.planning.graph import DependencyGraph


def test_wave_computation_linear_chain():
    """Test wave computation on a linear dependency chain: A -> B -> C -> D."""
    graph = DependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")
    graph.add_node("D")
    
    # A depends on B, B on C, C on D
    graph.add_edge("A", "B")
    graph.add_edge("B", "C")
    graph.add_edge("C", "D")
    
    waves = graph.compute_waves()
    
    # D has no dependencies, so wave 0
    assert waves["D"] == 0
    # C depends only on D (wave 0), so wave 1
    assert waves["C"] == 1
    # B depends only on C (wave 1), so wave 2
    assert waves["B"] == 2
    # A depends only on B (wave 2), so wave 3
    assert waves["A"] == 3


def test_wave_computation_parallel_deps():
    """Test wave computation with parallel branches: A -> B, A -> C, B -> D, C -> D."""
    graph = DependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")
    graph.add_node("D")
    
    # A depends on both B and C
    graph.add_edge("A", "B")
    graph.add_edge("A", "C")
    # Both B and C depend on D
    graph.add_edge("B", "D")
    graph.add_edge("C", "D")
    
    waves = graph.compute_waves()
    
    # D has no dependencies, so wave 0
    assert waves["D"] == 0
    # B and C both depend only on D (wave 0), so wave 1
    assert waves["B"] == 1
    assert waves["C"] == 1
    # A depends on B and C (both wave 1), so wave 2
    assert waves["A"] == 2


def test_wave_computation_diamond():
    """Test wave computation on diamond dependency: A -> B, A -> C, B -> D, C -> D."""
    graph = DependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")
    graph.add_node("D")
    
    graph.add_edge("A", "B")
    graph.add_edge("A", "C")
    graph.add_edge("B", "D")
    graph.add_edge("C", "D")
    
    waves = graph.compute_waves()
    
    assert waves["D"] == 0
    assert waves["B"] == 1
    assert waves["C"] == 1
    assert waves["A"] == 2


def test_wave_computation_complex():
    """Test wave computation on a more complex graph."""
    graph = DependencyGraph()
    
    # Build a graph like:
    #       A
    #      / \
    #     B   C
    #    /|   |\
    #   D E   F G
    #    \|   |/
    #     H   I
    #      \ /
    #       J
    
    for node in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]:
        graph.add_node(node)
    
    graph.add_edge("A", "B")
    graph.add_edge("A", "C")
    graph.add_edge("B", "D")
    graph.add_edge("B", "E")
    graph.add_edge("C", "F")
    graph.add_edge("C", "G")
    graph.add_edge("D", "H")
    graph.add_edge("E", "H")
    graph.add_edge("F", "I")
    graph.add_edge("G", "I")
    graph.add_edge("H", "J")
    graph.add_edge("I", "J")
    
    waves = graph.compute_waves()
    
    # J at bottom (wave 0)
    assert waves["J"] == 0
    # H and I both depend on J (wave 1)
    assert waves["H"] == 1
    assert waves["I"] == 1
    # D, E depend on H; F, G depend on I (wave 2)
    assert waves["D"] == 2
    assert waves["E"] == 2
    assert waves["F"] == 2
    assert waves["G"] == 2
    # B depends on D and E (both wave 2), so wave 3
    assert waves["B"] == 3
    # C depends on F and G (both wave 2), so wave 3
    assert waves["C"] == 3
    # A depends on B and C (both wave 3), so wave 4
    assert waves["A"] == 4


def test_wave_computation_no_deps():
    """Test wave computation on isolated nodes with no dependencies."""
    graph = DependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")
    
    waves = graph.compute_waves()
    
    # All nodes have no dependencies, so all wave 0
    assert waves["A"] == 0
    assert waves["B"] == 0
    assert waves["C"] == 0


def test_wave_computation_single_node():
    """Test wave computation with a single node."""
    graph = DependencyGraph()
    graph.add_node("single")
    
    waves = graph.compute_waves()
    
    assert waves["single"] == 0


def test_forced_by_computation_linear():
    """Test forced-by computation on linear chain."""
    graph = DependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")
    graph.add_node("D")
    
    graph.add_edge("A", "B")
    graph.add_edge("B", "C")
    graph.add_edge("C", "D")
    
    waves = graph.compute_waves()
    forced_by = graph.compute_forced_by(waves)
    
    # D has no dependencies
    assert forced_by["D"] == []
    # C is forced by D (wave 0, C is wave 1)
    assert forced_by["C"] == ["D"]
    # B is forced by C (wave 1, B is wave 2)
    assert forced_by["B"] == ["C"]
    # A is forced by B (wave 2, A is wave 3)
    assert forced_by["A"] == ["B"]


def test_forced_by_computation_parallel():
    """Test forced-by computation with parallel paths."""
    graph = DependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")
    graph.add_node("D")
    
    graph.add_edge("A", "B")
    graph.add_edge("A", "C")
    graph.add_edge("B", "D")
    graph.add_edge("C", "D")
    
    waves = graph.compute_waves()
    forced_by = graph.compute_forced_by(waves)
    
    # D wave 0, no forced-by
    assert forced_by["D"] == []
    # B wave 1, forced by D
    assert forced_by["B"] == ["D"]
    # C wave 1, forced by D
    assert forced_by["C"] == ["D"]


def test_wave_computation_with_cycle_component():
    """Test wave computation when a dependency cycle exists."""
    graph = DependencyGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")

    graph.add_edge("A", "B")
    graph.add_edge("B", "A")
    graph.add_edge("C", "A")

    waves = graph.compute_waves_with_cycles()

    assert waves["A"] == 0
    assert waves["B"] == 0
    assert waves["C"] == 1

    cycle_edges = graph.get_cycle_edges()
    assert ("A", "B") in cycle_edges
    assert ("B", "A") in cycle_edges


def test_forced_by_computation_mixed_waves():
    """Test forced-by identifies only wave-1 predecessors."""
    graph = DependencyGraph()
    
    # A -> B -> D
    # A -> C -> D
    # A -> E (where E is wave 0)
    graph.add_node("A")
    graph.add_node("B")
    graph.add_node("C")
    graph.add_node("D")
    graph.add_node("E")
    
    graph.add_edge("A", "B")
    graph.add_edge("A", "C")
    graph.add_edge("A", "E")
    graph.add_edge("B", "D")
    graph.add_edge("C", "D")
    
    waves = graph.compute_waves()
    forced_by = graph.compute_forced_by(waves)
    
    # Waves: D=0, E=0, B=1, C=1, A=2
    assert waves["D"] == 0
    assert waves["E"] == 0
    assert waves["B"] == 1
    assert waves["C"] == 1
    assert waves["A"] == 2
    
    # A is forced by B and C (wave 1), NOT E (wave 0)
    # Because E is in wave 0, not wave 1
    assert set(forced_by["A"]) == {"B", "C"}


def test_forced_by_stable_order():
    """Test that forced-by list has stable ordering (alphabetical)."""
    graph = DependencyGraph()
    
    graph.add_node("top")
    for node in ["zebra", "alpha", "beta"]:
        graph.add_node(node)
        graph.add_edge("top", node)
    
    waves = graph.compute_waves()
    forced_by = graph.compute_forced_by(waves)
    
    # All three are in same wave (wave 0), top is wave 1
    # Forced-by should be alphabetically sorted
    assert forced_by["top"] == ["alpha", "beta", "zebra"]
