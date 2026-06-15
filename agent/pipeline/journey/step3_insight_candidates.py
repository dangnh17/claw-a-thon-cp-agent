from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

INPUT_FILES = {
    "motif": "chain_motif_profile_by_3h.parquet",
    "terminal": "chain_terminal_profile_by_3h.parquet",
    "routing": "routing_shift_profile_by_3h.parquet",
    "loop": "suspicious_loop_profile_by_3h.parquet",
    "summary": "run_summary.json",
}
TARGET_OUTPUT_FILES = (
    "insight_cards_for_llm.json",
    "watchlist_candidates.json",
    "baseline_behavior_cards.json",
    "candidate_archive.parquet",
    "step3_summary.json",
)
WINDOW_LABELS = (
    "00:00-03:00", "03:00-06:00", "06:00-09:00", "09:00-12:00",
    "12:00-15:00", "15:00-18:00", "18:00-21:00", "21:00-24:00",
)
QUESTION_ORDER = {"Q1": 0, "Q2": 1}
CARD_LIMITS = {
    "insight": {"total": 12, "per_window": 4},
    "watchlist": {"total": 12, "per_window": 4},
    "baseline_behavior": {"total": 8, "per_window": 2},
}
TYPE_QUOTAS = {
    "insight": {"motif": 3, "routing": 3, "terminal": 3, "loop": 3},
    "watchlist": {"motif": 3, "routing": 3, "terminal": 3, "loop": 3},
}
ARCHIVE_COLUMNS = (
    "candidate_id", "candidate_type", "question_area", "time_window_3h", "signature",
    "title", "label", "tier", "candidate_score", "baseline_behavior_score",
    "affected_session_count", "affected_user_count", "metric_name", "metric_value",
    "baseline_metric_name", "baseline_metric_value", "lift_vs_baseline", "z_score",
    "normalized_delta", "low_baseline_support", "analysis_tokens_json", "event_families_json",
    "domains_json", "support_details_json", "type_specific_metrics_json", "suspicion_signals_json",
    "example_session_hashes_json", "dedupe_status", "dedupe_parent_candidate_id",
    "related_candidate_ids_json",
)
STEP2B_REQUIRED_POLICIES = {
    "time_window_policy": "fixed_3_hour_boundary",
    "timezone_policy": "no_china_time_conversion",
    "session_split_policy": "inactivity_gap_30_minutes",
    "primary_token": "analysis_token_v4",
}
TELEMETRY_KEYWORDS = (
    "render", "component", "platform", "lifecycle", "bridge", "exposure", "ads",
    "page", "root", "system", "telemetry", "poll", "polling", "heartbeat",
    "preload", "impression", "home",
)
SCORING_VERSION = "step3_v1"


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(float(value)):
        return None
    return round(float(value), 6)


def clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_list(text: Any) -> list[str]:
    if text is None:
        return []
    if isinstance(text, list):
        return [str(item) for item in text]
    if not str(text).strip():
        return []
    value = json.loads(str(text))
    if not isinstance(value, list):
        raise ValueError(f"Expected JSON list, received {type(value).__name__}")
    return [str(item) for item in value]


def normalize_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def hash_candidate(candidate_type: str, window: str, signature: str) -> str:
    return hashlib.sha256(f"step3::{candidate_type}::{window}::{signature}".encode("utf-8")).hexdigest()


def join_tokens(tokens: Sequence[str]) -> str:
    return " -> ".join(tokens)


def contains_subsequence(container: Sequence[str], fragment: Sequence[str]) -> bool:
    if not fragment or len(fragment) > len(container):
        return False
    limit = len(container) - len(fragment) + 1
    return any(list(container[i:i+len(fragment)]) == list(fragment) for i in range(limit))


def token_overlap_jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def validate_step2b_summary(summary: dict[str, Any]) -> None:
    policies = summary.get("policies")
    if not isinstance(policies, dict):
        raise ValueError("Step 2B run_summary.json is missing the policies object.")
    for key, expected in STEP2B_REQUIRED_POLICIES.items():
        if policies.get(key) != expected:
            raise ValueError(f"Step 2B policy mismatch for {key}: expected {expected!r}, received {policies.get(key)!r}")


