from __future__ import annotations

import csv
import glob as glob_module
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import pyarrow.parquet as pq

WINDOW_LABELS = (
    "00:00-03:00",
    "03:00-06:00",
    "06:00-09:00",
    "09:00-12:00",
    "12:00-15:00",
    "15:00-18:00",
    "18:00-21:00",
    "21:00-24:00",
)
TARGET_OUTPUT_FILES = (
    "chain_motif_profile_by_3h.parquet",
    "chain_terminal_profile_by_3h.parquet",
    "routing_shift_profile_by_3h.parquet",
    "suspicious_loop_profile_by_3h.parquet",
    "run_summary.json",
)
MOTIF_COLUMNS = (
    "time_window_3h", "rank", "ngram_length", "analysis_token_ngram", "event_family_ngram",
    "domain_ngram", "occurrence_count", "affected_session_count", "affected_user_count",
    "eligible_session_count", "share", "baseline_support_session_count",
    "baseline_support_user_count", "baseline_share", "lift_vs_baseline", "z_score",
    "normalized_delta", "low_baseline_support", "example_session_hashes",
)
TERMINAL_COLUMNS = (
    "time_window_3h", "rank", "prefix_length", "analysis_token_prefix", "event_family_prefix",
    "domain_prefix", "terminal_token", "terminal_event_family", "terminal_domain",
    "terminal_session_count", "affected_user_count", "prefix_exposure_session_count",
    "stop_rate", "baseline_support_session_count", "baseline_support_user_count",
    "baseline_stop_rate", "lift_vs_baseline", "z_score", "normalized_delta",
    "low_baseline_support", "example_session_hashes",
)
ROUTING_COLUMNS = (
    "time_window_3h", "rank", "source_token", "source_event_family", "source_domain",
    "target_token", "target_event_family", "target_domain", "transition_count",
    "unique_session_count", "unique_user_count", "source_total_count", "share",
    "baseline_support_session_count", "baseline_support_user_count", "baseline_share",
    "lift_vs_baseline", "z_score", "normalized_delta", "low_baseline_support",
    "example_session_hashes",
)
LOOP_COLUMNS = (
    "time_window_3h", "rank", "loop_length", "loop_detection_mode", "loop_token_chain",
    "loop_event_family_chain", "loop_domain_chain", "affected_session_count",
    "affected_user_count", "eligible_session_count", "share", "baseline_support_session_count",
    "baseline_support_user_count", "baseline_share", "lift_vs_baseline", "z_score",
    "normalized_delta", "low_baseline_support", "average_repeat_count", "max_repeat_count",
    "median_duration_seconds", "p95_duration_seconds", "no_progress_rate",
    "terminal_after_loop_rate", "error_evidence_rate", "suspicion_signals", "severity_score",
    "example_session_hashes",
)

CANONICAL_KEYS = (
    "appSesssionid", "app_session_id", "appSessionId", "sessionID", "webSessionId", "flowSessionId",
)
SUCCESS_TOKENS = {"success", "successful", "succeed", "succeeded", "complete", "completed", "approve", "approved", "verified", "finished"}
FAILURE_TOKENS = {"error", "failed", "fail", "failure", "timeout", "timedout", "exception", "unavailable"}
SUCCESS_BLOCKERS = FAILURE_TOKENS | {"pending", "waiting", "cancel", "cancelled", "canceled", "unknown", "incomplete", "not", "no"}
BENIGN_ERROR_CODES = {"0", "none", "null", "false", "success", "successful", "ok"}
BENIGN_ERROR_VALUES = BENIGN_ERROR_CODES | {"", "no_error", "noerror"}
PERSONAL_NAME_KEYS = {"name", "fullname", "customername", "receivername", "sendername", "beneficiaryname"}
SAFE_LABEL_KEYS = {"screenname", "actionname", "apiname", "eventname", "componentname"}
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<![\w.-])\+?\d(?:[\s().-]*\d){8,14}(?![\w.-])")
LONG_NUMBER_RE = re.compile(r"(?<![\w.-])\d(?:[\s.-]*\d){6,18}(?![\w.-])")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.IGNORECASE)
LONG_ID_RE = re.compile(r"\b(?=[A-Za-z0-9_-]{24,}\b)(?=.*\d)[A-Za-z0-9_-]+\b")
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
COORDINATE_RE = re.compile(r"(?<!\d)-?\d{1,3}\.\d{3,}\s*[,;/]\s*-?\d{1,3}\.\d{3,}(?!\d)")
LABELED_PII_RE = re.compile(
    r"\b(address|dia\s*chi|phone|mobile|email|account(?:\s*id)?|user(?:\s*id)?|"
    r"zalo(?:pay)?(?:\s*id)?|card(?:\s*number)?|bank(?:\s*account)?|lat(?:itude)?|"
    r"lng|long(?:itude)?)\s*[:=]\s*[^,;|]+", re.IGNORECASE,
)
SECRET_LABEL_RE = re.compile(r"\b(token|authorization|auth|cookie|set-cookie)\s*[:=]\s*\S+", re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WORD_RE = re.compile(r"[a-z0-9]+")

MAX_EXAMPLES = 5
MAX_EVIDENCE_LENGTH = 256
INACTIVITY_GAP_SECONDS = 30 * 60
MIN_SUPPORT_SESSIONS = 3
MIN_SUPPORT_USERS = 3


@dataclass(frozen=True)
class MappingRecord:
    analysis_token: str
    final_event_name: str
    domain: str
    journey_stage: str


@dataclass
class NormalizedEvent:
    event_id: str
    event_family: str
    analysis_token: str
    final_event_name: str
    domain: str
    journey_stage: str
    event_time: datetime
    event_epoch: float
    time_window_3h: str
    file_index: int
    line_number: int
    session_base_hash: str
    user_hash: str | None
    has_error: bool


@dataclass
class CompressedRun:
    analysis_token: str
    event_family_token: str
    domain_token: str
    events: list[NormalizedEvent]


@dataclass
class AnalyticalSession:
    session_hash: str
    events: list[NormalizedEvent]
    user_hashes: set[str]
    start_window: str
    duration_seconds: float
    analysis_token_chain_raw: tuple[str, ...]
    event_family_chain_raw: tuple[str, ...]
    domain_chain_raw: tuple[str, ...]
    compressed_runs: list[CompressedRun]
    analysis_token_chain: tuple[str, ...]
    event_family_chain: tuple[str, ...]
    domain_chain: tuple[str, ...]
    has_error: bool


@dataclass
class SessionLoopStats:
    repeat_count: int
    duration_seconds: float
    no_progress: bool
    terminal_after_loop: bool
    error_evidence: bool


def _iter_events(parquet_path: str, date_filter: str | None = None) -> Iterable[dict[str, Any]]:
    table = pq.read_table(parquet_path)
    for batch in table.to_batches():
        for row in zip(*[batch.column(i).to_pylist() for i in range(batch.num_columns)]):
            row_dict = dict(zip(table.schema.names, row))
            if date_filter:
                ts = row_dict.get("client_timestamp") or row_dict.get("timestamp") or ""
                if str(ts)[:10] != date_filter:
                    continue
            yield row_dict


def normalize_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value).strip()
    return text or None


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        normalized = normalize_scalar(value)
        if normalized:
            return normalized
    return None


