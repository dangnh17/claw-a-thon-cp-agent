# Debug Investigator — Feature Spec

## Purpose

Debug Investigator helps support and engineering teams investigate user-reported bugs from Jira tickets without manually querying logs.

User pastes a ticket description → AI extracts investigation fields → agent plans safe log queries → backend executes approved queries → system returns structured timeline, statistics, and evidence-based insight.

The goal is to make the investigation agent effective, traceable, and safe.

The agent must prioritize:

* correctness
* evidence-based reasoning
* minimal hallucination
* clear uncertainty
* fast investigation workflow
* safe and bounded log queries

If evidence is insufficient, the system must explicitly say so.

---

## High-Level Flow

```text
User pastes ticket text
  → POST /api/debug/parse-ticket
      LLM extracts zalopay_id, incident_time, device, OS, app version, and error description

  → POST /api/debug/investigate
      Agent creates an investigation query plan
      Backend validates the plan against query policy
      Backend queries approved parquet data sources
      Backend normalizes events
      Agent may request additional approved queries based on intermediate evidence
      Backend stops when evidence is sufficient or query budget is exhausted
      LLM generates structured insight from evidence only

  → UI shows:
      parsed chips
      warning banners
      stats bar
      interactive timeline
      event detail panel
      session journey
      structured insight
```

---

## Data Sources

| Table                    | Parquet path                                 | Purpose                                                                         |
| ------------------------ | -------------------------------------------- | ------------------------------------------------------------------------------- |
| `TRACKING_EVENT_V2`      | `output/tracking_events_v2/records.parquet`  | User event chain: `event_id`, `previous_event_id`, `metadata`, `timestamp`      |
| `ACCESS_LOG`             | `output/access_log/records.parquet`          | Webview/page loads: `page`, `app`, `traceID`, `timeStamp` as epoch milliseconds |
| `MOBILE_VITAL_EVENT_LOG` | `output/mobile_vital_events/records.parquet` | Failed API calls: `endpoint`, `error_message`, `network_type`, `timestamp`      |

Backend must not import from or reference:

```text
client-log-visualizer/
```

That directory is out of scope for this repo.

---

## Module Boundaries

Do not edit:

```text
agent/app.py
frontend/loader.js
frontend/index.html
```

Backend module registration:

```text
agent/modules/__init__.py
```

Frontend module registration:

```text
frontend/modules/index.js
```

Frontend module file:

```text
frontend/modules/debug-investigator.js
```

Mapping files:

```text
agent/data/mapping/
```

---

## Time Handling

All parsed and returned timestamps must use ISO-8601 with timezone.

Default timezone:

```text
Asia/Ho_Chi_Minh
```

If the ticket does not include timezone information, interpret the incident time as `Asia/Ho_Chi_Minh`.

Example parsed time:

```json
{
  "incident_time": "2026-06-13T16:25:00+07:00",
  "timezone": "Asia/Ho_Chi_Minh"
}
```

---

## Window Semantics

`window_minutes` means total window size.

Example:

```json
{
  "incident_time": "2026-06-13T16:25:00+07:00",
  "window_minutes": 60
}
```

Backend searches:

```text
2026-06-13T15:55:00+07:00 → 2026-06-13T16:55:00+07:00
```

Do not interpret `window_minutes=60` as ±60 minutes.

---

## Agentic Query Strategy

The date-shift logic is the default fallback strategy, not the only investigation strategy.

The agent may propose query steps based on:

* parsed ticket fields
* intermediate query results
* missing data
* detected trace IDs
* failed endpoints
* event chain gaps
* timestamp proximity
* suspicious access or vital events

The agent is responsible for planning the investigation strategy.

The backend is responsible for safely executing only approved queries.

---

## Agent Responsibilities

The agent may decide:

* initial time window
* whether to expand or shrink the time window
* whether to shift incident date when no data is found
* which table to query first
* whether to query all tables or only selected tables
* whether to query by `zalopay_id`
* whether to follow `traceID`
* whether to follow `session_id`
* whether to inspect failed endpoints
* whether to follow event chains using `event_id` and `previous_event_id`
* whether there is enough evidence to stop
* which events are most relevant for insight
* which findings should be shown to the user

