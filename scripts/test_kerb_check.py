"""Tests for the kerb summary logic (synthetic data, no network)."""
import pandas as pd

from kerb_check import analyze


def make_features():
    index = pd.MultiIndex.from_tuples(
        [("node", 1), ("node", 2), ("node", 3), ("way", 4), ("node", 5)], names=["element", "id"]
    )
    return pd.DataFrame(
        {
            "kerb": ["lowered", "raised", None, "flush", None],
            "highway": ["crossing", None, "crossing", None, None],
            "barrier": [None, "kerb", None, None, None],
            "wheelchair": [None, None, None, None, "yes"],
        },
        index=index,
    )


def test_counts_kerb_features_by_type_and_value():
    info = analyze(make_features())
    assert info["kerb"]["total"] == 3
    assert info["kerb"]["by_type"] == {"node": 2, "way": 1}
    assert info["kerb"]["by_value"]["lowered"] == 1


def test_separates_kerb_on_crossing_nodes_from_other_kerb_nodes():
    info = analyze(make_features())
    assert info["crossing_nodes"] == 2
    assert info["kerb_on_crossing_nodes"] == 1
    assert info["barrier_values_on_kerb_features"] == {"kerb": 1}


def test_empty_result_does_not_crash():
    info = analyze(pd.DataFrame())
    assert info["kerb"]["total"] == 0
    assert info["crossing_nodes"] == 0