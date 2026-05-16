"""
Centralised settings loaded from environment variables via pydantic-settings.
All modules import from here — never read os.environ directly elsewhere.
"""

import json
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AI
    gemini_api_key: str = ""
    openai_api_key: str = ""

    # Confluence
    confluence_url: str = ""
    confluence_email: str = ""    # Atlassian account email used for Basic auth
    confluence_token: str = ""
    confluence_space_key: str = ""

    # Auth
    org_api_key: str = "changeme"

    # Infrastructure
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite:///./memora.db"

    # Storage
    upload_dir: str = "uploads"
    jobs_dir: str = "jobs"          # transcript.json and extracted audio live here

    # Dev flags
    mock_transcription: bool = False   # skip OpenAI API; return a stub transcript
    mock_agent: bool = False           # skip Gemini API; return stub extraction + skip Confluence


    # CORS — accepts JSON array or comma-separated string
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:80"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


settings = Settings()