The agent must not execute raw SQL, Python code, or arbitrary expressions.

The agent must output structured query plans only.

---

## Backend Responsibilities

The backend must enforce:

* table allowlist
* filter allowlist
* maximum query attempts
* maximum time range per query
* maximum date shift
* no query past current time
* required scoped filter
* row limit per query
* timeout per query
* schema validation for every query plan
* safe conversion from structured query plan to actual parquet query
* no raw execution of agent-generated code

Backend must never execute:

```text
raw SQL generated by LLM
raw Python generated by LLM
eval(...)
exec(...)
arbitrary pandas/polars expressions generated by LLM
```

---

## Query Policy

Default query policy:

```json
{
  "allowed_tables": [
    "TRACKING_EVENT_V2",
    "ACCESS_LOG",
    "MOBILE_VITAL_EVENT_LOG"
  ],
  "max_query_attempts": 10,
  "default_window_minutes": 60,
  "max_window_minutes": 240,
  "max_shift_days": 4,
  "max_rows_per_query": 1000,
  "require_scoped_filter": true,
  "allowed_filter_keys": [
    "zalopay_id",
    "traceID",
    "session_id",
    "device_id",
    "endpoint",
    "event_id",
    "previous_event_id",
    "timestamp",
    "time_range"
  ]
}
```

A query plan must be rejected if it violates this policy.

---

## Structured Query Plan Schema

The agent must propose queries using structured JSON.

Example:

```json
{
  "goal": "Investigate blank screen issue around reported incident time",
  "steps": [
    {
      "id": "q1",
      "table": "ALL",
      "reason": "Find logs for reported user around incident time",
      "filters": {
        "zalopay_id": "240919000001944",
        "time_range": {
          "start": "2026-06-13T15:55:00+07:00",
          "end": "2026-06-13T16:55:00+07:00"
        }
      },
      "limit": 500
    }
  ],
  "stop_conditions": [
    "found_vital_error_near_incident",
    "found_tracking_journey_and_access_context",
    "query_budget_exhausted"
  ]
}
```

Allowed `table` values:

```text
ALL
TRACKING_EVENT_V2
ACCESS_LOG
MOBILE_VITAL_EVENT_LOG
```

---

## Query Step Schema

Each query step should follow this shape:

```json
{
  "id": "q1",
  "table": "MOBILE_VITAL_EVENT_LOG",
  "reason": "Check failed API calls near the reported incident time",
  "filters": {
    "zalopay_id": "240919000001944",
    "time_range": {
      "start": "2026-06-13T16:10:00+07:00",
      "end": "2026-06-13T16:40:00+07:00"
    },
    "endpoint_contains": "history"
  },
  "limit": 200
}
```

The backend must validate:

* table exists in allowlist
* filter keys are allowed
* time range is not too large
* time range is not in the future
* limit does not exceed policy
* query has at least one scoped filter

---

## Default Time Fallback Strategy

If no data is found in all 3 tables for the initial query, the agent may use the default date-shift strategy.

Offset order:

```text
0, -1, +1, -2, +2, -3, +3, -4, +4 days
```

Rules:

* Skip candidate windows whose start time is after current time.
* Stop at the first candidate where any of the 3 tables has data.
* If shifted data is found, return:

  * `status="shifted_found"`
  * `time_mismatch=true`
  * `shift_days`
  * `actual_time_range`
* If no data is found after all attempts, return:

  * `status="not_found"`
  * empty `events`
  * clear warning

---

## Mapping Files

Mapping files are located in:

```text
agent/data/mapping/
```

Do not reference:

```text
client-log-visualizer/
```

---

## `mapping-event.json`

Maps `event_id` to human-readable event name.

Example:

```json
{
  "01.3150.000": {
    "name": "INPUT_SCREEN.LOAD_SCREEN"
  }
}
```

---

## `mapping-screen-id.json`

Maps 4-digit screen code to screen name and description.

