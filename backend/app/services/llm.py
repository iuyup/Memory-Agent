import anthropic
from app.config import settings


class LLMService:
    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def generate_chat(
        self,
        system_prompt: str,
        messages: list[dict],
        model: str = "claude-sonnet-4-20250514",
    ) -> str:
        response = await self.client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""

    async def generate_extraction(
        self,
        user_msg: str,
        assistant_msg: str,
        model: str = "claude-haiku-4-5-20251001",
    ) -> dict:
        # TODO: implement extraction logic in Day 2
        return {}


llm_service = LLMService()