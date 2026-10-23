from datetime import datetime, timedelta, timezone

import pytest

from mba_hunter.analyzer import Analysis
from mba_hunter.config import load_config
from mba_hunter.db import Database
from mba_hunter.ebay import Listing, html_to_text, parse_summary
from mba_hunter.filters import check_listing
from mba_hunter.telegram import TelegramBot


def make_listing(title="Apple MacBook Air M2 13 pouces 8Go 256Go", price=500.0, shipping=10.0, hours=24):
    end = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    return Listing(
        item_id="v1|123|0", title=title, url="https://www.ebay.fr/itm/123", current_price=price,
        currency="EUR", shipping=shipping, end_date=end, bid_count=3, condition="Occasion",
        seller="vendeur", seller_feedback_pct=99.5, seller_feedback_score=420, image_url=None, location="FR",
    )


def make_analysis(prix_max=580.0):
    return Analysis(score=72, prix_max=prix_max, risques=["batterie non précisée"], points_positifs=["AZERTY"],
                    resume="Bonne affaire.", recommandation="encherir", model="claude-opus-5-5")


@pytest.fixture
def filters():
    return load_config()["filters"] | {"max_price": 650}


@pytest.mark.parametrize("title,expected", [
    ("Apple MacBook Air M2 13 pouces 8Go 256Go", True),
    ("MacBook Air 13 M2 2022 minuit", True),
    ("MacBook Air M1 13 pouces", False),
    ("MacBook Air M2 15 pouces", False),
    ("MacBook Air M2 15\" 2023", False),
    ("MacBook Air M2 13 HS pour pièces", False),
    ("Coque MacBook Air M2 13", False),
    ("MacBook Pro M2 13", True),   # plus de motif « air » requis : seul M2 compte
    ("Ordinateur portable 13 pouces", False),
])
def test_title_filters(filters, title, expected):
    assert check_listing(make_listing(title=title), filters)[0] is expected


def test_shipping_excluded_from_cap_by_default(filters):
    assert filters["include_shipping"] is False
    assert check_listing(make_listing(price=640, shipping=30), filters)[0]
    assert not check_listing(make_listing(price=660, shipping=0), filters)[0]


def test_shipping_included_when_enabled(filters):
    filters = filters | {"include_shipping": True}
    assert check_listing(make_listing(price=640, shipping=5), filters)[0]
    assert not check_listing(make_listing(price=640, shipping=15), filters)[0]


def test_search_filters_delivery_country_not_seller_country():
    from mba_hunter.ebay import EbayClient

    seen = {}
    client = EbayClient("a", "b")
    client._get = lambda path, params=None, extra_headers=None: seen.update(params) or {}
    client.search("MacBook M2", 650, delivery_country="FR")
    assert "deliveryCountry:FR" in seen["filter"]
    assert "itemLocationCountry" not in seen["filter"]
    client.search("MacBook M2", 650)
    assert "deliveryCountry" not in seen["filter"]


def test_ending_too_soon(filters):
    assert not check_listing(make_listing(hours=0.5), filters)[0]


