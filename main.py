import os
from datetime import datetime

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from greennode_agentbase import (
    GreenNodeAgentBaseApp,
    PingStatus,
    RequestContext,
)

load_dotenv()

app = GreenNodeAgentBaseApp()

LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

if not LLM_MODEL or not LLM_BASE_URL or not LLM_API_KEY:
    raise ValueError(
        "LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY environment variables are required. "
        "Set them in your .env file or use /agentbase-llm to get a platform API key."
    )

llm = ChatOpenAI(
    model=LLM_MODEL,
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)


@tool
def get_current_time() -> str:
    """Get the current date and time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


agent = create_agent(llm, tools=[get_current_time])


@app.entrypoint
def handler(payload: dict, context: RequestContext) -> dict:
    message = payload.get("message", "Hello")

    result = agent.invoke({"messages": [{"role": "user", "content": message}]})
    ai_message = result["messages"][-1]

    return {
        "status": "success",
        "response": ai_message.content,
        "timestamp": datetime.now().isoformat(),
        "session_id": context.session_id,
    }


@app.ping
def health_check() -> PingStatus:
    return PingStatus.HEALTHY


if __name__ == "__main__":
    app.run(port=8080, host="0.0.0.0")
