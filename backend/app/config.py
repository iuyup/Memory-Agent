from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    LLM_PROVIDER: str = "deepseek"

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_CHAT_MODEL: str = "deepseek-chat"

    ANTHROPIC_API_KEY: str = ""

    OPENAI_API_KEY: str = ""
    JWT_SECRET: str = "dev-secret-change-in-production"
    DATABASE_PATH: Path = Path("./data/memory.db")


settings = Settings()