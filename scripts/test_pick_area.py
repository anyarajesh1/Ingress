"""Tests for study-area ranking (synthetic data, no network)."""
import pandas as pd

from pick_area import rank_boxes


def make_df():
    rows = []
    # A dense cluster of stairs + crossings near (500, 500)
    for i in range(30):
        rows.append({"x": 400 + i * 5, "y": 450 + (i % 5) * 20, "steps_m": 12.0, "crossings": 1, "sidewalk_m": 30.0})
    # Sparse sidewalks far away at (5000, 5000)
    for i in range(10):
        rows.append({"x": 5000 + i * 40, "y": 5000, "steps_m": 0.0, "crossings": 0, "sidewalk_m": 30.0})
    return pd.DataFrame(rows)


def test_densest_box_is_ranked_first():
    best = rank_boxes(make_df(), top=3)[0]
    assert best["steps_m"] > 0
    assert best["center_x"] < 2000 and best["center_y"] < 2000


def test_results_sorted_by_score():
    scores = [r["score"] for r in rank_boxes(make_df(), top=5)]
    assert scores == sorted(scores, reverse=True)


def test_top_candidates_do_not_overlap():
    picks = rank_boxes(make_df(), top=5)
    for i, a in enumerate(picks):
        for b in picks[i + 1:]:
            gap = max(abs(a["center_x"] - b["center_x"]), abs(a["center_y"] - b["center_y"]))
            assert gap >= 1000