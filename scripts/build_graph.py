"""Build the routing graph: simplified walking network + elevation + slopes.

Steps:
  1. Load the walking network saved by osm_coverage.py for the study area.
  2. Simplify it so long paths become single segments (more reliable slopes),
     without merging stairs, crossings or curb-ramp nodes away.
  3. Download elevation (USGS 3DEP) for the area and attach it to every node.
  4. Compute each segment's slope (grade) and flag the unreliable ones.
  5. Save the routing graph, slope statistics, and two pictures.

Usage (from the repo root):
    pip install -r scripts/requirements-elevation.txt
    python scripts/build_graph.py
    python scripts/build_graph.py --resolution 10     # force a coarser DEM
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import time
from pathlib import Path

import numpy as np
import osmnx as ox
import pandas as pd

from osm_coverage import column, first

ROOT = Path(__file__).resolve().parents[1]

# Nodes carrying these tags stay as segment endpoints (curb ramps, crossings, signals).
NODE_KEEP = ["kerb", "highway", "barrier"]
# Neighboring edges are only merged when all of these tags match, so stairs,
# crossings and surface changes stay separate segments.
EDGE_DIFFER = ["highway", "footway", "surface", "incline", "ramp", "handrail", "bridge", "tunnel"]

SLOPE_ERROR = 0.40      # |grade| above 40% on a non-stair segment is treated as a data error
MIN_RELIABLE_M = 8.0    # segments shorter than this have unreliable slopes
THRESHOLDS = (0.05, 0.0833, 0.10)   # 1:20, 1:12 and 10%, common accessibility reference points
DEM_PAD_DEG = 0.0015    # padding around the graph when requesting elevation (~150 m)


# ----------------------------------------------------------------------------- graph


def simplify_for_routing(graph):
    """Merge chains of identical path pieces into single segments."""
    return ox.simplify_graph(graph.copy(), node_attrs_include=NODE_KEEP, edge_attrs_differ=EDGE_DIFFER)


def graph_bbox(graph, pad: float = DEM_PAD_DEG) -> tuple[float, float, float, float]:
    """(west, south, east, north) of the graph in degrees, with padding."""
    nodes = ox.graph_to_gdfs(graph, edges=False)
    west, south, east, north = nodes.total_bounds
    return float(west - pad), float(south - pad), float(east + pad), float(north + pad)


# ----------------------------------------------------------------------------- elevation


def pick_resolution(availability: dict, preferred=(1, 3, 5, 10)) -> int:
    """Best available 3DEP resolution in meters (falls back to 10 m)."""
    for res in preferred:
        if availability.get(f"{res}m") is True:
            return res
    return 10


BUSY_HINTS = ("502", "503", "504", "429", "timeout", "timed out", "bad gateway", "unavailable", "connection")


def show(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def fetch_dem(bbox, tif_path: Path, resolution: int | None = None, attempts: int = 4,
              wait: int = 10, sleep=time.sleep) -> int:
    """Download a DEM for the bounding box and save it as a GeoTIFF. Returns the resolution used.

    The public USGS servers are sometimes overloaded (HTTP 502/503), so temporary errors
    are retried with growing waits. Other errors are raised immediately.
    """
    import py3dep  # imported here so the unit tests do not need this heavy package

    availability = py3dep.check_3dep_availability(bbox)
    print(f"3DEP availability: {availability}")
    if resolution is None:
        resolution = pick_resolution(availability)

    for attempt in range(1, attempts + 1):
        print(f"Requesting a {resolution} m DEM (attempt {attempt}/{attempts}) ...")
        try:
            dem = py3dep.get_dem(bbox, resolution)
            dem.rio.to_raster(tif_path)
            print(f"Saved DEM -> {show(tif_path)}")
            return resolution
        except Exception as exc:
            text = f"{type(exc).__name__} {exc}".lower()
            if not any(hint in text for hint in BUSY_HINTS):
                raise
            if attempt < attempts:
                delay = wait * 2 ** (attempt - 1)
                print(f"  USGS server is busy. Waiting {delay}s before retrying ...")
                sleep(delay)
    raise RuntimeError(
        f"The USGS server did not deliver a {resolution} m DEM after {attempts} attempts. "
        "Try again in a few minutes, or choose a coarser file explicitly with --resolution 10 "
        "(coarser data gives less accurate slopes)."
    )


def attach_elevation(graph, tif_path: Path):
    """Add node elevations from a raster, then edge grades. Returns a lat/lon graph."""
    import rasterio

    with rasterio.open(tif_path) as src:
        raster_crs = src.crs.to_string()
        bounds = src.bounds
        nodata = src.nodata
    projected = ox.project_graph(graph, to_crs=raster_crs)
    check_inside_raster(projected, bounds)
    # cpus=1 avoids multiprocessing problems on macOS.
    projected = ox.elevation.add_node_elevations_raster(projected, tif_path, cpus=1)
    check_elevations(projected, nodata=nodata)
    projected = ox.elevation.add_edge_grades(projected, add_absolute=True)
    return ox.project_graph(projected, to_latlong=True)


def check_inside_raster(graph, bounds, max_outside: float = 0.01) -> None:
    """Fail loudly if nodes fall outside the DEM. Sampling outside a raster silently returns 0 m,
    which would create fake cliffs, so this must be checked before sampling."""
    xs = np.array([d["x"] for _, d in graph.nodes(data=True)], dtype=float)
    ys = np.array([d["y"] for _, d in graph.nodes(data=True)], dtype=float)
    outside = (xs < bounds.left) | (xs > bounds.right) | (ys < bounds.bottom) | (ys > bounds.top)
    if len(xs) and outside.mean() > max_outside:
        raise RuntimeError(
            f"{int(outside.sum())} of {len(xs)} nodes lie outside the elevation raster. "
            "Request a DEM that covers the whole graph."
        )


def check_elevations(graph, low: float = -100.0, high: float = 4500.0, nodata=None) -> None:
    """Stop early if too many nodes got no valid elevation (gaps, nodata values)."""
    values = np.array([d.get("elevation", np.nan) for _, d in graph.nodes(data=True)], dtype=float)
    bad = ~np.isfinite(values) | (values < low) | (values > high)
    if nodata is not None and np.isfinite(nodata):
        bad |= values == nodata
    share = bad.mean() if len(values) else 1.0
    if share > 0.01:
        raise RuntimeError(
            f"{bad.sum()} of {len(values)} nodes ({share:.1%}) have no valid elevation. "
            "The DEM may not cover the whole area."
        )


# ----------------------------------------------------------------------------- slopes


def as_list(value):
    """Turn an osmid (int, list, or list-like string) into a list of strings."""
    if isinstance(value, str) and value.startswith("["):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return [value]
    if isinstance(value, (list, tuple, set)):
        return sorted(str(v) for v in value)
    return [str(value)]


def unique_physical_edges(edges: pd.DataFrame) -> pd.DataFrame:
    """One row per physical segment (OSMnx stores walkable segments in both directions)."""
    if not isinstance(edges.index, pd.MultiIndex) or edges.index.nlevels < 2:
        return edges
    u = edges.index.get_level_values(0)
    v = edges.index.get_level_values(1)
    ways = column(edges, "osmid").apply(as_list)
    keys = pd.Series([(min(a, b), max(a, b), tuple(w)) for a, b, w in zip(u, v, ways)])
    return edges[~keys.duplicated().values]


def clean_grades(graph):
    """Flag unreliable slopes. Adds grade_used, grade_reliable and grade_error to every edge.

    - Stairs are not slopes; they are blocked or allowed by profile rules later.
    - Segments shorter than MIN_RELIABLE_M have noisy slopes (small elevation errors
      are large relative to a short run), so they are flagged, not trusted.
    - |grade| above SLOPE_ERROR on a non-stair segment is treated as a data error.
    Unreliable segments get grade_used = 0.0 and are counted as "unknown" by the router.
    """
    for _, _, _, data in graph.edges(keys=True, data=True):
        highway = first(data.get("highway"))
        grade = data.get("grade")
        length = float(data.get("length", 0) or 0)
        valid = grade is not None and math.isfinite(grade)
        is_steps = highway == "steps"
        error = valid and (not is_steps) and abs(grade) > SLOPE_ERROR
        reliable = valid and (not is_steps) and (not error) and length >= MIN_RELIABLE_M
        data["grade_error"] = int(bool(error) or not valid)
        data["grade_reliable"] = int(bool(reliable))
        data["grade_used"] = float(grade) if reliable else 0.0
    return graph


def share(count: int, total: int):
    return round(100 * count / total, 1) if total else None


def slope_stats(graph, meta: dict) -> dict:
    """Summary numbers for the README and for choosing / confirming the study area."""
    edges = unique_physical_edges(ox.graph_to_gdfs(graph, nodes=False))
    nodes = ox.graph_to_gdfs(graph, edges=False)
    hw = column(edges, "highway").apply(first)
    steps = hw == "steps"
    reliable = edges["grade_reliable"].astype(int) == 1
    length = pd.to_numeric(edges["length"], errors="coerce").fillna(0)
    grade_abs = edges["grade_used"].astype(float).abs()

    elev = pd.to_numeric(nodes["elevation"], errors="coerce")
    stats = {
        "area": meta,
        "elevation_m": {
            "min": round(float(elev.min()), 1),
            "max": round(float(elev.max()), 1),
            "relief": round(float(elev.max() - elev.min()), 1),
        },
        "segments": {
            "physical_segments": int(len(edges)),
            "total_length_km": round(float(length.sum()) / 1000, 2),
            "stairs": int(steps.sum()),
            "reliable_slope": int(reliable.sum()),
            "unreliable_short_or_missing": int((~reliable & ~steps).sum()),
            "data_errors": int((edges["grade_error"].astype(int) == 1).sum()),
        },
        "steeper_than": {},
        "grade_abs_percentiles_percent": {},
    }
    rel_len = float(length[reliable].sum())
    for t in THRESHOLDS:
        over = reliable & (grade_abs > t)
        label = f"{round(t * 100, 2):g}%"
        stats["steeper_than"][label] = {
            "segments": int(over.sum()),
            "percent_of_reliable_segments": share(int(over.sum()), int(reliable.sum())),
            "percent_of_reliable_length": share(float(length[over].sum()), rel_len),
        }
    if reliable.any():
        for p in (50, 90, 99):
            stats["grade_abs_percentiles_percent"][f"p{p}"] = round(float(np.percentile(grade_abs[reliable], p)) * 100, 2)
    return stats


# ----------------------------------------------------------------------------- pictures


def plot_slope_map(graph, path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    edges = unique_physical_edges(ox.graph_to_gdfs(graph, nodes=False)).copy()
    edges["grade_abs"] = edges["grade_used"].astype(float).abs()
    steps = column(edges, "highway").apply(first) == "steps"
    reliable = edges["grade_reliable"].astype(int) == 1

    fig, ax = plt.subplots(figsize=(9, 9))
    edges[~reliable & ~steps].plot(ax=ax, color="lightgray", linewidth=0.8)
    edges[reliable & ~steps].plot(
        ax=ax, column="grade_abs", cmap="RdYlGn_r", vmin=0, vmax=0.12, linewidth=1.4,
        legend=True, legend_kwds={"label": "absolute slope (0 to 12%+)", "shrink": 0.6},
    )
    if steps.any():
        edges[steps].plot(ax=ax, color="black", linewidth=2.2)
    ax.set_title(f"{title}\ncolored = reliable slope, gray = unreliable/short, black = stairs")
    ax.set_axis_off()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_histogram(graph, path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    edges = unique_physical_edges(ox.graph_to_gdfs(graph, nodes=False))
    reliable = edges["grade_reliable"].astype(int) == 1
    grade = edges.loc[reliable, "grade_used"].astype(float).abs() * 100

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(grade.clip(upper=20), bins=40, color="#4c78a8")
    for t, label in ((5, "5% (1:20)"), (8.33, "8.33% (1:12)")):
        ax.axvline(t, color="crimson", linestyle="--", linewidth=1)
        ax.text(t + 0.1, ax.get_ylim()[1] * 0.92, label, color="crimson", fontsize=8)
    ax.set_xlabel("absolute slope (%), values above 20% shown at 20")
    ax.set_ylabel("segments")
    ax.set_title(title)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default=str(ROOT / "docs" / "study_area.json"))
    parser.add_argument("--resolution", type=int, help="force a DEM resolution in meters (1, 3, 5, 10)")
    args = parser.parse_args()

    area = json.loads(Path(args.area).read_text())
    name = area["name"]
    data_dir = ROOT / "backend" / "data"
    docs_dir = ROOT / "docs"
    (docs_dir / "img").mkdir(parents=True, exist_ok=True)

    source = data_dir / f"walk_{name}.graphml"
    if not source.exists():
        raise SystemExit(
            f"Missing {source.relative_to(ROOT)}. Run:\n  python scripts/osm_coverage.py --name {name} "
            f"--lat {area['lat']} --lon {area['lon']} --dist {area['half_width_m']}"
        )

    print(f"Loading {source.relative_to(ROOT)} ...")
    raw = ox.load_graphml(source)
    print(f"  raw graph: {raw.number_of_nodes()} nodes, {raw.number_of_edges()} edges (both directions)")

    graph = simplify_for_routing(raw)
    print(f"  simplified: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")

    tif = data_dir / f"dem_{name}.tif"
    resolution = fetch_dem(graph_bbox(graph), tif, args.resolution)

    print("Attaching elevation and computing slopes ...")
    graph = clean_grades(attach_elevation(graph, tif))

    meta = {"name": name, "dem_resolution_m": resolution, "dem_source": "USGS 3DEP", "built_on": str(pd.Timestamp.today().date())}
    stats = slope_stats(graph, meta)
    (docs_dir / f"slope_stats_{name}.json").write_text(json.dumps(stats, indent=2))
    plot_slope_map(graph, docs_dir / "img" / f"slope_map_{name}.png", f"Slope map: {name} ({resolution} m DEM)")
    plot_histogram(graph, docs_dir / "img" / f"slope_hist_{name}.png", f"Slope distribution: {name}")
    ox.save_graphml(graph, data_dir / f"routing_{name}.graphml")

    print("\n=== Slope summary ===")
    print(json.dumps({k: stats[k] for k in ("elevation_m", "segments", "steeper_than", "grade_abs_percentiles_percent")}, indent=2))
    print(f"\nSaved: docs/slope_stats_{name}.json, docs/img/slope_map_{name}.png, "
          f"docs/img/slope_hist_{name}.png, backend/data/routing_{name}.graphml (gitignored)")


if __name__ == "__main__":
    main()