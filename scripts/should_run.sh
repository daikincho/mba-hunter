#!/usr/bin/env bash
# Décide si un déclenchement planifié doit lancer le run quotidien.
#   code 0 = lancer, code 1 = passer.
# Règle : il est au moins 13h à Paris ET aucun run quotidien n'a déjà réussi aujourd'hui (heure de Paris).
# Le run écrit la date du jour dans la table kv (clé last_daily_run) quand il se termine correctement.
# PARIS_HOUR et PARIS_TODAY permettent de simuler une heure dans les tests.
set -u
DB="${DB:-data/mba_hunter.db}"
HOUR="${PARIS_HOUR:-$(TZ=Europe/Paris date +%H)}"
TODAY="${PARIS_TODAY:-$(TZ=Europe/Paris date +%F)}"

if [ "$((10#$HOUR))" -lt 13 ]; then
  echo "Il est ${HOUR}h à Paris : pas encore 13h, on passe."
  exit 1
fi

LAST=""
if [ -f "$DB" ]; then
  LAST=$(sqlite3 "$DB" "SELECT value FROM kv WHERE key = 'last_daily_run'" 2>/dev/null || true)
fi
if [ "$LAST" = "$TODAY" ]; then
  echo "Le run du jour (${TODAY}) a déjà eu lieu, on passe."
  exit 1
fi
echo "Il est ${HOUR}h à Paris et aucun run n'a réussi aujourd'hui (dernier : ${LAST:-jamais}) : on lance."
exit 0
