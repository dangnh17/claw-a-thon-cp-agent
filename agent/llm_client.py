"""
LLM client — wraps GreenNode MaaS (OpenAI-compatible API).

Swap the base_url + model to use any OpenAI-compatible endpoint.
"""
import os
from openai import OpenAI

MAAS_BASE_URL = "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"
DEFAULT_MODEL  = "google/gemma-4-31b-it"


def get_client() -> OpenAI:
    api_key = os.getenv("AI_PLATFORM_API_KEY")
    if not api_key:
        raise RuntimeError("AI_PLATFORM_API_KEY is not set")
    return OpenAI(
        base_url=os.getenv("LLM_BASE_URL", MAAS_BASE_URL),
        api_key=api_key,
    )


def call_llm(prompt: str, *, max_tokens: int = 2000, timeout: int = 120) -> str:
    """Send a single-turn prompt and return the text response."""
    client = get_client()
    model  = os.getenv("LLM_MODEL", DEFAULT_MODEL)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=1,
        top_p=0.7,
        presence_penalty=0,
        timeout=timeout,
    )
    return response.choices[0].message.content.strip()
