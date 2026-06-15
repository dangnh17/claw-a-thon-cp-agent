from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pyarrow.parquet as pq

TRACKING_SESSION_NAMESPACE = "step1_event_meaning::tracking_session_id::"
CANONICAL_SESSION_NAMESPACE = "step1_event_meaning::canonical_session_id::"
USER_KEY_NAMESPACE = "step1_event_meaning::user_key::"

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

MESSAGE_KEYS = (
    "message", "error_message", "errorMessage", "message_title", "message_CTA",
    "displayMessage", "toast_message", "user_msg", "user_msg0", "user_msg1",
    "sofMessage", "promotion_error_msg",
)
SCREEN_KEYS = (
    "screen", "from_screen", "screen_name", "screen_type", "screen_id",
    "screenID", "esofTypeScreen",
)
ACTION_KEYS = (
    "action", "action_name", "action_type", "action_value", "previous_action",
    "next_action", "user_action", "notification_action", "sof_action", "sof_actions",
    "NavigationAction", "nextAction", "authenAction", "cta_action_link",
)
API_KEYS = ("api", "apiName")
ERROR_CODE_KEYS = {
    "error_code", "errorcode", "moyaerrorcode", "zpwerrorcode",
    "bcerrorcode", "promotion_error_code",
}
PERSONAL_NAME_KEYS = {
    "name", "fullname", "customername", "receivername", "sendername", "beneficiaryname",
}
SAFE_LABEL_KEYS = {
    "screenname", "actionname", "apiname", "eventname", "componentname",
}
TOP_N_DEFAULT = 10
TOP_N_METADATA_KEYS = 20
MAX_EXAMPLES_PER_EVENT = 20

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
JWT_RE = re.compile(r"\b(?=[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)(?=.*[A-Za-z])[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE)
COOKIE_RE = re.compile(r"\b(cookie|set-cookie)\b", re.IGNORECASE)


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


def normalize_key_name(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.strip().lower())


def normalize_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, str)):
        text = str(value).strip()
        return text or None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        text = format(value, "g").strip()
        return text or None
    text = str(value).strip()
    return text or None


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        normalized = normalize_scalar(value)
        if normalized:
            return normalized
    return None


