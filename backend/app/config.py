"""Application settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'opspilot.db'}"))
    docs_dir: Path = field(default_factory=lambda: Path(os.getenv("DOCS_DIR", str(BASE_DIR / "data" / "docs"))))
    static_dir: Path = field(default_factory=lambda: Path(os.getenv("STATIC_DIR", str(BASE_DIR / "static"))))

    # LLM (any OpenAI-compatible API). Without a key the agent runs in deterministic "rules" mode.
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_S", "30")))
    agent_mode: str = field(default_factory=lambda: os.getenv("AGENT_MODE", "auto"))  # auto | llm | rules
    max_tool_steps: int = field(default_factory=lambda: int(os.getenv("MAX_TOOL_STEPS", "4")))

    # Policy limits enforced in code, not by the model.
    return_window_days: int = 30
    max_message_chars: int = 1000

    # Basic abuse protection for the public demo.
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")))
    cors_origins: str = field(default_factory=lambda: os.getenv("CORS_ORIGINS", "*"))
    seed_on_startup: bool = field(default_factory=lambda: _bool("SEED_ON_STARTUP", True))

    @property
    def resolved_mode(self) -> str:
        if self.agent_mode in {"llm", "rules"}:
            return self.agent_mode
        return "llm" if self.llm_api_key else "rules"


def get_settings() -> Settings:
    return Settings()
