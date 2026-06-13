# Agent Template — Dynamic Module Architecture

---

## Architecture

```
Browser
  └── HTTP (port 8080)
        └── FastAPI (agent/app.py)
              ├── /api/data/*        ← Shared data management
              ├── /api/feature-a/*   ← Member 1
              ├── /api/feature-b/*   ← Member 2
              └── /api/feature-c/*   ← Member 3

Data: output/**/*.parquet  (PyArrow + Pandas)
LLM:  agent/llm_client.py  (OpenAI-compatible MaaS)
```

---

## File Structure

```
├── CLAUDE.md                   # Instructions for AI agents
├── run_agent.py                # Entry point
├── .env.example
├── requirements.txt
│
├── agent/
│   ├── app.py                  # ⛔ DO NOT EDIT — auto-mounts routers
│   ├── llm_client.py           # Shared LLM client
│   ├── modules/
│   │   ├── __init__.py         # ✏️ Add 1 line per feature
│   │   ├── feature_a.py        # Template — copy to add feature
│   │   └── data_ingest.py      # ⛔ Shared data API
│   └── data/
│       └── store.py            # ⛔ Parquet helpers
│
├── frontend/
│   ├── index.html              # ⛔ DO NOT EDIT
│   ├── loader.js               # ⛔ DO NOT EDIT
│   ├── style.css               # Shared styles
│   └── modules/
│       ├── index.js            # ✏️ Add 1 line per feature
│       ├── feature-a.js        # Template — copy to add feature
│       └── data-ingest.js      # ⛔ Shared data UI
│
└── output/                     # Parquet files (gitignored)
```

---

## Adding a New Feature (per-member workflow)

### Backend

```bash
cp agent/modules/feature_a.py agent/modules/feature_b.py
```

1. Change `prefix="/api/feature-b"` and `DATA_PATH`
2. Register in `agent/modules/__init__.py`:

```python
from agent.modules import feature_b   # +1 line
# inside get_all_routers():
feature_b.router,                     # +1 line
```

### Frontend

```bash
cp frontend/modules/feature-a.js frontend/modules/feature-b.js
```

1. Change `id`, `label`, `icon`, `API_PREFIX`
2. Register in `frontend/modules/index.js`:

```js
import featureB from "./feature-b.js";   // +1 line
featureB,                                // +1 line in the array
```

The new tab appears automatically. **Do not touch `index.html`, `loader.js`, or `app.py`.**

---

## Conflict Risk

| File | Who edits | Risk |
|------|-----------|------|
| `agent/modules/feature_x.py` | 1 person | None |
| `frontend/modules/feature-x.js` | 1 person | None |
| `agent/modules/__init__.py` | Whole team | **Low** — each adds 1 line |
| `frontend/modules/index.js` | Whole team | **Low** — each adds 1 line |
| `agent/app.py`, `loader.js`, `index.html` | Nobody | None |

If a conflict occurs: keep all existing lines and add yours.

---

## Data Layer (Parquet)

Always use **absolute paths** (relative paths break when the server is started from a different CWD):

```python
import os
from agent.data.store import append_record, read_records

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DATA_PATH = os.path.join(_BASE_DIR, "output", "feature_a", "results.parquet")

append_record(DATA_PATH, {"score": 4.2, "label": "good"})
rows = read_records(DATA_PATH)
```

**HTTP API for agents:**

```
GET  /api/data/datasets
GET  /api/data/schema?dataset=feature_a/results.parquet
GET  /api/data/preview?dataset=feature_a/results.parquet&limit=20
POST /api/data/ingest   {"dataset": "...", "records": [...], "mode": "append"}
POST /api/data/upload   (multipart .parquet file)
```

---

## Docker

### Docker Compose

```bash
cp .env.example .env   # fill in API key
docker compose up --build
```

Open `http://localhost:8080`. Parquet data is persisted to `./output/` across restarts.

```bash
docker compose up --build -d   # run in background
docker compose logs -f          # follow logs
docker compose down             # stop
```

---

## Run (local)

```bash
cp .env.example .env   # fill in API key
pip install -r requirements.txt
python run_agent.py
```

Open `http://localhost:8080` — the **Data** tab lets you upload and inspect datasets.

---

## Deploy to GreenNode AgentBase (agent instructions)

> **For AI agents**: Use the `/agentbase-deploy` skill for this workflow. Read `.claude/skills/agentbase-deploy/SKILL.md` before executing any step.

The production runtime is already provisioned:

```
Runtime ID: runtime-1340b598-f28c-4ec9-84b6-8d958772d544
Console:    https://aiplatform.console.vngcloud.vn/agent-runtime?tab=runtime
```

Each redeploy is an **update** (not create). Every step must be executed fresh — no Docker layer cache.

---

## LLM Client

```python
from agent.llm_client import call_llm

text = call_llm("Your prompt here", max_tokens=1000)
```

Configured via `.env`: `LLM_MODEL`, `LLM_BASE_URL`, `AI_PLATFORM_API_KEY`.
