from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agent.llm_client import call_llm

INPUT_FILE = "mvp_journey_report.md"
OUTPUT_FILE = "visual_summary.md"
SUMMARY_FILE = "step5_summary.json"
DEFAULT_MAX_TOKENS = 8000

SYSTEM_PROMPT = """You are a user journey analytics assistant.
Task: convert a technical analytics report into a concise visual summary.
Rules:
- Use only data from the input report. Do not invent patterns.
- Do not infer root cause.
- Use cautious wording: possible, candidate signal, needs verification.
- Write all output in Vietnamese.
- Use Unicode → for arrows. Never use LaTeX notation ($\\rightarrow$, \\to, etc.).
"""

USER_PROMPT_TEMPLATE = """Convert the journey analytics report below into a visual summary with exactly these sections (keep headings as-is):

## Tóm tắt nhanh
3-5 bullet points, one key signal each, in plain business language (Vietnamese).

## Bảng tín hiệu chính
Start this section with exactly these two lines, each on its own line separated by a blank line:

**Q1 — Thay đổi hành vi:** Các pattern routing/motif tăng/giảm bất thường so với baseline trong khung giờ phân tích.

**Q2 — Điểm ma sát:** Các điểm người dùng có thể dừng lại (terminal) hoặc lặp vòng (loop) bất thường.


Then a Markdown table with columns: Khu vực | Pattern | Cường độ | Sessions bị ảnh hưởng | Ghi chú
- Cường độ values: 🔴 Mạnh (z≥2 or lift≥1.5), 🟡 Trung bình, ⚪ Cần xác minh
- Max 8 rows, strongest signals from Q1 and Q2 only.
- Use Unicode → for arrows between screen names. Never use LaTeX.

## Luồng sự kiện đáng chú ý (Q1)
A Mermaid flowchart (LR) of the strongest routing/motif changes in Q1. Max 6 nodes. Use short screen names.
Syntax example:
```mermaid
flowchart LR
    A[screen_a] -->|lift: X| B[screen_b]
```

## Điểm dừng & vòng lặp nghi vấn (Q2)
Two Markdown tables (Vietnamese labels):

**Terminal concentration (điểm dừng tiềm năng):**

| Pattern | Stop rate (hiện tại) | Stop rate (baseline) | Sessions | Ghi chú |
|---------|---------------------|---------------------|----------|---------|
| ...     | ...                 | ...                 | ...      | ...     |

**Suspicious loops (vòng lặp nghi vấn):**

| Pattern | Share (hiện tại) | Share (baseline) | Sessions | Ghi chú |
|---------|-----------------|-----------------|----------|---------|
| ...     | ...             | ...             | ...      | ...     |

Fill each table from the Q2 findings. Use — for unavailable values.

## Watchlist
Max 4 short bullets of lower-confidence signals worth monitoring.

---

Input report:
{report_md}
"""


def run(input_dir: str, output_dir: str, **kwargs) -> dict[str, Any]:
    """
    Step 5: Generate user-friendly visual summary from Step 4 markdown report.
    Returns step5_summary dict.
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    report_path = input_path / INPUT_FILE
    if not report_path.exists():
        raise FileNotFoundError(f"Step 4 report not found: {report_path}")

    report_md = report_path.read_text(encoding="utf-8")
    if not report_md.strip():
        raise ValueError("Step 4 report is empty")

    user_prompt = USER_PROMPT_TEMPLATE.format(report_md=report_md)
    combined_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"

    success = False
    summary_md = ""
    error_message = ""

    try:
        raw = call_llm(combined_prompt, max_tokens=DEFAULT_MAX_TOKENS, timeout=120)
        if raw and raw.strip():
            summary_md = raw.strip()
            success = True
        else:
            error_message = "LLM returned empty response"
    except Exception as exc:
        error_message = str(exc)

    step5_summary: dict[str, Any] = {
        "input_file": str(report_path),
        "success": success,
        "error_message": error_message if not success else None,
        "output_length": len(summary_md),
    }

    # Write atomically
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent))
    try:
        (staging / OUTPUT_FILE).write_text(summary_md, encoding="utf-8")
        with (staging / SUMMARY_FILE).open("w", encoding="utf-8") as f:
            json.dump(step5_summary, f, ensure_ascii=False, indent=2)
        backup = output_path.with_name(f".{output_path.name}.previous")
        if backup.exists():
            shutil.rmtree(backup)
        if output_path.exists():
            os.replace(output_path, backup)
        try:
            os.replace(staging, output_path)
        except BaseException:
            if output_path.exists():
                shutil.rmtree(output_path)
            if backup.exists():
                os.replace(backup, output_path)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return step5_summary
