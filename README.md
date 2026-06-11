# http-page-summary

A GreenNode AgentBase agent.

## Prerequisites

- Python 3.10+
- A GreenNode IAM Service Account: https://iam.console.vngcloud.vn/service-accounts

## Setup

1. Create and activate a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Configure credentials for local development:

   ```bash
   cp .env.example .env
   ```

   Then edit `.env` with your credentials and LLM provider settings, or edit `.greennode.json` with your GreenNode IAM `client_id` and `client_secret`.

   When deployed on AgentBase Runtime, IAM credentials and agent identity are managed by the runtime system and injected automatically.

## Configure LLM

This project uses any OpenAI-compatible LLM provider. Set the following in `.env`:

```bash
LLM_API_KEY=your-api-key
LLM_BASE_URL=your-provider-base-url
LLM_MODEL=your-model-name
```

Provider examples:

- GreenNode AIP: use `/agentbase-llm` to get an API key and set `LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1`
- OpenAI: set `LLM_BASE_URL=https://api.openai.com/v1`
- Ollama: set `LLM_BASE_URL=http://localhost:11434/v1`

For production, use `/agentbase-identity` to store API keys on the platform and inject them at runtime.

## Run Locally

```bash
python3 main.py
```

The agent starts on `http://127.0.0.1:8080`.

Open the UI in your browser:

```text
http://127.0.0.1:8080/
```

The UI is served by the same backend process and calls `/summary` on the same origin.

There is also a standalone UI repo at `../http-page-summary-ui` if you want to develop or deploy the UI separately later.

If a separately deployed UI calls this backend from another origin, set the backend CORS allowlist:

```bash
UI_ALLOWED_ORIGINS=http://127.0.0.1:5173,https://your-ui-domain.example python3 main.py
```

Test it:

```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, agent!"}'
```

Summarize a page through the API:

```bash
curl -X POST http://127.0.0.1:8080/summary \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

Health check:

```bash
curl http://127.0.0.1:8080/health
```

## Deploy to AgentBase Runtime

Use `/agentbase-deploy` to build, push, and deploy this agent to AgentBase Runtime.

Use `/agentbase-memory` later if the agent needs conversation memory.

Use `/agentbase-identity` to register an agent identity or store API keys securely on the platform.

## Project Structure

- `main.py` - Agent entrypoint, summary API, and health check
- `ui/` - Static UI files served by the backend on the same port
- `Dockerfile` - Container image definition
- `requirements.txt` - Python dependencies
- `.greennode.json` - AgentBase configuration
- `.env.example` - Environment variable template