def test_parse_summary_auction():
    listing = parse_summary({
        "itemId": "v1|1|0", "title": "MacBook Air M2", "itemWebUrl": "u",
        "price": {"value": "900.00", "currency": "EUR"},
        "currentBidPrice": {"value": "410.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "12.50", "currency": "EUR"}}],
        "bidCount": 7, "seller": {"username": "s", "feedbackPercentage": "98.7", "feedbackScore": 12},
    })
    assert listing.current_price == 410.0
    assert listing.shipping == 12.5
    assert listing.bid_count == 7


def test_html_to_text():
    assert html_to_text("<p>Batterie&nbsp;: 92 %</p><script>x()</script><br>OK") == "Batterie : 92 %\nOK"


def test_db_roundtrip():
    db = Database(":memory:")
    listing = make_listing()
    db.save(listing, "error", error="boom")
    assert not db.is_known(listing.item_id)  # les erreurs sont retentées
    db.save(listing, "notified", make_analysis())
    assert db.is_known(listing.item_id)
    assert db.get(listing.item_id)["prix_max"] == 580.0


class FakeBot(TelegramBot):
    def __init__(self, updates):
        super().__init__("token", "42")
        self.updates = updates
        self.calls = []

    def _get_updates(self, offset, wait):
        return [u for u in self.updates if u["update_id"] >= offset]

    def call(self, method, timeout=30, **params):
        self.calls.append((method, params))
        return {"message_id": 1}


def click(update_id, data, chat_id=42):
    return {"update_id": update_id, "callback_query": {
        "id": str(update_id), "data": data, "message": {"message_id": 7, "chat": {"id": chat_id}},
    }}


def test_validate_sends_link_and_max_price():
    db = Database(":memory:")
    listing = make_listing()
    db.save(listing, "notified", make_analysis())
    bot = FakeBot([click(1, f"ok:{listing.item_id}")])

    assert bot.process_updates(db) == 1
    assert db.get(listing.item_id)["status"] == "validated"
    sent = [p["text"] for m, p in bot.calls if m == "sendMessage"]
    assert len(sent) == 1 and listing.url in sent[0] and "580.00 €" in sent[0]
    # Offset persisté : le clic n'est pas retraité
    assert bot.process_updates(db) == 0


def test_refuse_and_foreign_chat():
    db = Database(":memory:")
    listing = make_listing()
    db.save(listing, "notified", make_analysis())
    bot = FakeBot([click(1, f"ko:{listing.item_id}", chat_id=999), click(2, f"ko:{listing.item_id}")])

    assert bot.process_updates(db) == 1
    assert db.get(listing.item_id)["status"] == "refused"
    assert not any(m == "sendMessage" for m, _ in bot.calls)


def test_find_and_delete_by_short_id():
    db = Database(":memory:")
    a, b = make_listing(), make_listing()
    b.item_id = "v1|456|0"
    db.save(a, "notified", make_analysis())
    db.save(b, "skipped", make_analysis())
    assert db.find_ids("123") == ["v1|123|0"]          # partie numérique
    assert db.find_ids("v1|456|0") == ["v1|456|0"]     # id complet
    assert db.find_ids("12") == []                      # pas de correspondance partielle
    assert db.delete(["v1|123|0"]) == 1
    assert [r["item_id"] for r in db.list_listings()] == ["v1|456|0"]
    assert [r["item_id"] for r in db.list_listings("skipped")] == ["v1|456|0"]
    assert db.list_listings("notified") == []


def test_cli_list_and_forget(tmp_path, capsys):
    import yaml
    from mba_hunter import main as m

    cfg = load_config() | {"database": {"path": str(tmp_path / "t.db")}}
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(cfg))
    db = Database(tmp_path / "t.db")
    db.save(make_listing(), "notified", make_analysis())
    db.close()

    assert m.main(["list", "--config", str(cfg_file)]) == 0
    assert "123" in capsys.readouterr().out
    assert m.main(["forget", "999", "--config", str(cfg_file)]) == 1   # inconnue
    assert m.main(["forget", "123", "--config", str(cfg_file)]) == 0
    assert m.main(["list", "--config", str(cfg_file)]) == 0
    assert "Aucune annonce" in capsys.readouterr().out
    with pytest.raises(SystemExit):                                     # ni ID ni --all
        m.main(["forget", "--config", str(cfg_file)])


def test_probe_reports_where_listing_disappears(capsys):
    from mba_hunter.probe import normalize_item_id, run_probe

    assert normalize_item_id("336854225095") == "v1|336854225095|0"
    assert normalize_item_id("v1|1|0") == "v1|1|0"

    class FakeClient:
        def get_item(self, item_id):
            return {"title": "MacBook Air M2 13 256Go", "buyingOptions": ["AUCTION"],
                    "currentBidPrice": {"value": "561.00", "currency": "EUR"}, "bidCount": 4}

        def with_marketplace(self, marketplace):
            return self

        def search_raw(self, params, headers=None):
            # l'annonce n'apparaît que lorsque le filtre de livraison vers la France est ajouté
            found = "deliveryCountry:FR" in params.get("filter", "")
            items = [{"itemId": "v1|336854225095|0"}] if found else []
            return {"total": len(items), "itemSummaries": items}

    run_probe(FakeClient(), load_config(), "336854225095")
    out = capsys.readouterr().out
    assert "561.00 EUR" in out
    assert "enchère : total=0 → absente" in out
    assert "+ deliveryCountry:FR : total=1 → TROUVÉE (position 1)" in out
