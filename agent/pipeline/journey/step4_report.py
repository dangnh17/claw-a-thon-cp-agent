from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.llm_client import call_llm

INPUT_FILES = (
    "insight_cards_for_llm.json",
    "watchlist_candidates.json",
    "baseline_behavior_cards.json",
    "step3_summary.json",
)
TARGET_OUTPUT_FILES = (
    "llm_payload.json",
    "llm_response.json",
    "mvp_journey_report.md",
    "step4_summary.json",
)
DEFAULT_MAX_TOKENS = 25000
REQUIRED_REPORT_HEADINGS = (
    "Executive Summary",
    "Q1",
    "Q2",
    "Watchlist",
    "Method Guardrails",
)
GUARDRAILS = [
    "Use only provided candidate cards and metrics.",
    "Do not invent patterns or recompute metrics.",
    "Do not infer root cause.",
    "Do not call terminal concentration a confirmed drop-off.",
    "Do not call loops confirmed issues.",
    "Separate baseline behavior from anomaly and friction candidates.",
    "Use evidence-first analysis: pattern, time window, metric vs baseline, interpretation, caveat.",
    "Group related cards into one finding when they describe the same behavior.",
    "Do not list every card mechanically.",
    "Use cautious wording: possible, candidate, observed signal, needs verification.",
    "Focus only on Q1 and Q2.",
]
WORKER_SPECS = (
    {"name": "q1_baseline", "focus": "Summarize stable baseline behavior for Q1 only.", "max_findings": 4},
    {"name": "q1_anomalies", "focus": "Summarize the strongest Q1 motif and routing changes versus baseline.", "max_findings": 5},
    {"name": "q2_terminals", "focus": "Summarize terminal concentration candidates and related routing for Q2.", "max_findings": 4},
    {"name": "q2_loops", "focus": "Summarize suspicious loop candidates and related routing for Q2.", "max_findings": 4},
    {"name": "watchlist", "focus": "Select a short watchlist of lower-confidence candidates worth monitoring.", "max_findings": 4},
)


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_inputs(input_dir: Path) -> dict[str, Any]:
    missing = [str(input_dir / name) for name in INPUT_FILES if not (input_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing Step 3 inputs: {safe_json(sorted(missing))}")
    payloads = {name: load_json(input_dir / name) for name in INPUT_FILES}
    for key in INPUT_FILES[:3]:
        wrapper = payloads[key]
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("cards"), list):
            raise ValueError(f"{key} must contain a wrapper object with a cards list.")
    if not isinstance(payloads["step3_summary.json"], dict):
        raise ValueError("step3_summary.json must contain a JSON object.")
    return payloads


def normalize_number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = float(stripped)
        except ValueError:
            return None
        if math.isnan(parsed) or math.isinf(parsed):
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def clip_text(value: Any, limit: int = 180) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def clean_text_list(value: Any, *, item_limit: int, text_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = clip_text(item, text_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= item_limit:
            break
    return cleaned


def compact_card_for_llm(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": clip_text(card.get("candidate_id"), 96),
        "candidate_type": clip_text(card.get("candidate_type"), 32),
        "question_area": clip_text(card.get("question_area"), 8),
        "tier": clip_text(card.get("tier"), 32),
        "label": clip_text(card.get("label"), 64),
        "time_window_3h": clip_text(card.get("time_window_3h"), 32),
        "title": clip_text(card.get("title"), 160),
        "signature": clip_text(card.get("signature"), 220),
        "analysis_tokens": clean_text_list(card.get("analysis_tokens"), item_limit=6, text_limit=80),
        "domains": clean_text_list(card.get("domains"), item_limit=4, text_limit=80),
        "metric_name": clip_text(card.get("metric_name"), 32),
        "metric_value": normalize_number(card.get("metric_value")),
        "baseline_metric_name": clip_text(card.get("baseline_metric_name"), 32),
        "baseline_metric_value": normalize_number(card.get("baseline_metric_value")),
        "lift_vs_baseline": normalize_number(card.get("lift_vs_baseline")),
        "z_score": normalize_number(card.get("z_score")),
        "normalized_delta": normalize_number(card.get("normalized_delta")),
        "low_baseline_support": bool(card.get("low_baseline_support")),
        "affected_session_count": normalize_number(card.get("affected_session_count")),
        "affected_user_count": normalize_number(card.get("affected_user_count")),
        "suspicion_signals": clean_text_list(card.get("suspicion_signals"), item_limit=6, text_limit=80),
        "related_candidates_summary": clean_text_list(card.get("related_candidates_summary"), item_limit=3, text_limit=160),
        "evidence_bullets": clean_text_list(card.get("evidence_bullets"), item_limit=3, text_limit=160),
    }


def unique_cards_by_candidate_id(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        candidate_id = str(card.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        unique.append(card)
    return unique


def related_summary_blob(card: dict[str, Any]) -> str:
    summaries = card.get("related_candidates_summary")
    if not isinstance(summaries, list):
        return ""
    return " ".join(str(item).strip().lower() for item in summaries if str(item).strip())


def routing_mentions(card: dict[str, Any], phrase: str) -> bool:
    if card.get("candidate_type") != "routing":
        return False
    return phrase.lower() in related_summary_blob(card)


def build_worker_groups(
    *,
    baseline_cards: list[dict[str, Any]],
    insight_cards: list[dict[str, Any]],
    watchlist_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combined = insight_cards + watchlist_cards
    q1_cards = [compact_card_for_llm(card) for card in combined if card.get("question_area") == "Q1"]
    terminal_cards = [compact_card_for_llm(card) for card in combined if card.get("candidate_type") == "terminal"]
    loop_cards = [compact_card_for_llm(card) for card in combined if card.get("candidate_type") == "loop"]
    terminal_routing_cards = [compact_card_for_llm(card) for card in combined if routing_mentions(card, "terminal concentration")]
    loop_routing_cards = [compact_card_for_llm(card) for card in combined if routing_mentions(card, "suspicious loop")]
    groups_by_name = {
        "q1_baseline": unique_cards_by_candidate_id([compact_card_for_llm(card) for card in baseline_cards]),
        "q1_anomalies": unique_cards_by_candidate_id(q1_cards),
        "q2_terminals": unique_cards_by_candidate_id(terminal_cards + terminal_routing_cards),
        "q2_loops": unique_cards_by_candidate_id(loop_cards + loop_routing_cards),
        "watchlist": unique_cards_by_candidate_id([compact_card_for_llm(card) for card in watchlist_cards]),
    }
    return [
        {"name": spec["name"], "focus": spec["focus"], "max_findings": spec["max_findings"], "cards": groups_by_name[spec["name"]]}
        for spec in WORKER_SPECS
    ]


def build_local_interpretation(worker_name: str, card: dict[str, Any]) -> str:
    signature = card.get("signature") or "this pattern"
    if worker_name == "q1_baseline":
        return f"Observed stable baseline behavior around {signature} in this 3-hour window."
    if worker_name == "q1_anomalies":
        return f"Observed Q1 change versus baseline around {signature}."
    if worker_name == "q2_terminals":
        return f"Observed terminal concentration candidate around {signature}."
    if worker_name == "q2_loops":
        return f"Observed suspicious loop candidate around {signature}."
    return f"Observed lower-confidence candidate around {signature} that is worth monitoring."


def build_local_caveat(worker_name: str, card: dict[str, Any]) -> str:
    caveats: list[str] = []
    if card.get("low_baseline_support"):
        caveats.append("Baseline support is low.")
    if card.get("baseline_metric_value") is None or card.get("lift_vs_baseline") is None:
        caveats.append("Baseline comparison is unavailable.")
    if worker_name in {"q2_terminals", "q2_loops", "watchlist"}:
        caveats.append("Treat this as candidate friction, not confirmed root cause.")
    if not caveats:
        caveats.append("Needs verification against downstream session outcomes.")
    return " ".join(caveats)


def reduce_worker_group_locally(group: dict[str, Any]) -> dict[str, Any]:
    selected_cards = list(group["cards"][: group["max_findings"]])
    omitted_candidate_ids = [
        str(card.get("candidate_id"))
        for card in group["cards"][group["max_findings"] :]
        if card.get("candidate_id")
    ]
    findings: list[dict[str, Any]] = []
    for card in selected_cards:
        related_context = clean_text_list(card.get("related_candidates_summary"), item_limit=2, text_limit=160)
        if not related_context:
            related_context = clean_text_list(card.get("suspicion_signals"), item_limit=2, text_limit=120)
        findings.append({
            "finding_title": clip_text(card.get("title") or card.get("signature") or "Untitled finding", 140),
            "candidate_ids": [str(card.get("candidate_id"))] if card.get("candidate_id") else [],
            "time_windows": [str(card.get("time_window_3h"))] if card.get("time_window_3h") else [],
            "lead_signature": clip_text(card.get("signature"), 220),
            "metric_name": clip_text(card.get("metric_name"), 32),
            "current_value": normalize_number(card.get("metric_value")),
            "baseline_value": normalize_number(card.get("baseline_metric_value")),
            "lift_vs_baseline": normalize_number(card.get("lift_vs_baseline")),
            "z_score": normalize_number(card.get("z_score")),
            "affected_sessions": normalize_number(card.get("affected_session_count")),
            "affected_users": normalize_number(card.get("affected_user_count")),
            "interpretation": build_local_interpretation(group["name"], card),
            "caveat": build_local_caveat(group["name"], card),
            "related_context": related_context,
        })
    return {
        "worker_name": group["name"],
        "focus": group["focus"],
        "findings": findings,
        "omitted_candidate_ids": omitted_candidate_ids,
    }


def build_debug_wrapper(
    *,
    step3_summary: dict[str, Any],
    baseline_cards: list[dict[str, Any]],
    insight_cards: list[dict[str, Any]],
    watchlist_cards: list[dict[str, Any]],
    report_language: str,
    worker_groups: list[dict[str, Any]],
    window_filters: list[str],
) -> dict[str, Any]:
    run_context = {
        "source": "step3_insight_candidates",
        "step3_summary": step3_summary,
        "card_counts": {
            "baseline_behavior_cards": len(baseline_cards),
            "insight_cards": len(insight_cards),
            "watchlist_candidates": len(watchlist_cards),
        },
        "window_filters": window_filters,
    }
    return {
        "run_context": run_context,
        "baseline_behavior_cards": baseline_cards,
        "insight_cards": insight_cards,
        "watchlist_candidates": watchlist_cards,
        "guardrails": GUARDRAILS,
        "target_questions": ["Q1", "Q2"],
        "report_language": report_language,
        "window_filters": window_filters,
        "chunking_strategy": {
            "mode": "worker_reduction_plus_final_synthesis",
            "worker_groups": [
                {"worker_name": g["name"], "focus": g["focus"], "max_findings": g["max_findings"], "input_card_count": len(g["cards"])}
                for g in worker_groups
            ],
        },
        "worker_input_groups": {g["name"]: g["cards"] for g in worker_groups},
        "worker_request_bodies": [],
        "worker_results": [],
        "request_body": None,
    }


def build_system_prompt(report_language: str) -> str:
    language_text = (
        "Write the report body in Vietnamese, while keeping the required section headings exactly as provided."
        if report_language == "vi"
        else "Write the report in English."
    )
    return (
        "You are a journey analytics assistant.\n"
        "Use only the provided cards, reduced findings, and metrics.\n"
        "Do not invent patterns.\n"
        "Do not recompute metrics.\n"
        "Do not infer root cause.\n"
        "Separate baseline behavior from behavior change.\n"
        "Do not call terminal concentration a confirmed drop-off.\n"
        "Do not call loops confirmed issues.\n"
        "Treat terminal concentration only as a possible stop point.\n"
        "Treat loop findings only as suspicious or candidate signals.\n"
        "Group related cards into one finding instead of listing every card.\n"
        "Use evidence-first analysis for each main finding in this order: candidate_id, pattern, time window, current metric, baseline metric, lift and z_score, affected sessions and users, interpretation, caveat.\n"
        "Use signal-strength wording carefully: say strong only when z_score is at least 2 or lift_vs_baseline is at least 1.5; otherwise use moderate, weak, or needs verification.\n"
        "Group related worker findings into one narrative when they describe the same behavior.\n"
        "Do not list every finding mechanically.\n"
        "Do not speculate about bugs, campaigns, policy changes, user intent, fraud, conversion, retention, or any other cause not present in the findings.\n"
        "Prefer 'needs verification' over any causal explanation.\n"
        "Treat every candidate label as an observed analytic signal, not a diagnosis.\n"
        "Use cautious wording: possible, candidate, observed signal, needs verification.\n"
        "Focus only on Q1 and Q2.\n"
        f"{language_text}"
    )


def build_user_prompt(
    *,
    wrapper: dict[str, Any],
    worker_results: list[dict[str, Any]],
    report_language: str,
    window_filters: list[str],
) -> str:
    language_note = "Vietnamese" if report_language == "vi" else "English"
    window_filter_note = ""
    if window_filters:
        window_filter_note = f"Focus on these time windows only: {', '.join(window_filters)}.\n"
    final_context = {
        "run_context": wrapper["run_context"],
        "worker_results": worker_results,
        "guardrails": wrapper["guardrails"],
        "target_questions": wrapper["target_questions"],
        "window_filters": window_filters,
    }
    return (
        f"Write a Markdown journey analytics report in {language_note}.\n"
        f"{window_filter_note}"
        "Use exactly these five section headings as Markdown headings:\n"
        "## Executive Summary\n"
        "## Q1\n"
        "## Q2\n"
        "## Watchlist\n"
        "## Method Guardrails\n"
        "Use only the reduced findings and metrics below.\n"
        "Prioritize the strongest signals rather than covering every finding.\n"
        "Keep the complete report under 1,400 words. Use at most 4 main findings in Q1, at most 4 main findings in Q2, and at most 4 short Watchlist bullets.\n"
        "Do not repeat a candidate_id in more than one finding.\n"
        "Include candidate_id for each main finding when available.\n"
        "Use concrete metrics: current value, baseline value, lift, z_score, affected sessions, and affected users. Say \"unavailable\" when a metric is null.\n"
        "For Q1, include 2 to 3 baseline behavior cards when available and 2 to 3 motif or routing increase/decrease cards when available.\n"
        "Split Q1 clearly into: Stable baseline behavior, Observed behavior changes, and Routing or motif changes by 3-hour window.\n"
        "For Q2, include both terminal and loop evidence when available.\n"
        "When available, include at least 2 terminal concentration cards and at least 2 suspicious loop cards.\n"
        "Do not let terminal cards consume the whole Q2 section.\n"
        "Only mention routing in Q2 when the reduced findings link it to terminal or loop behavior through related context.\n"
        "Never say users definitely failed, dropped off, or got stuck.\n"
        "Use wording like possible stop point, candidate friction, suspicious loop signal, and needs verification.\n"
        "Use the heading text 'Terminal concentration candidates'; do not rename it as drop-off.\n"
        "Use signal-strength wording consistently: strong only if z_score is at least 2 or lift_vs_baseline is at least 1.5; otherwise use moderate, weak, or needs verification.\n"
        "Keep the Watchlist section short.\n"
        "Include only low-confidence, new, or sparse signals worth monitoring in Watchlist.\n"
        "If a signature still looks like a raw event code rather than a semantic phrase, mark it as semantic-unclear in Watchlist.\n"
        "Do not mention Q3. Do not claim root cause. Do not mention raw logs.\n\n"
        "Worker findings payload:\n"
        f"{json.dumps(final_context, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def normalize_report_markdown(markdown: str) -> str:
    return markdown.replace("â€”", "—").strip()


def canonicalize_heading(heading: str) -> str:
    normalized = (
        heading.replace("â€”", "—")
        .replace("–", "—")
        .replace(" - ", " — ")
    )
    return " ".join(normalized.split())


def validate_required_headings(markdown: str) -> list[str]:
    found: set[str] = set()
    required = {canonicalize_heading(item): item for item in REQUIRED_REPORT_HEADINGS}
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip().rstrip("#").strip()
        numbered = heading.split(". ", 1)
        if len(numbered) == 2 and numbered[0].isdigit():
            heading = numbered[1].strip()
        canonical = canonicalize_heading(heading)
        if canonical in required:
            found.add(required[canonical])
    return [heading for heading in REQUIRED_REPORT_HEADINGS if heading not in found]


def failure_markdown(message: str) -> str:
    return (
        "## Step 4 Report Generation Failed\n\n"
        f"{message}\n\n"
        "See `llm_response.json` and `step4_summary.json` for details.\n"
    )


def write_artifacts(
    *,
    output_dir: Path,
    llm_payload: dict[str, Any],
    llm_response: dict[str, Any],
    markdown: str,
    step4_summary: dict[str, Any],
) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        payloads = {
            TARGET_OUTPUT_FILES[0]: llm_payload,
            TARGET_OUTPUT_FILES[1]: llm_response,
            TARGET_OUTPUT_FILES[3]: step4_summary,
        }
        for file_name, payload in payloads.items():
            with (staging_dir / file_name).open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        with (staging_dir / TARGET_OUTPUT_FILES[2]).open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown)
            if not markdown.endswith("\n"):
                handle.write("\n")
        # atomic install
        backup_dir = output_dir.with_name(f".{output_dir.name}.previous")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if output_dir.exists():
            os.replace(output_dir, backup_dir)
        try:
            os.replace(staging_dir, output_dir)
        except BaseException:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            if backup_dir.exists():
                os.replace(backup_dir, output_dir)
            raise
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    except BaseException:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise


def run(
    input_dir: str,
    output_dir: str,
    window_filters: list[str],
    report_language: str = "vi",
    **kwargs,
) -> dict[str, Any]:
    """
    Step 4: Generate Markdown journey report via LLM.
    Workers reduce locally (no LLM); only final synthesis calls call_llm().
    window_filters are injected into the prompt — Step 3 data is not filtered here.
    Returns step4_summary dict. Always reruns (not cached by cache_key).
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    inputs = load_inputs(input_path)
    insight_cards = list(inputs["insight_cards_for_llm.json"]["cards"])
    watchlist_cards = list(inputs["watchlist_candidates.json"]["cards"])
    baseline_cards = list(inputs["baseline_behavior_cards.json"]["cards"])
    step3_summary = dict(inputs["step3_summary.json"])

    request_timestamp = datetime.now(timezone.utc).isoformat()

    worker_groups = build_worker_groups(
        baseline_cards=baseline_cards,
        insight_cards=insight_cards,
        watchlist_cards=watchlist_cards,
    )
    llm_payload = build_debug_wrapper(
        step3_summary=step3_summary,
        baseline_cards=baseline_cards,
        insight_cards=insight_cards,
        watchlist_cards=watchlist_cards,
        report_language=report_language,
        worker_groups=worker_groups,
        window_filters=window_filters,
    )

    llm_response: dict[str, Any] = {"status": "success", "worker_calls": [], "final_call": None}
    worker_results: list[dict[str, Any]] = []
    markdown: str
    success = False

    # Step 1: local worker reductions (no LLM)
    for group in worker_groups:
        local_result = reduce_worker_group_locally(group)
        worker_results.append(local_result)
        llm_payload["worker_request_bodies"].append({
            "worker_name": group["name"],
            "request_body": None,
            "mode": "local_reduction",
        })
        llm_payload["worker_results"].append(local_result)
        llm_response["worker_calls"].append({
            "worker_name": group["name"],
            "focus": group["focus"],
            "status": "local_reduction",
            "input_card_count": len(group["cards"]),
            "finding_count": len(local_result["findings"]),
        })

    # Step 2: final synthesis via call_llm
    system_prompt = build_system_prompt(report_language)
    user_prompt = build_user_prompt(
        wrapper=llm_payload,
        worker_results=worker_results,
        report_language=report_language,
        window_filters=window_filters,
    )
    combined_prompt = f"{system_prompt}\n\n{user_prompt}"
    llm_payload["request_body"] = {"mode": "call_llm", "max_tokens": DEFAULT_MAX_TOKENS}

    final_call: dict[str, Any] = {}
    try:
        extracted_markdown = call_llm(combined_prompt, max_tokens=DEFAULT_MAX_TOKENS, timeout=120)
        final_call = {"status": "success", "extracted_content_length": len(extracted_markdown)}
        llm_response["final_call"] = final_call

        if not extracted_markdown or not extracted_markdown.strip():
            llm_response.update({
                "status": "error",
                "error_type": "empty_markdown",
                "message": "call_llm response did not contain extractable markdown content.",
                "failed_stage": "final_synthesis",
            })
            markdown = failure_markdown("LLM response did not contain extractable markdown content.")
        else:
            markdown = normalize_report_markdown(extracted_markdown)
            missing_headings = validate_required_headings(markdown)
            if missing_headings:
                llm_response.update({
                    "status": "error",
                    "error_type": "missing_required_headings",
                    "message": "Report is missing required Markdown headings.",
                    "failed_stage": "final_synthesis",
                    "missing_required_headings": missing_headings,
                })
                markdown = failure_markdown("LLM returned a report missing one or more required headings.")
            else:
                success = True
    except Exception as exc:  # noqa: BLE001
        final_call = {"status": "error", "error_type": "call_llm_exception", "message": str(exc)}
        llm_response.update({
            "status": "error",
            "error_type": "call_llm_exception",
            "message": str(exc),
            "failed_stage": "final_synthesis",
        })
        llm_response["final_call"] = final_call
        markdown = failure_markdown(f"LLM call raised an exception: {exc}")

    step4_summary: dict[str, Any] = {
        "input_card_counts": {
            "insight_cards": len(insight_cards),
            "watchlist_candidates": len(watchlist_cards),
            "baseline_behavior_cards": len(baseline_cards),
        },
        "payload_caps": {"insight_cards": 12, "watchlist_candidates": 12, "baseline_behavior_cards": 8},
        "report_language": report_language,
        "window_filters": window_filters,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "request_timestamp": request_timestamp,
        "request_status": "attempted",
        "response_status": llm_response.get("status"),
        "markdown_extraction_status": "success" if success else "failed",
        "heading_validation_status": "success" if success else "failed",
        "missing_required_headings": llm_response.get("missing_required_headings", []),
        "chunking_strategy": {
            "mode": "worker_reduction_plus_final_synthesis",
            "worker_names": [g["name"] for g in worker_groups],
        },
        "request_counts": {
            "worker_calls_planned": len(worker_groups),
            "worker_calls_completed": 0,
            "worker_groups_reduced": len(worker_groups),
            "final_calls_attempted": 1,
            "total_calls_attempted": 1,
        },
        "worker_statuses": {call.get("worker_name", f"worker_{i}"): call.get("status") for i, call in enumerate(llm_response.get("worker_calls", []))},
        "output_files": {name: True for name in TARGET_OUTPUT_FILES},
        "final_status": "success" if success else "failed",
        "client_response": {
            "ok": success,
            "content_type": "text/markdown; charset=utf-8",
            "render_as": "markdown",
            "sanitize_rendered_html": True,
            "report_path": "mvp_journey_report.md",
            "body_markdown": markdown,
        },
    }

    write_artifacts(
        output_dir=output_path,
        llm_payload=llm_payload,
        llm_response=llm_response,
        markdown=markdown,
        step4_summary=step4_summary,
    )
    return step4_summary
