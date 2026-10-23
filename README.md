# mba-hunter

Veille quotidienne des **enchères eBay.fr de MacBook Air M2 13"** :

1. recherche via l'eBay Browse API (`EBAY_FR`, enchères uniquement) ;
2. filtre par titre (regex) et par prix max (`config.yaml`, port inclus) ;
3. analyse de chaque annonce par Claude → JSON `score` / `risques` / `prix_max` / `resume` / `recommandation` ;
4. stockage en SQLite (`data/mba_hunter.db`) ;
5. notification Telegram avec boutons **✅ Valider** / **❌ Refuser**.

**Aucune enchère automatique** : « Valider » renvoie le lien de l'annonce et le prix max conseillé. L'enchère se place à la main.

## Installation locale

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Python 3.10+ recommandé
pip install -r requirements.txt
cp .env.example .env   # puis remplir les clés
```

| Variable | Où l'obtenir |
|---|---|
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | [developer.ebay.com](https://developer.ebay.com/my/keys) → keyset **Production** (App ID / Cert ID) |
| `ANTHROPIC_API_KEY` | [platform.claude.com](https://platform.claude.com) |
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | envoyer un message au bot puis ouvrir `https://api.telegram.org/bot<TOKEN>/getUpdates` → `message.chat.id` |

## Utilisation

```bash
python -m mba_hunter run --dry-run   # recherche + analyse, affiche au lieu d'envoyer sur Telegram
python -m mba_hunter run             # clics en attente + recherche + notifications
python -m mba_hunter callbacks       # traite seulement les clics Valider/Refuser
python -m mba_hunter listen          # écoute les clics en continu (réponse immédiate)
python -m mba_hunter list            # affiche les annonces en base (--status notified|validated|refused|skipped|error)
python -m mba_hunter forget 123456   # retire une annonce de la base (ID de la colonne ID de `list`)
python -m mba_hunter forget --all    # vide la base
pytest                               # tests (pip install pytest)
```

### Revoir une annonce déjà traitée

`forget` supprime la ligne : au prochain `run`, l'annonce est traitée comme nouvelle (réanalysée par Claude, donc payant),
et renotifiée si elle est toujours dans les résultats eBay et passe les filtres.

Depuis GitHub, sans toucher à ton Mac : onglet *Actions → mba-hunter → Run workflow*, puis
- `command = list` : le tableau des annonces apparaît dans le log de l'étape « Lancer mba-hunter » ;
- `command = forget` et `target = 123456` (plusieurs IDs séparés par des espaces, ou `all`) : le workflow supprime les lignes et recommite la base.

En local, fais d'abord `git pull` (le bot recommite la base) et pense à pousser la base modifiée.

Réglages dans [config.yaml](config.yaml) : `filters.max_price`, motifs d'exclusion, modèle et effort Claude, score minimum pour notifier, nombre max d'analyses par passage.

Une annonce est notifiée si `score >= min_score_to_notify` **et** si le `prix_max` conseillé dépasse l'enchère actuelle. Le `prix_max` renvoyé par Claude est plafonné à `max_price − frais de port`.

## GitHub Actions

Workflow [.github/workflows/hunt.yml](.github/workflows/hunt.yml) :

- **06:00 UTC chaque jour** : `run` (recherche complète) ;
- **toutes les 4 h** : `callbacks`. Ce passage est nécessaire parce que Telegram ne conserve les clics que 24 h, et qu'aucun serveur n'écoute en continu. Après un clic, la réponse arrive donc dans les 4 h. Pour une réponse immédiate, lancer `listen` sur une machine allumée ;
- la base SQLite est recommitée dans le dépôt après chaque passage (`[skip ci]`).

À configurer :

1. *Settings → Secrets and variables → Actions → onglet **Secrets** → **New repository secret***, une fois pour chacune des 5 variables ci-dessus, avec exactement le même nom.
   - Il faut des **Repository secrets**. Ni *Variables* (visibles en clair, non lues par `secrets.*`), ni *Environment secrets* (rattachés à un environnement que le workflow ne déclare pas).
   - Un secret absent ou mal nommé n'est pas signalé par GitHub : le workflow reçoit simplement une valeur vide (« Variable d'environnement manquante » dans les logs).
2. *Settings → Actions → General → Workflow permissions* sur **Read and write**.

## Statuts en base

`notified` (en attente de décision) · `validated` · `refused` · `skipped` (analysée, non pertinente) · `error` (retentée au prochain passage).