Example:

```json
{
  "3150": {
    "name": "Transfer",
    "description": "Transfer screen"
  }
}
```

---

## Event Mapping Fallback

For tracking events:

1. Try exact match in `mapping-event.json`.
2. If not found, parse screen code from event ID using:

```text
event_id.split(".")[1]
```

only if the event ID matches:

```text
^\d{2}\.\d{4}\.\d{3}$
```

3. Look up the screen code in `mapping-screen-id.json`.
4. If still not found, keep raw event ID and set:

```json
{
  "mapping_status": "unknown"
}
```

Example:

```text
event_id: 01.3150.000
screen_code: 3150
```

---

## API

# `POST /api/debug/parse-ticket`

## Input

```json
{
  "ticket_text": "<ticket content>"
}
```

## Output

```json
{
  "zalopay_id": "240919000001944",
  "incident_time": "2026-06-13T16:25:00+07:00",
  "timezone": "Asia/Ho_Chi_Minh",
  "device": "Vivo",
  "os_version": "Android 16",
  "app_version": "11.8.0",
  "error_description": "Blank screen on Transaction History and Payment Code",
  "confidence": "high",
  "missing_fields": [],
  "warnings": []
}
```

## Parse Rules

Required for investigation:

```text
zalopay_id
incident_time
```

Optional fields:

```text
device
os_version
app_version
error_description
```

Rules:

* Do not invent missing values.
* Normalize dates into ISO-8601 with timezone.
* If timezone is missing, use `Asia/Ho_Chi_Minh`.
* If required fields are missing, return `confidence="low"`.
* Missing required or useful fields must be included in `missing_fields`.
* Ambiguous fields must be included in `warnings`.
* Preserve original ticket text for investigation context.

Example missing field response:

```json
{
  "zalopay_id": null,
  "incident_time": null,
  "timezone": "Asia/Ho_Chi_Minh",
  "device": "Vivo",
  "os_version": null,
  "app_version": "11.8.0",
  "error_description": "Blank screen",
  "confidence": "low",
  "missing_fields": [
    "zalopay_id",
    "incident_time"
  ],
  "warnings": [
    "Cannot investigate without zalopay_id and incident_time"
  ]
}
```

---

# `POST /api/debug/investigate`

## Input

```json
{
  "zalopay_id": "240919000001944",
  "ticket_text": "...",
  "incident_time": "2026-06-13T16:25:00+07:00",
  "window_minutes": 60
}
```

## Output

```json
{
  "status": "found",
  "zalopay_id": "240919000001944",
  "incident_time": "2026-06-13T16:25:00+07:00",
  "window_minutes": 60,
  "requested_time_range": {
    "start": "2026-06-13T15:55:00+07:00",
    "end": "2026-06-13T16:55:00+07:00"
  },
  "actual_time_range": {
    "start": "2026-06-13T15:55:00+07:00",
    "end": "2026-06-13T16:55:00+07:00"
  },
  "time_mismatch": false,
  "shift_days": 0,
  "tracking_count": 12,
  "access_count": 3,
  "vital_count": 1,
  "error_count": 1,
  "query_summary": {
    "attempts_used": 2,
    "tables_scanned": [
      "TRACKING_EVENT_V2",
      "ACCESS_LOG",
      "MOBILE_VITAL_EVENT_LOG"
    ],
    "strategy": "agent_planned",
    "correlation_basis": [
      "zalopay_id",
      "timestamp_proximity",
      "traceID"
    ]
  },
  "events": [],
  "insight": {
    "summary": "...",
    "user_flow": "...",
    "likely_root_cause": "...",
    "confidence": "medium",
    "evidence": [],
    "recommendations": [],
    "unknowns": []
  },
  "warnings": []
}
```

---

## Investigation Status Values

Allowed values:

```text
found
shifted_found
not_found
partial
parse_error
policy_rejected
query_error
```

Meaning:

