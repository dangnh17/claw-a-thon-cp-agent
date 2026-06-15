# ── Module registry ───────────────────────────────────────────────────────────
#
# Each team member:
#   1. Creates  agent/modules/feature_x.py  with a FastAPI `router`
#   2. Adds ONE import line below + ONE entry to ROUTERS
#
# Git conflict in this file = add your line back. That's it.
# ─────────────────────────────────────────────────────────────────────────────

from agent.modules import feature_a        # member 1
from agent.modules import funnel_analysis  # funnel analysis
from agent.modules import debug_investigator # debug investigator
from agent.modules import journey_insight  # journey insight

from fastapi import APIRouter


def get_all_routers() -> list[APIRouter]:
    """Return all feature routers. Imported by agent/app.py."""
    return [
        funnel_analysis.router,
        debug_investigator.router,
        journey_insight.router,
    ]
