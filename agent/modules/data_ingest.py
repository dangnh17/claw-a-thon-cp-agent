"""
Data Ingest — shared module for uploading/ingesting data into the Parquet store.

Agents and team members can call these endpoints to:
  - Upload .parquet files directly
  - Ingest JSON records (list of dicts)
  - List datasets and view their schema
  - Preview rows from any dataset

Prefix: /api/data
"""
import io
import json
import os
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent.data.store import (
    append_records,
    list_datasets,
    read_records,  # used by /preview and /ingest (overwrite mode reads existing)
    write_records,
    get_schema,
)

router = APIRouter(prefix="/api/data", tags=["data"])

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output")


# ── Models ────────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    """Ingest JSON records into a named dataset."""
    dataset: str                       # e.g. "feature_a/results"
    records: list[dict[str, Any]]
    mode: str = "append"               # "append" | "overwrite"


class DatasetInfo(BaseModel):
    name: str
    path: str
    size_bytes: int
    columns: dict[str, str]
    row_count: int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/datasets", response_model=list[DatasetInfo])
async def list_all_datasets():
    """List all Parquet datasets under output/."""
    datasets = list_datasets(OUTPUT_DIR)
    result = []
    for d in datasets:
        row_count = pq.read_metadata(d["path"]).num_rows
        result.append(DatasetInfo(
            name=d["name"],
            path=d["path"],
            size_bytes=d["size_bytes"],
            columns=d["schema"],
            row_count=row_count,
        ))
    return result


@router.get("/schema")
async def get_dataset_schema(dataset: str):
    """
    Return schema for a dataset.
    dataset = relative path under output/, e.g. "feature_a/results.parquet"
    """
    path = _resolve(dataset)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset}")
    return {"dataset": dataset, "schema": get_schema(path)}


@router.get("/preview")
async def preview(dataset: str, limit: int = 20):
    """Return first N rows from a dataset."""
    path = _resolve(dataset)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset}")
    rows = read_records(path)

    import math

    def _sanitize(val):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
        return val

    clean_rows = [
        {k: _sanitize(v) for k, v in row.items()} for row in rows[:limit]
    ]
    return {"dataset": dataset, "total": len(rows), "rows": clean_rows}


@router.post("/ingest")
async def ingest_json(req: IngestRequest):
    """
    Ingest JSON records into a Parquet dataset.

    Agent skill usage:
        POST /api/data/ingest
        {
          "dataset": "feature_a/results",
          "records": [{"col1": "val", "col2": 1.0}, ...],
          "mode": "append"   // or "overwrite"
        }
    """
    if not req.records:
        raise HTTPException(status_code=400, detail="records list is empty")

    path = _resolve(req.dataset + ".parquet" if not req.dataset.endswith(".parquet") else req.dataset)

    if req.mode == "overwrite":
        write_records(path, req.records)
    else:
        append_records(path, req.records)

    return {
        "ok": True,
        "dataset": req.dataset,
        "mode": req.mode,
        "added": len(req.records),
        "path": path,
    }


@router.delete("/dataset")
async def delete_dataset(dataset: str):
    """
    Delete a dataset (removes the .parquet file and its directory if empty).

    dataset = relative path under output/, e.g. "feature_a/records.parquet"
    """
    path = _resolve(dataset)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset}")
    os.remove(path)
    parent = os.path.dirname(path)
    if os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)
    return {"ok": True, "deleted": dataset}


@router.post("/upload")
async def upload_parquet(
    file: UploadFile = File(...),
    dataset: str = "",
):
    """
    Upload a .parquet file directly.
    dataset = target name (optional); defaults to uploaded filename without extension.
    """
    if not file.filename.endswith(".parquet"):
        raise HTTPException(status_code=400, detail="Only .parquet files accepted")

    content = await file.read()

    # Validate it's a real parquet file
    try:
        table = pq.read_table(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid parquet: {e}")

    name = dataset or os.path.splitext(file.filename)[0]
    path = _resolve(name + ".parquet")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "wb") as f:
        f.write(content)

    return {
        "ok": True,
        "dataset": name,
        "rows": table.num_rows,
        "columns": table.num_columns,
        "schema": {field.name: str(field.type) for field in table.schema},
        "path": path,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve(dataset: str) -> str:
    """Resolve a dataset name to an absolute output path. Prevents path traversal.

    Uses realpath + prefix check instead of string stripping, which was bypassable
    via sequences like '....//secret' that normpath + lstrip failed to catch.
    """
    candidate = os.path.realpath(os.path.join(OUTPUT_DIR, dataset))
    safe_root = os.path.realpath(OUTPUT_DIR) + os.sep
    if not candidate.startswith(safe_root):
        raise HTTPException(status_code=400, detail="Invalid dataset path")
    return candidate
