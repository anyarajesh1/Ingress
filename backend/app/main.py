"""Ingress API entry point.

Run locally from the repo root:
    python -m uvicorn app.main:app --reload --app-dir backend
"""

from fastapi import FastAPI

app = FastAPI(
    title="Ingress API",
    version="0.1.0",
    description="Accessibility-aware routing over OpenStreetMap sidewalk data.",
)


@app.get("/health")
def health() -> dict:
    """Liveness check used by the host platform and by CI."""
    return {"status": "ok", "service": "ingress-api", "version": app.version}