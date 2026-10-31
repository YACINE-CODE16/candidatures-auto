#!/usr/bin/env bash
# Lancement dans le cloud (Claude Code sur le web). Sans danger si on le relance.
#
#   bash lancer_cloud.sh --dependances   # installe seulement ce qui manque
#   bash lancer_cloud.sh                 # restaure, travaille ~18 min au plus, sauvegarde
#
# Une commande ne peut durer qu'environ 30 min dans le cloud : au bout de 18 min, le pipeline ne lance plus
# d'agent (chacun est limité à 10 min), sauvegarde, et la dernière ligne affichée est « A SUIVRE » :
# il suffit alors de relancer, il reprend là où il en est.
set -uo pipefail
cd "$(dirname "$0")"
export PIPELINE_CLOUD=1
export DUREE_MAX_MIN="${DUREE_MAX_MIN:-18}"
export DUREE_AGENT_MAX_S="${DUREE_AGENT_MAX_S:-600}"
PY=.venv/bin/python
A_SUIVRE=75   # code de sortie de main.py quand il reste du travail

chromium() { find "$HOME/.cache/ms-playwright" -type f -name chrome 2>/dev/null | sort | tail -1; }

installer() {
  [ -x "$PY" ] || python3 -m venv .venv || { echo "ERREUR : Python 3 introuvable."; return 1; }
  "$PY" -c "import claude_agent_sdk, cryptography, gspread" 2>/dev/null \
    || .venv/bin/pip install -q -r requirements.txt || { echo "ERREUR : installation des dépendances Python impossible (réseau « Full » ?)."; return 1; }
  if ! command -v soffice >/dev/null || ! command -v pdfinfo >/dev/null; then
    local sudo=""; [ "$(id -u)" = 0 ] || sudo="sudo -n"
    $sudo apt-get update -qq && $sudo apt-get install -y -qq libreoffice-writer poppler-utils fonts-liberation \
      fonts-crosextra-carlito fonts-crosextra-caladea fonts-ebgaramond >/dev/null
  fi
  if [ -z "$(chromium)" ]; then
    local trace; trace=$(mktemp)
    npx -y playwright install --with-deps chromium >"$trace" 2>&1 || npx -y playwright install chromium >"$trace" 2>&1 || tail -5 "$trace"
    rm -f "$trace"
  fi
  local manque=""
  for outil in soffice pdfinfo pdftotext npx; do command -v "$outil" >/dev/null || manque="$manque $outil"; done
  [ -n "$(chromium)" ] || manque="$manque chromium"
  [ -z "$manque" ] || { echo "ERREUR : manquant :$manque. Ajoute le script d'installation aux réglages de l'environnement (voir README)."; return 1; }
}

installer || exit 1
[ "${1:-}" = "--dependances" ] && { echo "Dépendances prêtes."; exit 0; }
export CHROMIUM_PATH="${CHROMIUM_PATH:-$(chromium)}"

# Une seule restauration par machine : la relance d'une tranche ne doit pas écraser un travail plus récent.
if [ ! -f secrets/.restaure ]; then
  "$PY" -m outils.coffre restaurer || { echo "ERREUR : coffre non restauré, rien n'a été lancé. Voir le message ci-dessus."; exit 1; }
  touch secrets/.restaure
fi

"$PY" main.py "$@"
code=$?

"$PY" -m outils.sauvegarde sauvegarder || { echo "ERREUR : sauvegarde impossible, le travail de ce lancement pourrait être perdu."; [ "$code" = 0 ] && code=1; [ "$code" = "$A_SUIVRE" ] && code=1; }

if [ "$code" = "$A_SUIVRE" ]; then
  echo "A SUIVRE"
  exit 0
fi
exit "$code"
