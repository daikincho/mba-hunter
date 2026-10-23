from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analyzer import Analysis
from .ebay import Listing

# Statuts d'une annonce :
#   error     analyse échouée, sera retentée au prochain passage
#   skipped   analysée mais non notifiée (score trop bas ou prix déjà dépassé)
#   notified  envoyée sur Telegram, en attente de décision
#   validated / refused   décision prise via Telegram
SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    item_id             TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    url                 TEXT NOT NULL,
    current_price       REAL NOT NULL,
    shipping            REAL,
    currency            TEXT NOT NULL,
    end_date            TEXT,
    bid_count           INTEGER,
    seller              TEXT,
    image_url           TEXT,
    score               INTEGER,
    prix_max            REAL,
    risques             TEXT,
    analysis            TEXT,
    status              TEXT NOT NULL,
    error               TEXT,
    telegram_message_id INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM listings WHERE item_id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def is_known(self, item_id: str) -> bool:
        """Vrai si l'annonce a déjà été traitée (les erreurs sont retentées)."""
        row = self.get(item_id)
        return row is not None and row["status"] != "error"

    def list_listings(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM listings", ()
        if status:
            sql, args = sql + " WHERE status = ?", (status,)
        rows = self.conn.execute(sql + " ORDER BY updated_at DESC", args).fetchall()
        return [dict(r) for r in rows]

    def find_ids(self, target: str) -> List[str]:
        """Identifiants correspondant à `target` : id complet (v1|123|0) ou seulement sa partie numérique (123)."""
        rows = self.conn.execute(
            "SELECT item_id FROM listings WHERE item_id = ? OR instr(item_id, ?) > 0", (target, f"|{target}|")
        ).fetchall()
        return [r["item_id"] for r in rows]

    def delete(self, item_ids: List[str]) -> int:
        cur = self.conn.executemany("DELETE FROM listings WHERE item_id = ?", [(i,) for i in item_ids])
        self.conn.commit()
        return cur.rowcount

    def refresh_price(self, listing: Listing) -> None:
        self.conn.execute(
            "UPDATE listings SET current_price = ?, bid_count = ?, end_date = ?, updated_at = ? WHERE item_id = ?",
            (listing.current_price, listing.bid_count, listing.end_date, _now(), listing.item_id),
        )
        self.conn.commit()

    def save(self, listing: Listing, status: str, analysis: Optional[Analysis] = None, error: str = "") -> None:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO listings (item_id, title, url, current_price, shipping, currency, end_date, bid_count,
                                  seller, image_url, score, prix_max, risques, analysis, status, error,
                                  created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                current_price = excluded.current_price, shipping = excluded.shipping,
                end_date = excluded.end_date, bid_count = excluded.bid_count,
                score = excluded.score, prix_max = excluded.prix_max, risques = excluded.risques,
                analysis = excluded.analysis, status = excluded.status, error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                listing.item_id, listing.title, listing.url, listing.current_price, listing.shipping,
                listing.currency, listing.end_date, listing.bid_count, listing.seller, listing.image_url,
                analysis.score if analysis else None,
                analysis.prix_max if analysis else None,
                json.dumps(analysis.risques, ensure_ascii=False) if analysis else None,
                analysis.to_json() if analysis else None,
                status, error or None, now, now,
            ),
        )
        self.conn.commit()

    def set_status(self, item_id: str, status: str, telegram_message_id: Optional[int] = None) -> None:
        if telegram_message_id is None:
            self.conn.execute(
                "UPDATE listings SET status = ?, updated_at = ? WHERE item_id = ?", (status, _now(), item_id)
            )
        else:
            self.conn.execute(
                "UPDATE listings SET status = ?, telegram_message_id = ?, updated_at = ? WHERE item_id = ?",
                (status, telegram_message_id, _now(), item_id),
            )
        self.conn.commit()

    def get_kv(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_kv(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()
