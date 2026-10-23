"""Analyse d'une annonce par Claude, avec sortie JSON structurée."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import List

import anthropic

from .ebay import Listing

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es un expert du marché de l'occasion Apple en France. Tu évalues des annonces eBay \
d'enchères de MacBook Air M2 13 pouces (2022, A2681) pour un acheteur particulier qui fixe lui-même \
ses enchères à la main.

Pour chaque annonce, évalue :
- la conformité : bien un MacBook Air M2 13" (pas M1/M3, pas 15"), configuration RAM/SSD, clavier AZERTY ou non ;
- l'état : rayures, écran, batterie (cycles, capacité), fonctionnement, accessoires (chargeur), facture, garantie/AppleCare ;
- les risques : verrouillage iCloud/MDM, pièces, photos génériques, description vague, vendeur peu noté ou récent, \
  expédition depuis l'étranger, signaux d'arnaque ;
- le prix : la cote d'occasion actuelle en France pour cette configuration et cet état.

Champs attendus :
- score : entier de 0 à 100, attractivité globale de l'affaire (conformité × état × risque × marge de prix) ;
- prix_max : enchère maximale conseillée en euros, frais de port EXCLUS, sans jamais dépasser le budget indiqué \
  (budget moins frais de port). Mets 0 si l'annonce est à éviter ;
- risques : liste courte et concrète des risques identifiés (vide si aucun) ;
- points_positifs : liste courte des atouts ;
- resume : 2 phrases maximum, en français ;
- recommandation : "encherir", "surveiller" ou "eviter".

Ne déduis rien qui n'est pas dans l'annonce : une information absente (batterie, iCloud…) est un risque, pas un fait."""

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "prix_max": {"type": "number"},
        "risques": {"type": "array", "items": {"type": "string"}},
        "points_positifs": {"type": "array", "items": {"type": "string"}},
        "resume": {"type": "string"},
        "recommandation": {"type": "string", "enum": ["encherir", "surveiller", "eviter"]},
    },
    "required": ["score", "prix_max", "risques", "points_positifs", "resume", "recommandation"],
    "additionalProperties": False,
}


class AnalysisError(Exception):
    pass


@dataclass
class Analysis:
    score: int
    prix_max: float
    risques: List[str]
    points_positifs: List[str]
    resume: str
    recommandation: str
    model: str

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


def format_listing(listing: Listing, budget: float) -> str:
    shipping = f"{listing.shipping:.2f} €" if listing.shipping is not None else "non précisés"
    hours = listing.hours_left()
    lines = [
        f"Budget maximum de l'acheteur (port inclus) : {budget:.2f} €",
        "",
        f"Titre : {listing.title}",
        f"Enchère actuelle : {listing.current_price:.2f} {listing.currency} ({listing.bid_count} enchère(s))",
        f"Frais de port : {shipping}",
        f"Fin de l'enchère : {listing.end_date} ({hours:.1f} h restantes)" if hours is not None else "Fin : inconnue",
        f"État déclaré : {listing.condition}",
        f"Vendeur : {listing.seller} — {listing.seller_feedback_pct}% positif, {listing.seller_feedback_score} évaluations",
        f"Localisation : {listing.location}",
    ]
    if listing.condition_description:
        lines.append(f"Précisions sur l'état : {listing.condition_description}")
    if listing.aspects:
        lines.append("Caractéristiques :")
        lines.extend(f"  - {k} : {v}" for k, v in listing.aspects.items())
    if listing.description:
        lines += ["", "Description du vendeur :", listing.description]
    return "\n".join(lines)


class Analyzer:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium"):
        # Une clé API non rattachée à un workspace exige l'en-tête anthropic-workspace-id.
        workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self.client = anthropic.Anthropic(default_headers=headers)
        self.model = model
        self.effort = effort

    def analyze(self, listing: Listing, budget: float) -> Analysis:
        try:
            # fallbacks="default" : si le modèle décline la requête, l'API la relance
            # automatiquement sur le modèle de repli recommandé.
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM_PROMPT,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
                },
                messages=[{"role": "user", "content": format_listing(listing, budget)}],
            )
        except anthropic.RateLimitError as e:
            raise AnalysisError(f"limite de débit Claude atteinte : {e}") from e
        except anthropic.APIStatusError as e:
            raise AnalysisError(f"erreur API Claude {e.status_code} : {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AnalysisError(f"connexion à l'API Claude impossible : {e}") from e

        if response.stop_reason == "refusal":
            raise AnalysisError("analyse refusée par le modèle")
        if response.stop_reason == "max_tokens":
            raise AnalysisError("réponse tronquée (max_tokens)")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise AnalysisError("aucun texte dans la réponse")
        data = json.loads(text)

        shipping = listing.shipping or 0.0
        return Analysis(
            score=max(0, min(100, int(data["score"]))),
            # Le prix conseillé ne dépasse jamais le budget configuré.
            prix_max=round(max(0.0, min(float(data["prix_max"]), budget - shipping)), 2),
            risques=list(data["risques"]),
            points_positifs=list(data["points_positifs"]),
            resume=data["resume"],
            recommandation=data["recommandation"],
            model=response.model,
        )
