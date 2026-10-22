# mba-hunter

Veille eBay.fr (enchères MacBook Air M2 13") → analyse Claude → SQLite → Telegram. Voir README.md.

- Règle métier : **jamais d'enchère automatique**. La validation Telegram renvoie seulement le lien + le prix max.
- Code et messages utilisateur en français.
- Config non secrète dans `config.yaml` ; secrets uniquement via variables d'environnement (`.env` en local, secrets GitHub en CI).
- `data/mba_hunter.db` est versionnée volontairement (persistance entre exécutions GitHub Actions).
- Tests : `pytest` ; aucun test ne doit appeler eBay, Claude ou Telegram pour de vrai.
- Code compatible Python 3.9 (`from __future__ import annotations`), CI en 3.12.
