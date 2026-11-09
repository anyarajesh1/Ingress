"""Tests for the elevation / slope pipeline. No network access: a synthetic raster stands in for 3DEP."""
import math

import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

import build_graph as bg

LAT0, LON0 = 33.648, -117.836
SLOPE = 0.05  # the synthetic terrain rises 5% along the raster's x direction


def lonlat_to_5070(lon, lat):
    return Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform(lon, lat)


def make_raster(path):
    """A 400 m x 400 m tilted plane in EPSG:5070 with 1 m cells."""
    cx, cy = lonlat_to_5070(LON0, LAT0)
    x0, y0 = cx - 200, cy + 200
    cols = np.arange(400, dtype="float32")
    data = np.tile(50 + SLOPE * cols, (400, 1)).astype("float32")  # rises with x
    with rasterio.open(
        path, "w", driver="GTiff", height=400, width=400, count=1, dtype="float32",
        crs="EPSG:5070", transform=from_origin(x0, y0, 1, 1),
    ) as dst:
        dst.write(data, 1)
    return x0


def add_both(graph, u, v, osmid=1, **attrs):
    for a, b in ((u, v), (v, u)):
        ya, xa, yb, xb = graph.nodes[a]["y"], graph.nodes[a]["x"], graph.nodes[b]["y"], graph.nodes[b]["x"]
        length = ox.distance.great_circle(ya, xa, yb, xb)
        graph.add_edge(a, b, length=length, osmid=osmid, **attrs)


def make_graph():
    """East-west footway (3 nodes ~30 m apart), a north-south footway, a short link and stairs."""
    g = nx.MultiDiGraph(crs="EPSG:4326")
    dlon = 30 / (111320 * math.cos(math.radians(LAT0)))
    dlat = 30 / 110574
    g.add_node(1, x=LON0, y=LAT0)
    g.add_node(2, x=LON0 + dlon, y=LAT0)
    g.add_node(3, x=LON0 + 2 * dlon, y=LAT0)
    g.add_node(4, x=LON0, y=LAT0 + dlat)           # north of node 1
    g.add_node(5, x=LON0 + 2 * dlon + 0.00003, y=LAT0)   # ~3 m past node 3 (short link)
    g.add_node(6, x=LON0 + 2 * dlon + 0.00003, y=LAT0 + dlat)
    for u, v, osmid, hw in ((1, 2, 10, "footway"), (2, 3, 11, "footway"), (1, 4, 12, "footway"),
                            (3, 5, 13, "footway"), (5, 6, 14, "steps")):
        add_both(g, u, v, osmid=osmid, highway=hw)
    return g


@pytest.fixture()
def built(tmp_path):
    tif = tmp_path / "dem.tif"
    make_raster(tif)
    graph = bg.attach_elevation(make_graph(), tif)
    return bg.clean_grades(graph)


def test_recovers_known_slope_on_eastward_path(built):
    # Expected grade = SLOPE * (change in projected x) / length, from the raster's own geometry.
    for u, v in ((1, 2), (2, 1), (2, 3), (3, 2)):
        xu, _ = lonlat_to_5070(built.nodes[u]["x"], built.nodes[u]["y"])
        xv, _ = lonlat_to_5070(built.nodes[v]["x"], built.nodes[v]["y"])
        length = built[u][v][0]["length"]
        expected = SLOPE * (xv - xu) / length
        assert built[u][v][0]["grade"] == pytest.approx(expected, abs=0.004)


def test_reverse_direction_has_opposite_grade(built):
    assert built[1][2][0]["grade"] == pytest.approx(-built[2][1][0]["grade"], abs=1e-9)


def test_short_segments_and_stairs_are_not_reliable(built):
    assert built[3][5][0]["grade_reliable"] == 0      # ~3 m link is too short
    assert built[5][6][0]["grade_reliable"] == 0      # stairs are not slopes
    assert built[5][6][0]["grade_used"] == 0.0
    assert built[1][2][0]["grade_reliable"] == 1


def test_nodes_keep_lat_lon_coordinates_after_round_trip(built):
    assert built.nodes[1]["x"] == pytest.approx(LON0, abs=1e-6)
    assert built.nodes[1]["y"] == pytest.approx(LAT0, abs=1e-6)


def test_slope_stats_counts_each_physical_segment_once(built):
    stats = bg.slope_stats(built, {"name": "test"})
    assert stats["segments"]["physical_segments"] == 5   # 10 directed edges -> 5 physical
    assert stats["segments"]["stairs"] == 1
    assert stats["elevation_m"]["relief"] > 0
    assert "5%" in stats["steeper_than"]


def test_pictures_are_written(built, tmp_path):
    bg.plot_slope_map(built, tmp_path / "map.png", "test")
    bg.plot_histogram(built, tmp_path / "hist.png", "test")
    assert (tmp_path / "map.png").stat().st_size > 1000
    assert (tmp_path / "hist.png").stat().st_size > 1000


def test_missing_elevation_coverage_raises(tmp_path):
    tif = tmp_path / "dem.tif"
    make_raster(tif)
    g = make_graph()
    for n in g.nodes:          # move the whole graph far outside the raster
        g.nodes[n]["x"] += 1.0
    with pytest.raises(RuntimeError, match="outside the elevation raster"):
        bg.attach_elevation(g, tif)


