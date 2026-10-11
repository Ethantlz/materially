"""Runtime settings.

Secrets come from the process environment, which `infisical run -- ...` fills in. As a fallback for a
machine without Infisical, a dotenv file at $C15_KEYS (default ~/.c15/keys.env) is loaded without
overriding anything already set. Nothing secret ever lives in the repo.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
KEYS_PATH = Path(os.environ.get("C15_KEYS", Path.home() / ".c15" / "keys.env")).expanduser()
load_dotenv(KEYS_PATH, override=False)


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    db_name: str
    data_dir: Path
    litellm_base_url: str
    litellm_api_key: str
    luminal_api_key: str
    luminal_base_url: str
    openrouter_api_key: str
    openrouter_base_url: str
    anthropic_api_key: str
    firecrawl_api_key: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    test_inbox: str

    @property
    def smtp_ready(self) -> bool:
        return bool(self.smtp_host and self.test_inbox)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def get_settings() -> Settings:
    return Settings(
        mongodb_uri=_env("MONGODB_URI"),
        db_name=_env("DB_NAME", "demo"),
        data_dir=Path(_env("DATA_DIR") or ROOT / "data"),
        litellm_base_url=_env("LITELLM_BASE_URL", "http://localhost:4000/v1"),
        litellm_api_key=_env("LITELLM_API_KEY"),
        luminal_api_key=_env("LUMINAL_API_KEY"),
        luminal_base_url=_env("LUMINAL_BASE_URL"),
        openrouter_api_key=_env("OPENROUTER_API_KEY"),
        openrouter_base_url=_env("OPENROUTER_BASE_URL", "https://openrouter.ai/api"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        firecrawl_api_key=_env("FIRECRAWL_API_KEY"),
        smtp_host=_env("SMTP_HOST"),
        smtp_port=int(_env("SMTP_PORT", "587")),
        smtp_user=_env("SMTP_USER"),
        smtp_password=_env("SMTP_PASSWORD"),
        smtp_from=_env("SMTP_FROM") or _env("SMTP_USER"),
        test_inbox=_env("TEST_INBOX"),
    )
