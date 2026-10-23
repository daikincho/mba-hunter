# mba-hunter

Veille quotidienne des **enchères eBay.fr de MacBook M2 (Air et Pro)** :

1. recherche via l'eBay Browse API (`EBAY_FR`, enchères uniquement) ;
2. filtre par titre (regex) et par prix max (`config.yaml`, frais de port exclus par défaut) ;
3. analyse de chaque annonce par Claude → JSON `score` / `risques` / `prix_max` / `resume` / `recommandation` ;
4. stockage en SQLite (`data/mba_hunter.db`) ;
5. notification Telegram (lien, score, prix max conseillé, risques). Les boutons **✅ Valider** / **❌ Refuser** existent mais sont désactivés (`telegram.buttons: false`).

**Aucune enchère automatique** : le message contient le lien de l'annonce et le prix max conseillé. L'enchère se place à la main.

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
python -m mba_hunter chat-id         # affiche ton TELEGRAM_CHAT_ID (écris d'abord un message au bot)
python -m mba_hunter test-telegram   # envoie un message de test (vérifie TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID)
python -m mba_hunter probe 123456    # diagnostic : pourquoi cette annonce eBay n'est-elle pas trouvée ?
pytest                               # tests (pip install pytest)
```

### Revoir une annonce déjà traitée

`forget` supprime la ligne : au prochain `run`, l'annonce est traitée comme nouvelle (réanalysée par Claude, donc payant),
et renotifiée si elle est toujours dans les résultats eBay et passe les filtres.

Depuis GitHub, sans toucher à ton Mac : onglet *Actions → mba-hunter → Run workflow*, puis
- `command = list` : le tableau des annonces apparaît dans le log de l'étape « Lancer mba-hunter » ;
- `command = probe` et `target = 123456` (numéro de l'annonce sur eBay) : affiche l'annonce vue par l'API, puis teste plusieurs variantes de recherche pour voir à quelle étape elle disparaît ;
- `command = forget` et `target = 123456` (plusieurs IDs séparés par des espaces, ou `all`) : le workflow supprime les lignes et recommite la base.

En local, fais d'abord `git pull` (le bot recommite la base) et pense à pousser la base modifiée.

Réglages dans [config.yaml](config.yaml) : `filters.max_price`, motifs d'exclusion, modèle et effort Claude, score minimum pour notifier, nombre max d'analyses par passage.

Toutes les annonces analysées sont envoyées sur Telegram, y compris celles que Claude déconseille (prix max « aucun »). Seul `min_score_to_notify` peut les filtrer (0 par défaut). Le `prix_max` renvoyé par Claude est plafonné à `max_price`. Les frais de port ne comptent pas dans ce plafond (`filters.include_shipping: false`) ; mets `true` pour les inclure.

La recherche couvre les annonces de **tous les pays** livrables en France (`ebay.delivery_country: FR`) et de tout pays de vendeur (`ebay.item_location_region: WORLDWIDE`, équivalent de « Provenance : Monde entier » dans l'appli).

## GitHub Actions

Workflow [.github/workflows/hunt.yml](.github/workflows/hunt.yml) :

- **13h00 (heure de Paris) chaque jour** : `run` (recherche complète). `cron` ne connaît que l'UTC, donc deux déclenchements (11h et 12h UTC) existent et l'étape « Vérifier l'heure » ne garde que celui qui tombe à 13h à Paris, été comme hiver ;
- les boutons Telegram sont désactivés (`telegram.buttons: false`) : il n'y a plus de passage `callbacks` automatique. S'ils reviennent, remettre dans le workflow un passage toutes les 4 h (Telegram ne conserve les clics que 24 h) ;
- la base SQLite est recommitée dans le dépôt après chaque passage (`[skip ci]`).

À configurer :

1. *Settings → Secrets and variables → Actions → onglet **Secrets** → **New repository secret***, une fois pour chacune des 5 variables ci-dessus, avec exactement le même nom.
   - Il faut des **Repository secrets**. Ni *Variables* (visibles en clair, non lues par `secrets.*`), ni *Environment secrets* (rattachés à un environnement que le workflow ne déclare pas).
   - Un secret absent ou mal nommé n'est pas signalé par GitHub : le workflow reçoit simplement une valeur vide (« Variable d'environnement manquante » dans les logs).
2. *Settings → Actions → General → Workflow permissions* sur **Read and write**.

## Statuts en base

`notified` (en attente de décision) · `validated` · `refused` · `skipped` (analysée, non pertinente) · `error` (retentée au prochain passage).
