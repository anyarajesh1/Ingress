"""Measure how much accessibility-relevant data OpenStreetMap has for a study area.

Downloads a walking network around a point, then reports:
  - how many stairs, sidewalks and crossings exist
  - what share of pedestrian ways carry surface / width / incline / wheelchair tags
  - what share of crossings have a kerb (curb ramp) tag at one of their ends

This answers the first project question honestly: is there enough data in this
area for accessibility-aware routing to be meaningful?

Usage:
    pip install -r scripts/requirements.txt
    python scripts/osm_coverage.py --name uci --lat 33.6460 --lon -117.8427 --dist 1500
    python scripts/osm_coverage.py --name uci --offline     # reuse the saved graph, no download
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import osmnx as ox
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# OSMnx drops most tags by default. Keep the ones accessibility routing needs.
EXTRA_WAY_TAGS = [
    "footway", "crossing", "kerb", "wheelchair", "incline", "surface",
    "smoothness", "width", "tactile_paving", "handrail", "ramp", "lit",
]
EXTRA_NODE_TAGS = ["kerb", "wheelchair", "tactile_paving", "crossing", "barrier"]

# Highway values that represent pedestrian infrastructure.
PEDESTRIAN_WAYS = {"footway", "path", "pedestrian", "steps", "corridor"}


def first(value):
    """Take the first item of list-like values (real lists, or list strings from GraphML)."""
    if isinstance(value, str) and value.startswith("["):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def present(series: pd.Series) -> pd.Series:
    """True where a tag holds a real value (not missing, not empty)."""

    def ok(v):
        if isinstance(v, (list, tuple, set)):
            return len(v) > 0
        return pd.notna(v) and v != ""

    return series.apply(ok)


def column(df: pd.DataFrame, name: str) -> pd.Series:
    """Return a column, or an all-missing series if the tag never appeared."""
    if name in df.columns:
        return df[name]
    return pd.Series([None] * len(df), index=df.index, dtype=object)


def pct(part: int, whole: int):
    return round(100 * part / whole, 1) if whole else None



def undirected_unique(edges: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per physical segment.

    OSMnx walking graphs store every segment twice (u->v and v->u), which doubles
    counts and lengths. Rows are merged when they connect the same two nodes and
    come from the same OpenStreetMap way.
    """
    if not isinstance(edges.index, pd.MultiIndex) or edges.index.nlevels < 2:
        return edges
    u = edges.index.get_level_values(0)
    v = edges.index.get_level_values(1)
    way = column(edges, "osmid").astype(str).values
    keys = pd.Series([(min(a, b), max(a, b), w) for a, b, w in zip(u, v, way)])
    return edges[~keys.duplicated().values]


def crossings_with_kerb(nodes: pd.DataFrame, edges: pd.DataFrame, is_crossing: pd.Series) -> dict:
    """Count crossing segments that have a kerb tag on at least one end node.

    Curb-ramp tags usually sit on the nodes where a sidewalk meets a crossing,
    not on the crossing way itself, so we look at each crossing's two end nodes.
    """
    kerb_nodes = set(nodes.index[present(column(nodes, "kerb")).values])
    total = int(is_crossing.sum())
    if not isinstance(edges.index, pd.MultiIndex) or edges.index.nlevels < 2:
        return {"total": total, "with_kerb_tag_at_an_end": None, "percent": None,
                "kerb_tagged_nodes_in_network": len(kerb_nodes)}
    us = edges.index.get_level_values(0)
    vs = edges.index.get_level_values(1)
    touches = [(u in kerb_nodes) or (v in kerb_nodes) for u, v in zip(us, vs)]
    hit = int((pd.Series(touches).values & is_crossing.values).sum())
    return {
        "total": total,
        "with_kerb_tag_at_an_end": hit,
        "percent": pct(hit, total),
        "kerb_tagged_nodes_in_network": len(kerb_nodes),
    }


