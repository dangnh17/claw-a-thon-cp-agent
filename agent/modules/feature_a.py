"""
Feature A — template for one team member's feature.

Copy this file to feature_b.py / feature_c.py, change the prefix and logic.
Then register in agent/modules/__init__.py (one line).

Convention:
    prefix  = "/api/feature-a"   ← unique per feature, avoids route conflicts
    tag     = "feature-a"        ← groups routes in /docs
"""
import json
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.llm_client import call_llm
from agent.data.store import append_record, read_records

router = APIRouter(prefix="/api/feature-a", tags=["feature-a"])

# Each feature stores data in its own subdirectory
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_PATH = os.path.join(_BASE_DIR, "output", "feature_a", "records.parquet")


# ── Request / response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    input_text: str
    extra_context: str = ""


class RunResponse(BaseModel):
    result: str
    confidence: float
    saved_at: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/run", response_model=RunResponse)
async def run(req: RunRequest):
    """Main task for Feature A."""
    if not req.input_text.strip():
        raise HTTPException(status_code=400, detail="input_text is empty")

    prompt = f"""You are Feature A's agent.

Input: {req.input_text}
Context: {req.extra_context}

Respond in JSON: {{"result": "<string>", "confidence": <0.0-1.0>}}"""

    raw = call_llm(prompt, max_tokens=500)
    try:
        data = json.loads(raw.split("```json")[-1].split("```")[0].strip() if "```" in raw else raw)
    except Exception:
        data = {"result": raw, "confidence": 0.0}

    record = {
        "result": data.get("result", ""),
        "confidence": data.get("confidence", 0.0),
        "input_text": req.input_text,
        "saved_at": datetime.now().isoformat(),
    }
    append_record(DATA_PATH, record)

    return RunResponse(**{k: record[k] for k in RunResponse.model_fields})


@router.get("/history")
async def history():
    """Return past results for Feature A."""
    return read_records(DATA_PATH)
