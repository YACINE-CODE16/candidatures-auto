"""Où en est l'installation ? L'assistant s'en sert pour reprendre à la bonne étape.

    python -m outils.etat_installation
"""

import json
import os
import re

import requests
from dotenv import load_dotenv

from outils.lancement import RACINE

PROFIL = RACINE / "profil"
ETAPES = ("reseau", "google", "cv", "criteres", "verite", "sheet", "test_a_blanc", "coffre")


def _reseau() -> bool:
    try:
        return requests.get("https://www.googleapis.com/discovery/v1/apis", timeout=10).ok
    except requests.RequestException:
        return False


def _google() -> bool:
    from outils.google_auth import JETON, identifiants
    if not JETON.exists():
        return False
    try:
        return identifiants().valid
    except Exception:   # jeton expiré ou retiré : l'étape est à refaire
        return False


def _verite() -> bool:
    fichier, candidat = PROFIL / "verite.md", PROFIL / "candidat.json"
    if not (fichier.exists() and candidat.exists()):
        return False
    prenom = json.loads(candidat.read_text(encoding="utf-8"))["prenom"]
    return bool(re.search(rf"Validé par {re.escape(prenom)} le \d{{2}}/\d{{2}}/\d{{4}}", fichier.read_text(encoding="utf-8")))


def _sheet() -> bool:
    fichier = RACINE / "secrets" / "drive.json"
    return fichier.exists() and bool(json.loads(fichier.read_text()).get("sheet_id"))


def etat() -> dict[str, bool]:
    return {
        "reseau": _reseau(),
        "google": _google(),
        "cv": (PROFIL / "modeles" / "principal.blocs.txt").exists(),
        "criteres": (PROFIL / "candidat.json").exists() and (PROFIL / "coordonnees.json").exists(),
        "verite": _verite(),
        "sheet": _sheet(),
        "test_a_blanc": any((RACINE / "dossiers").glob("*/CV_*.pdf")),
        "coffre": bool(os.getenv("COFFRE_CLE")) or "COFFRE_CLE=" in ((RACINE / ".env").read_text() if (RACINE / ".env").exists() else ""),
    }


def prochaine(valeurs: dict[str, bool]) -> str | None:
    return next((e for e in ETAPES if not valeurs[e]), None)


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    valeurs = etat()
    print(json.dumps({**valeurs, "prochaine_etape": prochaine(valeurs)}, indent=1))
