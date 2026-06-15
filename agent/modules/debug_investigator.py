"""
Debug Investigator — investigate bugs from Jira tickets.

Architecture:
  1. POST /parse-ticket  → LLM extracts structured fields from ticket text
  2. POST /investigate   → LLM generates structured query plan
                         → Backend validates plan against policy
                         → Backend executes approved queries against parquet sources
                         → LLM generates structured insight from evidence only

Data sources (parquet — never reference client-log-visualizer):
  TRACKING_EVENT_V2       : user event chain
  ACCESS_LOG              : webview page loads
  MOBILE_VITAL_EVENT_LOG  : failed API calls
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel

from agent.llm_client import call_llm
from agent.data.store import read_dataframe

router = APIRouter(prefix="/api/debug", tags=["debug-investigator"])

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

TRACKING_PATH = os.path.join(_BASE_DIR, "output", "tracking_events_v2", "records.parquet")
ACCESS_PATH   = os.path.join(_BASE_DIR, "output", "access_log", "records.parquet")
VITAL_PATH    = os.path.join(_BASE_DIR, "output", "mobile_vital_events", "records.parquet")

# Mapping files live under agent/data/mapping/ — never reference client-log-visualizer
_MAPPING_DIR = os.path.join(_BASE_DIR, "agent", "data", "mapping")

try:
    with open(os.path.join(_MAPPING_DIR, "mapping-event.json")) as f:
        _EVENT_MAP: dict = json.load(f)
except Exception:
    _EVENT_MAP = {}

try:
    with open(os.path.join(_MAPPING_DIR, "mapping-screen-id.json")) as f:
        _SCREEN_MAP: dict = json.load(f)
except Exception:
    _SCREEN_MAP = {}

VN_TZ = timezone(timedelta(hours=7))


# ── Query Policy ─────────────────────────────────────────────────────────────

QUERY_POLICY = {
    "allowed_tables": ["ALL", "TRACKING_EVENT_V2", "ACCESS_LOG", "MOBILE_VITAL_EVENT_LOG"],
    "max_query_attempts": 10,
    "default_window_minutes": 60,
    "max_window_minutes": 240,
    "max_shift_days": 4,
    "max_rows_per_query": 1000,
    "require_scoped_filter": True,
    "allowed_filter_keys": [
        "zalopay_id", "traceID", "session_id", "device_id",
        "endpoint", "event_id", "previous_event_id", "timestamp", "time_range"
    ]
}


# ── API Models ────────────────────────────────────────────────────────────────

class ParseTicketRequest(BaseModel):
    ticket_text: str


class ParseTicketResponse(BaseModel):
    zalopay_id: Optional[str] = None
    incident_time: Optional[str] = None
    timezone: str = "Asia/Ho_Chi_Minh"
    device: str = ""
    os_version: str = ""
    app_version: str = ""
    error_description: str = ""
    confidence: str = "low"
    missing_fields: list[str] = []
    warnings: list[str] = []


class InvestigateRequest(BaseModel):
    zalopay_id: str
    ticket_text: str = ""
    incident_time: str = ""
    window_minutes: int = 60


class TimeRangeResult(BaseModel):
    start: str
    end: str


class MappingInfo(BaseModel):
    event_name: str = ""
    screen_code: str = ""
    screen_name: str = ""
    mapping_status: str = "unknown"   # exact | screen_fallback | unknown


class CorrelationInfo(BaseModel):
    trace_id: str = ""
    session_id: str = ""
    device_id: str = ""


class EventItem(BaseModel):
    id: str                             # e.g. tracking-0001
    source: str                         # tracking | access | vital
    timestamp: str                      # ISO-8601 with timezone
    ts: int                             # epoch ms (for vis-timeline)
    ts_str: str                         # HH:MM:SS display
    title: str
    subtitle: str = ""
    severity: str = "info"              # info | warning | error | fatal | unknown
    mapping: Optional[MappingInfo] = None
    correlation: CorrelationInfo = CorrelationInfo()
    raw: dict = {}


class EvidenceItem(BaseModel):
    event_id: str
    timestamp: str
    reason: str


class InsightResult(BaseModel):
    summary: str = ""
    user_flow: str = ""
    likely_root_cause: str = ""
    confidence: str = "low"
    evidence: list[EvidenceItem] = []
    recommendations: list[str] = []
    unknowns: list[str] = []


class QuerySummary(BaseModel):
    attempts_used: int = 0
    tables_scanned: list[str] = []
    strategy: str = "agent_planned"
    correlation_basis: list[str] = []


class InvestigateResponse(BaseModel):
    status: str                         # found | shifted_found | not_found | partial | parse_error | policy_rejected | query_error
    zalopay_id: str
    incident_time: str
    window_minutes: int
    requested_time_range: TimeRangeResult
    actual_time_range: TimeRangeResult
    time_mismatch: bool = False
    shift_days: int = 0
    tracking_count: int = 0
    access_count: int = 0
    vital_count: int = 0
    error_count: int = 0
    query_summary: QuerySummary = QuerySummary()
    events: list[EventItem] = []
    insight: InsightResult = InsightResult()
    warnings: list[str] = []


# ── Query Plan Schemas ────────────────────────────────────────────────────────

class QueryTimeRange(BaseModel):
    start: str
    end: str


class QueryFilters(BaseModel):
    zalopay_id: Optional[str] = None
    traceID: Optional[str] = None
    session_id: Optional[str] = None
    device_id: Optional[str] = None
    endpoint: Optional[str] = None
    event_id: Optional[str] = None
    previous_event_id: Optional[str] = None
    time_range: Optional[QueryTimeRange] = None


class QueryStep(BaseModel):
    id: str
    table: str
    reason: str
    filters: QueryFilters
    limit: int = 500


class QueryPlan(BaseModel):
    goal: str
    steps: list[QueryStep]
    stop_conditions: list[str] = []


# ── Low-level Helpers ─────────────────────────────────────────────────────────

def _parse_ts(val) -> Optional[datetime]:
    """Parse a timestamp from epoch ms or ISO 8601 string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s == "nan":
        return None
    if s.isdigit() and len(s) >= 13:
        return datetime.fromtimestamp(int(s) / 1000, tz=VN_TZ)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except Exception:
        return None


