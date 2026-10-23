from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Secrets:
    ebay_client_id: str
    ebay_client_secret: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    path = Path(path or os.environ.get("MBA_HUNTER_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    max_price = config.get("filters", {}).get("max_price")
    if not isinstance(max_price, (int, float)) or max_price <= 0:
        raise ValueError("config.yaml : filters.max_price doit être un nombre positif")
    return config


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Variable d'environnement manquante : {name}")
    return value


def load_secrets(need_ebay: bool = True, need_telegram: bool = True, need_chat_id: bool = True) -> Secrets:
    _load_dotenv()
    return Secrets(
        ebay_client_id=_require("EBAY_CLIENT_ID") if need_ebay else "",
        ebay_client_secret=_require("EBAY_CLIENT_SECRET") if need_ebay else "",
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN") if need_telegram else "",
        telegram_chat_id=_require("TELEGRAM_CHAT_ID") if need_telegram and need_chat_id else "",
    )


def _load_dotenv() -> None:
    """Charge un fichier .env minimal (KEY=VALUE) sans écraser l'environnement existant."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
