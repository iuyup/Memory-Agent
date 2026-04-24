import json
import re
from abc import ABC, abstractmethod

import anthropic
import openai
from app.config import settings


def _safe_parse_json(raw: str) -> dict:
    """Parse JSON with resilience against common LLM output issues."""
    raw = raw.strip()
    # strip code fence
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 尝试找到第一个 { 到最后一个 } 的子串
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 2048
    ) -> str:
        ...

    @abstractmethod
    async def extract_json(
        self, system_prompt: str, user_content: str, max_tokens: int = 1024
    ) -> dict:
        ...


class DeepSeekProvider(LLMProvider):
    def __init__(self):
        self.client = openai.AsyncOpenAI(
            base_url="https://api.deepseek.com",
            api_key=settings.DEEPSEEK_API_KEY,
        )
        self.model = settings.DEEPSEEK_CHAT_MODEL

    async def chat(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 2048
    ) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    async def extract_json(
        self, system_prompt: str, user_content: str, max_tokens: int = 1024
    ) -> dict:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt + "\n只输出纯 JSON，不要任何其他内容。"},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
        )
        raw = response.choices[0].message.content or ""
        return _safe_parse_json(raw)


class AnthropicProvider(LLMProvider):
    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def chat(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 2048
    ) -> str:
        response = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""

    async def extract_json(
        self, system_prompt: str, user_content: str, max_tokens: int = 1024
    ) -> dict:
        response = await self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            system=system_prompt + "\n只输出纯 JSON，不要任何其他内容。",
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text if response.content else ""
        return _safe_parse_json(raw)


class MiniMaxProvider(LLMProvider):
    def __init__(self):
        self.client = openai.AsyncOpenAI(
            base_url=settings.MINIMAX_API_BASE_URL,
            api_key=settings.MINIMAX_API_KEY,
        )
        self.model = settings.MINIMAX_CHAT_MODEL

    async def chat(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 2048
    ) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    async def extract_json(
        self, system_prompt: str, user_content: str, max_tokens: int = 1024
    ) -> dict:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt + "\n只输出纯 JSON，不要任何其他内容。"},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
        )
        raw = response.choices[0].message.content or ""
        return _safe_parse_json(raw)


_PROVIDER_MAP = {
    "deepseek": DeepSeekProvider,
    "anthropic": AnthropicProvider,
    "minimax": MiniMaxProvider,
}


def get_llm_provider(name: str = settings.LLM_PROVIDER) -> LLMProvider:
    cls = _PROVIDER_MAP.get(name)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {name}")
    return cls()


class LLMService:
    def __init__(self):
        self.chat_provider = get_llm_provider()
        self.extraction_provider = get_llm_provider()

    async def generate_chat(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 2048
    ) -> str:
        return await self.chat_provider.chat(system_prompt, messages, max_tokens)

    async def generate_extraction(
        self, system_prompt: str, user_content: str, max_tokens: int = 1024
    ) -> dict:
        return await self.extraction_provider.extract_json(
            system_prompt, user_content, max_tokens
        )


_llm_service = None


def get_llm_service() -> "LLMService":
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service