| Status            | Meaning                                                |
| ----------------- | ------------------------------------------------------ |
| `found`           | Data found in requested time range                     |
| `shifted_found`   | Data found after shifting incident date                |
| `not_found`       | No data found after all approved attempts              |
| `partial`         | Some tables succeeded, some failed or were unavailable |
| `parse_error`     | Required fields missing or invalid                     |
| `policy_rejected` | Agent query plan violated backend policy               |
| `query_error`     | Query execution failed unexpectedly                    |

---

## Normalized Event Schema

All events returned to UI must use a common schema.

```json
{
  "id": "tracking-0001",
  "source": "tracking",
  "timestamp": "2026-06-13T16:24:58.123+07:00",
  "title": "INPUT_SCREEN.LOAD_SCREEN",
  "subtitle": "Transaction History",
  "severity": "info",
  "mapping": {
    "event_name": "INPUT_SCREEN.LOAD_SCREEN",
    "screen_code": "3150",
    "screen_name": "Transaction History",
    "mapping_status": "exact"
  },
  "correlation": {
    "trace_id": "abc123",
    "session_id": null,
    "device_id": null
  },
  "raw": {}
}
```

---

## Event Source Values

Allowed `source` values:

```text
tracking
access
vital
```

---

## Event Severity Values

Allowed `severity` values:

```text
info
warning
error
fatal
unknown
```

---

## Source-Specific Raw Fields

### Tracking Event

```json
{
  "id": "tracking-0001",
  "source": "tracking",
  "timestamp": "2026-06-13T16:24:58.123+07:00",
  "title": "INPUT_SCREEN.LOAD_SCREEN",
  "subtitle": "Transaction History",
  "severity": "info",
  "raw": {
    "event_id": "01.3150.000",
    "previous_event_id": "01.3149.000",
    "metadata": {}
  }
}
```

### Access Log

```json
{
  "id": "access-0001",
  "source": "access",
  "timestamp": "2026-06-13T16:24:59.000+07:00",
  "title": "Webview page load",
  "subtitle": "/transaction-history",
  "severity": "info",
  "correlation": {
    "trace_id": "abc123"
  },
  "raw": {
    "page": "/transaction-history",
    "app": "ZaloPay",
    "traceID": "abc123",
    "timeStamp": 1781342699000,
    "status_code": 200
  }
}
```

### Mobile Vital Event

```json
{
  "id": "vital-0001",
  "source": "vital",
  "timestamp": "2026-06-13T16:25:01.000+07:00",
  "title": "Failed API call",
  "subtitle": "/api/transaction/history",
  "severity": "error",
  "raw": {
    "endpoint": "/api/transaction/history",
    "error_message": "timeout",
    "network_type": "4G",
    "timestamp": "2026-06-13T16:25:01+07:00"
  }
}
```

---

## Error Count Rule

`error_count` is the number of normalized events where:

```text
severity in ["error", "fatal"]
```

Default classification:

* Vital events are treated as `error` unless explicitly classified otherwise.
* Access logs are `error` if they contain HTTP status code `>= 400`.
* Tracking events are `error` only if metadata contains explicit error-like fields.
* Unknown or unmapped events should not automatically be considered errors.

---

## Correlation Rules

Backend should correlate records using the strongest available signal.

Priority order:

1. `traceID`
2. `session_id`
3. `device_id`
4. `app_version`
5. timestamp proximity
6. event chain via `event_id` and `previous_event_id`

If no direct correlation ID exists, the system must state that correlation is based on timestamp proximity only.

Example:

```json
{
  "correlation_basis": [
    "zalopay_id",
    "timestamp_proximity"
  ],
  "warnings": [
    "No traceID or session_id was available. Events are correlated by timestamp proximity only."
  ]
}
```

---

## Evidence Ranking

Backend or agent should rank suspicious events before generating insight.

Recommended signals:

* failed API call close to incident time
* access log error close to incident time
* tracking journey stops unexpectedly
* missing next event after screen load
* long gap between tracking events
* repeated API failure
* retry loop
* network type unavailable or unstable
* endpoint related to reported feature
* traceID connecting access and vital logs
* page or screen name matching ticket description

Each ranked finding should include evidence event IDs.

