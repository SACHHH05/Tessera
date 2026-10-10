"""Settings and the role -> Nemotron tier mapping."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# Which Nemotron size each agent role uses. Ultra reasons, Super builds, Nano is fast and cheap.
ROLE_TIER = {
    "main": "ultra",  # account manager: client chat, requirements
    "tl": "super",  # team lead: planning, standups, re-planning
    "dev": "super",  # senior developer: writes code
    "tester": "nano",  # tester: writes and runs tests
    "status": "nano",  # standup and status summaries
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str
    base_url: str
    model_ultra: str
    model_super: str
    model_nano: str
    tavily_api_key: str
    db_path: str
    max_rounds: int
    max_tokens_per_project: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            nebius_api_key=os.environ.get("NEBIUS_API_KEY", ""),
            base_url=os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL),
            model_ultra=os.environ.get("TESSERA_MODEL_ULTRA", ""),
            model_super=os.environ.get("TESSERA_MODEL_SUPER", ""),
            model_nano=os.environ.get("TESSERA_MODEL_NANO", ""),
            tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
            db_path=os.environ.get("TESSERA_DB_PATH", "tessera.db"),
            max_rounds=int(os.environ.get("TESSERA_MAX_ROUNDS", "3")),
            max_tokens_per_project=int(os.environ.get("TESSERA_MAX_TOKENS_PER_PROJECT", "400000")),
        )

    def model_for(self, role: str) -> str:
        tier = ROLE_TIER[role]
        model = {"ultra": self.model_ultra, "super": self.model_super, "nano": self.model_nano}[tier]
        if not model:
            raise ConfigError(
                f"No model configured for tier '{tier}' (role '{role}'). "
                f"Set TESSERA_MODEL_{tier.upper()} in .env. "
                "Run `python scripts/hello_nemotron.py --list` to find valid IDs."
            )
        return model
