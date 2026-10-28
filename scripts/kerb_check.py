"""Check how much curb-ramp (kerb) data raw OpenStreetMap has around the study area.

Makes ONE request to the public Overpass server (it is often busy, so the script
retries with growing waits) and summarizes kerb, tactile paving and wheelchair tags.

Usage:
    python scripts/kerb_check.py --lat 33.6460 --lon -117.8427 --dist 1500
"""

from __future__ import annotations

import argparse
import time

import osmnx as ox
import pandas as pd

BUSY_HINTS = ("502", "503", "504", "429", "timeout", "timed out", "capacity", "too many requests")


def fetch(center: tuple[float, float], dist: int, attempts: int = 5, wait: int = 15) -> pd.DataFrame:
    """Fetch kerb/tactile/wheelchair/crossing features in one query, retrying when the server is busy."""
    tags = {"kerb": True, "tactile_paving": True, "wheelchair": True, "highway": "crossing"}
    for attempt in range(1, attempts + 1):
        try:
            return ox.features_from_point(center, tags=tags, dist=dist)
        except Exception as exc:
            text = f"{type(exc).__name__} {exc}".lower()
            if "insufficient" in text or "no matching" in text:
                return pd.DataFrame()
            if attempt == attempts or not any(hint in text for hint in BUSY_HINTS):
                raise
            delay = wait * 2 ** (attempt - 1)
            print(f"  Overpass is busy (attempt {attempt}/{attempts}). Waiting {delay}s ...")
            time.sleep(delay)
    return pd.DataFrame()


def tag_summary(df: pd.DataFrame, tag: str) -> dict:
    """Count features carrying a tag, split by element type and by value."""
    if df.empty or tag not in df.columns:
        return {"total": 0, "by_type": {}, "by_value": {}}
    tagged = df[df[tag].notna()]
    return {
        "total": int(len(tagged)),
        "by_type": {str(k): int(v) for k, v in tagged.index.get_level_values("element").value_counts().items()},
        "by_value": {str(k): int(v) for k, v in tagged[tag].astype(str).value_counts().head(8).items()},
    }


def analyze(df: pd.DataFrame) -> dict:
    """Pure function over the raw feature table, so it can be unit tested."""
    result = {tag: tag_summary(df, tag) for tag in ("kerb", "tactile_paving", "wheelchair")}
    crossing_nodes = 0
    kerb_on_crossing_nodes = 0
    if not df.empty and "highway" in df.columns:
        is_node = df.index.get_level_values("element") == "node"
        is_crossing = (df["highway"] == "crossing").values
        crossing_nodes = int((is_node & is_crossing).sum())
        if "kerb" in df.columns:
            kerb_on_crossing_nodes = int((is_node & is_crossing & df["kerb"].notna().values).sum())
    result["crossing_nodes"] = crossing_nodes
    result["kerb_on_crossing_nodes"] = kerb_on_crossing_nodes
    result["barrier_values_on_kerb_features"] = {}
    if not df.empty and "kerb" in df.columns and "barrier" in df.columns:
        kerb_rows = df[df["kerb"].notna()]
        result["barrier_values_on_kerb_features"] = {
            str(k): int(v) for k, v in kerb_rows["barrier"].astype(str).value_counts().head(5).items()
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, default=33.6460)
    parser.add_argument("--lon", type=float, default=-117.8427)
    parser.add_argument("--dist", type=int, default=1500)
    args = parser.parse_args()
    center = (args.lat, args.lon)

    print(f"Querying raw OpenStreetMap around {center} (+/- {args.dist} m) ...\n")
    info = analyze(fetch(center, args.dist))

    for tag in ("kerb", "tactile_paving", "wheelchair"):
        s = info[tag]
        print(f"{tag}=*  ->  {s['total']} features")
        if s["total"]:
            print(f"   by element type: {s['by_type']}")
            print(f"   top values:      {s['by_value']}")

    print(f"\nhighway=crossing nodes: {info['crossing_nodes']}")
    print(f"...of which also carry a kerb tag: {info['kerb_on_crossing_nodes']}")
    if info["barrier_values_on_kerb_features"]:
        print(f"barrier=* on kerb-tagged features: {info['barrier_values_on_kerb_features']}")

    kerb_total = info["kerb"]["total"]
    print()
    if kerb_total == 0:
        print("VERDICT: no kerb tags in raw OpenStreetMap here. Treat crossings as 'unknown'.")
    else:
        elsewhere = kerb_total - info["kerb_on_crossing_nodes"]
        print(f"VERDICT: {kerb_total} kerb-tagged features exist, so curb-ramp data is sparse but not absent.")
        print(f"{elsewhere} of them are NOT on highway=crossing nodes (usually kerb nodes at the ends of crossings).")
        print("The older coverage metric only counted the other kind, which is why it showed 0%.")


if __name__ == "__main__":
    main()