"""
Journey Insight

Pipeline: Step1 → Step2b → Step3 → Step4 → Markdown report.
Steps 1, 2b, and 3 are cached by cache_key; Step 4 always reruns.
"""
from __future__ import annotations

import glob as glob_module
import hashlib
import json
import os
import shutil
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from agent.data.store import append_record, read_records
from agent.pipeline.journey.step1_event_meaning import run as run_step1
from agent.pipeline.journey.step2b_natural_chain_mining import run as run_step2b
from agent.pipeline.journey.step3_insight_candidates import run as run_step3
from agent.pipeline.journey.step4_report import run as run_step4
from agent.pipeline.journey.step5_visual_summary import run as run_step5

router = APIRouter(prefix="/api/journey-insight", tags=["journey-insight"])

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_PATH = os.path.join(_BASE_DIR, "output", "journey_insight", "runs.parquet")

MAPPING_FILES = [
    os.path.join(_BASE_DIR, "agent", "data", "mapping", "event_family_mapping_final_v5.csv"),
    os.path.join(_BASE_DIR, "agent", "data", "mapping", "event_id_mapping_final_v5.csv"),
    os.path.join(_BASE_DIR, "agent", "data", "mapping", "screen_taxonomy_reference_final_v4.csv"),
]
VALID_WINDOWS = frozenset([
    "00:00-03:00", "03:00-06:00", "06:00-09:00", "09:00-12:00",
    "12:00-15:00", "15:00-18:00", "18:00-21:00", "21:00-24:00",
])
_LOCK = threading.Lock()
# Track in-progress run_ids to prevent duplicate concurrent runs
_IN_PROGRESS: set[str] = set()
_IN_PROGRESS_LOCK = threading.Lock()


# ── Request / response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    input_glob: str = "output/tracking_events_v2/*.parquet"
    date_filter: str | None = None  # e.g. "2026-06-12"; None = all dates
    force_rerun: bool = False        # skip step1/2b/3 cache and run fresh


class ReportRequest(BaseModel):
    run_id: str
    window_filters: list[str]


# ── Data helpers ──────────────────────────────────────────────────────────────

