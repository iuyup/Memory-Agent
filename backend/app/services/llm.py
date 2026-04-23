import json
from abc import ABC, abstractmethod

import anthropic
import openai
from app.config import settings


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
        # strip markdown code fence
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        elif raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        return json.loads(raw.strip())


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
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        elif raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        return json.loads(raw.strip())


_PROVIDER_MAP = {
    "deepseek": DeepSeekProvider,
    "anthropic": AnthropicProvider,
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


llm_service = LLMService()