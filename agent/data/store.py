"""
Parquet data layer — thin helpers for reading and writing records.

All features share this module; each feature uses its own path prefix:
    output/feature_a/records.parquet
    output/feature_b/records.parquet

Usage:
    from agent.data.store import append_record, read_records, write_records, read_dataframe

    append_record("output/feature_a/results.parquet", {"score": 4.2, "label": "good"})
    rows = read_records("output/feature_a/results.parquet")
"""
import os
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def write_records(path: str, records: list[dict[str, Any]]) -> None:
    """Write a list of dicts to a Parquet file (overwrites)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = pd.DataFrame(records)
    pq.write_table(pa.Table.from_pandas(df), path)


def read_records(path: str) -> list[dict[str, Any]]:
    """Read all records from a Parquet file. Returns [] if file doesn't exist."""
    if not os.path.exists(path):
        return []
    table = pq.read_table(path)
    df = table.to_pandas()
    # Replace NaN/NaT with None so values are JSON-serializable (json.dumps rejects float nan)
    return df.where(df.notna(), other=None).to_dict(orient="records")


def append_record(path: str, record: dict[str, Any]) -> None:
    """Append a single record to a Parquet file (read + rewrite)."""
    existing = read_records(path)
    existing.append(record)
    write_records(path, existing)


def append_records(path: str, records: list[dict[str, Any]]) -> None:
    """Append multiple records to a Parquet file (read + rewrite)."""
    existing = read_records(path)
    existing.extend(records)
    write_records(path, existing)


def read_dataframe(path: str) -> pd.DataFrame:
    """Read a Parquet file as a DataFrame. Returns empty DataFrame if not found."""
    if not os.path.exists(path):
        return pd.DataFrame()
    return pq.read_table(path).to_pandas()


def get_schema(path: str) -> dict[str, str]:
    """Return column → dtype mapping for an existing Parquet file."""
    if not os.path.exists(path):
        return {}
    table = pq.read_table(path)
    return {field.name: str(field.type) for field in table.schema}


def list_datasets(base_dir: str = "output") -> list[dict[str, Any]]:
    """Discover all .parquet files under base_dir."""
    result = []
    for root, _, files in os.walk(base_dir):
        for fname in files:
            if fname.endswith(".parquet"):
                full = os.path.join(root, fname)
                rel  = os.path.relpath(full, base_dir)
                size = os.path.getsize(full)
                result.append({
                    "path": full,
                    "name": rel,
                    "size_bytes": size,
                    "schema": get_schema(full),
                })
    return result
