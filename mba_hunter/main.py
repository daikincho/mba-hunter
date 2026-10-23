"""Point d'entrée : python -m mba_hunter {run,callbacks,listen}."""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Dict, List, Optional

import requests

from .analyzer import AnalysisError, Analyzer
from .config import ROOT, load_config, load_secrets
from .db import Database
from .ebay import EbayClient, EbayError
from .filters import check_listing
from .telegram import TelegramBot, TelegramError, format_listing_message

log = logging.getLogger("mba_hunter")

MAX_CONSECUTIVE_FAILURES = 3


def open_db(config: Dict[str, Any]) -> Database:
    return Database(ROOT / config["database"]["path"])


def hunt(config: Dict[str, Any], db: Database, bot: Optional[TelegramBot]) -> None:
    """Recherche eBay → filtre → analyse Claude → stockage → notification."""
    secrets = load_secrets(need_telegram=False)
    ebay_cfg, filters, claude_cfg = config["ebay"], config["filters"], config["claude"]
    budget = float(filters["max_price"])

    ebay = EbayClient(
        secrets.ebay_client_id, secrets.ebay_client_secret,
        marketplace=ebay_cfg.get("marketplace", "EBAY_FR"), sandbox=ebay_cfg.get("sandbox", False),
    )
    listings = ebay.search(
        query=ebay_cfg["query"],
        max_price=budget,
        category_ids=ebay_cfg.get("category_ids", ""),
        buying_options=ebay_cfg.get("buying_options", "AUCTION"),
        sort=ebay_cfg.get("sort", "endingSoonest"),
        limit=ebay_cfg.get("limit", 100),
        max_pages=ebay_cfg.get("max_pages", 1),
    )

    analyzer = Analyzer(model=claude_cfg["model"], effort=claude_cfg.get("effort", "medium"))
    max_analyses = claude_cfg.get("max_analyses_per_run", 25)
    min_score = claude_cfg.get("min_score_to_notify", 50)
    analyzed = notified = 0
    rejected: Dict[str, int] = {}
    known = 0
    failures = errors = 0  # échecs d'analyse consécutifs / au total

    for listing in listings:
        if db.is_known(listing.item_id):
            db.refresh_price(listing)
            known += 1
            continue
        keep, reason = check_listing(listing, filters)
        if not keep:
            log.debug("Écartée %s (%s) : %s", listing.item_id, listing.title, reason)
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        if analyzed >= max_analyses:
            log.info("Limite de %d analyses atteinte, le reste attendra le prochain passage", max_analyses)
            break

        try:
            if ebay_cfg.get("fetch_details", True):
                ebay.enrich(listing)
            analysis = analyzer.analyze(listing, budget)
        except (AnalysisError, EbayError, requests.RequestException) as e:  # une annonce en échec ne bloque pas les autres
            log.error("Analyse impossible pour %s : %s", listing.item_id, e)
            db.save(listing, "error", error=str(e))
            failures += 1
            errors += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("%d échecs consécutifs, arrêt du passage (problème de configuration ?)", failures)
                break
            continue
        failures = 0
        analyzed += 1

        total_now = listing.total_price(filters.get("include_shipping", True))
        worth_it = analysis.score >= min_score and analysis.prix_max > listing.current_price
        log.info("%s — score %d, prix max %.2f €, actuel %.2f €", listing.title, analysis.score,
                 analysis.prix_max, total_now)
        if not worth_it:
            db.save(listing, "skipped", analysis)
            continue

        db.save(listing, "notified", analysis)
        if bot is None:
            print(format_listing_message(listing, analysis), end="\n\n")
            continue
        try:
            message_id = bot.send_listing(listing, analysis)
            db.set_status(listing.item_id, "notified", telegram_message_id=message_id)
            notified += 1
        except TelegramError as e:
            log.error("Envoi Telegram impossible pour %s : %s", listing.item_id, e)
            db.save(listing, "error", analysis, error=str(e))

    if listings and not (analyzed or known or errors):
        log.warning("Les %d annonces trouvées ont toutes été écartées par les filtres", len(listings))
    for reason, count in sorted(rejected.items(), key=lambda kv: -kv[1]):
        log.info("  %3d écartée(s) — %s", count, reason)
    log.info("Terminé : %d trouvée(s), %d déjà connue(s), %d écartée(s), %d analysée(s), %d en erreur, %d notifiée(s)",
             len(listings), known, sum(rejected.values()), analyzed, errors, notified)


