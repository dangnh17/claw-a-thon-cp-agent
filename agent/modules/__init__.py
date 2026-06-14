# ── Module registry ───────────────────────────────────────────────────────────
#
# Each team member:
#   1. Creates  agent/modules/feature_x.py  with a FastAPI `router`
#   2. Adds ONE import line below + ONE entry to ROUTERS
#
# Git conflict in this file = add your line back. That's it.
# ─────────────────────────────────────────────────────────────────────────────

from agent.modules import feature_a    # member 1
# from agent.modules import feature_b  # member 2  ← uncomment when ready
# from agent.modules import feature_c  # member 3

from fastapi import APIRouter


def get_all_routers() -> list[APIRouter]:
    """Return all feature routers. Imported by agent/app.py."""
    return [
        feature_a.router,
        # feature_b.router,
        # feature_c.router,
    ]