def compute_stats(nodes: pd.DataFrame, edges: pd.DataFrame, meta: dict) -> dict:
    """Pure function over the node/edge tables so it can be unit tested."""
    edges = undirected_unique(edges)
    hw = column(edges, "highway").apply(first)
    fw = column(edges, "footway").apply(first)
    length = pd.to_numeric(column(edges, "length"), errors="coerce").fillna(0)

    is_steps = hw == "steps"
    is_ped = hw.isin(PEDESTRIAN_WAYS)
    is_crossing = fw == "crossing"
    is_sidewalk = fw == "sidewalk"
    flat_ped = is_ped & ~is_steps  # pedestrian ways that are not stairs

    ped_total = int(flat_ped.sum())
    tag_coverage = {}
    for tag in ["surface", "width", "incline", "wheelchair", "smoothness"]:
        has = present(column(edges, tag)) & flat_ped
        tag_coverage[tag] = {
            "count": int(has.sum()),
            "percent_of_pedestrian_ways": pct(int(has.sum()), ped_total),
        }

    node_hw = column(nodes, "highway").apply(first)
    crossing_nodes = node_hw == "crossing"
    kerb_present = present(column(nodes, "kerb"))
    kerb_on_crossings = int((kerb_present & crossing_nodes).sum())

    return {
        "area": meta,
        "segments": {
            "total_edges": int(len(edges)),
            "total_length_km": round(float(length.sum()) / 1000, 2),
            "pedestrian_ways": int(is_ped.sum()),
            "steps": int(is_steps.sum()),
            "steps_length_m": round(float(length[is_steps].sum()), 1),
            "sidewalks": int(is_sidewalk.sum()),
            "crossings": int(is_crossing.sum()),
        },
        "tag_coverage_on_pedestrian_ways": tag_coverage,
        "crossings_with_kerb_tag": crossings_with_kerb(nodes, edges, is_crossing),
        "crossing_nodes": {
            "total": int(crossing_nodes.sum()),
            "with_kerb_tag": kerb_on_crossings,
            "percent_with_kerb_tag": pct(kerb_on_crossings, int(crossing_nodes.sum())),
            "note": "narrow metric: only kerb tags on nodes that are themselves highway=crossing",
        },
    }


def print_report(stats: dict) -> None:
    area, seg = stats["area"], stats["segments"]
    print(f"\n=== Ingress coverage report: {area['name']} ===")
    print(f"Center: ({area['lat']}, {area['lon']})  radius/bbox: {area['dist_m']} m")
    print(f"Segments: {seg['total_edges']}  ({seg['total_length_km']} km)")
    print(f"Pedestrian ways: {seg['pedestrian_ways']}   Sidewalks: {seg['sidewalks']}   "
          f"Crossings: {seg['crossings']}")
    print(f"Steps: {seg['steps']} segments ({seg['steps_length_m']} m)")
    print("\nTag coverage on pedestrian ways (non-stairs):")
    for tag, info in stats["tag_coverage_on_pedestrian_ways"].items():
        print(f"  {tag:<12} {info['count']:>5}  ({info['percent_of_pedestrian_ways']}%)")
    ck = stats["crossings_with_kerb_tag"]
    print(f"\nCrossings with a kerb tag at an end: {ck['with_kerb_tag_at_an_end']} of "
          f"{ck['total']} ({ck['percent']}%)")
    print(f"Kerb-tagged nodes in the network: {ck['kerb_tagged_nodes_in_network']}")
    cn = stats["crossing_nodes"]
    print(f"(Narrow metric: {cn['with_kerb_tag']} of {cn['total']} highway=crossing nodes carry kerb)")
    print("\nData © OpenStreetMap contributors (ODbL)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="uci", help="short name for output files")
    parser.add_argument("--lat", type=float, default=33.6460, help="center latitude")
    parser.add_argument("--lon", type=float, default=-117.8427, help="center longitude")
    parser.add_argument("--dist", type=int, default=1500, help="half-width in meters")
    parser.add_argument("--offline", action="store_true",
                        help="reuse backend/data/walk_<name>.graphml instead of downloading")
    args = parser.parse_args()

    data_dir = ROOT / "backend" / "data"
    docs_dir = ROOT / "docs"
    graph_path = data_dir / f"walk_{args.name}.graphml"
    docs_dir.mkdir(exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.offline:
        print(f"Loading saved graph {graph_path.relative_to(ROOT)} ...")
        graph = ox.load_graphml(graph_path)
    else:
        ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | set(EXTRA_WAY_TAGS))
        ox.settings.useful_tags_node = sorted(set(ox.settings.useful_tags_node) | set(EXTRA_NODE_TAGS))
        print(f"Downloading walking network around ({args.lat}, {args.lon}) ...")
        # simplify=False keeps every OSM node so kerb/crossing tags are not merged away.
        graph = ox.graph_from_point(
            (args.lat, args.lon), dist=args.dist, dist_type="bbox",
            network_type="walk", simplify=False,
        )
        ox.save_graphml(graph, graph_path)
        print(f"Saved graph  -> backend/data/walk_{args.name}.graphml (gitignored)")

    nodes, edges = ox.graph_to_gdfs(graph)
    meta = {"name": args.name, "lat": args.lat, "lon": args.lon, "dist_m": args.dist,
            "source": "saved graph" if args.offline else "overpass download"}
    stats = compute_stats(nodes, edges, meta)
    print_report(stats)

    out_json = docs_dir / f"coverage_{args.name}.json"
    out_json.write_text(json.dumps(stats, indent=2))
    print(f"Saved report -> {out_json.relative_to(ROOT)}")


if __name__ == "__main__":
    main()