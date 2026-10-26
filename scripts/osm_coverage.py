"""Measure how much accessibility-relevant data OpenStreetMap has for a study area.

Downloads a walking network around a point, then reports:
  - how many stairs, sidewalks and crossings exist
  - what share of pedestrian ways carry surface / width / incline / wheelchair tags
  - what share of crossing nodes carry kerb (curb ramp) tags

This answers the first project question honestly: is there enough data in this
area for accessibility-aware routing to be meaningful?

Usage:
    pip install -r scripts/requirements.txt
    python scripts/osm_coverage.py --name uci --lat 33.6460 --lon -117.8427 --dist 1500
"""

from __future__ import annotations

import argparse
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
EXTRA_NODE_TAGS = ["kerb", "wheelchair", "tactile_paving", "crossing"]

# Highway values that represent pedestrian infrastructure.
PEDESTRIAN_WAYS = {"footway", "path", "pedestrian", "steps", "corridor"}


def first(value):
    """OSMnx sometimes returns lists for merged attributes. Take the first item."""
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


def compute_stats(nodes: pd.DataFrame, edges: pd.DataFrame, meta: dict) -> dict:
    """Pure function over the node/edge tables so it can be unit tested."""
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
        "crossing_nodes": {
            "total": int(crossing_nodes.sum()),
            "with_kerb_tag": kerb_on_crossings,
            "percent_with_kerb_tag": pct(kerb_on_crossings, int(crossing_nodes.sum())),
            "note": "approximate: assumes kerb tags sit on highway=crossing nodes",
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
    cn = stats["crossing_nodes"]
    print(f"\nCrossing nodes: {cn['total']}   with kerb tag: {cn['with_kerb_tag']} "
          f"({cn['percent_with_kerb_tag']}%)")
    print("\nData © OpenStreetMap contributors (ODbL)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="uci", help="short name for output files")
    parser.add_argument("--lat", type=float, default=33.6460, help="center latitude")
    parser.add_argument("--lon", type=float, default=-117.8427, help="center longitude")
    parser.add_argument("--dist", type=int, default=1500, help="half-width in meters")
    args = parser.parse_args()

    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | set(EXTRA_WAY_TAGS))
    ox.settings.useful_tags_node = sorted(set(ox.settings.useful_tags_node) | set(EXTRA_NODE_TAGS))

    print(f"Downloading walking network around ({args.lat}, {args.lon}) ...")
    # simplify=False keeps every OSM node so kerb/crossing tags are not merged away.
    graph = ox.graph_from_point(
        (args.lat, args.lon), dist=args.dist, dist_type="bbox",
        network_type="walk", simplify=False,
    )
    nodes, edges = ox.graph_to_gdfs(graph)

    meta = {"name": args.name, "lat": args.lat, "lon": args.lon, "dist_m": args.dist}
    stats = compute_stats(nodes, edges, meta)
    print_report(stats)

    docs_dir = ROOT / "docs"
    data_dir = ROOT / "backend" / "data"
    docs_dir.mkdir(exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    out_json = docs_dir / f"coverage_{args.name}.json"
    out_json.write_text(json.dumps(stats, indent=2))
    ox.save_graphml(graph, data_dir / f"walk_{args.name}.graphml")
    print(f"Saved report -> {out_json.relative_to(ROOT)}")
    print(f"Saved graph  -> backend/data/walk_{args.name}.graphml (gitignored)")


if __name__ == "__main__":
    main()