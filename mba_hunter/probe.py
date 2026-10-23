"""Diagnostic : pourquoi une annonce précise n'apparaît-elle pas dans les résultats de la recherche ?"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .ebay import EbayClient, EbayError


def normalize_item_id(raw: str) -> str:
    """336854225095 -> v1|336854225095|0 (un ID complet est conservé)."""
    raw = raw.strip()
    return raw if raw.startswith("v1|") else f"v1|{raw}|0"


def _find(client: EbayClient, params: Dict[str, Any], item_id: str, pages: int = 3) -> Tuple[Optional[int], Any]:
    """Retourne (position de l'annonce ou None, total annoncé par eBay)."""
    total, seen = None, 0
    for page in range(pages):
        data = client.search_raw({**params, "limit": 200, "offset": page * 200})
        total = data.get("total", 0)
        for item in data.get("itemSummaries", []):
            seen += 1
            if item.get("itemId") == item_id:
                return seen, total
        if not data.get("next"):
            break
    return None, total


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
    print(f"Titre            : {item.get('title')}")
    print(f"Modes d'achat    : {item.get('buyingOptions')}")
    print(f"Prix / enchère   : {price.get('value')} {price.get('currency')}  ({item.get('bidCount')} enchère(s))")
    print(f"Localisation     : {(item.get('itemLocation') or {}).get('country')}")
    print(f"Catégorie        : {item.get('categoryPath')}  (id {item.get('categoryId')})")
    print(f"Fin              : {item.get('itemEndDate')}")
    print(f"Expédition       : {[(o.get('shippingCost') or {}).get('value') for o in item.get('shippingOptions', [])]}")

    auction = f"buyingOptions:{{{ebay_cfg.get('buying_options', 'AUCTION')}}}"
    price_f, cur_f = f"price:[..{max_price:g}]", "priceCurrency:EUR"
    query = ebay_cfg["query"]
    title_words = (item.get("title") or "").split()
    variants: List[Tuple[str, Dict[str, Any]]] = [
        (f"requête actuelle {query!r} + tous les filtres", {"q": query, "filter": ",".join([auction, price_f, cur_f])}),
        ("sans filtre de prix", {"q": query, "filter": auction}),
        ("sans filtre enchère", {"q": query, "filter": ",".join([price_f, cur_f])}),
        ("sans aucun filtre", {"q": query}),
        ("requête 'macbook air m2'", {"q": "macbook air m2", "filter": auction}),
        ("requête 'macbook'", {"q": "macbook", "filter": auction}),
        ("mots du titre (6 premiers)", {"q": " ".join(title_words[:6]), "filter": auction}),
    ]
    print("\n=== Recherches ===")
    for label, params in variants:
        try:
            position, total = _find(client, params, item_id)
        except EbayError as e:
            print(f"- {label} : erreur {e}")
            continue
        verdict = f"TROUVÉE (position {position})" if position else "absente"
        print(f"- {label} : total={total} → {verdict}")
    return 0
