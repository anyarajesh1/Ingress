"""Rank candidate 1 km x 1 km study areas inside the downloaded walking network.

A good demo area has real stairs and many crossings, so the accessible route
visibly differs from the shortest one. Elevation is not used yet (Day 2 adds it),
so treat this ranking as a first filter, then look at the top boxes on a map.

Usage:
    python scripts/pick_area.py --name uci                # print ranking
    python scripts/pick_area.py --name uci --pick 1       # save candidate #1
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd

from osm_coverage import column, first, undirected_unique

ROOT = Path(__file__).resolve().parents[1]
CELL_M = 250      # grid cell size in meters
BOX_CELLS = 4     # 4 x 4 cells = 1 km x 1 km candidate box


def norm(value):
    """Normalize tag values, including list-like strings that GraphML round trips create."""
    if isinstance(value, str) and value.startswith("["):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return first(value)


def classify_edges(edges: gpd.GeoDataFrame) -> pd.DataFrame:
    """Reduce an edge table (projected CRS, meters) to the columns the ranking needs."""
    hw = column(edges, "highway").apply(norm)
    fw = column(edges, "footway").apply(norm)
    length = pd.to_numeric(column(edges, "length"), errors="coerce").fillna(0)
    centers = edges.geometry.centroid
    return pd.DataFrame(
        {
            "x": centers.x.values,
            "y": centers.y.values,
            "steps_m": np.where(hw == "steps", length, 0.0),
            "crossings": (fw == "crossing").astype(int).values,
            "sidewalk_m": np.where(fw == "sidewalk", length, 0.0),
        }
    )


def rank_boxes(df: pd.DataFrame, cell: int = CELL_M, box_cells: int = BOX_CELLS, top: int = 5):
    """Slide a box over a grid of cells and rank by stairs, crossings and sidewalks."""
    df = df.copy()
    df["cx"] = np.floor(df["x"] / cell).astype(int)
    df["cy"] = np.floor(df["y"] / cell).astype(int)
    grid = df.groupby(["cx", "cy"])[["steps_m", "crossings", "sidewalk_m"]].sum()

    cx_min, cx_max = df["cx"].min(), df["cx"].max()
    cy_min, cy_max = df["cy"].min(), df["cy"].max()

    results = []
    for cx0 in range(cx_min, max(cx_max - box_cells + 2, cx_min + 1)):
        for cy0 in range(cy_min, max(cy_max - box_cells + 2, cy_min + 1)):
            window = grid[
                (grid.index.get_level_values("cx") >= cx0)
                & (grid.index.get_level_values("cx") < cx0 + box_cells)
                & (grid.index.get_level_values("cy") >= cy0)
                & (grid.index.get_level_values("cy") < cy0 + box_cells)
            ]
            if window.empty:
                continue
            steps_m = float(window["steps_m"].sum())
            crossings = int(window["crossings"].sum())
            sidewalk_km = float(window["sidewalk_m"].sum()) / 1000
            score = steps_m / 10 + crossings + sidewalk_km * 5
            results.append(
                {
                    "center_x": (cx0 + box_cells / 2) * cell,
                    "center_y": (cy0 + box_cells / 2) * cell,
                    "steps_m": round(steps_m, 1),
                    "crossings": crossings,
                    "sidewalk_km": round(sidewalk_km, 2),
                    "score": round(score, 1),
                }
            )
    results.sort(key=lambda r: r["score"], reverse=True)

    # Keep only candidates that are not overlapping an already chosen, better box.
    min_gap = cell * box_cells
    chosen: list[dict] = []
    for r in results:
        far_enough = all(
            max(abs(r["center_x"] - c["center_x"]), abs(r["center_y"] - c["center_y"])) >= min_gap
            for c in chosen
        )
        if far_enough:
            chosen.append(r)
        if len(chosen) == top:
            break
    return chosen


def to_latlon(x: float, y: float, crs) -> tuple[float, float]:
    point = gpd.GeoSeries(gpd.points_from_xy([x], [y]), crs=crs).to_crs(4326)
    return round(float(point.y.iloc[0]), 5), round(float(point.x.iloc[0]), 5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="uci", help="name used when the graph was built")
    parser.add_argument("--pick", type=int, help="save candidate N (1-based) to docs/study_area.json")
    args = parser.parse_args()

    graph_path = ROOT / "backend" / "data" / f"walk_{args.name}.graphml"
    print(f"Loading {graph_path.relative_to(ROOT)} ...")
    graph = ox.load_graphml(graph_path)
    edges = undirected_unique(ox.graph_to_gdfs(graph, nodes=False))
    edges = ox.projection.project_gdf(edges)
    candidates = rank_boxes(classify_edges(edges))
    rows = []
    for i, c in enumerate(candidates, start=1):
        lat, lon = to_latlon(c["center_x"], c["center_y"], edges.crs)
        rows.append({"rank": i, "lat": lat, "lon": lon, **{k: c[k] for k in ("steps_m", "crossings", "sidewalk_km", "score")}})
    table = pd.DataFrame(rows)
    print("\nTop candidate 1 km x 1 km boxes (no elevation yet):\n")
    print(table.to_string(index=False))
    print("\nPaste each lat,lon into Google Maps to see what the area looks like.")

    if args.pick:
        chosen = rows[args.pick - 1]
        out = {
            "name": f"{args.name}-core",
            "lat": chosen["lat"],
            "lon": chosen["lon"],
            "half_width_m": 500,
            "chosen_on": datetime.date.today().isoformat(),
            "basis": "stairs length, crossings and sidewalk length (elevation not yet considered)",
            "stats": {k: chosen[k] for k in ("steps_m", "crossings", "sidewalk_km")},
        }
        path = ROOT / "docs" / "study_area.json"
        path.write_text(json.dumps(out, indent=2))
        print(f"\nSaved candidate #{args.pick} -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()