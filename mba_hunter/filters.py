from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .ebay import Listing


def check_listing(listing: Listing, filters: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Retourne (garder, raison_du_rejet)."""
    title = listing.title
    for pattern in filters.get("required_patterns", []):
        if not re.search(pattern, title, re.IGNORECASE):
            return False, f"motif requis absent : {pattern}"
    for pattern in filters.get("excluded_patterns", []):
        if re.search(pattern, title, re.IGNORECASE):
            return False, f"motif exclu : {pattern}"

    if listing.currency != "EUR":
        return False, f"devise {listing.currency}"
    total = listing.total_price(filters.get("include_shipping", False))
    if total > filters["max_price"]:
        return False, f"prix {total:.2f} € > max {filters['max_price']} €"

    hours = listing.hours_left()
    if hours is not None and hours < filters.get("min_hours_left", 0):
        return False, f"se termine dans {hours:.1f} h"
    return True, None
