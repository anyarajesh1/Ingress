# Ingress

**Accessibility-aware routing for any sidewalk, built on open data.**

Most map apps assume everyone can climb stairs and handle steep slopes. Ingress finds routes for people who can't: wheelchair users, stroller parents, and people with limited mobility. It routes over an OpenStreetMap sidewalk network enriched with elevation, then shows how the accessible route differs from the standard shortest one.

> **Status: in progress.** Started October 2026. See the roadmap below for what exists today.

## What it will do

1. **Accessibility-aware routing.** Choose a mobility profile (manual wheelchair, power chair, stroller, cane user). Each profile has its own limits on slope, stairs, and missing curb ramps. The router finds the lowest-difficulty path and compares it to the standard shortest path (extra distance, steepest slope, stairs avoided).
2. **Reachability maps.** "Where can I get in 10 minutes?" for a walker versus a wheelchair user, to show what barriers cost in practice.
3. **Entrance evidence from photos.** OpenStreetMap is missing entrance details in many places. A vision model reads nearby street-level photos and returns structured answers (steps visible, ramp visible) with a confidence value. Results are labeled as AI estimates, never presented as fact, and users can correct them.
4. **Measured honestly.** Map data coverage is reported per area, and the vision model is evaluated against hand-labeled images, including how often it answers "unclear."

## What it is not

Ingress does not certify ADA compliance. The ADA is a legal standard that cannot be judged from a map tag or a photo. Ingress surfaces accessibility *indicators* and is a planning aid, not a legal or safety tool.

## Findings: how much accessibility data does OpenStreetMap have?

Measured on 25 October 2026 with `scripts/osm_coverage.py` around UCI (3 km × 3 km). Each physical segment is counted once (OSMnx stores walkable segments in both directions, which would double the counts). OpenStreetMap is edited constantly, so these numbers will change.

| Measure | Result |
|---|---|
| Walkable network | 23,064 segments, 276.9 km |
| Sidewalk segments / crossing segments | 5,734 / 836 |
| Stairs | 286 segments (1.6 km) |
| Surface tag on pedestrian ways | 51.7% |
| Width tag | 1.5% |
| Incline tag | 0.7% |
| Wheelchair tag | 3 ways |
| Crossings with a curb-ramp (kerb) tag at an end | 83 of 836 (9.9%) |

A direct query of raw OpenStreetMap for the same area found 86 kerb-tagged features, most marked as lowered (a curb ramp).

**What this means for the design**
- Slope can't come from map tags, so elevation data supplies it.
- Surface is usable for penalties, but about half of segments are unknown, so every route reports how much of it relies on missing data.
- Curb-ramp data exists for about 1 in 10 crossings, and wheelchair tags are nearly absent. The router treats unmapped crossings as "unknown" instead of assuming they are accessible or blocked, and entrance photos plus user corrections are planned to fill gaps.

## Roadmap

**Phase 1: Routing engine**
- [x] Repository, CI, design notes
- [x] Script to measure OpenStreetMap accessibility tag coverage for a study area
- [ ] Choose study area from coverage results
- [ ] Elevation and per-segment slope
- [ ] Mobility profiles and difficulty costs
- [ ] Router with comparison metrics and tests

**Phase 2: Product**
- [ ] FastAPI endpoints with precomputed graph loading
- [ ] Map interface: two routes, comparison card, profile switcher, elevation chart
- [ ] Reachability shading
- [ ] Deployment

**Phase 3: Evidence and polish**
- [ ] Street-level image selection (camera must face the entrance)
- [ ] Vision model wrapper with structured output
- [ ] Hand-labeled evaluation set and accuracy report
- [ ] User corrections stored in Postgres
- [ ] Demo video and write-up

## Planned stack

| Layer | Tools |
|---|---|
| Backend | Python, FastAPI, Pydantic |
| Routing | OSMnx, NetworkX, rasterio (elevation) |
| Database | PostgreSQL with PostGIS |
| Vision | Hosted vision LLM behind a swappable wrapper |
| Frontend | Next.js, TypeScript, MapLibre GL |
| Quality | pytest, GitHub Actions, Docker |
| Hosting | Vercel, Render |

## Quick start (data coverage report)

```bash
pip install -r scripts/requirements.txt
python scripts/osm_coverage.py --name uci --lat 33.6460 --lon -117.8427 --dist 1500
```

This downloads a walking network, prints how many stairs, sidewalks and crossings exist, and reports how much of the network carries surface, width, incline, wheelchair, and curb-ramp tags. The JSON report is saved to `docs/`.

Run the tests with `pytest scripts -q`.

## Data and attribution

- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, available under the ODbL.
- Street-level imagery (planned) from [Mapillary](https://www.mapillary.com), shared under CC-BY-SA. Attribution will be shown wherever images or image-derived data appear.

## License

MIT. See [LICENSE](LICENSE).