def normalize_key_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def token_set(value: str | None) -> set[str]:
    return set(WORD_RE.findall((value or "").lower()))


def parse_event_time(value: Any) -> datetime | None:
    text = normalize_scalar(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def event_family_from_event_id(event_id: str) -> str:
    parts = event_id.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else event_id


def window_from_event_time(event_time: datetime) -> str:
    return WINDOW_LABELS[event_time.hour // 3]


def hash_value(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}{value}".encode("utf-8")).hexdigest()


def key_indicates_sensitive_data(key: str | None) -> bool:
    if not key:
        return False
    normalized = normalize_key_name(key)
    if not normalized or normalized in SAFE_LABEL_KEYS:
        return False
    if normalized in PERSONAL_NAME_KEYS:
        return True
    if normalized in {
        "id", "zaloid", "zalopayid", "userid", "userkey", "accountid", "customerid",
        "receiverid", "senderid", "beneficiaryid", "phonenumber", "phone", "mobile",
        "msisdn", "email", "emailaddress", "address", "lat", "lng", "long", "latitude",
        "longitude", "sessionid", "deviceid", "userip", "clientip", "ipaddress", "userinfo", "user",
    }:
        return True
    if "zalopay" in normalized or "zalo" in normalized:
        return True
    if any(fragment in normalized for fragment in ("authorization", "cookie", "token")):
        return True
    if any(fragment in normalized for fragment in ("email", "phone", "mobile", "msisdn")):
        return True
    if normalized.endswith(("userid", "accountid")):
        return True
    if any(fragment in normalized for fragment in ("sessionid", "deviceid", "userinfo", "address")):
        return True
    if "bank" in normalized and "status" not in normalized:
        return True
    if "card" in normalized and "status" not in normalized:
        return True
    return normalized in {"auth", "authinfo", "authrequestinfo", "authinternalrequestinfo", "authchallengeresultinfo"}


def strip_url_query(match: re.Match) -> str:
    value = match.group(0)
    try:
        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return "<masked-url>"


def sanitize_text(value: Any, key: str | None = None) -> str | None:
    text = normalize_scalar(value)
    if text is None:
        return None
    if key_indicates_sensitive_data(key):
        return "<masked>"
    text = CONTROL_RE.sub(" ", text)
    text = URL_RE.sub(strip_url_query, text)
    text = EMAIL_RE.sub("<masked>", text)
    text = JWT_RE.sub("<masked>", text)
    text = BEARER_RE.sub("<masked>", text)
    text = SECRET_LABEL_RE.sub("<masked>", text)
    text = UUID_RE.sub("<masked>", text)
    text = COORDINATE_RE.sub("<masked>", text)
    text = LABELED_PII_RE.sub("<masked>", text)
    text = PHONE_RE.sub("<masked>", text)
    text = LONG_NUMBER_RE.sub("<masked>", text)
    text = LONG_ID_RE.sub("<masked>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_EVIDENCE_LENGTH] if text else None


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def examples_json(values: Iterable[str]) -> str:
    return safe_json(sorted(set(values))[:MAX_EXAMPLES])


def round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def parse_metadata(raw_metadata: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(raw_metadata, dict):
        return raw_metadata, False
    if isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            parsed = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {}, True
        return (parsed, False) if isinstance(parsed, dict) else ({}, False)
    return {}, False


def sanitize_values(values: Iterable[tuple[str, Any]]) -> tuple[str, ...]:
    cleaned = {
        sanitized
        for key, value in values
        if (sanitized := sanitize_text(value, key)) is not None
    }
    return tuple(sorted(cleaned))


def status_values(raw_event: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, ...]:
    pairs: list[tuple[str, Any]] = [("status", raw_event.get("status"))]
    for key, value in metadata.items():
        normalized = normalize_key_name(str(key))
        if "status" in normalized and "errorcode" not in normalized:
            pairs.append((str(key), value))
    return sanitize_values(pairs)


def error_code_values(raw_event: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, ...]:
    pairs: list[tuple[str, Any]] = [("error_code", raw_event.get("error_code"))]
    for key, value in metadata.items():
        normalized = normalize_key_name(str(key))
        if normalized == "errorcode" or ("error" in normalized and "code" in normalized):
            pairs.append((str(key), value))
    return sanitize_values(pairs)


def error_field_values(raw_event: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, ...]:
    pairs: list[tuple[str, Any]] = []
    for source in (raw_event, metadata):
        for key, value in source.items():
            normalized = normalize_key_name(str(key))
            if "error" in normalized and "code" not in normalized:
                pairs.append((str(key), value))
    return sanitize_values(pairs)


def detect_event_error(statuses, error_codes, error_fields, semantic_name, journey_stage) -> bool:
    if any(token_set(value) & FAILURE_TOKENS for value in statuses):
        return True
    if any(value.strip().lower() not in BENIGN_ERROR_CODES for value in error_codes):
        return True
    if any(value.strip().lower() not in BENIGN_ERROR_VALUES for value in error_fields):
        return True
    return any(token_set(value) & FAILURE_TOKENS for value in (semantic_name, journey_stage))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_mapping(path: Path) -> tuple[dict[str, MappingRecord], dict[str, Any]]:
    rows = read_csv_rows(path)
    required = {"event_family", "analysis_token_v4", "final_event_name_v4", "final_domain_v4", "final_journey_stage_v4"}
    if not rows or not required <= set(rows[0]):
        raise ValueError(f"{path} must contain columns {sorted(required)}")
    mapping: dict[str, MappingRecord] = {}
    stats = {"analysis_token_present": 0, "final_event_name_present": 0, "final_domain_present": 0, "final_stage_present": 0}
    for row in rows:
        event_family = first_non_empty(row.get("event_family"))
        if not event_family:
            continue
        analysis_token = first_non_empty(row.get("analysis_token_v4"))
        final_event_name = first_non_empty(row.get("final_event_name_v4"))
        domain = first_non_empty(row.get("final_domain_v4"))
        journey_stage = first_non_empty(row.get("final_journey_stage_v4"))
        if analysis_token:
            stats["analysis_token_present"] += 1
        if final_event_name:
            stats["final_event_name_present"] += 1
        if domain:
            stats["final_domain_present"] += 1
        if journey_stage:
            stats["final_stage_present"] += 1
        mapping[event_family] = MappingRecord(
            analysis_token=analysis_token or final_event_name or event_family,
            final_event_name=final_event_name or event_family,
            domain=domain or "unknown",
            journey_stage=journey_stage or event_family,
        )
    validation = {"family_mapping_row_count": len(mapping), **stats}
    return mapping, validation


def load_event_id_mapping(path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    rows = read_csv_rows(path)
    required = {"event_id", "event_family"}
    if not rows or not required <= set(rows[0]):
        raise ValueError(f"{path} must contain columns {sorted(required)}")
    mapping: dict[str, str] = {}
    for row in rows:
        event_id = first_non_empty(row.get("event_id"))
        event_family = first_non_empty(row.get("event_family"))
        if not event_id or not event_family:
            continue
        mapping[event_id] = event_family
    return mapping, {"event_id_mapping_row_count": len(mapping)}


def mapping_for_family(family_mapping: dict[str, MappingRecord], event_family: str) -> MappingRecord:
    return family_mapping.get(
        event_family,
        MappingRecord(analysis_token=event_family, final_event_name=event_family, domain="unknown", journey_stage=event_family),
    )


def normalize_event(raw_event, metadata, family_mapping, file_index, line_number):
    event_id = first_non_empty(raw_event.get("event_id"))
    if not event_id:
        return None, "missing_event_id"
    event_time = parse_event_time(
        first_non_empty(raw_event.get("client_timestamp"), raw_event.get("timestamp"))
    )
    if event_time is None:
        return None, "missing_event_time"
    canonical_id = first_non_empty(*(metadata.get(key) for key in CANONICAL_KEYS))
    tracking_id = first_non_empty(raw_event.get("tracking_session_id"))
    user_key = first_non_empty(
        raw_event.get("zalopay_id"), metadata.get("zalopay_id"), metadata.get("userID"),
        metadata.get("user_id"), raw_event.get("zalo_id"), metadata.get("zalo_id"), metadata.get("zaloid"),
    )
    canonical_hash = hash_value("step2b::canonical_session::", canonical_id) if canonical_id else None
    tracking_hash = hash_value("step2b::tracking_session::", tracking_id) if tracking_id else None
    user_hash = hash_value("step2b::user::", user_key) if user_key else None

    if canonical_hash:
        session_base_hash = hash_value("step2b::session_base::", f"c:{canonical_hash}")
    elif tracking_hash and user_hash:
        session_base_hash = hash_value("step2b::session_base::", f"tu:{tracking_hash}:{user_hash}")
    elif user_hash:
        session_base_hash = hash_value("step2b::session_base::", f"u:{user_hash}")
    else:
        return None, "missing_session_identity"

    event_family = event_family_from_event_id(event_id)
    mapped = mapping_for_family(family_mapping, event_family)
    statuses = status_values(raw_event, metadata)
    error_codes = error_code_values(raw_event, metadata)
    error_fields = error_field_values(raw_event, metadata)
    has_error = detect_event_error(statuses, error_codes, error_fields, mapped.final_event_name, mapped.journey_stage)
    try:
        event_epoch = event_time.timestamp()
    except (OSError, OverflowError, ValueError):
        return None, "invalid_event_time"

    return NormalizedEvent(
        event_id=event_id, event_family=event_family, analysis_token=mapped.analysis_token,
        final_event_name=mapped.final_event_name, domain=mapped.domain,
        journey_stage=mapped.journey_stage, event_time=event_time, event_epoch=event_epoch,
        time_window_3h=window_from_event_time(event_time), file_index=file_index,
        line_number=line_number, session_base_hash=session_base_hash, user_hash=user_hash,
        has_error=has_error,
    ), None


def ordered_distinct(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def summarize_run_values(values: Iterable[str]) -> str:
    ordered = ordered_distinct(values)
    if not ordered:
        return "unknown"
    if len(ordered) == 1:
        return ordered[0]
    return "multi:" + "|".join(ordered)


def compress_session_runs(events: Sequence[NormalizedEvent]) -> list[CompressedRun]:
    runs: list[CompressedRun] = []
    for event in events:
        if runs and runs[-1].analysis_token == event.analysis_token:
            runs[-1].events.append(event)
        else:
            runs.append(CompressedRun(analysis_token=event.analysis_token, event_family_token="", domain_token="", events=[event]))
    for run in runs:
        run.event_family_token = summarize_run_values(e.event_family for e in run.events)
        run.domain_token = summarize_run_values(e.domain for e in run.events)
    return runs


def make_session(session_base_hash: str, split_ordinal: int, events: list[NormalizedEvent]) -> AnalyticalSession:
    session_hash = hash_value("step2b::analytical_session::", f"{session_base_hash}:{split_ordinal}")
    compressed_runs = compress_session_runs(events)
    return AnalyticalSession(
        session_hash=session_hash, events=events,
        user_hashes={e.user_hash for e in events if e.user_hash},
        start_window=events[0].time_window_3h,
        duration_seconds=max(0.0, events[-1].event_epoch - events[0].event_epoch),
        analysis_token_chain_raw=tuple(e.analysis_token for e in events),
        event_family_chain_raw=tuple(e.event_family for e in events),
        domain_chain_raw=tuple(e.domain for e in events),
        compressed_runs=compressed_runs,
        analysis_token_chain=tuple(r.analysis_token for r in compressed_runs),
        event_family_chain=tuple(r.event_family_token for r in compressed_runs),
        domain_chain=tuple(r.domain_token for r in compressed_runs),
        has_error=any(e.has_error for e in events),
    )


def build_sessions(events: Sequence[NormalizedEvent]) -> list[AnalyticalSession]:
    grouped: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        grouped[event.session_base_hash].append(event)
    sessions: list[AnalyticalSession] = []
    for session_base_hash in sorted(grouped):
        ordered = sorted(grouped[session_base_hash], key=lambda e: (e.event_epoch, e.file_index, e.line_number))
        split_ordinal = 0
        current: list[NormalizedEvent] = []
        previous_epoch: float | None = None
        for event in ordered:
            if previous_epoch is not None and event.event_epoch - previous_epoch > INACTIVITY_GAP_SECONDS:
                sessions.append(make_session(session_base_hash, split_ordinal, current))
                split_ordinal += 1
                current = []
            current.append(event)
            previous_epoch = event.event_epoch
        if current:
            sessions.append(make_session(session_base_hash, split_ordinal, current))
    return sorted(sessions, key=lambda s: (s.events[0].event_epoch, s.events[0].file_index, s.events[0].line_number, s.session_hash))


def support_threshold(session_count: int, user_count: int) -> bool:
    return session_count >= MIN_SUPPORT_SESSIONS or user_count >= MIN_SUPPORT_USERS


def example_session_hashes(session_hashes: Iterable[str]) -> str:
    return examples_json(session_hashes)


def json_chain(values: Sequence[str]) -> str:
    return safe_json(list(values))


def combine_windows(values_by_window: dict[str, set[str]], current_window: str) -> set[str]:
    combined: set[str] = set()
    for window, values in values_by_window.items():
        if window != current_window:
            combined.update(values)
    return combined


def z_score_two_proportion(success_a, total_a, success_b, total_b) -> float | None:
    if total_a <= 0 or total_b <= 0:
        return None
    pooled = (success_a + success_b) / (total_a + total_b)
    variance = pooled * (1.0 - pooled) * ((1.0 / total_a) + (1.0 / total_b))
    if variance <= 0.0:
        return None
    return (success_a / total_a - success_b / total_b) / math.sqrt(variance)


def compute_lift(share: float, baseline_share: float) -> float | None:
    if baseline_share <= 0.0:
        return None
    return share / baseline_share


def normalized_delta(share: float, baseline_share: float) -> float:
    return (share - baseline_share) / max(math.sqrt(baseline_share * (1.0 - baseline_share)), 1e-6)


def numeric_for_sort(value: float | None) -> float:
    return value if value is not None else -1e30


def mine_motif_rows(sessions: Sequence[AnalyticalSession]) -> list[dict[str, Any]]:
    occurrence_counts: dict[tuple, Counter[str]] = defaultdict(Counter)
    session_sets: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    user_sets: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    eligible_by_window: dict[int, Counter[str]] = defaultdict(Counter)
    total_eligible_by_length: Counter[int] = Counter()

    for session in sessions:
        chain = session.analysis_token_chain
        family_chain = session.event_family_chain
        domain_chain = session.domain_chain
        for length in range(2, 6):
            if len(chain) < length:
                continue
            eligible_by_window[length][session.start_window] += 1
            total_eligible_by_length[length] += 1
            seen_in_session: set[tuple] = set()
            for index in range(0, len(chain) - length + 1):
                key = (length, tuple(chain[index:index+length]), tuple(family_chain[index:index+length]), tuple(domain_chain[index:index+length]))
                occurrence_counts[key][session.start_window] += 1
                if key not in seen_in_session:
                    session_sets[key][session.start_window].add(session.session_hash)
                    user_sets[key][session.start_window].update(session.user_hashes)
                    seen_in_session.add(key)

    rows: list[dict[str, Any]] = []
    for key, counts_by_window in occurrence_counts.items():
        length = int(key[0])
        analysis_token_ngram = tuple(key[1])
        event_family_ngram = tuple(key[2])
        domain_ngram = tuple(key[3])
        total_eligible = total_eligible_by_length[length]
        for window in counts_by_window:
            affected_sessions = session_sets[key][window]
            affected_users = user_sets[key][window]
            if not support_threshold(len(affected_sessions), len(affected_users)):
                continue
            eligible_sessions = eligible_by_window[length][window]
            baseline_sessions = len(combine_windows(session_sets[key], window))
            baseline_users = len(combine_windows(user_sets[key], window))
            baseline_eligible = total_eligible - eligible_sessions
            share = len(affected_sessions) / eligible_sessions
            baseline_share = baseline_sessions / baseline_eligible if baseline_eligible > 0 else 0.0
            lift = compute_lift(share, baseline_share)
            z_sc = z_score_two_proportion(len(affected_sessions), eligible_sessions, baseline_sessions, baseline_eligible)
            delta = normalized_delta(share, baseline_share)
            if not (share >= 0.01 or (lift is not None and lift >= 1.25) or (z_sc is not None and z_sc >= 2.0)):
                continue
            rows.append({
                "time_window_3h": window, "rank": 0, "ngram_length": length,
                "analysis_token_ngram": json_chain(analysis_token_ngram),
                "event_family_ngram": json_chain(event_family_ngram),
                "domain_ngram": json_chain(domain_ngram),
                "occurrence_count": counts_by_window[window],
                "affected_session_count": len(affected_sessions),
                "affected_user_count": len(affected_users),
                "eligible_session_count": eligible_sessions,
                "share": round_metric(share),
                "baseline_support_session_count": baseline_sessions,
                "baseline_support_user_count": baseline_users,
                "baseline_share": round_metric(baseline_share),
                "lift_vs_baseline": round_metric(lift),
                "z_score": round_metric(z_sc),
                "normalized_delta": round_metric(delta),
                "low_baseline_support": baseline_sessions < 3 and baseline_users < 3,
                "example_session_hashes": example_session_hashes(affected_sessions),
            })
    rows.sort(key=lambda r: (WINDOW_LABELS.index(r["time_window_3h"]), -numeric_for_sort(r["z_score"]), -numeric_for_sort(r["lift_vs_baseline"]), -numeric_for_sort(r["share"]), -r["affected_session_count"], r["analysis_token_ngram"]))
    rank_by_window: Counter[str] = Counter()
    for row in rows:
        rank_by_window[row["time_window_3h"]] += 1
        row["rank"] = rank_by_window[row["time_window_3h"]]
    return rows


def mine_terminal_rows(sessions: Sequence[AnalyticalSession]) -> list[dict[str, Any]]:
    terminal_sessions: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    terminal_users: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    exposure_sessions: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for session in sessions:
        chain = session.analysis_token_chain
        family_chain = session.event_family_chain
        domain_chain = session.domain_chain
        if len(chain) < 2:
            continue
        for prefix_length in range(1, min(4, len(chain) - 1) + 1):
            key = (len(chain[-(prefix_length+1):-1]), tuple(chain[-(prefix_length+1):-1]), tuple(family_chain[-(prefix_length+1):-1]), tuple(domain_chain[-(prefix_length+1):-1]), chain[-1], family_chain[-1], domain_chain[-1])
            terminal_sessions[key][session.start_window].add(session.session_hash)
            terminal_users[key][session.start_window].update(session.user_hashes)
        for prefix_length in range(1, 5):
            fragment_length = prefix_length + 1
            if len(chain) < fragment_length:
                continue
            seen_fragments: set[tuple] = set()
            for index in range(0, len(chain) - fragment_length + 1):
                key = (prefix_length, tuple(chain[index:index+prefix_length]), tuple(family_chain[index:index+prefix_length]), tuple(domain_chain[index:index+prefix_length]), chain[index+prefix_length], family_chain[index+prefix_length], domain_chain[index+prefix_length])
                if key in seen_fragments:
                    continue
                exposure_sessions[key][session.start_window].add(session.session_hash)
                seen_fragments.add(key)

    rows: list[dict[str, Any]] = []
    for key, terminal_by_window in terminal_sessions.items():
        prefix_length = int(key[0])
        prefix_tokens = tuple(key[1])
        prefix_families = tuple(key[2])
        prefix_domains = tuple(key[3])
        terminal_token = str(key[4])
        terminal_family = str(key[5])
        terminal_domain = str(key[6])
        for window in terminal_by_window:
            terminal_count = len(terminal_by_window[window])
            affected_users = len(terminal_users[key][window])
            if not support_threshold(terminal_count, affected_users):
                continue
            exposure_count = len(exposure_sessions[key][window])
            if exposure_count == 0:
                continue
            baseline_terminal_sessions = len(combine_windows(terminal_by_window, window))
            baseline_terminal_users = len(combine_windows(terminal_users[key], window))
            baseline_exposure_sessions = len(combine_windows(exposure_sessions[key], window))
            stop_rate = terminal_count / exposure_count
            baseline_stop_rate = baseline_terminal_sessions / baseline_exposure_sessions if baseline_exposure_sessions > 0 else 0.0
            lift = compute_lift(stop_rate, baseline_stop_rate)
            z_sc = z_score_two_proportion(terminal_count, exposure_count, baseline_terminal_sessions, baseline_exposure_sessions)
            delta = normalized_delta(stop_rate, baseline_stop_rate)
            if not (stop_rate >= 0.40 or (lift is not None and lift >= 1.50) or (z_sc is not None and z_sc >= 2.0)):
                continue
            rows.append({
                "time_window_3h": window, "rank": 0, "prefix_length": prefix_length,
                "analysis_token_prefix": json_chain(prefix_tokens),
                "event_family_prefix": json_chain(prefix_families),
                "domain_prefix": json_chain(prefix_domains),
                "terminal_token": terminal_token, "terminal_event_family": terminal_family,
                "terminal_domain": terminal_domain, "terminal_session_count": terminal_count,
                "affected_user_count": affected_users, "prefix_exposure_session_count": exposure_count,
                "stop_rate": round_metric(stop_rate),
                "baseline_support_session_count": baseline_terminal_sessions,
                "baseline_support_user_count": baseline_terminal_users,
                "baseline_stop_rate": round_metric(baseline_stop_rate),
                "lift_vs_baseline": round_metric(lift), "z_score": round_metric(z_sc),
                "normalized_delta": round_metric(delta),
                "low_baseline_support": baseline_terminal_sessions < 3 and baseline_terminal_users < 3,
                "example_session_hashes": example_session_hashes(terminal_by_window[window]),
            })
    rows.sort(key=lambda r: (WINDOW_LABELS.index(r["time_window_3h"]), -numeric_for_sort(r["z_score"]), -numeric_for_sort(r["lift_vs_baseline"]), -numeric_for_sort(r["stop_rate"]), -r["terminal_session_count"], r["analysis_token_prefix"], r["terminal_token"]))
    rank_by_window: Counter[str] = Counter()
    for row in rows:
        rank_by_window[row["time_window_3h"]] += 1
        row["rank"] = rank_by_window[row["time_window_3h"]]
    return rows


def mine_routing_rows(sessions: Sequence[AnalyticalSession]) -> list[dict[str, Any]]:
    transition_counts: dict[tuple, Counter[str]] = defaultdict(Counter)
    session_sets: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    user_sets: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    source_totals: dict[tuple, Counter[str]] = defaultdict(Counter)

    for session in sessions:
        for source_run, target_run in zip(session.compressed_runs, session.compressed_runs[1:]):
            window = source_run.events[-1].time_window_3h
            key = (source_run.analysis_token, source_run.event_family_token, source_run.domain_token, target_run.analysis_token, target_run.event_family_token, target_run.domain_token)
            src_key = key[:3]
            source_totals[src_key][window] += 1
            transition_counts[key][window] += 1
            session_sets[key][window].add(session.session_hash)
            user_sets[key][window].update(session.user_hashes)

    rows: list[dict[str, Any]] = []
    for key, counts_by_window in transition_counts.items():
        src_key = key[:3]
        total_source = sum(source_totals[src_key].values())
        for window in counts_by_window:
            unique_sessions = len(session_sets[key][window])
            unique_users_count = len(user_sets[key][window])
            if not support_threshold(unique_sessions, unique_users_count):
                continue
            transition_count = counts_by_window[window]
            source_total_count = source_totals[src_key][window]
            baseline_transition_count = sum(c for w, c in counts_by_window.items() if w != window)
            baseline_source_total_count = total_source - source_total_count
            baseline_support_sessions = len(combine_windows(session_sets[key], window))
            baseline_support_users = len(combine_windows(user_sets[key], window))
            share = transition_count / source_total_count if source_total_count > 0 else 0.0
            baseline_share = baseline_transition_count / baseline_source_total_count if baseline_source_total_count > 0 else 0.0
            lift = compute_lift(share, baseline_share)
            z_sc = z_score_two_proportion(transition_count, source_total_count, baseline_transition_count, baseline_source_total_count)
            delta = normalized_delta(share, baseline_share)
            if not ((lift is not None and lift >= 1.50) or (z_sc is not None and z_sc >= 2.0)):
                continue
            rows.append({
                "time_window_3h": window, "rank": 0,
                "source_token": key[0], "source_event_family": key[1], "source_domain": key[2],
                "target_token": key[3], "target_event_family": key[4], "target_domain": key[5],
                "transition_count": transition_count, "unique_session_count": unique_sessions,
                "unique_user_count": unique_users_count, "source_total_count": source_total_count,
                "share": round_metric(share), "baseline_support_session_count": baseline_support_sessions,
                "baseline_support_user_count": baseline_support_users,
                "baseline_share": round_metric(baseline_share), "lift_vs_baseline": round_metric(lift),
                "z_score": round_metric(z_sc), "normalized_delta": round_metric(delta),
                "low_baseline_support": baseline_support_sessions < 3 and baseline_support_users < 3,
                "example_session_hashes": example_session_hashes(session_sets[key][window]),
            })
    rows.sort(key=lambda r: (WINDOW_LABELS.index(r["time_window_3h"]), -numeric_for_sort(r["z_score"]), -numeric_for_sort(r["lift_vs_baseline"]), -numeric_for_sort(r["share"]), -r["transition_count"], r["source_token"], r["target_token"]))
    rank_by_window: Counter[str] = Counter()
    for row in rows:
        rank_by_window[row["time_window_3h"]] += 1
        row["rank"] = rank_by_window[row["time_window_3h"]]
    return rows


def loop_signature(loop_length, detection_mode, token_chain, family_chain, domain_chain):
    return (loop_length, detection_mode, tuple(token_chain), tuple(family_chain), tuple(domain_chain))


def merge_session_loop_stats(existing: SessionLoopStats, candidate: SessionLoopStats) -> SessionLoopStats:
    return SessionLoopStats(
        repeat_count=max(existing.repeat_count, candidate.repeat_count),
        duration_seconds=max(existing.duration_seconds, candidate.duration_seconds),
        no_progress=existing.no_progress or candidate.no_progress,
        terminal_after_loop=existing.terminal_after_loop or candidate.terminal_after_loop,
        error_evidence=existing.error_evidence or candidate.error_evidence,
    )


def detect_session_loops(session: AnalyticalSession) -> dict[tuple, SessionLoopStats]:
    results: dict[tuple, SessionLoopStats] = {}
    raw_tokens = session.analysis_token_chain_raw
    raw_families = session.event_family_chain_raw
    raw_domains = session.domain_chain_raw
    raw_events = session.events
    raw_index = 0
    while raw_index < len(raw_tokens):
        run_end = raw_index + 1
        while run_end < len(raw_tokens) and raw_tokens[run_end] == raw_tokens[raw_index]:
            run_end += 1
        repeat_count = run_end - raw_index
        if repeat_count >= 2:
            sig = loop_signature(1, "run_length_uncompressed", (raw_tokens[raw_index],), (summarize_run_values(raw_families[raw_index:run_end]),), (summarize_run_values(raw_domains[raw_index:run_end]),))
            candidate = SessionLoopStats(repeat_count=repeat_count, duration_seconds=max(0.0, raw_events[run_end-1].event_epoch - raw_events[raw_index].event_epoch), no_progress=run_end == len(raw_tokens), terminal_after_loop=run_end == len(raw_tokens), error_evidence=any(e.has_error for e in raw_events[raw_index:run_end]))
            existing = results.get(sig)
            results[sig] = candidate if existing is None else merge_session_loop_stats(existing, candidate)
        raw_index = run_end

    runs = session.compressed_runs
    chain = session.analysis_token_chain
    family_chain = session.event_family_chain
    domain_chain = session.domain_chain
    for loop_length in range(2, 5):
        if len(chain) < loop_length * 2:
            continue
        index = 0
        while index <= len(chain) - (loop_length * 2):
            body = chain[index:index+loop_length]
            repeat_count = 1
            while index + (repeat_count+1)*loop_length <= len(chain) and chain[index+repeat_count*loop_length:index+(repeat_count+1)*loop_length] == body:
                repeat_count += 1
            if repeat_count >= 2:
                end_index = index + repeat_count * loop_length
                sig = loop_signature(loop_length, "repeated_subchain_compressed", body, family_chain[index:index+loop_length], domain_chain[index:index+loop_length])
                repeated_runs = runs[index:end_index]
                candidate = SessionLoopStats(repeat_count=repeat_count, duration_seconds=max(0.0, repeated_runs[-1].events[-1].event_epoch - repeated_runs[0].events[0].event_epoch), no_progress=not (end_index < len(chain) and chain[end_index] not in set(body)), terminal_after_loop=end_index == len(chain), error_evidence=any(e.has_error for r in repeated_runs for e in r.events))
                existing = results.get(sig)
                results[sig] = candidate if existing is None else merge_session_loop_stats(existing, candidate)
                index = end_index
                continue
            index += 1
    return results


def loop_eligible_sessions(sessions: Sequence[AnalyticalSession], loop_length: int, detection_mode: str, window: str) -> int:
    if detection_mode == "run_length_uncompressed":
        return sum(len(s.analysis_token_chain_raw) >= 2 and s.start_window == window for s in sessions)
    return sum(len(s.analysis_token_chain) >= loop_length * 2 and s.start_window == window for s in sessions)


def loop_total_eligible(sessions: Sequence[AnalyticalSession], loop_length: int, detection_mode: str) -> int:
    if detection_mode == "run_length_uncompressed":
        return sum(len(s.analysis_token_chain_raw) >= 2 for s in sessions)
    return sum(len(s.analysis_token_chain) >= loop_length * 2 for s in sessions)


def loop_severity_score(share, no_progress_rate, terminal_after_loop_rate, error_evidence_rate, average_repeat_count, median_duration_seconds, lift_vs_baseline) -> float:
    score = (
        min(20.0, share * 120.0) + no_progress_rate * 25.0 + terminal_after_loop_rate * 15.0
        + error_evidence_rate * 15.0 + min(15.0, average_repeat_count * 3.0)
        + min(10.0, median_duration_seconds / 1800.0 * 4.0) + min(15.0, (lift_vs_baseline or 0.0) * 4.0)
    )
    return round_metric(min(100.0, score)) or 0.0


def mine_loop_rows(sessions: Sequence[AnalyticalSession]) -> list[dict[str, Any]]:
    by_key_window: dict[tuple, dict[str, dict[str, SessionLoopStats]]] = defaultdict(lambda: defaultdict(dict))
    user_sets: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for session in sessions:
        for sig, stats in detect_session_loops(session).items():
            by_key_window[sig][session.start_window][session.session_hash] = stats
            user_sets[sig][session.start_window].update(session.user_hashes)

    rows: list[dict[str, Any]] = []
    for sig, session_stats_by_window in by_key_window.items():
        loop_length = int(sig[0])
        detection_mode = str(sig[1])
        loop_tokens = tuple(sig[2])
        loop_families = tuple(sig[3])
        loop_domains = tuple(sig[4])
        total_eligible = loop_total_eligible(sessions, loop_length, detection_mode)
        for window, session_stats in session_stats_by_window.items():
            affected_session_count = len(session_stats)
            affected_user_count = len(user_sets[sig][window])
            if not support_threshold(affected_session_count, affected_user_count):
                continue
            eligible_session_count = loop_eligible_sessions(sessions, loop_length, detection_mode, window)
            baseline_session_stats = {sh: st for ow, pw in session_stats_by_window.items() if ow != window for sh, st in pw.items()}
            baseline_support_session_count = len(baseline_session_stats)
            baseline_support_user_count = len(combine_windows(user_sets[sig], window))
            baseline_eligible_session_count = total_eligible - eligible_session_count
            share = affected_session_count / eligible_session_count if eligible_session_count > 0 else 0.0
            baseline_share = baseline_support_session_count / baseline_eligible_session_count if baseline_eligible_session_count > 0 else 0.0
            lift = compute_lift(share, baseline_share)
            z_sc = z_score_two_proportion(affected_session_count, eligible_session_count, baseline_support_session_count, baseline_eligible_session_count)
            delta = normalized_delta(share, baseline_share)
            repeat_counts = [st.repeat_count for st in session_stats.values()]
            durations = [st.duration_seconds for st in session_stats.values()]
            no_progress_rate = mean(st.no_progress for st in session_stats.values())
            terminal_after_loop_rate = mean(st.terminal_after_loop for st in session_stats.values())
            error_evidence_rate = mean(st.error_evidence for st in session_stats.values())
            average_repeat_count = mean(repeat_counts)
            median_duration_seconds = percentile(durations, 0.50)
            p95_duration_seconds = percentile(durations, 0.95)
            suspicion_signals: list[str] = []
            if no_progress_rate >= 0.60:
                suspicion_signals.append("no_downstream_progress")
            if terminal_after_loop_rate >= 0.40:
                suspicion_signals.append("terminal_concentration")
            if error_evidence_rate >= 0.15:
                suspicion_signals.append("error_evidence")
            if average_repeat_count >= 3.0:
                suspicion_signals.append("high_repeat")
            if median_duration_seconds >= 1800.0:
                suspicion_signals.append("high_duration")
            if lift is not None and lift >= 1.50:
                suspicion_signals.append("abnormal_lift")
            if not suspicion_signals:
                continue
            rows.append({
                "time_window_3h": window, "rank": 0, "loop_length": loop_length,
                "loop_detection_mode": detection_mode, "loop_token_chain": json_chain(loop_tokens),
                "loop_event_family_chain": json_chain(loop_families), "loop_domain_chain": json_chain(loop_domains),
                "affected_session_count": affected_session_count, "affected_user_count": affected_user_count,
                "eligible_session_count": eligible_session_count, "share": round_metric(share),
                "baseline_support_session_count": baseline_support_session_count,
                "baseline_support_user_count": baseline_support_user_count,
                "baseline_share": round_metric(baseline_share), "lift_vs_baseline": round_metric(lift),
                "z_score": round_metric(z_sc), "normalized_delta": round_metric(delta),
                "low_baseline_support": baseline_support_session_count < 3 and baseline_support_user_count < 3,
                "average_repeat_count": round_metric(average_repeat_count), "max_repeat_count": max(repeat_counts),
                "median_duration_seconds": round_metric(median_duration_seconds),
                "p95_duration_seconds": round_metric(p95_duration_seconds),
                "no_progress_rate": round_metric(no_progress_rate),
                "terminal_after_loop_rate": round_metric(terminal_after_loop_rate),
                "error_evidence_rate": round_metric(error_evidence_rate),
                "suspicion_signals": json_chain(suspicion_signals),
                "severity_score": loop_severity_score(share, no_progress_rate, terminal_after_loop_rate, error_evidence_rate, average_repeat_count, median_duration_seconds, lift),
                "example_session_hashes": example_session_hashes(session_stats.keys()),
            })
    rows.sort(key=lambda r: (WINDOW_LABELS.index(r["time_window_3h"]), -numeric_for_sort(r["severity_score"]), -numeric_for_sort(r["share"]), -r["affected_session_count"], r["loop_token_chain"]))
    rank_by_window: Counter[str] = Counter()
    for row in rows:
        rank_by_window[row["time_window_3h"]] += 1
        row["rank"] = rank_by_window[row["time_window_3h"]]
    return rows


def write_parquet(rows: list[dict[str, Any]], columns: Sequence[str], path: Path) -> None:
    dataframe = pd.DataFrame(rows, columns=list(columns))
    dataframe.to_parquet(path, index=False, engine="pyarrow")


def run(input_dir: str, output_dir: str, mapping_dir: str | None = None, **kwargs) -> None:
    """
    Step 2b: Natural chain mining.
    Reads parquet files from input_dir, uses mapping CSVs from mapping_dir,
    writes 5 output files to output_dir.
    """
    import os as _os

    date_filter: str | None = kwargs.get("date_filter")

    output_path = Path(output_dir)

    parquet_files = sorted(glob_module.glob(str(Path(input_dir) / "*.parquet")))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in {input_dir!r}")

    # Resolve mapping files
    if mapping_dir is None:
        _base = Path(__file__).parent.parent.parent
        mapping_dir = str(_base / "data" / "mapping")
    family_mapping_path = Path(mapping_dir) / "event_family_mapping_final_v5.csv"
    event_id_mapping_path = Path(mapping_dir) / "event_id_mapping_final_v5.csv"

    if not family_mapping_path.is_file():
        raise FileNotFoundError(f"Missing mapping file: {family_mapping_path}")
    if not event_id_mapping_path.is_file():
        raise FileNotFoundError(f"Missing mapping file: {event_id_mapping_path}")

    family_mapping, mapping_validation = load_mapping(family_mapping_path)
    event_id_mapping, event_id_mapping_validation = load_event_id_mapping(event_id_mapping_path)

    quality: Counter[str] = Counter()
    observed_families: Counter[str] = Counter()
    unique_user_hashes: set[str] = set()
    normalized_events: list[NormalizedEvent] = []
    first_event_time: datetime | None = None
    last_event_time: datetime | None = None

    for file_index, parquet_path in enumerate(parquet_files):
        for line_number, raw_event in enumerate(_iter_events(parquet_path, date_filter=date_filter), 1):
            if not isinstance(raw_event, dict):
                quality["non_object_event_count"] += 1
                continue
            quality["parsed_event_count"] += 1
            metadata, metadata_failed = parse_metadata(raw_event.get("metadata"))
            if metadata_failed:
                quality["metadata_parse_error_count"] += 1
            event_id = first_non_empty(raw_event.get("event_id"))
            if event_id:
                derived_event_family = event_family_from_event_id(event_id)
                observed_families[derived_event_family] += 1
            event_time = parse_event_time(first_non_empty(raw_event.get("client_timestamp"), raw_event.get("timestamp")))
            if event_time is not None:
                if first_event_time is None or event_time.timestamp() < first_event_time.timestamp():
                    first_event_time = event_time
                if last_event_time is None or event_time.timestamp() > last_event_time.timestamp():
                    last_event_time = event_time
            event, skip_reason = normalize_event(raw_event, metadata, family_mapping, file_index, line_number)
            if event is None:
                quality[f"skipped_{skip_reason}_count"] += 1
                continue
            normalized_events.append(event)
            if event.user_hash:
                unique_user_hashes.add(event.user_hash)

    sessions = build_sessions(normalized_events)
    motif_rows = mine_motif_rows(sessions)
    terminal_rows = mine_terminal_rows(sessions)
    routing_rows = mine_routing_rows(sessions)
    loop_rows = mine_loop_rows(sessions)

    mapped_families = {f for f in observed_families if f in family_mapping}
    unmapped_families = set(observed_families) - mapped_families
    run_summary = {
        "input_file_count": len(parquet_files),
        "parsed_event_count": quality["parsed_event_count"],
        "normalized_event_count": len(normalized_events),
        "analytical_session_count": len(sessions),
        "unique_user_count": len(unique_user_hashes),
        "mapped_event_family_count": len(mapped_families),
        "unmapped_event_family_count": len(unmapped_families),
        "first_event_time": first_event_time.isoformat() if first_event_time else None,
        "last_event_time": last_event_time.isoformat() if last_event_time else None,
        "quality_counts": dict(sorted(quality.items())),
        "mapping": {
            "event_family_mapping_path": str(family_mapping_path),
            "event_id_mapping_path": str(event_id_mapping_path),
            **mapping_validation,
            **event_id_mapping_validation,
        },
        "output_row_counts": {
            "chain_motif_profile_by_3h": len(motif_rows),
            "chain_terminal_profile_by_3h": len(terminal_rows),
            "routing_shift_profile_by_3h": len(routing_rows),
            "suspicious_loop_profile_by_3h": len(loop_rows),
        },
        "policies": {
            "time_window_policy": "fixed_3_hour_boundary",
            "timezone_policy": "no_china_time_conversion",
            "session_split_policy": "inactivity_gap_30_minutes",
            "primary_token": "analysis_token_v4",
            "motif_window_policy": "session_start_window",
            "terminal_window_policy": "session_start_window",
            "loop_window_policy": "session_start_window",
            "routing_window_policy": "source_event_window",
            "chain_compression_policy": "remove_consecutive_duplicate_primary_tokens",
            "baseline_policy": "all_other_3_hour_windows_combined",
            "minimum_support_policy": {"sessions": MIN_SUPPORT_SESSIONS, "users": MIN_SUPPORT_USERS},
            "privacy": "hashed_user_and_session_ids;semantic_tokens_only;no_raw_ids_no_raw_pii_no_raw_session_ids",
            "llm_usage": "none",
        },
    }

    output_path.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent))
    try:
        write_parquet(motif_rows, MOTIF_COLUMNS, staging_dir / TARGET_OUTPUT_FILES[0])
        write_parquet(terminal_rows, TERMINAL_COLUMNS, staging_dir / TARGET_OUTPUT_FILES[1])
        write_parquet(routing_rows, ROUTING_COLUMNS, staging_dir / TARGET_OUTPUT_FILES[2])
        write_parquet(loop_rows, LOOP_COLUMNS, staging_dir / TARGET_OUTPUT_FILES[3])
        with (staging_dir / TARGET_OUTPUT_FILES[4]).open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(run_summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
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
