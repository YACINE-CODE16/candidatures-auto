"""Paramètres propres au candidat, lus dans profil/candidat.json (non versionné).

Tout ce qui est personnel (nom, email, contrat, zone, critères, modèles de CV) vit dans ce fichier,
jamais dans le code : le même dépôt sert à plusieurs personnes.
Modèle à copier : profil.exemple/candidat.json.
"""

import json
import os
import re
from functools import lru_cache

from outils.lancement import RACINE

FICHIER = RACINE / "profil" / "candidat.json"
EXEMPLE = RACINE / "profil.exemple" / "candidat.json"


@lru_cache(maxsize=1)
def candidat() -> dict:
    """Paramètres du candidat (l'exemple fictif sert seulement aux tests, avant l'installation)."""
    return json.loads((FICHIER if FICHIER.exists() else EXEMPLE).read_text(encoding="utf-8"))


def nom_complet() -> str:
    return f"{candidat()['prenom']} {candidat()['nom']}"


def prenom() -> str:
    return candidat()["prenom"]


def email() -> str:
    return candidat()["email"]


def en_cloud() -> bool:
    """Machine sans écran (Claude Code sur le web) : pas de mode assisté, l'offre passe en « Bloquée »."""
    return os.getenv("PIPELINE_CLOUD") == "1" or os.getenv("CLAUDE_CODE_REMOTE") == "true"


def sources_connectees() -> bool:
    """LinkedIn et JobTeaser connectés : désactivés par défaut (aucun mot de passe LinkedIn stocké)."""
    return os.getenv("SOURCES_CONNECTEES") == "1"


def projets_en_cours() -> bool:
    """Projets « En cours » ajoutés au CV : désactivés par défaut, à choisir par le candidat."""
    return os.getenv("PROJETS_EN_COURS") == "1"


# --------------------------------------------------------------------------
# Type de contrat
# --------------------------------------------------------------------------

_STAGE = r"stage|stagiaire|\bintern(ship)?s?\b|fin d.?[ée]tudes|graduate"
_ALTERNANCE = r"alternan|apprenti|contrat pro|professionnalisation|work.?study"
_SENIOR = r"senior|manager|\blead\b|\bhead\b|director|directeur|principal"
_VIE = re.compile(r"\bV\.?I\.?E\b")   # en capitales : « assurance vie » n'est pas un VIE

# shortcut: les codes JobTeaser hors « internship » n'ont pas été vérifiés (source désactivée par défaut).
CONTRATS = {
    "stage": {"pluriel": "STAGES", "un": "un stage", "fr": "stage", "en": "internship", "f_jt": "I", "jobteaser": "internship",
              "voulu": _STAGE, "refus": rf"\bcd[id]\b|{_ALTERNANCE}|{_SENIOR}",
              "exclus": "un CDI, un CDD, un poste senior / manager / lead / head / director, une alternance ou un VIE"},
    # LinkedIn n'a pas de type « alternance » : le mot-clé fait le tri, sans filtre f_JT.
    "alternance": {"pluriel": "ALTERNANCES", "un": "une alternance", "fr": "alternance", "en": "apprenticeship", "f_jt": "",
                   "jobteaser": "apprenticeship", "voulu": _ALTERNANCE, "refus": rf"\bcd[id]\b|{_STAGE}|{_SENIOR}",
                   "exclus": "un CDI, un CDD, un stage, un poste senior / manager / lead / head / director ou un VIE"},
    "cdi": {"pluriel": "CDI", "un": "un CDI", "fr": "CDI", "en": "permanent", "f_jt": "F", "jobteaser": "cdi",
            "voulu": r"\bcdi\b|permanent", "refus": rf"\bcdd\b|{_STAGE}|{_ALTERNANCE}",
            "exclus": "un CDD, un stage, une alternance ou un VIE"},
}


def contrat() -> str:
    return candidat().get("contrat", "stage")


def hors_contrat(offre: str, type_contrat: str | None = None) -> bool:
    """Titre ou URL d'une offre d'un autre type de contrat que celui recherché (sauf si elle mentionne aussi le bon)."""
    regle, offre = CONTRATS[type_contrat or contrat()], offre or ""
    return bool(re.search(regle["refus"], offre, re.I) or _VIE.search(offre)) and not re.search(regle["voulu"], offre, re.I)


def consigne_contrat() -> str:
    """Consigne des agents, générée depuis le contrat (la vérification reste faite par hors_contrat)."""
    r = CONTRATS[contrat()]
    return (f"UNIQUEMENT DES {r['pluriel']} : n'ouvre pas une page dont le titre ou l'URL indique {r['exclus']} "
            f"(sauf si c'est aussi {r['un']}) : passe à la suivante sans la lire. "
            "Une page carrière sans aucune offre pertinente : arrête-toi tout de suite.")


def libelle_contrat() -> str:
    return candidat().get("type_recherche") or CONTRATS[contrat()]["fr"]


def metiers() -> str:
    return ", ".join(candidat().get("metiers", []))


def zone() -> str:
    return candidat().get("zone", {}).get("libelle", "")


def mots_cles() -> str:
    """Recherches à essayer sur un site carrière : contrat + métier, en français et en anglais."""
    r, c = CONTRATS[contrat()], candidat()
    mots = [f"{r['fr']} {m}" for m in c.get("metiers", [])] + [m for m in c.get("metiers", [])]
    mots += [r["fr"], f"{r['en']} {c.get('annee', '')}".strip()]
    return ", ".join(f"« {m} »" for m in mots)


def criteres() -> str:
    """Critères et barème : ceux de candidat.json s'il les donne, sinon générés depuis ses champs."""
    c = candidat()
    if c.get("criteres"):
        return c["criteres"]
    secteurs = ", ".join(c.get("secteurs", [])) or "aucun en particulier"
    return (f"- {libelle_contrat()}, à partir de {c.get('date_debut', 'dès que possible')} ; "
            f"uniquement ce type de contrat ;\n"
            f"- zone : {zone()} ;\n- métiers : {metiers()} ;\n- secteurs prioritaires : {secteurs} ;\n"
            "- priorité aux offres récentes et peu postulées.\n"
            "Barème du score (0-100) : adéquation du poste (40), contrat et date (20), lieu (10), secteur prioritaire (15), "
            "fraîcheur de l'offre (15). Un critère éliminatoire (autre contrat, hors zone) donne un score inférieur à 40.")
