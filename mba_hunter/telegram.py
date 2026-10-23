"""Bot Telegram : notification des annonces et traitement des boutons Valider / Refuser.

Aucune enchère n'est placée automatiquement : « Valider » renvoie simplement le lien
de l'annonce et le prix maximum conseillé.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .analyzer import Analysis
from .db import Database
from .ebay import Listing

log = logging.getLogger(__name__)

OFFSET_KEY = "telegram_update_offset"
RECO_LABELS = {"encherir": "🟢 Enchérir", "surveiller": "🟡 Surveiller", "eviter": "🔴 Éviter"}


class TelegramError(Exception):
    pass


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.session = requests.Session()

    def call(self, method: str, timeout: int = 30, **params: Any) -> Any:
        resp = self.session.post(f"{self.base}/{method}", json=params, timeout=timeout)
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"{method} : {data.get('description', resp.text)}")
        return data["result"]

    # --- envoi -------------------------------------------------------------------

    def send_listing(self, listing: Listing, analysis: Analysis) -> int:
        params: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": format_listing_message(listing, analysis),
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Valider", "callback_data": f"ok:{listing.item_id}"},
                    {"text": "❌ Refuser", "callback_data": f"ko:{listing.item_id}"},
                ]]
            },
        }
        if listing.image_url:
            params["link_preview_options"] = {"url": listing.image_url, "prefer_large_media": True}
        else:
            params["link_preview_options"] = {"is_disabled": True}
        return self.call("sendMessage", **params)["message_id"]

    def send_text(self, text: str, reply_to: Optional[int] = None) -> None:
        params: Dict[str, Any] = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        self.call("sendMessage", **params)

    # --- réception ---------------------------------------------------------------

    def process_updates(self, db: Database, wait: int = 0) -> int:
        """Lit les clics en attente (getUpdates) et les traite. Retourne le nombre de clics traités."""
        offset = int(db.get_kv(OFFSET_KEY, "0") or 0)
        updates = self._get_updates(offset, wait)

        handled = 0
        for update in updates:
            # L'offset est avancé avant le traitement : un clic qui fait planter le
            # traitement n'est pas rejoué en boucle.
            db.set_kv(OFFSET_KEY, str(update["update_id"] + 1))
            query = update.get("callback_query")
            if query and self._handle_callback(db, query):
                handled += 1
        return handled

    def discover_chats(self) -> List[Dict[str, Any]]:
        """Conversations ayant écrit au bot (pour trouver TELEGRAM_CHAT_ID). Ne consomme aucune mise à jour."""
        # Sans offset, rien n'est confirmé : les clics en attente restent disponibles pour `callbacks`.
        resp = self.session.post(
            f"{self.base}/getUpdates",
            json={"timeout": 0, "allowed_updates": ["message", "callback_query"]},
            timeout=20,
        )
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"getUpdates : {data.get('description', resp.text)}")
        chats: Dict[Any, Dict[str, Any]] = {}
        for update in data["result"]:
            message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
            chat = message.get("chat")
            if chat:
                chats[chat["id"]] = chat
        return list(chats.values())

    def _get_updates(self, offset: int, wait: int) -> List[Dict[str, Any]]:
        resp = self.session.post(
            f"{self.base}/getUpdates",
            json={"offset": offset, "timeout": wait, "allowed_updates": ["callback_query"]},
            timeout=wait + 15,
        )
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"getUpdates : {data.get('description', resp.text)}")
        return data["result"]

    def _handle_callback(self, db: Database, query: Dict[str, Any]) -> bool:
        message = query.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != self.chat_id:
            log.warning("Clic ignoré : chat %s non autorisé", chat_id)
            return False

        action, _, item_id = (query.get("data") or "").partition(":")
        row = db.get(item_id) if item_id else None
        if action not in ("ok", "ko") or row is None:
            self._answer(query["id"], "Annonce inconnue")
            return False

        if row["status"] in ("validated", "refused"):
            self._answer(query["id"], "Déjà traité")
            return False

        message_id = message.get("message_id")
        if action == "ok":
            db.set_status(item_id, "validated")
            self._answer(query["id"], "Validé ✅")
            self._set_buttons(message_id, "✅ Validée")
            self.send_text(format_validation_message(row), reply_to=message_id)
        else:
            db.set_status(item_id, "refused")
            self._answer(query["id"], "Refusé ❌")
            self._set_buttons(message_id, "❌ Refusée")
        log.info("Annonce %s : %s", item_id, "validée" if action == "ok" else "refusée")
        return True

    def _answer(self, query_id: str, text: str) -> None:
        # Échoue si le clic date de plus de quelques minutes (traitement différé) : sans gravité.
        try:
            self.call("answerCallbackQuery", callback_query_id=query_id, text=text)
        except TelegramError as e:
            log.debug("answerCallbackQuery : %s", e)

    def _set_buttons(self, message_id: Optional[int], label: str) -> None:
        if not message_id:
            return
        try:
            self.call(
                "editMessageReplyMarkup",
                chat_id=self.chat_id,
                message_id=message_id,
                reply_markup={"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]},
            )
        except TelegramError as e:
            log.debug("editMessageReplyMarkup : %s", e)


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "inconnue"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime("%d/%m/%Y %H:%M UTC")


def format_listing_message(listing: Listing, analysis: Analysis) -> str:
    shipping = f"{listing.shipping:.2f} €" if listing.shipping is not None else "?"
    lines = [
        f"<b>{_esc(listing.title)}</b>",
        "",
        f"⭐ Score : <b>{analysis.score}/100</b> — {RECO_LABELS.get(analysis.recommandation, analysis.recommandation)}",
        f"💶 Enchère actuelle : {listing.current_price:.2f} € (+ port {shipping}) — {listing.bid_count} enchère(s)",
        f"🎯 Prix max conseillé : <b>{analysis.prix_max:.2f} €</b> (hors port)",
        f"⏰ Fin : {_fmt_date(listing.end_date)}",
        f"👤 {_esc(listing.seller)} ({listing.seller_feedback_pct}% / {listing.seller_feedback_score})",
        "",
        f"📝 {_esc(analysis.resume)}",
    ]
    if analysis.points_positifs:
        lines += ["", "<b>Points positifs</b>"] + [f"• {_esc(p)}" for p in analysis.points_positifs]
    if analysis.risques:
        lines += ["", "<b>Risques</b>"] + [f"• {_esc(r)}" for r in analysis.risques]
    lines += ["", f'<a href="{_esc(listing.url)}">Voir l\'annonce</a>']
    return "\n".join(lines)


def format_validation_message(row: Dict[str, Any]) -> str:
    shipping = f"{row['shipping']:.2f} €" if row.get("shipping") is not None else "?"
    prix_max = f"{row['prix_max']:.2f} €" if row.get("prix_max") is not None else "?"
    return "\n".join([
        f"✅ <b>Validé</b> — {_esc(row['title'])}",
        "",
        f"🔗 {_esc(row['url'])}",
        f"🎯 Prix max : <b>{prix_max}</b> (hors port, port : {shipping})",
        f"⏰ Fin : {_fmt_date(row.get('end_date'))}",
        "",
        "À toi de placer l'enchère manuellement sur eBay (aucune enchère automatique).",
    ])
