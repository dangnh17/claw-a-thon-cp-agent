"""
Funnel Analysis — Configurable Conversion Funnel.

Routes
------
  GET  /api/funnel-analysis/preset        – return the built-in preset step definitions
  POST /api/funnel-analysis/conversion    – run funnel with a CUSTOM step config from the UI
  GET  /api/funnel-analysis/conversion    – run funnel with the built-in preset (quick test)
  GET  /api/funnel-analysis/events/sample – top N distinct event_ids in the data (for UI autocomplete)
  POST /api/funnel-analysis/insight       – LLM drop-off analysis on a custom funnel result
  GET  /api/funnel-analysis/health        – sanity check

Funnel step format (used in POST body)
---------------------------------------
  {
    "steps": [
      {
        "name": "User access home",
        "event_ids": ["01.1008.000", "01.1008.001"],   // explicit IDs  (optional)
        "event_prefix": "01.1008."                      // prefix match  (optional)
      },
      ...
    ]
  }

  At least one of event_ids / event_prefix is required per step.
  A user qualifies for a step when ANY of their events match.
  The funnel is SEQUENTIAL: eligible(N) = raw(N) ∩ eligible(N-1).

Formatting
----------
  Percentages use COMMA decimal separator: 73,6%

Configuration
-------------
  FUNNEL_DATA_PATH  – override parquet path
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Union

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from agent.llm_client import call_llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/funnel-analysis", tags=["funnel-analysis"])

# ── Paths ─────────────────────────────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA_PATH = os.path.join(_BASE_DIR, "output", "tracking_events_v2", "records.parquet")
DATA_PATH = os.getenv("FUNNEL_DATA_PATH", DEFAULT_DATA_PATH)

MAPPING_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mapping")
EVENT_MAPPING_PATH  = os.path.join(MAPPING_DIR, "mapping-event.json")
SCREEN_MAPPING_PATH = os.path.join(MAPPING_DIR, "mapping-screen-id.json")

# ── Built-in preset ───────────────────────────────────────────────────────────

PRESET_STEPS: list[dict[str, Any]] = [
    {
        "name": "User access home",
        "event_prefix": "01.1008.",
        "event_ids": [],
    },
    {
        "name": "Click search bar",
        "event_ids": ["01.3160.000"],
    },
    {
        "name": "Input search query",
        "event_ids": [
            "01.3160.001",
            "01.3160.002",
            "01.3160.003",
            "01.3160.004",
            "01.3160.005",
            "01.3160.006",
            "01.3160.007",
            "01.3160.008",
            "01.3160.009",
            "01.3160.010",
        ],
    },
    {
        "name": "View kết quả",
        "event_ids": [
            "01.3160.002",
            "01.3160.003",
            "01.3160.004",
            "01.3160.005",
            "01.3160.006",
            "01.3160.007",
        ],
    },
    {
        "name": "Satisfy click",
        "event_ids": [
            "01.3160.002",
            "01.3160.003",
            "01.3160.004",
            "01.3160.005",
        ],
    },
]

# ── Pydantic models ───────────────────────────────────────────────────────────

class StepDef(BaseModel):
    name: str
    event_ids: list[str] = []
    event_prefix: str = ""

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Step name must not be empty")
        return v.strip()

    @field_validator("event_ids", mode="before")
    @classmethod
    def clean_event_ids(cls, v):
        return [e.strip() for e in (v or []) if str(e).strip()]


class FunnelRequest(BaseModel):
    steps: list[StepDef]

    @field_validator("steps")
    @classmethod
    def at_least_two(cls, v):
        if len(v) < 2:
            raise ValueError("Funnel must have at least 2 steps")
        for s in v:
            if not s.event_ids and not s.event_prefix:
                raise ValueError(
                    f"Step '{s.name}' has no event_ids or event_prefix — "
                    "add at least one event ID"
                )
        return v


class InsightRequest(BaseModel):
    steps: list[StepDef]          # same step config that was run
    funnel_result: list[dict]     # result from /conversion POST


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _fmt_pct(value: float) -> str:
    """100.0 → '100%', 73.6 → '73,6%'"""
    rounded = round(value, 1)
    s = f"{rounded:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return s.replace(".", ",") + "%"


def _load_mapping(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Could not load mapping %s: %s", path, exc)
        return {}


def _load_dataframe() -> pd.DataFrame:
    if not os.path.exists(DATA_PATH):
        raise HTTPException(
            status_code=503,
            detail=f"Tracking data not found at: {DATA_PATH}",
        )
    try:
        return pd.read_parquet(DATA_PATH, engine="pyarrow")
    except Exception as exc:
        logger.exception("Failed to read parquet: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to read tracking data: {exc}")


def _users_for_step(df: pd.DataFrame, step: Union[StepDef, dict]) -> set:
    """Return zalopay_ids that match either event_ids or event_prefix for a step."""
    if isinstance(step, dict):
        event_ids   = set(step.get("event_ids") or [])
        event_prefix = step.get("event_prefix", "")
    else:
        event_ids   = set(step.event_ids)
        event_prefix = step.event_prefix

    masks = []
    if event_prefix:
        masks.append(df["event_id"].str.startswith(event_prefix, na=False))
    if event_ids:
        masks.append(df["event_id"].isin(event_ids))
    if not masks:
        return set()

    combined = masks[0]
    for m in masks[1:]:
        combined = combined | m

    return set(df.loc[combined, "zalopay_id"].dropna().unique())


def _run_funnel(df: pd.DataFrame, steps: list) -> dict[str, Any]:
    """
    Core funnel computation. `steps` can be list[StepDef] or list[dict].
    Returns the full response payload.
    """
    if "zalopay_id" not in df.columns or "event_id" not in df.columns:
        raise HTTPException(
            status_code=500,
            detail="Parquet is missing required columns: zalopay_id, event_id",
        )

    raw_sets: list[set] = [_users_for_step(df, s) for s in steps]

    eligible: list[set] = []
    for i, raw in enumerate(raw_sets):
        eligible.append(raw if i == 0 else raw & eligible[i - 1])

    baseline = len(eligible[0]) or 1

    results = []
    for i, step in enumerate(steps):
        name  = step.name if hasattr(step, "name") else step.get("name", f"Step {i+1}")
        count = len(eligible[i])
        pct   = count / baseline * 100
        prev  = len(eligible[i - 1]) if i > 0 else count
        drop  = prev - count

        results.append({
            "step":               i + 1,
            "name":               name,
            "users":              count,
            "pct_of_baseline":    round(pct, 1),
            "pct_formatted":      _fmt_pct(pct),
            "drop_from_prev":     drop if i > 0 else 0,
            "drop_pct_formatted": _fmt_pct((drop / prev * 100) if prev and i > 0 else 0.0),
        })

    return {
        "funnel":             results,
        "total_events":       len(df),
        "baseline_users":     len(eligible[0]),
        "overall_conversion": _fmt_pct(results[-1]["pct_of_baseline"] if results else 0.0),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/preset")
async def get_preset():
    """Return the built-in preset funnel step definitions (for UI initialisation)."""
    event_map = _load_mapping(EVENT_MAPPING_PATH)
    steps_out = []
    for s in PRESET_STEPS:
        # Attach human-readable event names as hints
        ids = s.get("event_ids") or []
        hints = {eid: event_map[eid]["name"] for eid in ids if eid in event_map}
        steps_out.append({**s, "event_hints": hints})
    return {"steps": steps_out}


@router.post("/conversion")
async def conversion_custom(req: FunnelRequest):
    """
    Run the funnel with a custom step configuration supplied by the UI.

    Body example:
      {
        "steps": [
          {"name": "Home",   "event_prefix": "01.1008."},
          {"name": "Search", "event_ids": ["01.3160.000"]},
          {"name": "Result", "event_ids": ["01.3160.002","01.3160.003"]}
        ]
      }
    """
    df = _load_dataframe()
    return _run_funnel(df, req.steps)


@router.get("/conversion")
async def conversion_preset():
    """Run the built-in preset funnel (no request body needed — useful for quick tests)."""
    df = _load_dataframe()
    preset_step_defs = [StepDef(**{k: v for k, v in s.items() if k != "event_hints"})
                        for s in PRESET_STEPS]
    return _run_funnel(df, preset_step_defs)


@router.get("/events/sample")
async def events_sample(limit: int = 200, prefix: str = ""):
    """
    Return distinct event_ids from the parquet, optionally filtered by prefix.
    Includes human-readable name from mapping-event.json where available.
    Used by the UI to help users discover valid event IDs.
    """
    df = _load_dataframe()
    event_map = _load_mapping(EVENT_MAPPING_PATH)

    series = df["event_id"].dropna().unique()
    if prefix:
        series = [e for e in series if str(e).startswith(prefix)]

    # Sort and cap
    events = sorted(str(e) for e in series)[:limit]

    return {
        "total_distinct": len(df["event_id"].dropna().unique()),
        "returned": len(events),
        "events": [
            {
                "id":   e,
                "name": event_map.get(e, {}).get("name", ""),
            }
            for e in events
        ],
    }


@router.post("/insight")
async def insight_custom(req: InsightRequest):
    """
    Ask the LLM to analyse drop-offs in a funnel result that was already computed.
    Pass the same step config + the funnel_result from POST /conversion.
    """
    steps  = req.funnel_result
    event_map = _load_mapping(EVENT_MAPPING_PATH)

    step_lines = []
    for s in steps:
        ids = []
        if req.steps and s["step"] <= len(req.steps):
            ids = req.steps[s["step"] - 1].event_ids
        id_names = ", ".join(
            event_map.get(i, {}).get("name", i) for i in ids[:4]
        ) or "—"
        line = (
            f"Step {s['step']} ({s['name']}): "
            f"{s['users']:,} users ({s['pct_formatted']})"
        )
        if s.get("drop_from_prev"):
            line += f" — dropped {s['drop_from_prev']:,} ({s['drop_pct_formatted']})"
        line += f"\n   Events: {id_names}"
        step_lines.append(line)

    prompt = f"""Bạn là một senior product analyst đang phân tích phễu chuyển đổi của ứng dụng di động ZaloPay.

Kết quả phễu (người dùng duy nhất, tuần tự):
{chr(10).join(step_lines)}

Tỷ lệ chuyển đổi tổng thể: {steps[-1]['pct_formatted'] if steps else '—'}

Hãy cung cấp phân tích ngắn gọn (tối đa 300 từ) bằng tiếng Việt:
1. Điểm rớt lớn nhất — bước nào, mức độ, 2–3 nguyên nhân có thể.
2. Một bước đang hoạt động tốt hơn kỳ vọng — giải thích ngắn gọn.
3. Hai đề xuất cụ thể, khả thi để cải thiện bước yếu nhất."""

    try:
        llm_text = call_llm(prompt, max_tokens=700)
    except Exception as exc:
        logger.exception("LLM call failed: %s", exc)
        llm_text = f"LLM unavailable: {exc}"

    return {"insight": llm_text}


@router.get("/health")
async def health():
    """Confirm the parquet file is readable."""
    if not os.path.exists(DATA_PATH):
        raise HTTPException(status_code=503, detail=f"File not found: {DATA_PATH}")
    try:
        df = pd.read_parquet(DATA_PATH, engine="pyarrow")
        return {"ok": True, "path": DATA_PATH, "rows": len(df), "columns": df.columns.tolist()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
