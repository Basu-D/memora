"""
Centralised settings loaded from environment variables via pydantic-settings.
All modules import from here — never read os.environ directly elsewhere.
"""

import json

from pydantic import Field
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

    # Auth
    org_api_key: str = "changeme"

    # Infrastructure
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite:///./memora.db"

    @property
    def db_url(self) -> str:
        # SQLAlchemy 2.x requires 'postgresql://' but Railway emits 'postgres://'
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url

    # Storage
    upload_dir: str = "uploads"
    jobs_dir: str = "jobs"          # transcript.json and extracted audio live here

    # Dev flags
    mock_transcription: bool = False   # skip OpenAI API; return a stub transcript
    mock_agent: bool = False           # skip Gemini API; return stub extraction + skip Confluence


    # CORS — accepts JSON array ('["url1","url2"]') or comma-separated ("url1,url2").
    # Typed as str so pydantic-settings v2 never tries to JSON-parse it automatically.
    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://localhost:80",
        validation_alias="CORS_ORIGINS",
    )

    @property
    def cors_origins(self) -> list[str]:
        v = self.cors_origins_raw.strip()
        if v.startswith("["):
            return json.loads(v)
        return [o.strip() for o in v.split(",") if o.strip()]


settings = Settings()
