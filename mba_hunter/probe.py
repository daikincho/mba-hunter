"""Diagnostic : pourquoi une annonce précise n'apparaît-elle pas dans les résultats de la recherche ?"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .ebay import EbayClient, EbayError


def normalize_item_id(raw: str) -> str:
    """336854225095 -> v1|336854225095|0 (un ID complet est conservé)."""
    raw = raw.strip()
    return raw if raw.startswith("v1|") else f"v1|{raw}|0"


def _numeric(item_id: str) -> str:
    parts = item_id.split("|")
    return parts[1] if len(parts) > 1 else item_id


def _find(client: EbayClient, params: Dict[str, Any], item_id: str, headers: Optional[Dict[str, str]] = None,
          pages: int = 5) -> Tuple[Optional[int], Any]:
    """Retourne (position de l'annonce ou None, total annoncé par eBay)."""
    total, seen = None, 0
    for page in range(pages):
        data = client.search_raw({**params, "limit": 200, "offset": page * 200}, headers)
        total = data.get("total", 0)
        for item in data.get("itemSummaries", []):
            seen += 1
            if _numeric(item.get("itemId", "")) == _numeric(item_id):
                return seen, total
        if not data.get("next"):
            break
    return None, total


FR_CONTEXT = {"X-EBAY-C-ENDUSERCTX": "contextualLocation=country=FR,zip=75001"}


def run_probe(client: EbayClient, config: Dict[str, Any], raw_item_id: str) -> int:
    item_id = normalize_item_id(raw_item_id)
    ebay_cfg, max_price = config["ebay"], config["filters"]["max_price"]

    print(f"=== Annonce {item_id} ===")
    try:
        item = client.get_item(item_id)
    except EbayError as e:
        print(f"getItem impossible : {e}")
        return 1
    price = item.get("currentBidPrice") or item.get("price") or {}
    category_id = item.get("categoryId")
    print(f"Titre            : {item.get('title')}")
    print(f"Modes d'achat    : {item.get('buyingOptions')}")
    print(f"Prix / enchère   : {price.get('value')} {price.get('currency')}  ({item.get('bidCount')} enchère(s))")
    print(f"Localisation     : {(item.get('itemLocation') or {}).get('country')}")
    print(f"Catégorie        : {item.get('categoryPath')}  (id {category_id})")
    print(f"Fin              : {item.get('itemEndDate')}")
    print(f"Expédition       : {[(o.get('shippingCost') or {}).get('value') for o in item.get('shippingOptions', [])]}")

    auction = f"buyingOptions:{{{ebay_cfg.get('buying_options', 'AUCTION')}}}"
    query = ebay_cfg["query"]
    # (libellé, paramètres, en-têtes, place de marché)
    variants: List[Tuple[str, Dict[str, Any], Optional[Dict[str, str]], Optional[str]]] = [
        (f"référence : {query!r} + enchère", {"q": query, "filter": auction}, None, None),
        ("+ itemLocationRegion:WORLDWIDE", {"q": query, "filter": f"{auction},itemLocationRegion:WORLDWIDE"}, None, None),
        ("+ WORLDWIDE + en-tête acheteur FR", {"q": query, "filter": f"{auction},itemLocationRegion:WORLDWIDE"}, FR_CONTEXT, None),
        ("+ WORLDWIDE + en-tête FR + deliveryCountry:FR",
         {"q": query, "filter": f"{auction},itemLocationRegion:WORLDWIDE,deliveryCountry:FR"}, FR_CONTEXT, None),
        ("+ itemLocationRegion:EUROPEAN_UNION + en-tête FR",
         {"q": query, "filter": f"{auction},itemLocationRegion:EUROPEAN_UNION"}, FR_CONTEXT, None),
        ("+ itemLocationCountry:DE (pour comparaison)", {"q": query, "filter": f"{auction},itemLocationCountry:DE"}, None, None),
    ]
    print("\n=== Recherches ===")
    clients: Dict[str, EbayClient] = {}
    for label, params, headers, marketplace in variants:
        c = client
        if marketplace:
            if marketplace not in clients:
                clients[marketplace] = client.with_marketplace(marketplace)
            c = clients[marketplace]
        try:
            position, total = _find(c, params, item_id, headers)
        except EbayError as e:
            print(f"- {label} : erreur {e}")
            continue
        verdict = f"TROUVÉE (position {position})" if position else "absente"
        print(f"- {label} : total={total} → {verdict}")
    return 0