def short_id(item_id: str) -> str:
    """v1|336854225095|0 -> 336854225095 (la partie utile pour `forget`)."""
    parts = item_id.split("|")
    return parts[1] if len(parts) > 1 else item_id


def print_listings(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("Aucune annonce en base.")
        return
    print(f"{'ID':<13} {'STATUT':<10} {'PRIX':>8} {'SCORE':>5} {'MAX':>8}  {'FIN (UTC)':<16}  TITRE")
    for r in rows:
        score = "-" if r["score"] is None else r["score"]
        prix_max = "-" if r["prix_max"] is None else f"{r['prix_max']:.0f} €"
        fin = (r["end_date"] or "?")[:16].replace("T", " ")
        print(f"{short_id(r['item_id']):<13} {r['status']:<10} {r['current_price']:>6.0f} € {score:>5} "
              f"{prix_max:>8}  {fin:<16}  {r['title'][:50]}")
    print(f"\n{len(rows)} annonce(s).")


def forget(db: Database, targets: List[str], everything: bool) -> int:
    """Retire des annonces de la base : elles seront traitées comme nouvelles au prochain `run`."""
    if everything:
        ids = [r["item_id"] for r in db.list_listings()]
    else:
        ids = []
        for target in targets:
            found = db.find_ids(target)
            if not found:
                print(f"Introuvable : {target}")
            ids.extend(found)
    ids = list(dict.fromkeys(ids))
    if not ids:
        print("Rien à supprimer.")
        return 1
    print(f"{db.delete(ids)} annonce(s) supprimée(s) de la base.")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="mba_hunter", description="Chasse aux MacBook Air M2 sur eBay")
    parser.add_argument("command", choices=["run", "callbacks", "listen", "list", "forget"], help=(
        "run : traite les clics Telegram en attente puis lance la recherche ; "
        "callbacks : traite uniquement les clics ; listen : écoute les clics en continu ; "
        "list : affiche les annonces en base ; forget ID… : retire des annonces de la base"
    ))
    parser.add_argument("targets", nargs="*", metavar="ID", help="forget : identifiant(s) d'annonce (voir `list`)")
    parser.add_argument("--all", action="store_true", dest="everything", help="forget : supprime toutes les annonces")
    parser.add_argument("--status", choices=["notified", "validated", "refused", "skipped", "error"],
                        help="list : n'affiche que ce statut")
    parser.add_argument("--config", help="chemin du fichier config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="affiche les notifications au lieu de les envoyer")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config(args.config)
    db = open_db(config)
    try:
        if args.command == "list":
            print_listings(db.list_listings(args.status))
            return 0
        if args.command == "forget":
            if not args.targets and not args.everything:
                parser.error("forget : indique au moins un ID (voir `list`) ou --all")
            return forget(db, args.targets, args.everything)

        bot = None
        if not (args.dry_run and args.command == "run"):
            secrets = load_secrets(need_ebay=False)
            bot = TelegramBot(secrets.telegram_bot_token, secrets.telegram_chat_id)

        if args.command == "listen":
            log.info("Écoute des clics Telegram (Ctrl+C pour arrêter)…")
            while True:
                bot.process_updates(db, wait=50)

        if bot is not None:
            handled = bot.process_updates(db)
            log.info("%d clic(s) Telegram traité(s)", handled)
        if args.command == "run":
            try:
                hunt(config, db, bot)
            except EbayError as e:
                log.error("%s", e)
                return 1
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
