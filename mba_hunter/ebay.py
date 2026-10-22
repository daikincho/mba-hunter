"""Client eBay Browse API (OAuth client credentials) et normalisation des annonces."""
from __future__ import annotations

import base64
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

API_SCOPE = "https://api.ebay.com/oauth/api_scope"


@dataclass
class Listing:
    item_id: str
    title: str
    url: str
    current_price: float
    currency: str
    shipping: Optional[float]
    end_date: Optional[str]
    bid_count: int
    condition: str
    seller: str
    seller_feedback_pct: Optional[float]
    seller_feedback_score: Optional[int]
    image_url: Optional[str]
    location: str
    description: str = ""
    aspects: Dict[str, str] = field(default_factory=dict)
    condition_description: str = ""

    def total_price(self, include_shipping: bool = True) -> float:
        return self.current_price + ((self.shipping or 0.0) if include_shipping else 0.0)

    def hours_left(self, now: Optional[datetime] = None) -> Optional[float]:
        if not self.end_date:
            return None
        end = datetime.fromisoformat(self.end_date.replace("Z", "+00:00"))
        now = now or datetime.now(timezone.utc)
        return (end - now).total_seconds() / 3600


class EbayClient:
    def __init__(self, client_id: str, client_secret: str, marketplace: str = "EBAY_FR", sandbox: bool = False):
        host = "api.sandbox.ebay.com" if sandbox else "api.ebay.com"
        self.token_url = f"https://{host}/identity/v1/oauth2/token"
        self.browse_url = f"https://{host}/buy/browse/v1"
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace = marketplace
        self.session = requests.Session()
        self._token: Optional[str] = None
        self._token_expiry = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = self.session.post(
            self.token_url,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": API_SCOPE},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 7200))
        return self._token

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = self.session.get(
            f"{self.browse_url}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                "Accept-Language": "fr-FR",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def search(
        self,
        query: str,
        max_price: float,
        category_ids: str = "",
        buying_options: str = "AUCTION",
        sort: str = "endingSoonest",
        limit: int = 100,
        max_pages: int = 1,
    ) -> List[Listing]:
        filters = [
            f"buyingOptions:{{{buying_options}}}",
            f"price:[..{max_price:g}]",
            "priceCurrency:EUR",
        ]
        params: Dict[str, Any] = {"q": query, "filter": ",".join(filters), "sort": sort, "limit": min(limit, 200)}
        if category_ids:
            params["category_ids"] = category_ids

        listings: List[Listing] = []
        for page in range(max_pages):
            params["offset"] = page * params["limit"]
            data = self._get("/item_summary/search", params)
            items = data.get("itemSummaries", [])
            listings.extend(parse_summary(item) for item in items)
            if not data.get("next") or not items:
                break
        log.info("eBay : %d annonces trouvées pour %r", len(listings), query)
        return listings

    def enrich(self, listing: Listing) -> Listing:
        """Ajoute description, caractéristiques et état détaillé via getItem."""
        data = self._get(f"/item/{listing.item_id}")
        listing.description = html_to_text(data.get("description", ""))
        listing.condition_description = data.get("conditionDescription", "") or ""
        listing.aspects = {a["name"]: a["value"] for a in data.get("localizedAspects", []) if "name" in a}
        if listing.shipping is None:
            listing.shipping = _shipping_cost(data)
        return listing


def _amount(obj: Optional[Dict[str, Any]]) -> Optional[float]:
    if not obj or "value" not in obj:
        return None
    return float(obj["value"])


def _shipping_cost(item: Dict[str, Any]) -> Optional[float]:
    costs = [_amount(opt.get("shippingCost")) for opt in item.get("shippingOptions", [])]
    costs = [c for c in costs if c is not None]
    return min(costs) if costs else None


def parse_summary(item: Dict[str, Any]) -> Listing:
    price_obj = item.get("currentBidPrice") or item.get("price") or {}
    seller = item.get("seller", {})
    pct = seller.get("feedbackPercentage")
    return Listing(
        item_id=item["itemId"],
        title=item.get("title", ""),
        url=item.get("itemWebUrl", ""),
        current_price=_amount(price_obj) or 0.0,
        currency=price_obj.get("currency", "EUR"),
        shipping=_shipping_cost(item),
        end_date=item.get("itemEndDate"),
        bid_count=int(item.get("bidCount", 0)),
        condition=item.get("condition", ""),
        seller=seller.get("username", ""),
        seller_feedback_pct=float(pct) if pct is not None else None,
        seller_feedback_score=seller.get("feedbackScore"),
        image_url=(item.get("image") or {}).get("imageUrl"),
        location=(item.get("itemLocation") or {}).get("country", ""),
    )


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()