Example:

```json
{
  "finding": "Transaction history API failed near incident time",
  "confidence": "high",
  "evidence_event_ids": [
    "vital-0001",
    "access-0001"
  ]
}
```

---

## LLM Insight Rules

The LLM may only use:

* parsed ticket fields
* original ticket text
* normalized events
* query statistics
* mapping names
* ranked evidence

The LLM must not invent:

* API endpoints
* event names
* screen names
* device info
* network status
* root cause
* user actions not supported by logs
* timestamps not present in data

The insight must include:

```json
{
  "summary": "...",
  "user_flow": "...",
  "likely_root_cause": "...",
  "confidence": "low|medium|high",
  "evidence": [
    {
      "event_id": "vital-0001",
      "timestamp": "2026-06-13T16:25:01+07:00",
      "reason": "API failed near reported incident time"
    }
  ],
  "recommendations": [],
  "unknowns": []
}
```

If evidence is insufficient, the LLM must say:

```text
Insufficient evidence to determine root cause.
```

---

## Recommended Insight Output

Example:

```json
{
  "summary": "The user opened Transaction History around the reported time. A failed API call occurred shortly after the page load.",
  "user_flow": "The tracking journey indicates the user navigated to the Transaction History screen before the issue was reported.",
  "likely_root_cause": "The blank screen may be related to a failed transaction history API call.",
  "confidence": "medium",
  "evidence": [
    {
      "event_id": "tracking-0003",
      "timestamp": "2026-06-13T16:24:55+07:00",
      "reason": "User reached Transaction History screen."
    },
    {
      "event_id": "vital-0001",
      "timestamp": "2026-06-13T16:25:01+07:00",
      "reason": "Transaction history endpoint failed near the incident time."
    }
  ],
  "recommendations": [
    "Check backend logs for the failed transaction history endpoint around the same traceID.",
    "Verify whether the frontend handles empty or timeout response correctly.",
    "Compare with other users on the same app version and OS version."
  ],
  "unknowns": [
    "No screenshot or frontend rendering error was available.",
    "No direct frontend exception log was found."
  ]
}
```

---

## Privacy and Safety

The system may process user IDs and log metadata.

Rules:

* Do not log raw ticket text in production unless explicitly enabled.
* Mask or partially display sensitive user identifiers in UI if needed.
* Do not send unnecessary raw metadata to the LLM.
* Prefer summarized and normalized evidence for LLM calls.
* Keep raw logs available in detail panel only for authorized users.
* Avoid exposing unrelated user data.
* Never query logs without a scoped filter.

---

## Frontend UI

File:

```text
frontend/modules/debug-investigator.js
```

---

## Theme

Use light theme:

* white background
* light-gray panels
* subtle borders
* readable typography
* status colors for warning/error/success

---

## Main Input

Single textarea for pasted ticket text.

Actions:

```text
Điều tra (primary)
Làm mới (reset)
```

There is no inline status text next to the buttons. All loading states and errors are shown as banners below the input card.

---

## Parsed Chips

Display parsed result as chips after parse-ticket completes:

* UserID
* incident time
* device
* OS version
* app version
* confidence
* missing fields (if any)

Example:

```text
👤 240919000001944
🕐 2026-06-13 16:25
📱 Vivo
🖥 Android 16
📦 ZaloPay 11.8.0
Confidence: high
```

If `missing_fields` is non-empty, show each missing field as a red warning chip:

```text
⚠ Missing: incident_time
```

---

## Early Parse Validation

If `parse-ticket` response is missing `zalopay_id` or `incident_time`, the system must:

* Show an error banner immediately.
* Stop — do not call `/investigate`.

Example error banner:

```text
❌ Không thể điều tra — thiếu thông tin bắt buộc
Vui lòng bổ sung vào ticket: incident_time (ngày giờ xảy ra sự cố).
```

---

## Banners

All feedback (loading, error, warning, info) is displayed as banners below the input card.

Banner types:

