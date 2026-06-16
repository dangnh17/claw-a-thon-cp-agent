# CP Agent

🇻🇳 [Xem bản Tiếng Việt](README.vi.md)

> An AI-powered tool that automatically analyzes Event Logs, reconstructs User Journeys, pinpoints exactly where the funnel breaks and distinguishes UI-side (device) failures from Network/API (system) failures.

---

## Demo

[![Watch the demo](https://img.shields.io/badge/▶%20Watch%20Demo-blue?style=for-the-badge)](https://vngms-my.sharepoint.com/:v:/g/personal/duyhv3_vng_com_vn/IQDNBH7_WhR6TYVlG7uP4veMAZXS6WLoglyFmU0ZKTPMyO8)

## Problem

Event data is large, unstructured, and spread across multiple sources — making it hard for any team to extract actionable insights without significant manual effort:

1. **No clear picture of what happened.** Raw event logs are unordered and noisy; reconstructing a user's journey or pinpointing a failure requires filtering hundreds of events by hand.
2. **Failure causes are hard to distinguish.** The same symptom can stem from a client-side crash or a server-side timeout — different root causes that require different fixes, but look identical on the surface.
3. **No funnel visibility.** Teams lack a quick way to see where users drop off across funnel steps or compare conversion across time periods and segments.
4. **Insights stay locked in raw data.** Behavioral patterns and common failure paths exist in the data but are never surfaced — because extracting them manually doesn't scale.
5. **Non-technical staff are blocked.** CS/Ops cannot self-serve and must wait for Dev, slowing customer response and inflating MTTR.

---

## Users

| Who | How they use it |
|-----|----------------|
| **CS / Ops** | Paste error description + JSON → get instant diagnosis and the exact broken step to respond to customers. |
| **Dev / QC** | Instantly scope failures (UI or Network/API) without reading the full log manually. |
| **Product Owner** | View Success Rate and failure touchpoints per feature to make timely optimization decisions. |

---

## Solution

The agent covers three capabilities:

**Debug Investigator** — Paste a Jira ticket; the LLM extracts data, queries event sources, and classifies the failure. Output includes a timestamped timeline, evidence quotes, and recommended actions.

**Funnel Analysis** — Define funnel steps by event ID or prefix; the agent calculates user counts, drop-off rates, and conversion at each step, then generates an LLM-written analysis of the weakest point.

**Journey Insight** — Runs a 5-step pipeline over raw tracking data to mine natural event-chain patterns, surface behavioral insights across user segments and time windows, and produce a Markdown report with a visual summary.

---

## Architecture

```
Browser (frontend/)
  │  ← Paste Jira ticket, define funnel steps, select time window
  │  ← View timeline, failure classification, funnel drop-off, journey report
  │
  └── HTTP (port 8080) ──→ FastAPI (agent/app.py)
                                ├── /api/debug/*            → Debug Investigator
                                ├── /api/funnel-analysis/*  → Funnel Analysis
                                └── /api/journey-insight/*  → Journey Insight

Data store: output/**/*.parquet   (PyArrow)
LLM:        agent/llm_client.py   (GreenNode MaaS — OpenAI-compatible)
```

---

## How to Run

### Prerequisites

- Python 3.10+
- GreenNode MaaS API key (`AI_PLATFORM_API_KEY`)

### 1. Install

```bash
git clone <repo-url>
cd claw-a-thon-cp-agent
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in the API key in `.env`:

```env
AI_PLATFORM_API_KEY=your-api-key-here
LLM_MODEL=google/gemma-4-31b-it
LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1
```

### 3. Start

```bash
python run_agent.py
```

Open `http://localhost:8080` — feature tabs appear automatically.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

```bash
docker compose up --build -d   # background
docker compose logs -f          # follow logs
docker compose down             # stop
```

---

## LLM Client

```python
from agent.llm_client import call_llm

text = call_llm("Analyze the following Event Log...", max_tokens=2000)
```

Configured via `.env`: `LLM_MODEL`, `LLM_BASE_URL`, `AI_PLATFORM_API_KEY`.

---

## File Structure

```
├── Dockerfile
├── docker-compose.yml
├── run_agent.py
├── .env.example
├── requirements.txt
│
├── agent/
│   ├── app.py
│   ├── llm_client.py
│   ├── modules/
│   │   ├── __init__.py
│   │   ├── data_ingest.py
│   │   ├── debug_investigator.py
│   │   ├── feature_a.py
│   │   ├── funnel_analysis.py
│   │   └── journey_insight.py
│   ├── pipeline/
│   │   └── journey/
│   │       ├── __init__.py
│   │       ├── step1_event_meaning.py
│   │       ├── step2b_natural_chain_mining.py
│   │       ├── step3_insight_candidates.py
│   │       ├── step4_report.py
│   │       └── step5_visual_summary.py
│   └── data/
│       └── store.py
│
├── frontend/
│   ├── index.html
│   ├── loader.js
│   ├── style.css
│   └── modules/
│       ├── index.js
│       ├── data-ingest.js
│       ├── debug-investigator.js
│       ├── feature-a.js
│       ├── funnel-analysis.js
│       └── journey-insight.js
│
└── output/
```