def parse_event_time(value: Any) -> datetime | None:
    text = normalize_scalar(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def event_family_from_event_id(event_id: str | None) -> str | None:
    if not event_id:
        return None
    parts = event_id.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return event_id


def window_from_event_time(event_time: datetime | None) -> str | None:
    if event_time is None:
        return None
    return WINDOW_LABELS[event_time.hour // 3]


import hashlib

def hash_value(namespace: str, value: str | None) -> str | None:
    if not value:
        return None
    digest = hashlib.sha256(f"{namespace}{value}".encode("utf-8")).hexdigest()
    return digest


def key_indicates_sensitive_data(key: str | None) -> bool:
    if not key:
        return False
    normalized = normalize_key_name(key)
    if not normalized:
        return False
    if normalized in SAFE_LABEL_KEYS:
        return False
    if normalized in PERSONAL_NAME_KEYS:
        return True
    if normalized in {
        "id", "zaloid", "zalopayid", "userid", "userkey", "accountid",
        "customerid", "receiverid", "senderid", "beneficiaryid", "phonenumber",
        "phone", "mobile", "msisdn", "email", "emailaddress", "address",
        "lat", "lng", "long", "latitude", "longitude", "sessionid", "deviceid",
        "userip", "clientip", "ipaddress", "userinfo", "user",
    }:
        return True
    if "zalopay" in normalized or "zalo" in normalized:
        return True
    if "sessionid" in normalized or "deviceid" in normalized:
        return True
    if "authorization" in normalized or "cookie" in normalized or "token" in normalized:
        return True
    if "email" in normalized:
        return True
    if any(token in normalized for token in ("phone", "mobile", "msisdn")):
        return True
    if normalized.endswith("userid") or normalized.endswith("accountid"):
        return True
    if "userinfo" in normalized or "address" in normalized:
        return True
    if "bank" in normalized and "status" not in normalized:
        return True
    if "card" in normalized and "status" not in normalized:
        return True
    if normalized in {"auth", "authinfo", "authrequestinfo", "authinternalrequestinfo", "authchallengeresultinfo"}:
        return True
    return False


def value_indicates_sensitive_data(value: str) -> bool:
    lowered = value.lower()
    if EMAIL_RE.search(value):
        return True
    if JWT_RE.search(value):
        return True
    if BEARER_RE.search(value):
        return True
    if COOKIE_RE.search(value):
        return True
    if "authorization" in lowered:
        return True
    if "token" in lowered and re.search(r"[A-Za-z0-9_-]{16,}", value):
        return True
    return False


def mask_scalar_for_key(key: str | None, value: Any) -> Any:
    normalized = normalize_scalar(value)
    if normalized is None:
        return value
    if key_indicates_sensitive_data(key):
        return "<masked>"
    if value_indicates_sensitive_data(normalized):
        return "<masked>"
    return value


def sanitize_metadata(value: Any, key: str | None = None) -> Any:
    if key_indicates_sensitive_data(key):
        return "<masked>"
    if isinstance(value, dict):
        return {k: sanitize_metadata(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_metadata(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_metadata(item, key) for item in value]
    return mask_scalar_for_key(key, value)


def normalized_metadata_keys(metadata: dict[str, Any] | None) -> list[str]:
    if not metadata:
        return []
    return sorted(str(key) for key in metadata.keys())


def safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ranked_json(counter: Counter[str], top_n: int | None = TOP_N_DEFAULT) -> str:
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if top_n is not None:
        items = items[:top_n]
    payload = [{"value": value, "count": count} for value, count in items if count > 0]
    return safe_json_dumps(payload)


def append_scalar(counter: Counter[str], key: str, value: Any) -> None:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, (list, tuple, set, dict)):
                continue
            append_scalar(counter, key, item)
        return
    if isinstance(value, dict):
        return
    normalized = normalize_scalar(value)
    if not normalized:
        return
    masked = mask_scalar_for_key(key, normalized)
    if masked == "<masked>":
        counter["<masked>"] += 1
        return
    normalized_masked = normalize_scalar(masked)
    if normalized_masked:
        counter[normalized_masked] += 1


def add_window(counter: Counter[str], window_label: str | None) -> None:
    if window_label:
        counter[window_label] += 1


def get_metadata_value(metadata: dict[str, Any] | None, key: str) -> Any:
    if not metadata:
        return None
    return metadata.get(key)


def add_evidence_from_sources(
    counter: Counter[str],
    event: dict[str, Any],
    metadata: dict[str, Any] | None,
    top_level_keys: Iterable[str],
    metadata_keys_iter: Iterable[str],
) -> None:
    for key in top_level_keys:
        append_scalar(counter, key, event.get(key))
    if metadata:
        for key in metadata_keys_iter:
            append_scalar(counter, key, get_metadata_value(metadata, key))


def collect_status_evidence(counter: Counter[str], event: dict[str, Any], metadata: dict[str, Any] | None) -> None:
    append_scalar(counter, "status", event.get("status"))
    if not metadata:
        return
    for key, value in metadata.items():
        normalized = normalize_key_name(str(key))
        if "status" in normalized and "errorcode" not in normalized:
            append_scalar(counter, str(key), value)


def collect_error_code_evidence(counter: Counter[str], event: dict[str, Any], metadata: dict[str, Any] | None) -> None:
    append_scalar(counter, "error_code", event.get("error_code"))
    if not metadata:
        return
    for key, value in metadata.items():
        normalized = normalize_key_name(str(key))
        if normalized in ERROR_CODE_KEYS or ("error" in normalized and "code" in normalized):
            append_scalar(counter, str(key), value)


@dataclass
class EventRow:
    event_id: str
    event_family: str
    event_time: datetime | None
    event_time_iso: str | None
    time_window_3h: str | None
    tracking_session_id: str | None
    canonical_session_id: str | None
    user_key: str | None
    app_version: str | None
    os: str | None
    device_model: str | None
    metadata: dict[str, Any] | None
    metadata_keys: list[str]
    previous_event_id: str | None


@dataclass
class GroupAccumulator:
    key: str
    event_family: str | None = None
    total_count: int = 0
    unique_users: set[str] = field(default_factory=set)
    unique_tracking_sessions: set[str] = field(default_factory=set)
    unique_canonical_sessions: set[str] = field(default_factory=set)
    first_event_time: datetime | None = None
    last_event_time: datetime | None = None
    windows: Counter[str] = field(default_factory=Counter)
    app_versions: Counter[str] = field(default_factory=Counter)
    os_values: Counter[str] = field(default_factory=Counter)
    device_models: Counter[str] = field(default_factory=Counter)
    metadata_keys_counter: Counter[str] = field(default_factory=Counter)
    messages: Counter[str] = field(default_factory=Counter)
    screens: Counter[str] = field(default_factory=Counter)
    actions: Counter[str] = field(default_factory=Counter)
    apis: Counter[str] = field(default_factory=Counter)
    statuses: Counter[str] = field(default_factory=Counter)
    error_codes: Counter[str] = field(default_factory=Counter)
    event_ids: Counter[str] = field(default_factory=Counter)

    def add_common(self, row: "EventRow", raw_event: dict[str, Any]) -> None:
        self.total_count += 1
        if row.user_key:
            self.unique_users.add(row.user_key)
        if row.tracking_session_id:
            self.unique_tracking_sessions.add(row.tracking_session_id)
        if row.canonical_session_id:
            self.unique_canonical_sessions.add(row.canonical_session_id)
        if row.event_time is not None:
            if self.first_event_time is None or row.event_time < self.first_event_time:
                self.first_event_time = row.event_time
            if self.last_event_time is None or row.event_time > self.last_event_time:
                self.last_event_time = row.event_time
        add_window(self.windows, row.time_window_3h)
        append_scalar(self.app_versions, "app_version", row.app_version)
        append_scalar(self.os_values, "os", row.os)
        append_scalar(self.device_models, "device_model", row.device_model)
        for key in row.metadata_keys:
            self.metadata_keys_counter[key] += 1
        add_evidence_from_sources(self.messages, raw_event, row.metadata, ("message",), MESSAGE_KEYS)
        add_evidence_from_sources(self.screens, raw_event, row.metadata, ("screen",), SCREEN_KEYS)
        add_evidence_from_sources(self.actions, raw_event, row.metadata, ("action",), ACTION_KEYS)
        add_evidence_from_sources(self.apis, raw_event, row.metadata, ("api",), API_KEYS)
        collect_status_evidence(self.statuses, raw_event, row.metadata)
        collect_error_code_evidence(self.error_codes, raw_event, row.metadata)


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def parse_metadata(raw_metadata: Any) -> tuple[dict[str, Any] | None, int, bool]:
    if raw_metadata is None:
        return None, 0, False
    if isinstance(raw_metadata, dict):
        return raw_metadata, 0, False
    if isinstance(raw_metadata, str):
        text = raw_metadata.strip()
        if not text:
            return None, 0, False
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None, 1, True
        if isinstance(parsed, dict):
            return parsed, 0, False
        return None, 0, False
    return None, 0, False


def normalize_event(raw_event: dict[str, Any]) -> tuple["EventRow | None", bool, int]:
    if not isinstance(raw_event, dict):
        return None, False, 0
    event_id = first_non_empty(raw_event.get("event_id"))
    if not event_id:
        return None, False, 0

    metadata, metadata_parse_error_count, metadata_parse_failed = parse_metadata(raw_event.get("metadata"))

    event_family = event_family_from_event_id(event_id) or event_id
    client_time = parse_event_time(raw_event.get("client_timestamp"))
    server_time = parse_event_time(raw_event.get("timestamp"))
    event_time = client_time or server_time
    event_time_iso = iso_or_none(event_time)
    time_window_3h = window_from_event_time(event_time)

    canonical_session_id = first_non_empty(
        get_metadata_value(metadata, "appSesssionid"),
        get_metadata_value(metadata, "app_session_id"),
        get_metadata_value(metadata, "appSessionId"),
        get_metadata_value(metadata, "sessionID"),
        get_metadata_value(metadata, "webSessionId"),
        get_metadata_value(metadata, "flowSessionId"),
    )
    user_key = first_non_empty(
        raw_event.get("zalopay_id"),
        get_metadata_value(metadata, "zalopay_id"),
        get_metadata_value(metadata, "userID"),
        get_metadata_value(metadata, "user_id"),
        raw_event.get("zalo_id"),
        get_metadata_value(metadata, "zalo_id"),
        get_metadata_value(metadata, "zaloid"),
    )
    app_version = first_non_empty(
        raw_event.get("app_version"),
        get_metadata_value(metadata, "appver"),
        get_metadata_value(metadata, "app_ver"),
        get_metadata_value(metadata, "appVer"),
        get_metadata_value(metadata, "appVersion"),
    )
    os_value = first_non_empty(
        raw_event.get("os"),
        get_metadata_value(metadata, "os"),
        get_metadata_value(metadata, "OS"),
        get_metadata_value(metadata, "device_os"),
    )
    device_model = first_non_empty(
        raw_event.get("device_model"),
        get_metadata_value(metadata, "device_model"),
        get_metadata_value(metadata, "deviceModel"),
        get_metadata_value(metadata, "device"),
    )
    metadata_keys = normalized_metadata_keys(metadata)
    previous_event_id = first_non_empty(raw_event.get("previous_event_id"))

    normalized_row = EventRow(
        event_id=event_id,
        event_family=event_family,
        event_time=event_time,
        event_time_iso=event_time_iso,
        time_window_3h=time_window_3h,
        tracking_session_id=first_non_empty(raw_event.get("tracking_session_id")),
        canonical_session_id=canonical_session_id,
        user_key=user_key,
        app_version=app_version,
        os=os_value,
        device_model=device_model,
        metadata=metadata,
        metadata_keys=metadata_keys,
        previous_event_id=previous_event_id,
    )
    return normalized_row, metadata_parse_failed, metadata_parse_error_count


def build_example_row(row: EventRow, metadata_parse_failed: bool) -> dict[str, Any]:
    if metadata_parse_failed:
        metadata_masked: Any = {"_metadata_parse_error": True}
    elif row.metadata is None:
        metadata_masked = None
    else:
        metadata_masked = sanitize_metadata(row.metadata)
    return {
        "event_id": row.event_id,
        "event_family": row.event_family,
        "event_time_iso": row.event_time_iso,
        "time_window_3h": row.time_window_3h,
        "previous_event_id": row.previous_event_id,
        "tracking_session_hash": hash_value(TRACKING_SESSION_NAMESPACE, row.tracking_session_id),
        "canonical_session_hash": hash_value(CANONICAL_SESSION_NAMESPACE, row.canonical_session_id),
        "user_key_hash": hash_value(USER_KEY_NAMESPACE, row.user_key),
        "app_version": row.app_version,
        "os": row.os,
        "device_model": row.device_model,
        "metadata_keys": row.metadata_keys,
        "metadata_masked": metadata_masked,
    }


def make_event_id_profile_rows(
    accumulators: dict[str, GroupAccumulator],
    example_counts: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_id, accumulator in accumulators.items():
        rows.append({
            "event_id": event_id,
            "event_family": accumulator.event_family,
            "total_count": accumulator.total_count,
            "unique_user_count": len(accumulator.unique_users),
            "unique_tracking_session_count": len(accumulator.unique_tracking_sessions),
            "unique_canonical_session_count": len(accumulator.unique_canonical_sessions),
            "first_event_time": accumulator.first_event_time,
            "first_event_time_iso": iso_or_none(accumulator.first_event_time),
            "last_event_time": accumulator.last_event_time,
            "last_event_time_iso": iso_or_none(accumulator.last_event_time),
            "top_3h_windows": ranked_json(accumulator.windows, top_n=None),
            "top_app_versions": ranked_json(accumulator.app_versions),
            "top_os": ranked_json(accumulator.os_values),
            "top_device_models": ranked_json(accumulator.device_models),
            "top_metadata_keys": ranked_json(accumulator.metadata_keys_counter, top_n=TOP_N_METADATA_KEYS),
            "top_messages": ranked_json(accumulator.messages),
            "top_screens": ranked_json(accumulator.screens),
            "top_actions": ranked_json(accumulator.actions),
            "top_apis": ranked_json(accumulator.apis),
            "top_statuses": ranked_json(accumulator.statuses),
            "top_error_codes": ranked_json(accumulator.error_codes),
            "example_count": example_counts.get(event_id, 0),
        })
    rows.sort(key=lambda item: (-item["total_count"], item["event_id"]))
    return rows


def make_event_family_profile_rows(accumulators: dict[str, GroupAccumulator]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_family, accumulator in accumulators.items():
        rows.append({
            "event_family": event_family,
            "total_count": accumulator.total_count,
            "unique_event_id_count": len(accumulator.event_ids),
            "top_event_ids": ranked_json(accumulator.event_ids, top_n=TOP_N_DEFAULT),
            "unique_user_count": len(accumulator.unique_users),
            "unique_tracking_session_count": len(accumulator.unique_tracking_sessions),
            "unique_canonical_session_count": len(accumulator.unique_canonical_sessions),
            "first_event_time": accumulator.first_event_time,
            "first_event_time_iso": iso_or_none(accumulator.first_event_time),
            "last_event_time": accumulator.last_event_time,
            "last_event_time_iso": iso_or_none(accumulator.last_event_time),
            "top_3h_windows": ranked_json(accumulator.windows, top_n=None),
            "top_metadata_keys": ranked_json(accumulator.metadata_keys_counter, top_n=TOP_N_METADATA_KEYS),
            "top_messages": ranked_json(accumulator.messages),
            "top_screens": ranked_json(accumulator.screens),
            "top_actions": ranked_json(accumulator.actions),
            "top_apis": ranked_json(accumulator.apis),
            "top_statuses": ranked_json(accumulator.statuses),
            "top_error_codes": ranked_json(accumulator.error_codes),
        })
    rows.sort(key=lambda item: (-item["total_count"], item["event_family"]))
    return rows


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    dataframe = pd.DataFrame(rows)
    if "first_event_time" in dataframe.columns:
        dataframe["first_event_time"] = pd.to_datetime(dataframe["first_event_time"], utc=True)
    if "last_event_time" in dataframe.columns:
        dataframe["last_event_time"] = pd.to_datetime(dataframe["last_event_time"], utc=True)
    dataframe.to_parquet(path, index=False, engine="pyarrow")


def write_examples_jsonl(path: Path, examples_by_event: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event_id in sorted(examples_by_event):
            rows = examples_by_event[event_id]
            counts[event_id] = len(rows)
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False))
                handle.write("\n")
    return counts


def run(input_dir: str, output_dir: str, **kwargs) -> None:
    """
    Step 1: Event meaning profiling.
    Reads records.parquet from input_dir, writes event_id_profile.parquet,
    event_family_profile.parquet, event_id_examples.jsonl, run_summary.json to output_dir.
    """
    import glob as glob_module

    date_filter: str | None = kwargs.get("date_filter")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    parquet_files = sorted(glob_module.glob(str(Path(input_dir) / "*.parquet")))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in {input_dir!r}")

    event_id_accumulators: dict[str, GroupAccumulator] = {}
    event_family_accumulators: dict[str, GroupAccumulator] = {}
    examples_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)

    parsed_event_count = 0
    bad_event_count = 0
    metadata_parse_error_count = 0

    unique_tracking_sessions: set[str] = set()
    unique_canonical_sessions: set[str] = set()
    unique_users: set[str] = set()
    first_event_time: datetime | None = None
    last_event_time: datetime | None = None

    for parquet_path in parquet_files:
        for raw_event in _iter_events(parquet_path, date_filter=date_filter):
            if not isinstance(raw_event, dict):
                bad_event_count += 1
                continue

            parsed_event_count += 1
            normalized_row, metadata_parse_failed, metadata_errors = normalize_event(raw_event)
            metadata_parse_error_count += metadata_errors
            if normalized_row is None:
                continue

            if normalized_row.tracking_session_id:
                unique_tracking_sessions.add(normalized_row.tracking_session_id)
            if normalized_row.canonical_session_id:
                unique_canonical_sessions.add(normalized_row.canonical_session_id)
            if normalized_row.user_key:
                unique_users.add(normalized_row.user_key)
            if normalized_row.event_time is not None:
                if first_event_time is None or normalized_row.event_time < first_event_time:
                    first_event_time = normalized_row.event_time
                if last_event_time is None or normalized_row.event_time > last_event_time:
                    last_event_time = normalized_row.event_time

            event_accumulator = event_id_accumulators.setdefault(
                normalized_row.event_id,
                GroupAccumulator(key=normalized_row.event_id, event_family=normalized_row.event_family),
            )
            event_accumulator.add_common(normalized_row, raw_event)

            family_accumulator = event_family_accumulators.setdefault(
                normalized_row.event_family,
                GroupAccumulator(key=normalized_row.event_family),
            )
            family_accumulator.add_common(normalized_row, raw_event)
            family_accumulator.event_ids[normalized_row.event_id] += 1

            if len(examples_by_event[normalized_row.event_id]) < MAX_EXAMPLES_PER_EVENT:
                examples_by_event[normalized_row.event_id].append(
                    build_example_row(normalized_row, metadata_parse_failed)
                )

    examples_path = output_path / "event_id_examples.jsonl"
    example_counts = write_examples_jsonl(examples_path, examples_by_event)

    event_id_rows = make_event_id_profile_rows(event_id_accumulators, example_counts)
    event_family_rows = make_event_family_profile_rows(event_family_accumulators)

    write_parquet(event_id_rows, output_path / "event_id_profile.parquet")
    write_parquet(event_family_rows, output_path / "event_family_profile.parquet")

    run_summary = {
        "input_file_count": len(parquet_files),
        "parsed_event_count": parsed_event_count,
        "bad_event_count": bad_event_count,
        "metadata_parse_error_count": metadata_parse_error_count,
        "unique_event_id_count": len(event_id_accumulators),
        "unique_event_family_count": len(event_family_accumulators),
        "unique_tracking_session_count": len(unique_tracking_sessions),
        "unique_canonical_session_count": len(unique_canonical_sessions),
        "unique_user_count": len(unique_users),
        "first_event_time_iso": iso_or_none(first_event_time),
        "last_event_time_iso": iso_or_none(last_event_time),
        "time_window_policy": "fixed_3_hour_boundary",
        "timezone_policy": "no_china_time_conversion",
    }
    summary_path = output_path / "run_summary.json"
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(run_summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