| Type  | Color  | When                                                                        |
| ----- | ------ | --------------------------------------------------------------------------- |
| info  | blue   | While loading (parsing ticket, querying log)                                |
| warn  | yellow | `time_mismatch=true`, `status=partial`, timestamp-proximity correlation     |
| error | red    | `status=not_found`, `status=policy_rejected`, `status=query_error`, missing required fields |

Loading banners are replaced by result banners when the response arrives.

Show yellow warning banner when `time_mismatch=true`:

* requested time range
* actual time range

Example:

```text
⚠️ Không có log tại thời gian báo cáo — hiển thị log từ 1 ngày trước
Đã yêu cầu: 2026-06-13T15:55:00+07:00 → 2026-06-13T16:55:00+07:00
Dữ liệu thực tế: 2026-06-12T15:55:00+07:00 → 2026-06-12T16:55:00+07:00
```

Show error banner when `status=not_found`:

```text
🔍 Không tìm thấy log
Không có log nào cho user này trong khoảng thời gian yêu cầu.
```

Show error banner when `status=policy_rejected`:

```text
🛡️ Query bị từ chối bởi policy
Agent đã đề xuất một query nằm ngoài policy cho phép. Không có query không an toàn nào được thực thi.
```

Show info banner when correlation is timestamp-proximity only:

```text
ℹ️ Tương quan dựa trên thời gian
No traceID or session_id was available. Events are correlated by timestamp proximity only.
```

---

## Stats Bar

Show exactly three primary cards:

* Tracking count
* Access count
* Vital count

No additional optional cards (error count, query attempts, confidence, time mismatch) are shown.

---

## Timeline Tab

Use:

```text
vis-timeline
```

Groups:

```text
Tracking
Access
Vital
```

Timeline item type:

```text
box
```

Required features:

* filter by source
* full-text search
* zoom in
* zoom out
* fit button
* Ctrl + scroll to zoom
* click event to open detail panel
* visual severity indicator
* highlight selected event
* preserve current zoom when filtering if possible

---

## Detail Panel

A slide-in panel on the right side.

Common fields:

* timestamp
* source
* title
* subtitle
* severity
* correlation fields
* raw JSON

Tracking-specific fields:

* event_id
* previous_event_id
* mapped event name
* screen code
* screen name
* mapping status
* metadata

Access-specific fields:

* page
* app
* traceID
* timeStamp
* status code if available

Vital-specific fields:

* endpoint
* error_message
* network_type
* timestamp

---

## Session Journey

For tracking events, the detail panel must also render Session Journey.

Session Journey shows:

* full ordered list of tracking events
* mapped event names
* screen names when available
* timestamp for each event
* clickable item to navigate to selected event in timeline
* current event highlighted

Ordering:

```text
timestamp ascending
```

If `previous_event_id` chain is available, use it to detect possible breaks or missing events.

---

## Insight Tab

Render structured insight sections in order:

* summary (plain text, no confidence badge)
* user flow
* likely root cause
* evidence (clickable items)
* recommendations
* unknowns
* query summary (attempts, tables scanned, strategy, correlation basis)

Confidence is available in the response but is not displayed inline with the summary.

Evidence items should be clickable when they reference event IDs.

Clicking an evidence item switches to the Timeline tab, selects the corresponding event, and opens its detail panel.

---

## Demo Ticket

```text
Mô tả lỗi: truy cập mục Lịch sử và Mã thanh toán bị trắng màn hình
Thời gian thực hiện: 16:25 13/6/2026
UserID: 240919000001944
Tên thiết bị: Vivo
Phiên bản hệ điều hành: Android 16
Phiên bản ZaloPay: 11.8.0
```

---

## Expected Parse Result For Demo

```json
{
  "zalopay_id": "240919000001944",
  "incident_time": "2026-06-13T16:25:00+07:00",
  "timezone": "Asia/Ho_Chi_Minh",
  "device": "Vivo",
  "os_version": "Android 16",
  "app_version": "11.8.0",
  "error_description": "Blank screen when accessing Transaction History and Payment Code",
  "confidence": "high",
  "missing_fields": [],
  "warnings": []
}
```

