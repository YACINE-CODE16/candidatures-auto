#!/usr/bin/env bash
# À lancer avant chaque push : ce dépôt est public, tout ce qui part est public.
#   MOTS_INTERDITS='nom|prenom|mon.adresse@|0612345678|12 rue de ...' bash verifier_avant_push.sh
# MOTS_INTERDITS (regex) reste hors du dépôt : la liste contient justement ce qui ne doit pas s'y trouver.
set -u
cd "$(dirname "$0")"
echec=0
ko() { echo "ÉCHEC : $1"; echec=1; }

command -v gitleaks >/dev/null || ko "gitleaks absent (brew install gitleaks)"
gitleaks detect --source . --redact --no-banner >/dev/null 2>&1 || ko "gitleaks a trouvé un secret dans l'historique (gitleaks detect --source . -v)"
gitleaks detect --no-git --source . --redact --no-banner >/dev/null 2>&1 || ko "gitleaks a trouvé un secret dans un fichier"

suivis=$(git ls-files --cached --others --exclude-standard)
echo "$suivis" | grep -E '\.(docx|pdf|bin)$|(^|/)verite\.md$|(^|/)\.env$|^(profil|secrets|navigateur)/' && ko "fichier personnel versionné"
echo "$suivis" | grep -E '^(dossiers|logs)/' | grep -v '\.gitkeep$' && ko "dossiers/ ou logs/ versionné"
for dossier in profil/x secrets/x .env dossiers/x logs/x navigateur/x; do
  git check-ignore -q "$dossier" || ko "$dossier n'est pas dans .gitignore"
done
python3 - <<'E' || ko "profil.exemple n'est pas le candidat fictif Sam Martin"
import json; c = json.load(open("profil.exemple/candidat.json", encoding="utf-8"))
raise SystemExit(0 if (c["prenom"], c["nom"]) == ("Sam", "MARTIN") and c["email"].endswith("@example.com") else 1)
E

# Téléphones français et adresses postales, hors exemples fictifs et hors ce script.
echo "$suivis" | grep -v '^verifier_avant_push.sh$' | xargs grep -nIE '(\+33|\b0[1-9])([ .-]?[0-9]{2}){4}\b' 2>/dev/null \
  | grep -v '+33 6 00 00 00 00' && ko "numéro de téléphone"
echo "$suivis" | grep -v '^verifier_avant_push.sh$' | xargs grep -nIiE '\b[0-9]{1,4} (bis )?(rue|avenue|boulevard|allée|impasse|place|chemin) ' 2>/dev/null \
  | grep -v "1 rue de l'Exemple" && ko "adresse postale"
echo "$suivis" | grep -v '^verifier_avant_push.sh$' | xargs grep -nIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[a-z]{2,}' 2>/dev/null \
  | grep -vE 'example\.(com|fr)|users\.noreply\.github\.com|jobalerts-noreply@linkedin\.com|jobs-noreply@linkedin\.com' && ko "adresse e-mail"

if [ -n "${MOTS_INTERDITS:-}" ]; then
  echo "$suivis" | xargs grep -nIiE "$MOTS_INTERDITS" 2>/dev/null | cut -d: -f1,2 && ko "mot interdit dans un fichier (ligne ci-dessus)"
  git log --all --format='%an %ae %s%n%b' | grep -iE "$MOTS_INTERDITS" >/dev/null && ko "mot interdit dans l'historique git (auteur ou message)"
  git log --all -p | grep -qiE "$MOTS_INTERDITS" && ko "mot interdit dans un ancien commit"
else
  echo "ATTENTION : MOTS_INTERDITS non défini, seules les règles générales ont été vérifiées."
fi

[ "$echec" = 0 ] && echo "OK : rien de personnel détecté." || exit 1
