"""Plafonds selon l'abonnement Claude (candidat.json : « abonnement »), surchargeables dans .env.

Pro : 10 dossiers et 10 envois par jour, rédacteur Haiku, chasseur une fois par jour.
Max : pas de plafond quotidien, 5 cycles par lancement. 0 = sans limite.
"""

import json
import os
from datetime import date

from outils.config import candidat
from outils.lancement import RACINE

DEFAUTS = {
    "pro": {"DOSSIERS_JOUR": 10, "ENVOIS_JOUR": 10, "CHASSEUR_PAR_JOUR": 1, "CYCLES": 1,
            "MODELE_REDACTEUR": "haiku", "MODELE_VERIFICATEUR": "sonnet"},
    "max": {"DOSSIERS_JOUR": 0, "ENVOIS_JOUR": 0, "CHASSEUR_PAR_JOUR": 0, "CYCLES": 5,
            "MODELE_REDACTEUR": "sonnet", "MODELE_VERIFICATEUR": "sonnet"},
}
COMPTEURS = RACINE / "logs" / "compteurs_du_jour.json"


def plafond(nom: str) -> int | str:
    """Valeur de .env si elle existe, sinon celle de l'abonnement (« pro » par défaut : le plus prudent)."""
    if valeur := os.getenv(nom):
        return int(valeur) if valeur.isdigit() else valeur
    return DEFAUTS[candidat().get("abonnement", "pro")][nom]


def _compteurs() -> dict:
    donnees = json.loads(COMPTEURS.read_text()) if COMPTEURS.exists() else {}
    return donnees if donnees.get("date") == date.today().isoformat() else {"date": date.today().isoformat()}


def autorises(quoi: str, demande: int) -> int:
    """Combien de « dossiers » ou d'« envois » faire maintenant, compte tenu de ce qui est déjà fait aujourd'hui."""
    limite = plafond(f"{quoi.upper()}_JOUR")
    return min(demande, max(0, limite - _compteurs().get(quoi, 0))) if limite else demande


def compter(quoi: str) -> None:
    donnees = _compteurs()
    donnees[quoi] = donnees.get(quoi, 0) + 1
    COMPTEURS.parent.mkdir(exist_ok=True)
    COMPTEURS.write_text(json.dumps(donnees))
