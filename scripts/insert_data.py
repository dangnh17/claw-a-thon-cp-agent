#!/usr/bin/env python3
"""
Manual data insertion script.

Usage:
    python scripts/insert_data.py --dataset feature_a/records --mode append \
        --records '[{"score": 4.2, "label": "good"}]'

    python scripts/insert_data.py --dataset feature_a/records --mode overwrite \
        --file /path/to/data.json
"""

import argparse
import json
import os
import sys

# Ensure repo root is on the path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _REPO_ROOT)

from agent.data.store import append_records, write_records, read_records  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Insert data into a Parquet dataset.")
    parser.add_argument(
        "--dataset", required=True,
        help="Dataset path relative to output/, e.g. feature_a/records"
    )
    parser.add_argument(
        "--mode", choices=["append", "overwrite"], default="append",
        help="append (default) or overwrite"
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--records",
        help="JSON array string, e.g. '[{\"key\": \"value\"}]'"
    )
    source.add_argument(
        "--file",
        help="Path to a JSON file containing an array of records"
    )

    args = parser.parse_args()

    # Load records
    if args.records:
        records = json.loads(args.records)
    else:
        with open(args.file, "r", encoding="utf-8") as f:
            records = json.load(f)

    if not isinstance(records, list):
        print("Error: records must be a JSON array", file=sys.stderr)
        sys.exit(1)

    data_path = os.path.join(_REPO_ROOT, "output", args.dataset + ".parquet")

    if args.mode == "overwrite":
        write_records(data_path, records)
        print(f"Overwrote {len(records)} record(s) → {data_path}")
    else:
        append_records(data_path, records)
        total = len(read_records(data_path))
        print(f"Appended {len(records)} record(s) → {data_path} (total: {total})")


if __name__ == "__main__":
    main()