def read_inputs(input_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = {name: input_dir / file_name for name, file_name in INPUT_FILES.items()}
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing Step 2B inputs: {safe_json(sorted(missing))}")
    summary = load_json(paths["summary"])
    if not isinstance(summary, dict):
        raise ValueError("Step 2B run_summary.json must contain a JSON object.")
    validate_step2b_summary(summary)
    frames = {
        "motif": pd.read_parquet(paths["motif"]),
        "terminal": pd.read_parquet(paths["terminal"]),
        "routing": pd.read_parquet(paths["routing"]),
        "loop": pd.read_parquet(paths["loop"]),
    }
    return frames, summary


def base_candidate(*, candidate_type, question_area, time_window_3h, signature, title,
                   affected_session_count, affected_user_count, metric_name, metric_value,
                   baseline_metric_name, baseline_metric_value, lift_vs_baseline, z_score,
                   normalized_delta, low_baseline_support, analysis_tokens, event_families,
                   domains, support_details, type_specific_metrics, suspicion_signals,
                   example_session_hashes, label) -> dict[str, Any]:
    return {
        "candidate_id": hash_candidate(candidate_type, time_window_3h, signature),
        "candidate_type": candidate_type, "question_area": question_area,
        "time_window_3h": time_window_3h, "signature": signature, "title": title,
        "label": label, "tier": "", "candidate_score": 0.0, "baseline_behavior_score": None,
        "affected_session_count": int(affected_session_count),
        "affected_user_count": int(affected_user_count),
        "metric_name": metric_name, "metric_value": float(metric_value),
        "baseline_metric_name": baseline_metric_name, "baseline_metric_value": float(baseline_metric_value),
        "lift_vs_baseline": None if lift_vs_baseline is None else float(lift_vs_baseline),
        "z_score": None if z_score is None else float(z_score),
        "normalized_delta": None if normalized_delta is None else float(normalized_delta),
        "low_baseline_support": bool(low_baseline_support),
        "analysis_tokens": analysis_tokens, "event_families": event_families,
        "domains": domains, "support_details": support_details,
        "type_specific_metrics": type_specific_metrics, "suspicion_signals": suspicion_signals,
        "example_session_hashes": example_session_hashes,
        "dedupe_status": "kept", "dedupe_parent_candidate_id": None, "related_candidate_ids": [],
        "background_telemetry": False, "telemetry_override": False,
    }


def candidate_from_motif(row: dict[str, Any]) -> dict[str, Any]:
    tokens = load_json_list(row["analysis_token_ngram"])
    families = load_json_list(row["event_family_ngram"])
    domains = load_json_list(row["domain_ngram"])
    signature = join_tokens(tokens)
    return base_candidate(
        candidate_type="motif", question_area="Q1", time_window_3h=str(row["time_window_3h"]),
        signature=signature, title=f"Motif spike candidate: {signature}",
        affected_session_count=int(row["affected_session_count"]),
        affected_user_count=int(row["affected_user_count"]),
        metric_name="share", metric_value=float(row["share"]),
        baseline_metric_name="baseline_share", baseline_metric_value=float(row["baseline_share"]),
        lift_vs_baseline=normalize_optional_float(row["lift_vs_baseline"]),
        z_score=normalize_optional_float(row["z_score"]),
        normalized_delta=normalize_optional_float(row["normalized_delta"]),
        low_baseline_support=bool(row["low_baseline_support"]),
        analysis_tokens=tokens, event_families=families, domains=domains,
        support_details={"occurrence_count": int(row["occurrence_count"]), "eligible_session_count": int(row["eligible_session_count"]), "baseline_support_session_count": int(row["baseline_support_session_count"]), "baseline_support_user_count": int(row["baseline_support_user_count"])},
        type_specific_metrics={"rank": int(row["rank"]), "ngram_length": int(row["ngram_length"])},
        suspicion_signals=[], example_session_hashes=load_json_list(row["example_session_hashes"]),
        label="motif_spike",
    )


def candidate_from_terminal(row: dict[str, Any]) -> dict[str, Any]:
    prefix_tokens = load_json_list(row["analysis_token_prefix"])
    tokens = prefix_tokens + [str(row["terminal_token"])]
    families = load_json_list(row["event_family_prefix"]) + [str(row["terminal_event_family"])]
    domains = load_json_list(row["domain_prefix"]) + [str(row["terminal_domain"])]
    signature = join_tokens(tokens)
    return base_candidate(
        candidate_type="terminal", question_area="Q2", time_window_3h=str(row["time_window_3h"]),
        signature=signature, title=f"Terminal concentration candidate: {signature}",
        affected_session_count=int(row["terminal_session_count"]),
        affected_user_count=int(row["affected_user_count"]),
        metric_name="stop_rate", metric_value=float(row["stop_rate"]),
        baseline_metric_name="baseline_stop_rate", baseline_metric_value=float(row["baseline_stop_rate"]),
        lift_vs_baseline=normalize_optional_float(row["lift_vs_baseline"]),
        z_score=normalize_optional_float(row["z_score"]),
        normalized_delta=normalize_optional_float(row["normalized_delta"]),
        low_baseline_support=bool(row["low_baseline_support"]),
        analysis_tokens=tokens, event_families=families, domains=domains,
        support_details={"prefix_exposure_session_count": int(row["prefix_exposure_session_count"]), "baseline_support_session_count": int(row["baseline_support_session_count"]), "baseline_support_user_count": int(row["baseline_support_user_count"])},
        type_specific_metrics={"rank": int(row["rank"]), "prefix_length": int(row["prefix_length"]), "terminal_token": str(row["terminal_token"]), "terminal_event_family": str(row["terminal_event_family"]), "terminal_domain": str(row["terminal_domain"])},
        suspicion_signals=[], example_session_hashes=load_json_list(row["example_session_hashes"]),
        label="terminal_concentration",
    )


def candidate_from_routing(row: dict[str, Any]) -> dict[str, Any]:
    tokens = [str(row["source_token"]), str(row["target_token"])]
    families = [str(row["source_event_family"]), str(row["target_event_family"])]
    domains = [str(row["source_domain"]), str(row["target_domain"])]
    signature = join_tokens(tokens)
    return base_candidate(
        candidate_type="routing", question_area="Q1", time_window_3h=str(row["time_window_3h"]),
        signature=signature, title=f"Routing shift candidate: {signature}",
        affected_session_count=int(row["unique_session_count"]),
        affected_user_count=int(row["unique_user_count"]),
        metric_name="share", metric_value=float(row["share"]),
        baseline_metric_name="baseline_share", baseline_metric_value=float(row["baseline_share"]),
        lift_vs_baseline=normalize_optional_float(row["lift_vs_baseline"]),
        z_score=normalize_optional_float(row["z_score"]),
        normalized_delta=normalize_optional_float(row["normalized_delta"]),
        low_baseline_support=bool(row["low_baseline_support"]),
        analysis_tokens=tokens, event_families=families, domains=domains,
        support_details={"transition_count": int(row["transition_count"]), "source_total_count": int(row["source_total_count"]), "baseline_support_session_count": int(row["baseline_support_session_count"]), "baseline_support_user_count": int(row["baseline_support_user_count"])},
        type_specific_metrics={"rank": int(row["rank"]), "transition_count": int(row["transition_count"]), "source_token": str(row["source_token"]), "target_token": str(row["target_token"])},
        suspicion_signals=[], example_session_hashes=load_json_list(row["example_session_hashes"]),
        label="routing_shift",
    )


def candidate_from_loop(row: dict[str, Any]) -> dict[str, Any]:
    tokens = load_json_list(row["loop_token_chain"])
    families = load_json_list(row["loop_event_family_chain"])
    domains = load_json_list(row["loop_domain_chain"])
    signature = join_tokens(tokens)
    return base_candidate(
        candidate_type="loop", question_area="Q2", time_window_3h=str(row["time_window_3h"]),
        signature=signature, title=f"Suspicious loop candidate: {signature}",
        affected_session_count=int(row["affected_session_count"]),
        affected_user_count=int(row["affected_user_count"]),
        metric_name="share", metric_value=float(row["share"]),
        baseline_metric_name="baseline_share", baseline_metric_value=float(row["baseline_share"]),
        lift_vs_baseline=normalize_optional_float(row["lift_vs_baseline"]),
        z_score=normalize_optional_float(row["z_score"]),
        normalized_delta=normalize_optional_float(row["normalized_delta"]),
        low_baseline_support=bool(row["low_baseline_support"]),
        analysis_tokens=tokens, event_families=families, domains=domains,
        support_details={"eligible_session_count": int(row["eligible_session_count"]), "baseline_support_session_count": int(row["baseline_support_session_count"]), "baseline_support_user_count": int(row["baseline_support_user_count"])},
        type_specific_metrics={"rank": int(row["rank"]), "loop_length": int(row["loop_length"]), "loop_detection_mode": str(row["loop_detection_mode"]), "average_repeat_count": float(row["average_repeat_count"]), "max_repeat_count": int(row["max_repeat_count"]), "median_duration_seconds": float(row["median_duration_seconds"]), "p95_duration_seconds": float(row["p95_duration_seconds"]), "no_progress_rate": float(row["no_progress_rate"]), "terminal_after_loop_rate": float(row["terminal_after_loop_rate"]), "error_evidence_rate": float(row["error_evidence_rate"]), "severity_score": float(row["severity_score"])},
        suspicion_signals=load_json_list(row["suspicion_signals"]),
        example_session_hashes=load_json_list(row["example_session_hashes"]),
        label="suspicious_loop",
    )


def compute_support_score(candidate: dict[str, Any]) -> float:
    return clip01(max(candidate["affected_session_count"] / 25.0, candidate["affected_user_count"] / 15.0))


def compute_anomaly_score(candidate: dict[str, Any]) -> float:
    lift = candidate["lift_vs_baseline"]
    z_score = candidate["z_score"]
    delta = candidate["normalized_delta"]
    return max(clip01(((lift or 1.0) - 1.0) / 2.0), clip01(abs(z_score or 0.0) / 5.0), clip01(abs(delta or 0.0) / 2.5))


def compute_scores(candidate: dict[str, Any]) -> None:
    support_score = compute_support_score(candidate)
    anomaly_score = compute_anomaly_score(candidate)
    baseline_confidence = 0.0 if candidate["low_baseline_support"] else 1.0
    metric_value = float(candidate["metric_value"])
    candidate_type = candidate["candidate_type"]
    if candidate_type == "motif":
        score = 100.0 * (0.35 * support_score + 0.35 * anomaly_score + 0.20 * clip01(metric_value / 0.30) + 0.10 * baseline_confidence)
        baseline_score = 100.0 * (0.45 * clip01(metric_value / 0.40) + 0.35 * support_score + 0.20 * (1.0 - min(1.0, anomaly_score)))
    elif candidate_type == "routing":
        score = 100.0 * (0.30 * support_score + 0.40 * anomaly_score + 0.20 * clip01(metric_value / 0.40) + 0.10 * baseline_confidence)
        baseline_score = 100.0 * (0.45 * clip01(metric_value / 0.40) + 0.35 * support_score + 0.20 * (1.0 - min(1.0, anomaly_score)))
    elif candidate_type == "terminal":
        score = 100.0 * (0.25 * support_score + 0.30 * clip01(metric_value / 0.80) + 0.30 * anomaly_score + 0.15 * baseline_confidence)
        baseline_score = None
    else:
        metrics = candidate["type_specific_metrics"]
        score = 100.0 * (0.20 * support_score + 0.25 * clip01(float(metrics["severity_score"]) / 100.0) + 0.20 * anomaly_score + 0.20 * max(float(metrics["no_progress_rate"]), float(metrics["terminal_after_loop_rate"])) + 0.15 * max(clip01(float(metrics["error_evidence_rate"]) / 0.25), clip01(float(metrics["median_duration_seconds"]) / 1800.0)))
        baseline_score = None
    candidate["candidate_score"] = round_metric(score) or 0.0
    candidate["baseline_behavior_score"] = round_metric(baseline_score)


def detect_background_telemetry(candidate: dict[str, Any]) -> None:
    if candidate["candidate_type"] != "loop":
        return
    metrics = candidate["type_specific_metrics"]
    combined = " ".join(v.lower() for v in [*candidate["analysis_tokens"], *candidate["domains"]])
    keyword_match = any(keyword in combined for keyword in TELEMETRY_KEYWORDS)
    override = ((candidate["lift_vs_baseline"] or 0.0) >= 2.0 or float(metrics["error_evidence_rate"]) >= 0.10 or float(metrics["median_duration_seconds"]) >= 300.0 or float(metrics["severity_score"]) >= 80.0)
    background = (int(metrics["loop_length"]) == 1 and keyword_match and float(metrics["error_evidence_rate"]) < 0.10 and float(metrics["median_duration_seconds"]) < 30.0 and ((candidate["lift_vs_baseline"] or 0.0) < 1.50))
    candidate["background_telemetry"] = background
    candidate["telemetry_override"] = override


def overlapping_dominant_group(left_tokens: Sequence[str], right_tokens: Sequence[str]) -> bool:
    return len(set(left_tokens) & set(right_tokens)) >= 2


def sort_for_dedupe(candidate: dict[str, Any]) -> tuple:
    return (WINDOW_LABELS.index(candidate["time_window_3h"]), -float(candidate["candidate_score"]), -candidate["affected_session_count"], -len(candidate["analysis_tokens"]), candidate["candidate_id"])


def mark_duplicate(weaker: dict[str, Any], stronger: dict[str, Any]) -> None:
    weaker["dedupe_status"] = "duplicate"
    weaker["dedupe_parent_candidate_id"] = stronger["candidate_id"]


def apply_same_type_dedupe(candidates: list[dict[str, Any]]) -> None:
    motif_candidates = sorted([c for c in candidates if c["candidate_type"] == "motif"], key=sort_for_dedupe)
    for stronger in motif_candidates:
        if stronger["dedupe_status"] != "kept":
            continue
        for weaker in motif_candidates:
            if weaker["candidate_id"] == stronger["candidate_id"] or weaker["dedupe_status"] != "kept" or stronger["time_window_3h"] != weaker["time_window_3h"] or len(stronger["analysis_tokens"]) <= len(weaker["analysis_tokens"]):
                continue
            if stronger["candidate_score"] >= weaker["candidate_score"] + 5.0 and stronger["affected_session_count"] >= weaker["affected_session_count"] and contains_subsequence(stronger["analysis_tokens"], weaker["analysis_tokens"]):
                mark_duplicate(weaker, stronger)

    terminal_groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        if c["candidate_type"] != "terminal":
            continue
        terminal_groups[(c["time_window_3h"], str(c["type_specific_metrics"]["terminal_token"]))].append(c)
    for group in terminal_groups.values():
        group.sort(key=lambda c: (-int(c["type_specific_metrics"]["prefix_length"]), -float(c["candidate_score"]), -c["affected_session_count"]))
        for stronger in group:
            if stronger["dedupe_status"] != "kept":
                continue
            stronger_prefix = stronger["analysis_tokens"][:-1]
            for weaker in group:
                if weaker["candidate_id"] == stronger["candidate_id"] or weaker["dedupe_status"] != "kept" or int(stronger["type_specific_metrics"]["prefix_length"]) <= int(weaker["type_specific_metrics"]["prefix_length"]):
                    continue
                weaker_prefix = weaker["analysis_tokens"][:-1]
                if len(weaker_prefix) > len(stronger_prefix):
                    continue
                if stronger_prefix[-len(weaker_prefix):] != weaker_prefix:
                    continue
                if stronger["affected_session_count"] >= math.ceil(weaker["affected_session_count"] * 0.9) and stronger["candidate_score"] >= weaker["candidate_score"]:
                    mark_duplicate(weaker, stronger)


def apply_semantic_cluster_dedupe(candidates: list[dict[str, Any]]) -> None:
    clusterable_types = {"motif", "routing", "loop"}
    grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        if c["candidate_type"] in clusterable_types:
            grouped[(c["time_window_3h"], c["candidate_type"])].append(c)
    for group in grouped.values():
        ordered = sorted(group, key=sort_for_dedupe)
        for stronger in ordered:
            if stronger["dedupe_status"] != "kept":
                continue
            for weaker in ordered:
                if weaker["candidate_id"] == stronger["candidate_id"] or weaker["dedupe_status"] != "kept":
                    continue
                if token_overlap_jaccard(stronger["analysis_tokens"], weaker["analysis_tokens"]) < 0.6:
                    continue
                if not overlapping_dominant_group(stronger["analysis_tokens"], weaker["analysis_tokens"]):
                    continue
                if stronger["candidate_score"] >= weaker["candidate_score"]:
                    mark_duplicate(weaker, stronger)
                    stronger["related_candidate_ids"].append(weaker["candidate_id"])


def apply_cross_type_dedupe(candidates: list[dict[str, Any]]) -> None:
    motifs = [c for c in candidates if c["candidate_type"] == "motif" and len(c["analysis_tokens"]) == 2 and c["dedupe_status"] == "kept"]
    routing = [c for c in candidates if c["candidate_type"] == "routing" and c["dedupe_status"] == "kept"]
    routing_lookup = {(c["time_window_3h"], tuple(c["analysis_tokens"])): c for c in routing}
    for motif in motifs:
        key = (motif["time_window_3h"], tuple(motif["analysis_tokens"]))
        routing_candidate = routing_lookup.get(key)
        if routing_candidate is None:
            continue
        if motif["candidate_score"] >= routing_candidate["candidate_score"]:
            mark_duplicate(routing_candidate, motif)
        else:
            mark_duplicate(motif, routing_candidate)


def apply_dedupe(candidates: list[dict[str, Any]]) -> None:
    apply_same_type_dedupe(candidates)
    apply_semantic_cluster_dedupe(candidates)
    apply_cross_type_dedupe(candidates)


def link_candidates(candidates: list[dict[str, Any]]) -> None:
    kept_or_suppressed = {c["candidate_id"]: c for c in candidates}
    by_window: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        by_window[c["time_window_3h"]].append(c)
    for window_candidates in by_window.values():
        for left in window_candidates:
            related: set[str] = set(left["related_candidate_ids"])
            for right in window_candidates:
                if left["candidate_id"] == right["candidate_id"]:
                    continue
                lt, rt = left["candidate_type"], right["candidate_type"]
                if (lt == "routing" and rt == "terminal") or (lt == "terminal" and rt == "routing") or (lt == "routing" and rt == "loop") or (lt == "loop" and rt == "routing"):
                    if set(left["analysis_tokens"]) & set(right["analysis_tokens"]):
                        related.add(right["candidate_id"])
                elif (lt == "motif" and rt == "loop") or (lt == "loop" and rt == "motif"):
                    if token_overlap_jaccard(left["analysis_tokens"], right["analysis_tokens"]) >= 0.5:
                        related.add(right["candidate_id"])
            left["related_candidate_ids"] = sorted(cid for cid in related if cid in kept_or_suppressed)


def is_decrease_candidate(candidate: dict[str, Any]) -> bool:
    if candidate["candidate_type"] not in {"motif", "routing"}:
        return False
    lift = candidate["lift_vs_baseline"]
    z_score = candidate["z_score"]
    return ((lift is not None and lift < 0.8) or (z_score is not None and z_score < -2.0))


def assign_title(candidate: dict[str, Any]) -> str:
    signature = candidate["signature"]
    if candidate["tier"] == "baseline_behavior":
        return f"Baseline behavior: {signature}"
    if candidate["candidate_type"] == "motif":
        return f"Motif decrease candidate: {signature}" if is_decrease_candidate(candidate) else f"Motif increase candidate: {signature}"
    if candidate["candidate_type"] == "routing":
        return f"Routing decrease candidate: {signature}" if is_decrease_candidate(candidate) else f"Routing shift candidate: {signature}"
    if candidate["candidate_type"] == "terminal":
        return f"Terminal concentration candidate: {signature}"
    return f"Suspicious loop candidate: {signature}"


def assign_label(candidate: dict[str, Any], default_label: str) -> str:
    if candidate["dedupe_status"] == "duplicate":
        return "duplicate_of_stronger_pattern"
    if candidate["background_telemetry"] and not candidate["telemetry_override"]:
        return "background_telemetry"
    if candidate["tier"] == "baseline_behavior":
        return "baseline_behavior"
    if candidate["tier"] in ("suppressed_archive", "watchlist") and candidate["low_baseline_support"]:
        return "low_confidence_sparse"
    return default_label


def assign_tiers(candidates: list[dict[str, Any]]) -> None:
    for candidate in candidates:
        default_label = candidate["label"]
        if candidate["dedupe_status"] == "duplicate":
            candidate["tier"] = "suppressed_archive"
            candidate["label"] = "duplicate_of_stronger_pattern"
            candidate["title"] = assign_title(candidate)
            continue
        if candidate["background_telemetry"] and not candidate["telemetry_override"]:
            candidate["tier"] = "suppressed_archive"
            candidate["label"] = "background_telemetry"
            candidate["title"] = assign_title(candidate)
            continue
        is_baseline = (
            candidate["candidate_type"] in {"motif", "routing"}
            and not candidate["low_baseline_support"]
            and candidate["metric_value"] >= 0.10
            and ((candidate["lift_vs_baseline"] is not None and 0.80 <= candidate["lift_vs_baseline"] <= 1.25) or (candidate["lift_vs_baseline"] is None and candidate["baseline_metric_value"] >= 0.10))
            and (candidate["z_score"] is None or abs(candidate["z_score"]) < 2.0)
            and (candidate["baseline_behavior_score"] or 0.0) >= 45.0
        )
        if is_baseline:
            candidate["tier"] = "baseline_behavior"
            candidate["label"] = "baseline_behavior"
            candidate["title"] = assign_title(candidate)
            continue
        insight_threshold = {"motif": 60.0, "routing": 60.0, "terminal": 55.0, "loop": 65.0}[candidate["candidate_type"]]
        if candidate["candidate_score"] >= insight_threshold:
            candidate["tier"] = "insight"
            candidate["label"] = assign_label(candidate, default_label)
            if candidate["candidate_type"] == "motif" and is_decrease_candidate(candidate):
                candidate["label"] = "motif_decrease"
            elif candidate["candidate_type"] == "routing" and is_decrease_candidate(candidate):
                candidate["label"] = "routing_decrease"
            candidate["title"] = assign_title(candidate)
            continue
        if candidate["candidate_score"] >= 40.0:
            candidate["tier"] = "watchlist"
            candidate["label"] = assign_label(candidate, default_label)
            if candidate["candidate_type"] == "motif" and is_decrease_candidate(candidate):
                candidate["label"] = "motif_decrease"
            elif candidate["candidate_type"] == "routing" and is_decrease_candidate(candidate):
                candidate["label"] = "routing_decrease"
            candidate["title"] = assign_title(candidate)
            continue
        candidate["tier"] = "suppressed_archive"
        candidate["label"] = assign_label(candidate, default_label)
        candidate["title"] = assign_title(candidate)


def evidence_bullets(candidate: dict[str, Any]) -> list[str]:
    bullets = [
        f"{candidate['affected_session_count']} sessions and {candidate['affected_user_count']} users in {candidate['time_window_3h']}.",
        f"{candidate['metric_name']} {candidate['metric_value']:.3f} vs {candidate['baseline_metric_name']} {candidate['baseline_metric_value']:.3f}.",
    ]
    if candidate["lift_vs_baseline"] is not None:
        bullets.append(f"Lift vs baseline {candidate['lift_vs_baseline']:.3f}.")
    else:
        bullets.append("Lift vs baseline unavailable because baseline is zero or low-support.")
    if candidate["z_score"] is not None:
        bullets.append(f"z-score {candidate['z_score']:.3f}.")
    if candidate["candidate_type"] == "routing":
        bullets.append(f"Transition count {candidate['support_details']['transition_count']} from source total {candidate['support_details']['source_total_count']}.")
    elif candidate["candidate_type"] == "terminal":
        bullets.append(f"Prefix exposure {candidate['support_details']['prefix_exposure_session_count']}.")
    elif candidate["candidate_type"] == "loop":
        metrics = candidate["type_specific_metrics"]
        bullets.append(f"No-progress {metrics['no_progress_rate']:.3f}, terminal-after-loop {metrics['terminal_after_loop_rate']:.3f}, severity {metrics['severity_score']:.3f}.")
    else:
        bullets.append(f"Occurrence count {candidate['support_details']['occurrence_count']} across eligible sessions {candidate['support_details']['eligible_session_count']}.")
    return bullets[:4]


def describe_related(candidate: dict[str, Any]) -> str:
    signature = candidate["signature"]
    if candidate["candidate_type"] == "terminal":
        return f"Same window has terminal concentration on {signature}"
    if candidate["candidate_type"] == "routing":
        return f"Same window has routing decrease {signature}" if is_decrease_candidate(candidate) else f"Same window has routing shift {signature}"
    if candidate["candidate_type"] == "loop":
        return f"Same window has suspicious loop {signature}"
    return f"Same window has motif decrease {signature}" if is_decrease_candidate(candidate) else f"Same window has motif increase {signature}"


def sort_key_for_cards(candidate: dict[str, Any], *, baseline: bool = False) -> tuple:
    if baseline:
        return (QUESTION_ORDER[candidate["question_area"]], -float(candidate["baseline_behavior_score"] or 0.0), 0 if candidate["candidate_type"] == "motif" else 1, -candidate["affected_session_count"], candidate["candidate_id"])
    return (QUESTION_ORDER[candidate["question_area"]], -float(candidate["candidate_score"]), -candidate["affected_session_count"], candidate["candidate_id"])


def select_candidates(candidates: list[dict[str, Any]], tier: str, *, baseline: bool = False) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts_by_window: Counter[str] = Counter()
    limits = CARD_LIMITS[tier]
    ordered = sorted([item for item in candidates if item["tier"] == tier], key=lambda item: sort_key_for_cards(item, baseline=baseline))
    quota_by_type = None if baseline else TYPE_QUOTAS[tier]
    counts_by_type: Counter[str] = Counter()
    selected_ids: set[str] = set()
    deferred: list[dict[str, Any]] = []
    for candidate in ordered:
        window = candidate["time_window_3h"]
        if counts_by_window[window] >= limits["per_window"]:
            deferred.append(candidate)
            continue
        if quota_by_type is not None and counts_by_type[candidate["candidate_type"]] >= quota_by_type[candidate["candidate_type"]]:
            deferred.append(candidate)
            continue
        selected.append(candidate)
        selected_ids.add(candidate["candidate_id"])
        counts_by_window[window] += 1
        counts_by_type[candidate["candidate_type"]] += 1
        if len(selected) >= limits["total"]:
            return selected
    for candidate in deferred:
        if candidate["candidate_id"] in selected_ids:
            continue
        window = candidate["time_window_3h"]
        if counts_by_window[window] >= limits["per_window"]:
            continue
        selected.append(candidate)
        selected_ids.add(candidate["candidate_id"])
        counts_by_window[window] += 1
        if len(selected) >= limits["total"]:
            break
    return selected


def build_card(candidate: dict[str, Any], all_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    related_summaries = []
    for related_id in candidate["related_candidate_ids"]:
        related = all_lookup.get(related_id)
        if related is None:
            continue
        related_summaries.append(describe_related(related))
        if len(related_summaries) >= 3:
            break
    return {
        "candidate_id": candidate["candidate_id"], "tier": candidate["tier"],
        "label": candidate["label"], "candidate_type": candidate["candidate_type"],
        "question_area": candidate["question_area"], "time_window_3h": candidate["time_window_3h"],
        "title": candidate["title"], "signature": candidate["signature"],
        "analysis_tokens": candidate["analysis_tokens"], "domains": candidate["domains"],
        "affected_session_count": candidate["affected_session_count"],
        "affected_user_count": candidate["affected_user_count"],
        "metric_name": candidate["metric_name"], "metric_value": round_metric(candidate["metric_value"]),
        "baseline_metric_name": candidate["baseline_metric_name"],
        "baseline_metric_value": round_metric(candidate["baseline_metric_value"]),
        "lift_vs_baseline": round_metric(candidate["lift_vs_baseline"]),
        "z_score": round_metric(candidate["z_score"]),
        "normalized_delta": round_metric(candidate["normalized_delta"]),
        "low_baseline_support": candidate["low_baseline_support"],
        "suspicion_signals": candidate["suspicion_signals"],
        "related_candidate_ids": candidate["related_candidate_ids"],
        "related_candidates_summary": related_summaries,
        "example_session_hashes": candidate["example_session_hashes"][:3],
        "evidence_bullets": evidence_bullets(candidate),
    }


def build_wrapper(cards: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
    policy = {"tier": tier, "max_cards": CARD_LIMITS[tier]["total"], "max_per_window": CARD_LIMITS[tier]["per_window"], "scoring_version": SCORING_VERSION}
    return {"card_count": len(cards), "selection_policy": policy, "cards": cards}


def archive_row(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"], "candidate_type": candidate["candidate_type"],
        "question_area": candidate["question_area"], "time_window_3h": candidate["time_window_3h"],
        "signature": candidate["signature"], "title": candidate["title"], "label": candidate["label"],
        "tier": candidate["tier"], "candidate_score": round_metric(candidate["candidate_score"]),
        "baseline_behavior_score": round_metric(candidate["baseline_behavior_score"]),
        "affected_session_count": candidate["affected_session_count"],
        "affected_user_count": candidate["affected_user_count"],
        "metric_name": candidate["metric_name"], "metric_value": round_metric(candidate["metric_value"]),
        "baseline_metric_name": candidate["baseline_metric_name"],
        "baseline_metric_value": round_metric(candidate["baseline_metric_value"]),
        "lift_vs_baseline": round_metric(candidate["lift_vs_baseline"]),
        "z_score": round_metric(candidate["z_score"]),
        "normalized_delta": round_metric(candidate["normalized_delta"]),
        "low_baseline_support": candidate["low_baseline_support"],
        "analysis_tokens_json": safe_json(candidate["analysis_tokens"]),
        "event_families_json": safe_json(candidate["event_families"]),
        "domains_json": safe_json(candidate["domains"]),
        "support_details_json": safe_json(candidate["support_details"]),
        "type_specific_metrics_json": safe_json(candidate["type_specific_metrics"]),
        "suspicion_signals_json": safe_json(candidate["suspicion_signals"]),
        "example_session_hashes_json": safe_json(candidate["example_session_hashes"]),
        "dedupe_status": candidate["dedupe_status"],
        "dedupe_parent_candidate_id": candidate["dedupe_parent_candidate_id"],
        "related_candidate_ids_json": safe_json(candidate["related_candidate_ids"]),
    }


def normalize_rows(frames: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in frames["motif"].to_dict(orient="records"):
        candidates.append(candidate_from_motif(row))
    for row in frames["terminal"].to_dict(orient="records"):
        candidates.append(candidate_from_terminal(row))
    for row in frames["routing"].to_dict(orient="records"):
        candidates.append(candidate_from_routing(row))
    for row in frames["loop"].to_dict(orient="records"):
        candidates.append(candidate_from_loop(row))
    for candidate in candidates:
        compute_scores(candidate)
        detect_background_telemetry(candidate)
    apply_dedupe(candidates)
    link_candidates(candidates)
    assign_tiers(candidates)
    return candidates


def write_parquet(rows: list[dict[str, Any]], columns: Sequence[str], path: Path) -> None:
    dataframe = pd.DataFrame(rows, columns=list(columns))
    dataframe.to_parquet(path, index=False, engine="pyarrow")


def run(input_dir: str, output_dir: str, **kwargs) -> None:
    """
    Step 3: Insight candidates from Step 2B aggregates.
    Reads from input_dir (step2b output), writes insight/watchlist/baseline cards + archive to output_dir.
    window_filters are NOT applied here (Option B — passed to Step 4 only).
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    frames, step2b_summary = read_inputs(input_path)
    candidates = normalize_rows(frames)

    insight_candidates = select_candidates(candidates, "insight")
    watchlist_candidates = select_candidates(candidates, "watchlist")
    baseline_candidates = select_candidates(candidates, "baseline_behavior", baseline=True)

    all_lookup = {c["candidate_id"]: c for c in candidates}
    insight_cards = [build_card(c, all_lookup) for c in insight_candidates]
    watchlist_cards = [build_card(c, all_lookup) for c in watchlist_candidates]
    baseline_cards = [build_card(c, all_lookup) for c in baseline_candidates]

    archive_rows = [archive_row(c) for c in candidates]
    tier_counts = Counter(c["tier"] for c in candidates)
    label_counts = Counter(c["label"] for c in candidates)
    dedupe_counts = Counter(c["dedupe_status"] for c in candidates)
    relation_count = sum(len(c["related_candidate_ids"]) for c in candidates)
    step3_summary = {
        "input_row_counts": {
            "chain_motif_profile_by_3h": len(frames["motif"]),
            "chain_terminal_profile_by_3h": len(frames["terminal"]),
            "routing_shift_profile_by_3h": len(frames["routing"]),
            "suspicious_loop_profile_by_3h": len(frames["loop"]),
        },
        "archive_row_count": len(archive_rows),
        "tier_counts": dict(sorted(tier_counts.items())),
        "label_counts": dict(sorted(label_counts.items())),
        "dedupe_counts": dict(sorted(dedupe_counts.items())),
        "card_counts": {
            "insight_cards_for_llm": len(insight_cards),
            "watchlist_candidates": len(watchlist_cards),
            "baseline_behavior_cards": len(baseline_cards),
        },
        "relation_counts": {"total_directed_links": relation_count, "selected_card_count": len(insight_candidates) + len(watchlist_candidates) + len(baseline_candidates)},
        "scoring": {"version": SCORING_VERSION, "card_limits": CARD_LIMITS},
        "privacy": "compact_cards_only;hashed_example_session_ids_only;no_raw_logs_no_raw_pii",
        "root_cause_policy": "no_root_cause_claims",
        "upstream_run_context": {
            "input_file_count": step2b_summary.get("input_file_count"),
            "analytical_session_count": step2b_summary.get("analytical_session_count"),
            "parsed_event_count": step2b_summary.get("parsed_event_count"),
        },
    }

    output_path.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent))
    try:
        wrappers = {
            TARGET_OUTPUT_FILES[0]: build_wrapper(insight_cards, tier="insight"),
            TARGET_OUTPUT_FILES[1]: build_wrapper(watchlist_cards, tier="watchlist"),
            TARGET_OUTPUT_FILES[2]: build_wrapper(baseline_cards, tier="baseline_behavior"),
        }
        for file_name, payload in wrappers.items():
            with (staging_dir / file_name).open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
        write_parquet(archive_rows, ARCHIVE_COLUMNS, staging_dir / TARGET_OUTPUT_FILES[3])
        with (staging_dir / TARGET_OUTPUT_FILES[4]).open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(step3_summary, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        # atomic install
        backup_dir = output_path.with_name(f".{output_path.name}.previous")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if output_path.exists():
            os.replace(output_path, backup_dir)
        try:
            os.replace(staging_dir, output_path)
        except BaseException:
            if output_path.exists():
                shutil.rmtree(output_path)
            if backup_dir.exists():
                os.replace(backup_dir, output_path)
            raise
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    except BaseException:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise
