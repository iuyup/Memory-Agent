from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    JWT_SECRET: str = "dev-secret-change-in-production"
    DATABASE_PATH: Path = Path("./data/memory.db")


settings = Settings()