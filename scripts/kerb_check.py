"""Check whether curb-ramp (kerb) data really is missing around the study area.

The coverage report found 0 kerb tags on crossing nodes. This script asks
OpenStreetMap directly, so we can tell a real data gap apart from a bug in our
pipeline.

Usage:
    python scripts/kerb_check.py --lat 33.6460 --lon -117.8427 --dist 1500
"""

from __future__ import annotations

import argparse

import osmnx as ox
import pandas as pd


def fetch(center: tuple[float, float], tags: dict, dist: int) -> pd.DataFrame:
    """Query raw OSM features. Returns an empty frame when nothing matches."""
    try:
        return ox.features_from_point(center, tags=tags, dist=dist)
    except Exception as exc:  # OSMnx raises when a query matches nothing
        text = f"{type(exc).__name__} {exc}".lower()
        if "insufficient" in text or "no matching" in text:
            return pd.DataFrame()
        raise


def summarize(df: pd.DataFrame, tag: str) -> dict:
    if df.empty or tag not in df.columns:
        return {"total": 0, "by_value": {}, "by_type": {}}
    tagged = df[df[tag].notna()]
    by_type = tagged.index.get_level_values("element").value_counts().to_dict()
    return {
        "total": int(len(tagged)),
        "by_value": tagged[tag].astype(str).value_counts().head(10).to_dict(),
        "by_type": {str(k): int(v) for k, v in by_type.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, default=33.6460)
    parser.add_argument("--lon", type=float, default=-117.8427)
    parser.add_argument("--dist", type=int, default=1500)
    args = parser.parse_args()
    center = (args.lat, args.lon)

    print(f"Querying raw OpenStreetMap around {center} (+/- {args.dist} m) ...\n")

    for tag in ["kerb", "tactile_paving", "wheelchair"]:
        df = fetch(center, {tag: True}, args.dist)
        info = summarize(df, tag)
        print(f"{tag}=*  ->  {info['total']} features")
        if info["total"]:
            print(f"   by element type: {info['by_type']}")
            print(f"   top values:      {info['by_value']}")

    crossings = fetch(center, {"highway": "crossing"}, args.dist)
    n_cross = 0
    if not crossings.empty:
        n_cross = int((crossings.index.get_level_values("element") == "node").sum())
    print(f"\nhighway=crossing nodes: {n_cross}")

    kerb_df = fetch(center, {"kerb": True}, args.dist)
    if kerb_df.empty:
        print("\nVERDICT: no kerb tags exist in raw OpenStreetMap for this area.")
        print("The 0% in the coverage report is a real data gap, not a bug.")
        print("Design consequence: treat crossings as 'unknown' and rely on photos/corrections.")
    else:
        print("\nVERDICT: kerb tags DO exist in raw OSM. If the coverage report said 0%,")
        print("our pipeline is dropping them. Paste this output and we will fix the script.")


if __name__ == "__main__":
    main()