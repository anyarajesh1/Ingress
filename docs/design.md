# Ingress design notes

Working notes on goals, the cost model, and how the project will be evaluated.
These are decisions made up front; they will be revised as real data comes in.

## Goals

- Route over real sidewalk data, with profile-specific limits.
- Explain every route difference with numbers (distance, slope, stairs avoided).
- Treat missing data as a first-class result: report coverage, never hide it.
- Treat AI output as evidence with a confidence level, never as ground truth.

## Non-goals

- Certifying ADA compliance or any legal standard.
- Live obstruction or construction reporting.
- Coverage beyond one small, well-checked study area at first.

## Routing model

The walking network is a graph. Each segment gets a cost for a given profile:

```
cost = length * (1 + slope_penalty + surface_penalty + crossing_penalty)
```

Some conditions are hard blocks rather than penalties (for example, stairs for a wheelchair profile). Each profile defines:

| Profile | Stairs | Max sustained slope | Missing curb ramp |
|---|---|---|---|
| Manual wheelchair | blocked | placeholder | heavy penalty |
| Power chair | blocked | placeholder | heavy penalty |
| Stroller | blocked | placeholder | moderate penalty |
| Cane user | allowed if handrail tagged | placeholder | light penalty |

Slope limits are intentionally left as placeholders. They will be set from published accessibility guidance (for example, the US Access Board's guidelines for public rights-of-way), cited in the README, and kept configurable, since individual limits vary. Verify current guidance before citing specific numbers.

When tags are missing, the router applies an explicit "unknown" policy (assume passable but add a small uncertainty penalty) and reports how much of the chosen route was based on missing data.

## Known data problems to plan for

- Sidewalks and crossings that are not connected in OpenStreetMap, which produces false "no route" results. Plan: connectivity check and report.
- Coarse elevation data makes slopes on very short segments unreliable. Plan: smooth slope over longer stretches and document it.
- Curb-ramp and wheelchair tags are sparse. Plan: measure coverage first (`scripts/osm_coverage.py`) and pick a study area where the comparison is meaningful (some stairs, some real slopes).

## Evaluation plan

1. **Map coverage:** percentage of pedestrian ways and crossing nodes carrying each relevant tag, per area.
2. **Routing:** unit tests with known cases (a route that must avoid a mapped staircase) plus side-by-side examples against the standard shortest path.
3. **Vision model:** hand-label roughly 60 street-level entrance images (steps: yes / no / unclear; ramp: yes / no / unclear). Report accuracy, the rate of "unclear" answers, and agreement with OpenStreetMap `wheelchair` tags where both exist. OSM tags are used as a second opinion, not as the answer key.
4. **Honest limits section** in the README summarizing where results are weak.