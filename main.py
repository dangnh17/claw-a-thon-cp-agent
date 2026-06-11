import os
import asyncio
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from greennode_agentbase import (
    GreenNodeAgentBaseApp,
    PingStatus,
    RequestContext,
)

load_dotenv()

app = GreenNodeAgentBaseApp()
UI_DIR = Path(__file__).parent / "ui"
SKILLS_DIR = Path(__file__).parent / ".agents" / "skills"

LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
UI_ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.environ.get(
        "UI_ALLOWED_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000",
    ).split(",")
    if origin.strip()
}

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


def load_skills() -> str:
    blocks: list[str] = []

    for skill_file in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        skill_name = skill_file.parent.name
        skill_body = skill_file.read_text(encoding="utf-8").strip()
        blocks.append(f"<skill name=\"{skill_name}\">\n{skill_body}\n</skill>")

    return "\n\n".join(blocks)


SKILLS_PROMPT = load_skills()
SYSTEM_PROMPT = f"""You have access to the following skills.

Use the most relevant skill when it matches the user's request.

{SKILLS_PROMPT}
"""

agent = create_agent(llm, tools=[get_current_time], system_prompt=SYSTEM_PROMPT)

class PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not self._skip_depth:
            self.parts.append(text)


def validate_https_url(raw_url: str) -> tuple[bool, str]:
    url = raw_url.strip()
    if not url:
        return False, "Vui lòng nhập đường link HTTPS."
    if any(char.isspace() for char in url):
        return False, "Đường link không được chứa khoảng trắng."

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False, "Chỉ hỗ trợ đường link bắt đầu bằng https://."
    if not parsed.netloc or not parsed.hostname:
        return False, "Đường link không hợp lệ."
    return True, url


def extract_page_text(html: str) -> str:
    parser = PageTextParser()
    parser.feed(html)
    return " ".join(parser.parts)


def summarize_https_url(url: str) -> str:
    is_valid, value = validate_https_url(url)
    if not is_valid:
        raise ValueError(value)

    response = httpx.get(
        value,
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "http-page-summary/1.0"},
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise ValueError("Link này không trả về nội dung HTML hoặc văn bản có thể tóm tắt.")

    page_text = extract_page_text(response.text)[:12000]
    if not page_text:
        raise ValueError("Không đọc được nội dung văn bản từ link này.")

    prompt = (
        "Dùng skill http-page-summary để tóm tắt đúng một trang web sau bằng tiếng Việt có dấu.\n\n"
        f"URL: {value}\n\n"
        f"Nội dung trang:\n{page_text}"
    )
    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    return result["messages"][-1].content


def cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if not origin or origin not in UI_ALLOWED_ORIGINS:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Vary": "Origin",
    }


async def homepage(request: Request) -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


async def ui_app_js(request: Request) -> FileResponse:
    return FileResponse(UI_DIR / "app.js", media_type="text/javascript")


async def ui_styles(request: Request) -> FileResponse:
    return FileResponse(UI_DIR / "styles.css", media_type="text/css")


async def summary_endpoint(request: Request) -> JSONResponse:
    headers = cors_headers(request)
    try:
        payload = await request.json()
        summary = await asyncio.to_thread(summarize_https_url, payload.get("url", ""))
        return JSONResponse({"status": "success", "summary": summary}, headers=headers)
    except ValueError as error:
        return JSONResponse({"status": "error", "error": str(error)}, status_code=400, headers=headers)
    except httpx.HTTPStatusError as error:
        return JSONResponse(
            {"status": "error", "error": f"Không thể đọc link này: HTTP {error.response.status_code}."},
            status_code=400,
            headers=headers,
        )
    except httpx.HTTPError:
        return JSONResponse(
            {"status": "error", "error": "Không thể kết nối tới link này."},
            status_code=400,
            headers=headers,
        )
    except Exception:
        return JSONResponse(
            {"status": "error", "error": "Có lỗi khi tạo tóm tắt."},
            status_code=500,
            headers=headers,
        )


async def summary_options(request: Request) -> JSONResponse:
    return JSONResponse({}, status_code=204, headers=cors_headers(request))


app.add_route("/", homepage, methods=["GET"])
app.add_route("/app.js", ui_app_js, methods=["GET"])
app.add_route("/styles.css", ui_styles, methods=["GET"])
app.add_route("/summary", summary_endpoint, methods=["POST"])
app.add_route("/summary", summary_options, methods=["OPTIONS"])


@app.entrypoint
def handler(payload: dict, context: RequestContext) -> dict:
    if payload.get("url"):
        summary = summarize_https_url(payload["url"])
        return {
            "status": "success",
            "response": summary,
            "timestamp": datetime.now().isoformat(),
            "session_id": context.session_id,
        }

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