def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert numpy/NaN values back to Python-native types for PyArrow serialization."""
    import math
    result = {}
    for k, v in row.items():
        if isinstance(v, float) and math.isnan(v):
            result[k] = None
        elif hasattr(v, "tolist"):  # numpy scalar or array
            result[k] = v.tolist()
        else:
            result[k] = v
    return result


def _update_run(run_id: str, updates: dict[str, Any]) -> None:
    with _LOCK:
        try:
            rows = read_records(DATA_PATH)
        except Exception:
            rows = []
        for row in rows:
            if row.get("run_id") == run_id:
                row.update(updates)
                break
        sanitized = [_sanitize_row(row) for row in rows]
        table = pa.Table.from_pylist(sanitized)
        pq.write_table(table, DATA_PATH)


def _read_run(run_id: str) -> dict[str, Any] | None:
    try:
        rows = read_records(DATA_PATH)
    except Exception:
        return None
    for row in rows:
        if row.get("run_id") == run_id:
            return _sanitize_row(row)
    return None


def _read_all_runs() -> list[dict[str, Any]]:
    try:
        rows = read_records(DATA_PATH)
    except Exception:
        rows = []
    return [_sanitize_row(row) for row in rows]


# ── Utility ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_parquet_files(input_glob: str) -> list[str]:
    """Resolve input_glob to absolute paths. input_glob may be relative to _BASE_DIR."""
    pattern = input_glob
    if not os.path.isabs(pattern):
        pattern = os.path.join(_BASE_DIR, pattern)
    return sorted(glob_module.glob(pattern))


def _compute_cache_key(input_glob: str, resolved_files: list[str], date_filter: str | None = None) -> str:
    payload = json.dumps({
        "input_glob": input_glob,
        "date_filter": date_filter,
        "files": [
            {
                "path": path,
                "size": os.path.getsize(path),
                "mtime": os.path.getmtime(path),
            }
            for path in resolved_files
        ],
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def infer_data_date(parquet_paths: list[str], date_filter: str | None = None) -> str | None:
    """Return data_date. If date_filter is set, use it directly (it's already validated)."""
    if date_filter:
        return date_filter
    dates: set[str] = set()
    for path in parquet_paths:
        try:
            table = pq.read_table(path, columns=["timestamp"])
            timestamps = [v for v in table["timestamp"].to_pylist() if v]
            for ts in timestamps:
                try:
                    dates.add(str(ts)[:10])
                except Exception:
                    pass
        except Exception:
            pass
    return min(dates) if dates else None


def get_available_dates(parquet_paths: list[str]) -> list[str]:
    """Return sorted list of unique dates present in the parquet files."""
    dates: set[str] = set()
    for path in parquet_paths:
        try:
            table = pq.read_table(path, columns=["timestamp"])
            for ts in table["timestamp"].to_pylist():
                if ts:
                    d = str(ts)[:10]
                    if len(d) == 10 and d[4] == "-" and d[7] == "-":
                        dates.add(d)
        except Exception:
            pass
    return sorted(dates)


_WINDOW_LABELS = [
    "00:00-03:00", "03:00-06:00", "06:00-09:00", "09:00-12:00",
    "12:00-15:00", "15:00-18:00", "18:00-21:00", "21:00-24:00",
]


def get_window_counts(parquet_paths: list[str], date_filter: str | None) -> dict[str, int]:
    """Return event count per 3h window for the given date (or all dates if None)."""
    from datetime import datetime as _dt

    counts: dict[str, int] = {w: 0 for w in _WINDOW_LABELS}
    for path in parquet_paths:
        try:
            cols = ["timestamp", "client_timestamp"]
            table = pq.read_table(path, columns=cols)
            client_ts_list = table["client_timestamp"].to_pylist()
            ts_list = table["timestamp"].to_pylist()
            for client_ts, server_ts in zip(client_ts_list, ts_list):
                ts_str = str(client_ts or server_ts or "")
                if not ts_str or ts_str == "None":
                    continue
                date_part = ts_str[:10]
                if date_filter and date_part != date_filter:
                    continue
                try:
                    hour = int(ts_str[11:13])
                    window = _WINDOW_LABELS[hour // 3]
                    counts[window] += 1
                except (ValueError, IndexError):
                    pass
        except Exception:
            pass
    return counts


def _validate_window_filters(window_filters: list[str] | None) -> None:
    if not window_filters:
        raise HTTPException(status_code=400, detail="window_filters is required and must be non-empty")
    invalid = [w for w in window_filters if w not in VALID_WINDOWS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid window values: {invalid}")


def _find_cached_run(cache_key: str) -> dict[str, Any] | None:
    """Find the most recent successful run with the same cache_key that has valid step1+2b+3 artifacts."""
    rows = _read_all_runs()
    candidates = [
        row for row in rows
        if row.get("cache_key") == cache_key
        and row.get("status") == "ok"
        and "step1" in (list(row.get("steps_completed")) if row.get("steps_completed") is not None else [])
        and "step2b" in (list(row.get("steps_completed")) if row.get("steps_completed") is not None else [])
        and "step3" in (list(row.get("steps_completed")) if row.get("steps_completed") is not None else [])
    ]
    if not candidates:
        return None
    # Sort by started_at descending to get the most recent
    candidates.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return candidates[0]


def _artifact_dir(run_id: str, step: str) -> str:
    return os.path.join(_BASE_DIR, "output", "journey_insight", run_id, step)


def _copy_artifact_dir(src: str, dst: str) -> None:
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


# ── Background pipeline tasks ─────────────────────────────────────────────────

def _run_pipeline_bg(
    run_id: str,
    input_glob: str,
    resolved_files: list[str],
    cache_key: str,
    date_filter: str | None = None,
    force_rerun: bool = False,
) -> None:
    try:
        # Precheck mapping files
        for mapping_path in MAPPING_FILES:
            if not os.path.exists(mapping_path):
                _update_run(run_id, {
                    "status": "error",
                    "error_step": "precheck",
                    "error_detail": f"Missing mapping file: {mapping_path}",
                })
                return

        step1_dir = _artifact_dir(run_id, "step1")
        step2b_dir = _artifact_dir(run_id, "step2b")
        step3_dir = _artifact_dir(run_id, "step3")

        # Check cache for Steps 1+2b+3 (skip if force_rerun)
        cached_run = None if force_rerun else _find_cached_run(cache_key)
        steps_completed: list[str] = []

        if cached_run and cached_run.get("run_id") != run_id:
            src_step1 = cached_run.get("step1_artifact_dir") or _artifact_dir(cached_run["run_id"], "step1")
            src_step2b = cached_run.get("step2b_artifact_dir") or _artifact_dir(cached_run["run_id"], "step2b")
            src_step3 = cached_run.get("step3_artifact_dir") or _artifact_dir(cached_run["run_id"], "step3")
            if (os.path.isdir(src_step1) and os.path.isdir(src_step2b) and os.path.isdir(src_step3)):
                _copy_artifact_dir(src_step1, step1_dir)
                _copy_artifact_dir(src_step2b, step2b_dir)
                _copy_artifact_dir(src_step3, step3_dir)
                steps_completed = ["step1", "step2b", "step3"]
                _update_run(run_id, {
                    "cached_from_run_id": cached_run["run_id"],
                    "steps_completed": steps_completed,
                })
            else:
                cached_run = None  # fall through to full run

        if not steps_completed:
            # Step 1
            try:
                os.makedirs(step1_dir, exist_ok=True)
                input_dir = os.path.dirname(resolved_files[0])
                run_step1(input_dir=input_dir, output_dir=step1_dir, date_filter=date_filter)
                steps_completed.append("step1")
                _update_run(run_id, {"steps_completed": steps_completed})
            except Exception as exc:
                _update_run(run_id, {
                    "status": "error",
                    "error_step": "step1",
                    "error_detail": traceback.format_exc()[:2000],
                })
                return

            # Step 2b
            try:
                os.makedirs(step2b_dir, exist_ok=True)
                run_step2b(input_dir=input_dir, output_dir=step2b_dir, date_filter=date_filter)
                steps_completed.append("step2b")
                _update_run(run_id, {"steps_completed": steps_completed})
            except Exception as exc:
                _update_run(run_id, {
                    "status": "error",
                    "error_step": "step2b",
                    "error_detail": traceback.format_exc()[:2000],
                })
                return

            # Step 3
            try:
                os.makedirs(step3_dir, exist_ok=True)
                run_step3(input_dir=step2b_dir, output_dir=step3_dir)
                steps_completed.append("step3")
                _update_run(run_id, {"steps_completed": steps_completed})
            except Exception as exc:
                _update_run(run_id, {
                    "status": "error",
                    "error_step": "step3",
                    "error_detail": traceback.format_exc()[:2000],
                })
                return

        # Steps 1-3 complete — ready for Generate Report
        _update_run(run_id, {
            "status": "ok",
            "generated_at": _now_iso(),
            "steps_completed": steps_completed,
        })
    finally:
        with _IN_PROGRESS_LOCK:
            _IN_PROGRESS.discard(run_id)


def _report_pipeline_bg(
    run_id: str,
    window_filters: list[str],
    step3_dir: str,
) -> None:
    try:
        step4_dir = _artifact_dir(run_id, "step4")
        step5_dir = _artifact_dir(run_id, "step5")

        # Step 4 — Step 3 artifacts already exist from Run Pipeline
        try:
            os.makedirs(step4_dir, exist_ok=True)
            step4_summary = run_step4(
                input_dir=step3_dir,
                output_dir=step4_dir,
                window_filters=window_filters,
            )
            if not step4_summary.get("client_response", {}).get("ok"):
                raise RuntimeError(
                    step4_summary.get("client_response", {}).get("body_markdown", "Step 4 failed")[:500]
                )
        except Exception:
            _update_run(run_id, {
                "status": "error",
                "error_step": "step4",
                "error_detail": traceback.format_exc()[:2000],
            })
            return

        # Step 5 — visual summary (best-effort; failure does not block status=ok)
        try:
            os.makedirs(step5_dir, exist_ok=True)
            run_step5(input_dir=step4_dir, output_dir=step5_dir)
            steps_completed = ["step1", "step2b", "step3", "step4", "step5"]
        except Exception:
            steps_completed = ["step1", "step2b", "step3", "step4"]

        _update_run(run_id, {
            "status": "ok",
            "generated_at": _now_iso(),
            "window_filters": window_filters,
            "steps_completed": steps_completed,
        })
    finally:
        with _IN_PROGRESS_LOCK:
            _IN_PROGRESS.discard(run_id)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/dates")
async def available_dates(input_glob: str = "output/tracking_events_v2/*.parquet"):
    """Return sorted list of unique dates available in the parquet files."""
    resolved_files = _resolve_parquet_files(input_glob)
    if not resolved_files:
        return {"dates": []}
    dates = get_available_dates(resolved_files)
    return {"dates": dates}


@router.get("/windows")
async def window_counts(
    input_glob: str = "output/tracking_events_v2/*.parquet",
    date_filter: str | None = None,
):
    """Return event count per 3h time window for the given date."""
    resolved_files = _resolve_parquet_files(input_glob)
    if not resolved_files:
        return {"windows": {w: 0 for w in _WINDOW_LABELS}}
    counts = get_window_counts(resolved_files, date_filter)
    return {"windows": counts}


@router.post("/run")
async def start_run(req: RunRequest, background_tasks: BackgroundTasks):
    """Start a new pipeline run (Steps 1-3). No window_filters needed."""
    resolved_files = _resolve_parquet_files(req.input_glob)
    if not resolved_files:
        raise HTTPException(status_code=400, detail=f"input_glob {req.input_glob!r} resolved to no parquet files")

    cache_key = _compute_cache_key(req.input_glob, resolved_files, req.date_filter)
    data_date = infer_data_date(resolved_files, req.date_filter)
    run_id = str(uuid.uuid4())
    started_at = _now_iso()

    step1_dir = _artifact_dir(run_id, "step1")
    step2b_dir = _artifact_dir(run_id, "step2b")
    step3_dir = _artifact_dir(run_id, "step3")
    step4_dir = _artifact_dir(run_id, "step4")

    record: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "generated_at": None,
        "status": "running",
        "data_date": data_date,
        "input_glob": req.input_glob,
        "cache_key": cache_key,
        "cached_from_run_id": None,
        "window_filters": [],
        "steps_completed": [],
        "error_step": None,
        "error_detail": None,
        "step1_artifact_dir": step1_dir,
        "step2b_artifact_dir": step2b_dir,
        "step3_artifact_dir": step3_dir,
        "step4_artifact_dir": step4_dir,
    }
    _ensure_data_dir()
    append_record(DATA_PATH, record)

    with _IN_PROGRESS_LOCK:
        _IN_PROGRESS.add(run_id)

    background_tasks.add_task(
        _run_pipeline_bg,
        run_id=run_id,
        input_glob=req.input_glob,
        resolved_files=resolved_files,
        cache_key=cache_key,
        date_filter=req.date_filter,
        force_rerun=req.force_rerun,
    )

    return {
        "status": "running",
        "run_id": run_id,
        "started_at": started_at,
        "data_date": data_date,
        "steps_completed": [],
    }


@router.post("/report")
async def regenerate_report(req: ReportRequest, background_tasks: BackgroundTasks):
    """Regenerate Step 3 + Step 4 for a different window selection on an existing successful run."""
    _validate_window_filters(req.window_filters)

    run = _read_run(req.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run_id {req.run_id!r} not found")
    if run.get("status") != "ok":
        raise HTTPException(status_code=409, detail=f"Run {req.run_id!r} is not in status 'ok' (current: {run.get('status')})")

    with _IN_PROGRESS_LOCK:
        if req.run_id in _IN_PROGRESS:
            raise HTTPException(status_code=409, detail=f"Run {req.run_id!r} already has a report generation in progress")
        _IN_PROGRESS.add(req.run_id)

    step3_dir = run.get("step3_artifact_dir") or _artifact_dir(req.run_id, "step3")

    # Mark as running
    _update_run(req.run_id, {
        "status": "running",
        "error_step": None,
        "error_detail": None,
    })

    background_tasks.add_task(
        _report_pipeline_bg,
        run_id=req.run_id,
        window_filters=req.window_filters,
        step3_dir=step3_dir,
    )

    return {
        "status": "running",
        "run_id": req.run_id,
        "started_at": run.get("started_at"),
        "data_date": run.get("data_date"),
        "window_filters": req.window_filters,
        "steps_completed": ["step1", "step2b", "step3"],
    }


@router.get("/report/{run_id}")
async def get_report(run_id: str):
    """Return the latest generated report for a specific run."""
    run = _read_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
    if not run.get("generated_at"):
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} has no generated report yet")

    step4_dir = run.get("step4_artifact_dir") or _artifact_dir(run_id, "step4")
    report_path = os.path.join(step4_dir, "mvp_journey_report.md")
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail=f"Report file missing for run {run_id!r}")

    with open(report_path, "r", encoding="utf-8") as handle:
        report_md = handle.read()

    if not report_md.strip():
        raise HTTPException(status_code=404, detail=f"Report file is empty for run {run_id!r}")

    return {
        "run_id": run_id,
        "generated_at": run.get("generated_at"),
        "data_date": run.get("data_date"),
        "window_filters": [str(x) for x in (run.get("window_filters") or [])],
        "report_md": report_md,
    }


@router.get("/summary/{run_id}")
async def get_summary(run_id: str):
    """Return the Step 5 visual summary markdown for a specific run."""
    run = _read_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")

    step5_dir = _artifact_dir(run_id, "step5")
    summary_path = os.path.join(step5_dir, "visual_summary.md")
    if not os.path.exists(summary_path):
        raise HTTPException(status_code=404, detail=f"Visual summary not found for run {run_id!r}")

    with open(summary_path, "r", encoding="utf-8") as handle:
        summary_md = handle.read()

    if not summary_md.strip():
        raise HTTPException(status_code=404, detail=f"Visual summary is empty for run {run_id!r}")

    return {
        "run_id": run_id,
        "generated_at": run.get("generated_at"),
        "data_date": run.get("data_date"),
        "window_filters": [str(x) for x in (run.get("window_filters") or [])],
        "summary_md": summary_md,
    }


@router.get("/status/{run_id}")
async def run_status(run_id: str):
    """Return minimal status for a specific run (used for polling)."""
    row = _read_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    return {
        "run_id": row.get("run_id"),
        "status": row.get("status"),
        "error_step": row.get("error_step"),
        "error_detail": row.get("error_detail"),
        "steps_completed": list(row.get("steps_completed") if row.get("steps_completed") is not None else []),
    }


@router.get("/latest")
async def latest_run(date: str | None = None):
    """Return the latest successful run. If date is provided, filter by data_date."""
    rows = _read_all_runs()
    rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    for row in rows:
        if row.get("status") != "ok":
            continue
        if date and row.get("data_date") != date:
            continue
        return {
            "run_id": row.get("run_id"),
            "data_date": row.get("data_date"),
            "window_filters": [str(x) for x in (row.get("window_filters") or [])],
        }
    raise HTTPException(status_code=404, detail="No successful run found")
