"""Generate local-only infra secrets into ~/.c15/infra.env (outside the repo). Never prints values.

Run once: `uv run python infra/init_secrets.py`. Re-running keeps existing values, because
LITELLM_SALT_KEY and INFISICAL_ENCRYPTION_KEY must never change once data is stored.
"""
import base64
import secrets
from pathlib import Path

PATH = Path.home() / ".c15" / "infra.env"

GENERATORS = {
    "INFISICAL_ENCRYPTION_KEY": lambda: secrets.token_hex(16),
    "INFISICAL_AUTH_SECRET": lambda: base64.b64encode(secrets.token_bytes(32)).decode(),
    "INFISICAL_DB_PASSWORD": lambda: secrets.token_urlsafe(24),
    "LITELLM_MASTER_KEY": lambda: "sk-" + secrets.token_urlsafe(32),
    "LITELLM_SALT_KEY": lambda: "sk-" + secrets.token_urlsafe(32),
    "LITELLM_DB_PASSWORD": lambda: secrets.token_urlsafe(24),
}


def main() -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if PATH.exists():
        for line in PATH.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    added = [k for k in GENERATORS if k not in existing]
    for k in added:
        existing[k] = GENERATORS[k]()
    PATH.write_text("".join(f"{k}={v}\n" for k, v in existing.items()))
    print(f"{PATH}: {len(existing)} keys ({', '.join(added) or 'none'} newly generated)")


if __name__ == "__main__":
    main()
