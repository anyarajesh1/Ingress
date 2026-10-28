"""Unit tests for the coverage statistics (no network access needed)."""
import pandas as pd

from osm_coverage import compute_stats

META = {"name": "test", "lat": 0, "lon": 0, "dist_m": 100}


def make_tables():
    edges = pd.DataFrame(
        {
            "highway": ["footway", "footway", "steps", "path", ["footway", "path"], "residential"],
            "footway": ["sidewalk", "crossing", None, None, None, None],
            "surface": ["asphalt", None, None, "paved", None, "asphalt"],
            "incline": [None, None, "up", None, "3%", None],
            "length": [100.0, 12.0, 8.0, 60.0, 40.0, 200.0],
        }
    )
    nodes = pd.DataFrame(
        {
            "highway": ["crossing", "crossing", None, "crossing"],
            "kerb": ["lowered", None, None, "raised"],
        }
    )
    return nodes, edges


def test_counts_steps_sidewalks_crossings():
    nodes, edges = make_tables()
    seg = compute_stats(nodes, edges, META)["segments"]
    assert seg["steps"] == 1
    assert seg["sidewalks"] == 1
    assert seg["crossings"] == 1


def test_kerb_coverage_on_crossing_nodes():
    nodes, edges = make_tables()
    cn = compute_stats(nodes, edges, META)["crossing_nodes"]
    assert cn["total"] == 3
    assert cn["with_kerb_tag"] == 2


def test_tag_coverage_ignores_stairs_and_roads():
    nodes, edges = make_tables()
    cov = compute_stats(nodes, edges, META)["tag_coverage_on_pedestrian_ways"]
    # 4 non-stair pedestrian ways; 2 have a surface tag
    assert cov["surface"]["count"] == 2
    assert cov["surface"]["percent_of_pedestrian_ways"] == 50.0


def test_missing_tag_columns_do_not_crash():
    nodes, edges = make_tables()
    edges = edges.drop(columns=["surface", "incline"])
    cov = compute_stats(nodes, edges, META)["tag_coverage_on_pedestrian_ways"]
    assert cov["surface"]["count"] == 0




def test_crossings_with_kerb_tag_at_an_end():
    edges = pd.DataFrame(
        {
            "highway": ["footway", "footway", "footway"],
            "footway": ["crossing", "crossing", "sidewalk"],
            "length": [10.0, 10.0, 10.0],
        },
        index=pd.MultiIndex.from_tuples([(1, 2, 0), (3, 4, 0), (2, 3, 0)], names=["u", "v", "key"]),
    )
    nodes = pd.DataFrame({"kerb": ["lowered", None, None, None]}, index=pd.Index([1, 2, 3, 4], name="osmid"))
    result = compute_stats(nodes, edges, META)["crossings_with_kerb_tag"]
    assert result["total"] == 2
    assert result["with_kerb_tag_at_an_end"] == 1
    assert result["percent"] == 50.0


def test_list_strings_from_graphml_are_parsed():
    from osm_coverage import first

    assert first("['steps', 'footway']") == "steps"
    assert first("footway") == "footway"
    assert first(None) is None



def test_each_physical_segment_is_counted_once():
    from osm_coverage import undirected_unique

    # The same way stored in both directions, plus one separate one-way-only segment.
    edges = pd.DataFrame(
        {
            "highway": ["steps", "steps", "footway"],
            "osmid": [100, 100, 200],
            "length": [8.0, 8.0, 50.0],
        },
        index=pd.MultiIndex.from_tuples([(1, 2, 0), (2, 1, 0), (3, 4, 0)], names=["u", "v", "key"]),
    )
    assert len(undirected_unique(edges)) == 2
    stats = compute_stats(pd.DataFrame(index=pd.Index([1, 2, 3, 4], name="osmid")), edges, META)
    assert stats["segments"]["steps"] == 1
    assert stats["segments"]["steps_length_m"] == 8.0