def _to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _safe_str(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def _resolve_event_mapping(eid: str) -> MappingInfo:
    """
    Resolve event_id to a MappingInfo.
    1. Exact match in mapping-event.json
    2. Screen code fallback via mapping-screen-id.json (format: XX.XXXX.XXX)
    3. Unknown — return raw eid
    """
    if eid in _EVENT_MAP:
        name = _EVENT_MAP[eid].get("name", "")
        return MappingInfo(event_name=name, mapping_status="exact")

    if re.match(r'^\d{2}\.\d{4}\.\d{3}$', eid):
        screen_code = eid.split(".")[1]
        screen_info = _SCREEN_MAP.get(screen_code, {})
        if screen_info:
            return MappingInfo(
                event_name=screen_info.get("name", screen_code),
                screen_code=screen_code,
                screen_name=screen_info.get("name", ""),
                mapping_status="screen_fallback"
            )

    return MappingInfo(event_name=eid or "Unknown Event", mapping_status="unknown")


def _parse_json_from_llm(raw: str) -> dict:
    """Strip markdown code fences and parse JSON."""
    cleaned = raw.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[-1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()
    return json.loads(cleaned)


# ── Policy Validator ──────────────────────────────────────────────────────────

def _validate_step(step: QueryStep, now: datetime) -> tuple[bool, str]:
    """Validate a query step against QUERY_POLICY. Returns (is_valid, rejection_reason)."""
    if step.table not in QUERY_POLICY["allowed_tables"]:
        return False, f"Table '{step.table}' not in allowlist"

    if step.limit > QUERY_POLICY["max_rows_per_query"]:
        return False, f"limit={step.limit} exceeds max_rows_per_query={QUERY_POLICY['max_rows_per_query']}"

    filters_dict = {k: v for k, v in step.filters.dict().items() if v is not None}
    allowed = set(QUERY_POLICY["allowed_filter_keys"]) | {"time_range"}
    for key in filters_dict:
        if key not in allowed:
            return False, f"Filter key '{key}' not in allowlist"

    # Require at least one scoped filter
    if QUERY_POLICY["require_scoped_filter"]:
        scoped = {"zalopay_id", "traceID", "session_id", "device_id", "event_id"}
        if not (scoped & set(filters_dict.keys())):
            return False, "Requires at least one scoped filter (zalopay_id, traceID, session_id, device_id, event_id)"

    if step.filters.time_range:
        tr = step.filters.time_range
        try:
            t_start = datetime.fromisoformat(tr.start.replace("Z", "+00:00"))
            t_end   = datetime.fromisoformat(tr.end.replace("Z", "+00:00"))
            if t_start.tzinfo is None:
                t_start = t_start.replace(tzinfo=VN_TZ)
            if t_end.tzinfo is None:
                t_end = t_end.replace(tzinfo=VN_TZ)
        except Exception:
            return False, "Invalid time_range format — must be ISO-8601"

        if t_start > now or t_end > now:
            return False, "time_range must not be in the future"

        window_min = (t_end - t_start).total_seconds() / 60
        if window_min > QUERY_POLICY["max_window_minutes"]:
            return False, f"time_range window {window_min:.0f}min exceeds max_window_minutes={QUERY_POLICY['max_window_minutes']}"

    return True, ""


# ── Extraction Functions ──────────────────────────────────────────────────────

def _extract_tracking(
    df: pd.DataFrame,
    filters: QueryFilters,
    t_from: Optional[datetime],
    t_to: Optional[datetime],
    limit: int,
    counters: dict,
) -> list[EventItem]:
    if df.empty:
        return []
    if filters.zalopay_id:
        df = df[df["zalopay_id"].astype(str) == filters.zalopay_id]
    if df.empty:
        return []

    items = []
    for _, row in df.iterrows():
        if len(items) >= limit:
            break
        ts = _parse_ts(row.get("client_timestamp") or row.get("timestamp"))
        if ts is None:
            continue
        if t_from and ts < t_from:
            continue
        if t_to and ts > t_to:
            continue

        eid      = _safe_str(row.get("event_id"))
        prev_eid = _safe_str(row.get("previous_event_id"))
        meta     = _safe_str(row.get("metadata"))[:300]
        mapping  = _resolve_event_mapping(eid)

        meta_lower = meta.lower()
        severity = "error" if any(k in meta_lower for k in ("error", "fail", "exception")) else "info"

        counters["tracking"] += 1
        items.append(EventItem(
            id=f"tracking-{counters['tracking']:04d}",
            source="tracking",
            timestamp=ts.isoformat(),
            ts=_to_epoch_ms(ts),
            ts_str=ts.strftime("%H:%M:%S"),
            title=mapping.event_name or eid or "Tracking Event",
            subtitle=mapping.screen_name or "",
            severity=severity,
            mapping=mapping,
            correlation=CorrelationInfo(
                session_id=_safe_str(row.get("session_id")),
                device_id=_safe_str(row.get("device_id")),
            ),
            raw={
                "event_id": eid,
                "previous_event_id": prev_eid,
                "metadata": meta,
            }
        ))
    return items


def _extract_access(
    df: pd.DataFrame,
    filters: QueryFilters,
    t_from: Optional[datetime],
    t_to: Optional[datetime],
    limit: int,
    counters: dict,
) -> list[EventItem]:
    if df.empty:
        return []

    if filters.zalopay_id:
        col = "zaloPayID" if "zaloPayID" in df.columns else "zalopay_id"
        if col in df.columns:
            df = df[df[col].astype(str) == filters.zalopay_id]
    if df.empty:
        return []

    items = []
    for _, row in df.iterrows():
        if len(items) >= limit:
            break
        ts = _parse_ts(row.get("timeStamp") or row.get("timestamp"))
        if ts is None:
            continue
        if t_from and ts < t_from:
            continue
        if t_to and ts > t_to:
            continue

        page = _safe_str(row.get("page"))
        if "://" in page:
            page = "/" + "/".join(page.split("://", 1)[1].split("/")[1:])
        page = page[:200]

        trace_id = _safe_str(row.get("traceID"))
        app      = _safe_str(row.get("app"))

        try:
            sc = int(row.get("status_code") or 0)
        except Exception:
            sc = 0
        severity = "error" if sc >= 400 else "info"

        counters["access"] += 1
        items.append(EventItem(
            id=f"access-{counters['access']:04d}",
            source="access",
            timestamp=ts.isoformat(),
            ts=_to_epoch_ms(ts),
            ts_str=ts.strftime("%H:%M:%S"),
            title="Webview page load",
            subtitle=page or "/",
            severity=severity,
            correlation=CorrelationInfo(trace_id=trace_id),
            raw={
                "page": page,
                "app": app,
                "traceID": trace_id,
                "timeStamp": _to_epoch_ms(ts),
                "status_code": sc if sc else None,
            }
        ))
    return items


def _extract_vital(
    df: pd.DataFrame,
    filters: QueryFilters,
    t_from: Optional[datetime],
    t_to: Optional[datetime],
    limit: int,
    counters: dict,
) -> list[EventItem]:
    if df.empty:
        return []
    if filters.zalopay_id:
        df = df[df["zalopay_id"].astype(str) == filters.zalopay_id]
    if df.empty:
        return []

    items = []
    for _, row in df.iterrows():
        if len(items) >= limit:
            break
        ts = _parse_ts(row.get("timestamp"))
        if ts is None:
            continue
        if t_from and ts < t_from:
            continue
        if t_to and ts > t_to:
            continue

        error_msg = _safe_str(row.get("error_message"))
        endpoint  = _safe_str(row.get("endpoint"))
        if "?" in endpoint:
            endpoint = endpoint.split("?")[0]
        endpoint = endpoint[-120:]
        network = _safe_str(row.get("network_type"))
        severity = "error" if error_msg else "info"

        counters["vital"] += 1
        items.append(EventItem(
            id=f"vital-{counters['vital']:04d}",
            source="vital",
            timestamp=ts.isoformat(),
            ts=_to_epoch_ms(ts),
            ts_str=ts.strftime("%H:%M:%S"),
            title="Failed API call" if error_msg else "API call",
            subtitle=endpoint or "/",
            severity=severity,
            raw={
                "endpoint": endpoint,
                "error_message": error_msg,
                "network_type": network,
                "timestamp": ts.isoformat(),
            }
        ))
    return items


def _execute_step(step: QueryStep, dfs: dict, counters: dict) -> list[EventItem]:
    """Execute a validated query step. Never executes raw LLM-generated code."""
    tables = (
        ["TRACKING_EVENT_V2", "ACCESS_LOG", "MOBILE_VITAL_EVENT_LOG"]
        if step.table == "ALL"
        else [step.table]
    )

    t_from = t_to = None
    if step.filters.time_range:
        try:
            t_from = datetime.fromisoformat(step.filters.time_range.start.replace("Z", "+00:00"))
            t_to   = datetime.fromisoformat(step.filters.time_range.end.replace("Z", "+00:00"))
            if t_from.tzinfo is None:
                t_from = t_from.replace(tzinfo=VN_TZ)
            if t_to.tzinfo is None:
                t_to = t_to.replace(tzinfo=VN_TZ)
        except Exception:
            pass

    results = []
    for table in tables:
        df = dfs.get(table)
        if df is None or df.empty:
            continue
        if table == "TRACKING_EVENT_V2":
            results.extend(_extract_tracking(df, step.filters, t_from, t_to, step.limit, counters))
        elif table == "ACCESS_LOG":
            results.extend(_extract_access(df, step.filters, t_from, t_to, step.limit, counters))
        elif table == "MOBILE_VITAL_EVENT_LOG":
            results.extend(_extract_vital(df, step.filters, t_from, t_to, step.limit, counters))
    return results


# ── Query Plan Generator ──────────────────────────────────────────────────────

def _default_plan(req: InvestigateRequest, t_from: datetime, t_to: datetime) -> QueryPlan:
    return QueryPlan(
        goal="Investigate reported issue — default plan",
        steps=[QueryStep(
            id="q1",
            table="ALL",
            reason="Query all tables for user around incident time",
            filters=QueryFilters(
                zalopay_id=req.zalopay_id,
                time_range=QueryTimeRange(start=t_from.isoformat(), end=t_to.isoformat())
            ),
            limit=500,
        )],
        stop_conditions=["query_budget_exhausted"],
    )


def _generate_query_plan(
    req: InvestigateRequest,
    incident_dt: datetime,
    t_from: datetime,
    t_to: datetime,
) -> QueryPlan:
    """Ask LLM to generate a structured query plan. Fall back to default if LLM fails."""
    prompt = f"""You are a ZaloPay log investigation agent. Generate a structured JSON query plan for this bug ticket.

Ticket: {req.ticket_text[:600] if req.ticket_text else "(none)"}
ZaloPayID: {req.zalopay_id}
Incident time: {incident_dt.isoformat()}
Default window: {t_from.isoformat()} → {t_to.isoformat()}

Available tables: TRACKING_EVENT_V2, ACCESS_LOG, MOBILE_VITAL_EVENT_LOG (or "ALL")
Allowed filter keys: zalopay_id, traceID, session_id, device_id, endpoint, event_id, previous_event_id, time_range
Max rows per step: {QUERY_POLICY['max_rows_per_query']}
Max window per step: {QUERY_POLICY['max_window_minutes']} minutes

Return JSON only, no markdown:
{{
  "goal": "brief investigation goal",
  "steps": [
    {{
      "id": "q1",
      "table": "ALL",
      "reason": "Find all logs for user around incident time",
      "filters": {{
        "zalopay_id": "{req.zalopay_id}",
        "time_range": {{"start": "{t_from.isoformat()}", "end": "{t_to.isoformat()}"}}
      }},
      "limit": 500
    }}
  ],
  "stop_conditions": ["found_vital_error_near_incident", "found_tracking_journey", "query_budget_exhausted"]
}}

Rules:
- Always include zalopay_id as scoped filter
- time_range must not be in the future
- limit ≤ {QUERY_POLICY['max_rows_per_query']}"""

    try:
        raw = call_llm(prompt, max_tokens=600)
        data = _parse_json_from_llm(raw)
        steps = []
        for s in data.get("steps", []):
            filters_raw = dict(s.get("filters", {}))
            time_range_raw = filters_raw.pop("time_range", None)
            time_range = QueryTimeRange(**time_range_raw) if isinstance(time_range_raw, dict) else None
            valid_keys = set(QueryFilters.__fields__.keys())
            filters_clean = {k: v for k, v in filters_raw.items() if k in valid_keys}
            filters = QueryFilters(**filters_clean, time_range=time_range)
            limit = min(int(s.get("limit", 500)), QUERY_POLICY["max_rows_per_query"])
            steps.append(QueryStep(
                id=str(s.get("id", f"q{len(steps)+1}")),
                table=str(s.get("table", "ALL")),
                reason=str(s.get("reason", "")),
                filters=filters,
                limit=limit,
            ))
        if not steps:
            return _default_plan(req, t_from, t_to)
        return QueryPlan(
            goal=str(data.get("goal", "Investigate bug")),
            steps=steps,
            stop_conditions=list(data.get("stop_conditions", [])),
        )
    except Exception:
        return _default_plan(req, t_from, t_to)


# ── Insight Builder ───────────────────────────────────────────────────────────

def _build_insight(
    events: list[EventItem],
    req: InvestigateRequest,
    time_mismatch: bool,
    shift_days: int,
) -> InsightResult:
    """Generate structured insight from normalized evidence. LLM must not invent data."""
    if not events:
        return InsightResult(
            summary="Không tìm thấy log nào trong khoảng thời gian được yêu cầu",
            user_flow="",
            likely_root_cause="Insufficient evidence to determine root cause.",
            confidence="low",
            evidence=[],
            recommendations=[
                "Xác minh thời gian xảy ra sự cố và UserID đã báo cáo.",
            ],
            unknowns=["Không có log tracking, access hoặc vital nào."]
        )

    sample = events[:80]
    events_text = "\n".join(
        f"[{e.id}] [{e.timestamp}] [{e.source.upper()}] {e.title}"
        + (f" — {e.subtitle}" if e.subtitle else "")
        + (f" | LỖI: {e.raw.get('error_message', '')}" if e.severity in ("error", "fatal") else "")
        for e in sample
    )

    errors = [e for e in events if e.severity in ("error", "fatal")]
    error_text = "\n".join(
        f"  [{e.id}] {e.subtitle}: {e.raw.get('error_message', '')}"
        for e in errors[:10]
    )

    mismatch_note = ""
    if time_mismatch:
        direction = "TRƯỚC" if shift_days < 0 else "SAU"
        mismatch_note = (
            f"\n⚠️ DỮ LIỆU ĐƯỢC TÌM THẤY {abs(shift_days)} NGÀY {direction} THỜI GIAN BÁO CÁO. "
            f"Lưu ý sự khác biệt này.\n"
        )

    prompt = f"""Bạn là kỹ sư phân tích lỗi ZaloPay. Phân tích log bên dưới và trả về JSON insight.
Chỉ được sử dụng thông tin từ log được cung cấp. Không được bịa thêm thông tin.
{mismatch_note}
=== TICKET ===
{req.ticket_text[:500] if req.ticket_text else "(không có ticket)"}

=== THÔNG TIN USER ===
ZaloPayID: {req.zalopay_id}
Thời gian sự cố (từ ticket): {req.incident_time or "không rõ"}

=== SỰ KIỆN ({len(events)} tổng, hiển thị {len(sample)}) ===
{events_text}
{"... (đã rút gọn)" if len(events) > 80 else ""}

=== LỖI API ({len(errors)}) ===
{error_text or "Không có lỗi API"}

Trả về JSON thuần túy (không có markdown). Dùng event ID chính xác như trong danh sách trên (ví dụ "vital-0001").
Tất cả text viết bằng tiếng Việt, ngoại trừ event ID và timestamp.

{{
  "summary": "tóm tắt tình trạng một câu",
  "user_flow": "mô tả user đi qua những màn hình/bước nào dựa trên tracking events",
  "likely_root_cause": "nguyên nhân gốc rễ có thể nhất. Nếu không đủ bằng chứng, viết chính xác: 'Insufficient evidence to determine root cause.'",
  "confidence": "low|medium|high",
  "evidence": [
    {{"event_id": "vital-0001", "timestamp": "...", "reason": "lý do event này là bằng chứng"}}
  ],
  "recommendations": ["hành động gợi ý 1", "hành động gợi ý 2"],
  "unknowns": ["điều không thể xác định từ log hiện tại"]
}}"""

    try:
        raw = call_llm(prompt, max_tokens=1000)
        data = _parse_json_from_llm(raw)
        evidence = []
        for ev in data.get("evidence", []):
            if isinstance(ev, dict) and ev.get("event_id"):
                try:
                    evidence.append(EvidenceItem(
                        event_id=str(ev["event_id"]),
                        timestamp=str(ev.get("timestamp", "")),
                        reason=str(ev.get("reason", "")),
                    ))
                except Exception:
                    pass
        return InsightResult(
            summary=str(data.get("summary", "")),
            user_flow=str(data.get("user_flow", "")),
            likely_root_cause=str(data.get("likely_root_cause", "")),
            confidence=str(data.get("confidence", "low")),
            evidence=evidence,
            recommendations=[str(r) for r in data.get("recommendations", []) if r],
            unknowns=[str(u) for u in data.get("unknowns", []) if u],
        )
    except Exception as ex:
        return InsightResult(
            summary="Đã phân tích xong",
            user_flow="",
            likely_root_cause="",
            confidence="low",
            evidence=[],
            recommendations=[],
            unknowns=[f"Lỗi phân tích insight: {str(ex)[:100]}"],
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/parse-ticket", response_model=ParseTicketResponse)
async def parse_ticket(req: ParseTicketRequest):
    """Extract structured fields from a Jira ticket using LLM."""
    if not req.ticket_text.strip():
        return ParseTicketResponse(
            confidence="low",
            missing_fields=["ticket_text"],
            warnings=["ticket_text is required"],
        )

    prompt = f"""You are a ZaloPay support assistant. Extract structured fields from this Jira ticket.
Return plain JSON only — no markdown, no explanation.

Ticket:
{req.ticket_text}

Return JSON:
{{
  "zalopay_id": "<UserID/ZaloPayID digits only — or null>",
  "incident_time": "<ISO 8601 with +07:00, e.g. 2026-06-13T16:25:00+07:00 — or null>",
  "device": "<device name or empty string>",
  "os_version": "<OS version or empty string>",
  "app_version": "<ZaloPay version or empty string>",
  "error_description": "<brief error description in English or empty string>"
}}

Rules:
- zalopay_id: digits only, strip spaces
- incident_time: ISO 8601 with +07:00 timezone; assume Asia/Ho_Chi_Minh if timezone not stated
- Return null (not empty string) for fields that are genuinely not found"""

    try:
        raw = call_llm(prompt, max_tokens=400)
        data = _parse_json_from_llm(raw)

        zalopay_id = data.get("zalopay_id") or None
        if zalopay_id:
            zalopay_id = re.sub(r'\D', '', str(zalopay_id)).strip() or None

        incident_time = data.get("incident_time") or None
        if incident_time:
            try:
                dt = datetime.fromisoformat(str(incident_time).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=VN_TZ)
                incident_time = dt.isoformat()
            except Exception:
                incident_time = None

        missing_fields = []
        warnings_list = []
        if not zalopay_id:
            missing_fields.append("zalopay_id")
        if not incident_time:
            missing_fields.append("incident_time")
        if missing_fields:
            warnings_list.append(f"Cannot investigate without: {', '.join(missing_fields)}")

        if len(missing_fields) >= 2:
            confidence = "low"
        elif len(missing_fields) == 1:
            confidence = "medium"
        else:
            confidence = "high"

        return ParseTicketResponse(
            zalopay_id=zalopay_id,
            incident_time=incident_time,
            timezone="Asia/Ho_Chi_Minh",
            device=str(data.get("device", "") or ""),
            os_version=str(data.get("os_version", "") or ""),
            app_version=str(data.get("app_version", "") or ""),
            error_description=str(data.get("error_description", "") or ""),
            confidence=confidence,
            missing_fields=missing_fields,
            warnings=warnings_list,
        )
    except Exception as ex:
        return ParseTicketResponse(
            confidence="low",
            missing_fields=["zalopay_id", "incident_time"],
            warnings=[f"LLM parse failed: {str(ex)[:100]}"],
        )


@router.post("/investigate", response_model=InvestigateResponse)
async def investigate(req: InvestigateRequest):
    """
    Investigate a bug report.
    1. LLM generates structured query plan.
    2. Backend validates each step against policy.
    3. Backend executes only approved steps against parquet sources.
    4. Date-shift fallback if no data found at reported time.
    5. LLM generates structured insight from normalized evidence only.
    """
    now = datetime.now(tz=VN_TZ)

    # Required field validation
    if not req.zalopay_id.strip():
        empty = TimeRangeResult(start="", end="")
        return InvestigateResponse(
            status="parse_error",
            zalopay_id=req.zalopay_id,
            incident_time="",
            window_minutes=req.window_minutes,
            requested_time_range=empty,
            actual_time_range=empty,
            warnings=["Cannot investigate without zalopay_id and incident_time."],
            insight=InsightResult(
                summary="Missing required fields.",
                likely_root_cause="Insufficient evidence to determine root cause.",
                confidence="low",
                unknowns=["zalopay_id is required"],
            ),
        )

    # Parse incident time (default: now)
    incident_dt: Optional[datetime] = None
    if req.incident_time:
        try:
            incident_dt = datetime.fromisoformat(req.incident_time.replace("Z", "+00:00"))
            if incident_dt.tzinfo is None:
                incident_dt = incident_dt.replace(tzinfo=VN_TZ)
        except Exception:
            try:
                incident_dt = datetime.fromtimestamp(int(req.incident_time) / 1000, tz=VN_TZ)
            except Exception:
                pass
    if incident_dt is None:
        incident_dt = now

    # Clamp window to policy max; compute time range centered on incident
    window_minutes = max(1, min(req.window_minutes, QUERY_POLICY["max_window_minutes"]))
    half    = timedelta(minutes=window_minutes // 2)
    t_from  = incident_dt - half
    t_to    = min(incident_dt + half, now)

    requested_time_range = TimeRangeResult(
        start=t_from.isoformat(),
        end=t_to.isoformat(),
    )

    # Load parquet data
    try:
        dfs = {
            "TRACKING_EVENT_V2":       read_dataframe(TRACKING_PATH),
            "ACCESS_LOG":              read_dataframe(ACCESS_PATH),
            "MOBILE_VITAL_EVENT_LOG":  read_dataframe(VITAL_PATH),
        }
    except Exception as ex:
        return InvestigateResponse(
            status="query_error",
            zalopay_id=req.zalopay_id,
            incident_time=incident_dt.isoformat(),
            window_minutes=window_minutes,
            requested_time_range=requested_time_range,
            actual_time_range=requested_time_range,
            warnings=[f"Failed to load data sources: {str(ex)[:200]}"],
        )

    # Generate and execute query plan
    plan = _generate_query_plan(req, incident_dt, t_from, t_to)

    counters: dict     = {"tracking": 0, "access": 0, "vital": 0}
    seen_keys: set     = set()
    all_events: list   = []
    tables_scanned     = set()
    rejected_steps     = []
    attempts_used      = 0
    warnings_list: list[str] = []

    def _add_events(new_events: list[EventItem]) -> None:
        for ev in new_events:
            # Natural key for deduplication
            nk = (
                ev.source,
                ev.ts,
                ev.raw.get("event_id", ""),
                ev.raw.get("page", ""),
                ev.raw.get("endpoint", ""),
            )
            if nk not in seen_keys:
                seen_keys.add(nk)
                all_events.append(ev)

    for step in plan.steps:
        if attempts_used >= QUERY_POLICY["max_query_attempts"]:
            warnings_list.append(f"Query budget exhausted after {attempts_used} attempts.")
            break

        is_valid, reason = _validate_step(step, now)
        if not is_valid:
            rejected_steps.append(step.id)
            warnings_list.append(f"Step {step.id} rejected: {reason}")
            continue

        step_events = _execute_step(step, dfs, counters)
        attempts_used += 1

        if step.table == "ALL":
            tables_scanned.update(["TRACKING_EVENT_V2", "ACCESS_LOG", "MOBILE_VITAL_EVENT_LOG"])
        else:
            tables_scanned.add(step.table)

        _add_events(step_events)

    # ── Date-shift fallback (spec: 0, -1, +1, -2, +2, -3, +3, -4, +4) ──────
    time_mismatch = False
    shift_days    = 0

    if not all_events:
        shift_order = [0, -1, 1, -2, 2, -3, 3, -4, 4]
        for day_offset in shift_order:
            if day_offset == 0:
                continue
            if abs(day_offset) > QUERY_POLICY["max_shift_days"]:
                continue
            if attempts_used >= QUERY_POLICY["max_query_attempts"]:
                warnings_list.append(
                    f"Query budget exhausted during date-shift fallback after {attempts_used} attempts."
                )
                break

            shifted_dt = incident_dt + timedelta(days=day_offset)
            if shifted_dt > now:
                continue

            sf     = shifted_dt - half
            st_end = min(shifted_dt + half, now)

            shift_step = QueryStep(
                id=f"shift_{day_offset:+d}d",
                table="ALL",
                reason=f"Date-shift fallback: {day_offset:+d} day(s) from incident time",
                filters=QueryFilters(
                    zalopay_id=req.zalopay_id,
                    time_range=QueryTimeRange(start=sf.isoformat(), end=st_end.isoformat()),
                ),
                limit=QUERY_POLICY["max_rows_per_query"],
            )

            is_valid, reason = _validate_step(shift_step, now)
            if not is_valid:
                continue

            shift_events = _execute_step(shift_step, dfs, counters)
            attempts_used += 1
            tables_scanned.update(["TRACKING_EVENT_V2", "ACCESS_LOG", "MOBILE_VITAL_EVENT_LOG"])

            if shift_events:
                _add_events(shift_events)
                time_mismatch = True
                shift_days    = day_offset
                warnings_list.append(
                    f"No data at reported time. Found data {day_offset:+d} day(s) from incident time."
                )
                break

    # ── Compile final results ─────────────────────────────────────────────────
    all_events.sort(key=lambda e: e.ts)

    tracking_events = [e for e in all_events if e.source == "tracking"]
    access_events   = [e for e in all_events if e.source == "access"]
    vital_events    = [e for e in all_events if e.source == "vital"]
    error_count     = sum(1 for e in all_events if e.severity in ("error", "fatal"))

    # Correlation basis
    correlation_basis = ["zalopay_id"]
    if any(e.correlation.trace_id for e in all_events):
        correlation_basis.append("traceID")
    else:
        correlation_basis.append("timestamp_proximity")
        if all_events:
            warnings_list.append(
                "No traceID or session_id was available. "
                "Events are correlated by timestamp proximity only."
            )

    # Actual time range
    if all_events:
        actual_time_range = TimeRangeResult(
            start=datetime.fromtimestamp(all_events[0].ts / 1000, tz=VN_TZ).isoformat(),
            end=datetime.fromtimestamp(all_events[-1].ts / 1000, tz=VN_TZ).isoformat(),
        )
    else:
        actual_time_range = requested_time_range

    # Status
    if not all_events and rejected_steps and not all_events:
        status = "policy_rejected"
    elif not all_events:
        status = "not_found"
    elif time_mismatch:
        status = "shifted_found"
    elif rejected_steps:
        status = "partial"
    else:
        status = "found"

    insight = _build_insight(all_events, req, time_mismatch, shift_days)

    return InvestigateResponse(
        status=status,
        zalopay_id=req.zalopay_id,
        incident_time=incident_dt.isoformat(),
        window_minutes=window_minutes,
        requested_time_range=requested_time_range,
        actual_time_range=actual_time_range,
        time_mismatch=time_mismatch,
        shift_days=shift_days,
        tracking_count=len(tracking_events),
        access_count=len(access_events),
        vital_count=len(vital_events),
        error_count=error_count,
        query_summary=QuerySummary(
            attempts_used=attempts_used,
            tables_scanned=sorted(tables_scanned),
            strategy="agent_planned",
            correlation_basis=correlation_basis,
        ),
        events=all_events,
        insight=insight,
        warnings=warnings_list,
    )