---

## Demo Data Note

Current parquet data is demo only and contains user:

```text
240919000001944
```

with data around:

```text
2026-06-13
```

To use real data without code changes, real data must follow the same:

* schema
* timestamp unit conventions
* timezone assumptions
* parquet path layout
* table naming expectations

---

## Error Handling

### Missing Required Fields

If `zalopay_id` or `incident_time` is missing:

```json
{
  "status": "parse_error",
  "events": [],
  "warnings": [
    "Cannot investigate without zalopay_id and incident_time."
  ]
}
```

### No Data Found

If no data is found after all allowed attempts:

```json
{
  "status": "not_found",
  "events": [],
  "tracking_count": 0,
  "access_count": 0,
  "vital_count": 0,
  "error_count": 0,
  "insight": {
    "summary": "No logs were found for this user in the requested or fallback time windows.",
    "likely_root_cause": "Insufficient evidence to determine root cause.",
    "confidence": "low",
    "evidence": [],
    "recommendations": [
      "Verify the reported incident time and user ID.",
      "Check whether the relevant logs were ingested successfully."
    ],
    "unknowns": [
      "No tracking, access, or vital logs were available."
    ]
  }
}
```

### Partial Data

If some tables fail but others succeed:

```json
{
  "status": "partial",
  "warnings": [
    "ACCESS_LOG could not be queried. Insight is based on tracking and vital logs only."
  ]
}
```

---

## Acceptance Criteria

1. User can paste the demo ticket and receive parsed fields.
2. `parse-ticket` extracts `zalopay_id`, `incident_time`, device, OS version, app version, and error description.
3. Parsed `incident_time` is returned as ISO-8601 with timezone.
4. Investigation starts with a safe scoped query plan.
5. Agent query plan is represented as structured JSON.
6. Backend validates every agent-proposed query against policy.
7. Backend never executes raw LLM-generated SQL, Python, or arbitrary expressions.
8. Backend queries only allowlisted parquet sources.
9. Backend enforces maximum query attempts.
10. Backend enforces maximum time range.
11. Backend enforces maximum shifted days.
12. Backend does not query past current time.
13. Backend enforces row limits.
14. Timeline displays normalized events from all available sources.
15. Tracking events are mapped using `mapping-event.json`.
16. Unknown tracking events fallback to `mapping-screen-id.json`.
17. Unknown mapping is handled gracefully.
18. If no data exists at requested time, agent may apply shifted-date fallback.
19. If shifted data is used, UI shows time mismatch warning.
20. Response includes `requested_time_range`.
21. Response includes `actual_time_range`.
22. Response includes `shift_days`.
23. Response includes `status`.
24. Response includes query summary.
25. Response includes counts for tracking, access, vital, and errors.
26. UI stats bar reflects returned counts.
27. UI timeline supports source filtering.
28. UI timeline supports full-text search.
29. UI timeline supports zoom controls.
30. Clicking an event opens detail panel.
31. Detail panel shows common event fields.
32. Detail panel shows source-specific fields.
33. Tracking detail panel shows Session Journey.
34. Insight cites evidence from returned events.
35. Insight does not invent unsupported root cause.
36. If evidence is insufficient, insight says so clearly.
37. If no logs are found, system returns `status="not_found"`.
38. Frontend does not require edits to `frontend/loader.js` or `frontend/index.html`.
49. Backend does not require edits to `agent/app.py`.

---

## Non-Goals

This feature does not aim to:

* replace full observability tools
* query arbitrary production databases
* execute free-form SQL from the agent
* debug without user/time scope
* guarantee root cause when logs are insufficient
* modify existing app shell files

---

## Summary

Debug Investigator should use a hybrid architecture:

```text
Hard-coded backend safety policy
+
Agentic query planning
+
Safe parquet query executor
+
Normalized event model
+
Evidence-ranked investigation
+
Guardrailed LLM insight
```

The agent should decide the investigation strategy, but the backend must control what is safe to execute.

This gives the system flexibility for real-world debugging while preventing unsafe or expensive queries.