def test_simplify_merges_chains_but_keeps_stairs_and_kerb_nodes():
    g = nx.MultiDiGraph(crs="EPSG:4326")
    for i in range(1, 6):
        g.add_node(i, x=LON0 + i * 0.0003, y=LAT0)
    g.nodes[3]["kerb"] = "lowered"
    # 1-2-3 footway, 3-4 footway, 4-5 steps
    for u, v, hw in ((1, 2, "footway"), (2, 3, "footway"), (3, 4, "footway"), (4, 5, "steps")):
        add_both(g, u, v, osmid=u * 10, highway=hw)
    simple = bg.simplify_for_routing(g)
    # 2 is a plain pass-through node (merged away); 3 carries a kerb tag so it stays; 4 joins footway/steps.
    assert 2 not in simple.nodes
    assert 3 in simple.nodes
    assert simple.number_of_edges() == 6   # 3 physical segments x 2 directions


def test_unique_physical_edges_handles_list_osmids_in_either_order():
    edges = pd.DataFrame(
        {"osmid": [[5, 6], [6, 5], 7], "length": [10.0, 10.0, 20.0]},
        index=pd.MultiIndex.from_tuples([(1, 2, 0), (2, 1, 0), (2, 3, 0)], names=["u", "v", "key"]),
    )
    assert len(bg.unique_physical_edges(edges)) == 2


def test_pick_resolution_prefers_finest_available():
    assert bg.pick_resolution({"1m": True, "10m": True}) == 1
    assert bg.pick_resolution({"1m": False, "3m": False, "5m": True, "10m": True}) == 5
    assert bg.pick_resolution({"1m": "Failed", "3m": "Failed", "5m": "Failed", "10m": "Failed"}) == 10


def test_nodata_values_are_rejected():
    g = nx.MultiDiGraph()
    for i in range(10):
        g.add_node(i, elevation=-9999.0)
    with pytest.raises(RuntimeError, match="no valid elevation"):
        bg.check_elevations(g, nodata=-9999.0)


def test_graphml_round_trip_through_the_real_pipeline(tmp_path):
    """Save -> load -> simplify -> elevation -> save -> load, as the real scripts do."""
    make_raster(tmp_path / "dem.tif")
    g = make_graph()
    g.nodes[2]["kerb"] = "lowered"
    ox.save_graphml(g, tmp_path / "walk.graphml")

    simple = bg.simplify_for_routing(ox.load_graphml(tmp_path / "walk.graphml"))
    assert 2 in simple.nodes                      # curb-ramp node kept as an endpoint
    built = bg.clean_grades(bg.attach_elevation(simple, tmp_path / "dem.tif"))
    ox.save_graphml(built, tmp_path / "routing.graphml")

    dtypes = {"grade": float, "grade_used": float, "grade_reliable": int, "grade_error": int, "length": float}
    back = ox.load_graphml(tmp_path / "routing.graphml", node_dtypes={"elevation": float}, edge_dtypes=dtypes)
    _, _, _, data = next(iter(back.edges(keys=True, data=True)))
    assert isinstance(data["grade_used"], float) and isinstance(data["grade_reliable"], int)
    stats = bg.slope_stats(back, {"name": "round-trip"})
    assert stats["segments"]["stairs"] == 1



class FakeDem:
    """Mimics the DataArray py3dep returns: it only needs a .rio.to_raster(path) method."""

    def __init__(self):
        self.rio = self

    def to_raster(self, path):
        open(path, "wb").write(b"fake")


def install_fake_py3dep(monkeypatch, failures, error=None):
    """Fake py3dep whose get_dem fails `failures` times (with `error`) before succeeding."""
    import sys
    import types

    calls = {"n": 0}

    def get_dem(bbox, resolution):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise error or Exception("502 Server Error: Bad Gateway for url: https://example.test")
        return FakeDem()

    module = types.SimpleNamespace(
        check_3dep_availability=lambda bbox: {"1m": True, "10m": True},
        get_dem=get_dem,
    )
    monkeypatch.setitem(sys.modules, "py3dep", module)
    return calls


def test_fetch_dem_retries_temporary_server_errors(monkeypatch, tmp_path):
    calls = install_fake_py3dep(monkeypatch, failures=2)
    waits = []
    used = bg.fetch_dem((0, 0, 1, 1), tmp_path / "dem.tif", sleep=waits.append)
    assert used == 1 and calls["n"] == 3
    assert waits == [10, 20]                       # growing waits between attempts
    assert (tmp_path / "dem.tif").exists()


def test_fetch_dem_does_not_retry_real_errors(monkeypatch, tmp_path):
    calls = install_fake_py3dep(monkeypatch, failures=5, error=ValueError("invalid bounding box"))
    waits = []
    with pytest.raises(ValueError):
        bg.fetch_dem((0, 0, 1, 1), tmp_path / "dem.tif", sleep=waits.append)
    assert calls["n"] == 1 and waits == []


def test_fetch_dem_gives_up_with_a_helpful_message(monkeypatch, tmp_path):
    install_fake_py3dep(monkeypatch, failures=99)
    with pytest.raises(RuntimeError, match="--resolution 10"):
        bg.fetch_dem((0, 0, 1, 1), tmp_path / "dem.tif", sleep=lambda s: None)