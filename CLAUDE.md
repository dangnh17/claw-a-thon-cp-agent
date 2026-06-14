# CLAUDE.md — Agent Instructions

This document tells Claude (and any AI agent) exactly how this codebase works so it can add features, ingest data, and extend the system without breaking teammates' work.

---

## Architecture Overview

```
Browser
  └── HTTP (port 8080)
        └── FastAPI  (agent/app.py)
              ├── /api/feature-a/*     ← member 1
              ├── /api/feature-b/*     ← member 2
              └── /api/feature-c/*    ← member 3

Data store: output/**/*.parquet       (Parquet via PyArrow — internal only)
LLM:        agent/llm_client.py       (OpenAI-compatible MaaS)
```

> `/api/data/*` is **not public**. Backend modules access data directly via `agent.data.store`. Manual insertion uses `scripts/insert_data.py`.

**Two files are auto-registries** — team members add exactly one line each:
- `agent/modules/__init__.py` — backend router registration
- `frontend/modules/index.js` — frontend tab registration

**Never edit:** `agent/app.py`, `frontend/loader.js`, `frontend/index.html`

---

## How to Add a Feature

### 1. Backend — copy the template

```bash
cp agent/modules/feature_a.py agent/modules/feature_b.py
```

Edit `feature_b.py`:
- Change `prefix="/api/feature-b"` and `tags=["feature-b"]`
- Change DATA_PATH (use the absolute-path pattern from the template):
  ```python
  _BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
  DATA_PATH = os.path.join(_BASE_DIR, "output", "feature_b", "records.parquet")
  ```
- Implement your logic in `run()` and `history()`

Register in `agent/modules/__init__.py` (add **2 lines**):
```python
from agent.modules import feature_b   # add this line

def get_all_routers():
    return [
        feature_a.router,
        feature_b.router,   # add this line
    ]
```

### 2. Frontend — copy the template

```bash
cp frontend/modules/feature-a.js frontend/modules/feature-b.js
```

Edit `feature-b.js`:
- Change `id: "feature-b"`, `label: "Feature B"`, `icon: "🧪"`
- Change `const API_PREFIX = "/api/feature-b"`
- Implement `render()` — build your UI inside `container`

Register in `frontend/modules/index.js` (add **2 lines**):
```js
import featureB from "./feature-b.js";   // add this line

export default [
  featureA,
  featureB,   // add this line
];
```

---

## Data Layer (Parquet)

### Python API

Always use **absolute paths** — relative paths break when the server is started from a different working directory. Use the same `_BASE_DIR` pattern as `feature_a.py`:

```python
import os
from agent.data.store import append_record, append_records, read_records, read_dataframe, get_schema

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_PATH = os.path.join(_BASE_DIR, "output", "feature_b", "results.parquet")

# Write one record
append_record(DATA_PATH, {"score": 4.2, "label": "good", "ts": "2026-01-01"})

# Write many records
append_records(DATA_PATH, [{"score": 3.1}, {"score": 4.8}])

# Read all rows as list of dicts
rows = read_records(DATA_PATH)

# Read as DataFrame
df = read_dataframe(DATA_PATH)

# Get column types
schema = get_schema(DATA_PATH)
# → {"score": "double", "label": "string", ...}
```

Each feature MUST use its own subdirectory: `output/feature_x/`.

### Manual insertion (CLI — for human operators)

`/api/data/*` is **not a public endpoint**. To insert data manually, use the CLI script:

```bash
# Append from JSON file (safe — does not delete existing data)
python scripts/insert_data.py --dataset feature_b/results --file /path/to/data.json

# Append inline
python scripts/insert_data.py --dataset feature_b/results \
    --records '[{"score": 4.2, "label": "good"}]'

# Overwrite (replaces all existing data)
python scripts/insert_data.py --dataset feature_b/results \
    --mode overwrite --file /path/to/data.json
```

---

## LLM Client

```python
from agent.llm_client import call_llm

text = call_llm("Your prompt here", max_tokens=1000)
```

Model and base URL are set via `.env` (`LLM_MODEL`, `LLM_BASE_URL`, `AI_PLATFORM_API_KEY`).

---

## Conflict Avoidance Rules

| File | Owner | Risk |
|------|-------|------|
| `agent/modules/feature_x.py` | 1 person | None |
| `frontend/modules/feature-x.js` | 1 person | None |
| `agent/modules/__init__.py` | Whole team | Low — each adds 1 line |
| `frontend/modules/index.js` | Whole team | Low — each adds 1 line |
| `agent/data/store.py` | Nobody edits | None |
| `agent/app.py`, `loader.js`, `index.html` | Nobody edits | None |

If you get a git conflict in `__init__.py` or `index.js`, the fix is always: keep all existing lines, add yours.

---

## Deploying to Production (AgentBase)

When the user says anything like **"deploy code"**, **"redeploy production"**, **"ship it"**, or **"update to production"**:

1. Read `.claude/skills/agentbase-deploy/SKILL.md` first.
2. Follow the **"Redeploy to GreenNode AgentBase"** section in `README.md` exactly.

Key rules:
- Always use `runtime.sh update` — the runtime already exists (`runtime-1340b598-f28c-4ec9-84b6-8d958772d544`), never `create`.
- Always build with `--no-cache --platform linux/amd64`.
- Always use `--from-cr` for registry auth — no credentials file needed.

---

## Running

Requires **Python 3.10+**.

```bash
cp .env.example .env   # fill in API key
pip install -r requirements.txt
python run_agent.py
```

Open `http://localhost:8080` — tabs auto-appear for each registered feature.

---

## File Structure

```
agent/
  app.py              # DO NOT EDIT — auto-mounts routers
  llm_client.py       # Shared LLM wrapper
  modules/
    __init__.py       # Registry — add 1 line per feature
    feature_a.py      # Template — copy to add your feature
    data_ingest.py    # Internal only — not mounted as public API
  data/
    store.py          # Parquet helpers (DO NOT EDIT)

scripts/
  insert_data.py      # Manual data insertion (CLI)

frontend/
  index.html          # DO NOT EDIT
  loader.js           # DO NOT EDIT
  style.css           # Shared CSS — OK to extend
  modules/
    index.js          # Registry — add 1 line per feature
    feature-a.js      # Template — copy to add your feature
    data-ingest.js    # (not registered — internal only)

output/               # Parquet files — gitignored
  feature_a/
    records.parquet
  feature_b/
    records.parquet
```
