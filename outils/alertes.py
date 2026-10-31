"""Pistes d'offres tirées des alertes emploi reçues dans le Gmail du candidat (lecture seule).

Les alertes LinkedIn Jobs listent des offres « Intitulé  Entreprise · Lieu ». Le code n'ouvre jamais
LinkedIn : il extrait ces lignes du mail, puis le chasseur retrouve chaque offre sur le site de l'entreprise.
"""

import base64
import html
import json
import re

from google.auth.transport.requests import AuthorizedSession

from outils.config import hors_contrat
from outils.gmail import API
from outils.google_auth import identifiants
from outils.lancement import RACINE

TRAITEES = RACINE / "logs" / "alertes_traitees.json"
EXPEDITEURS = "from:(jobalerts-noreply@linkedin.com OR jobs-noreply@linkedin.com)"


def _html(partie: dict) -> str:
    if partie.get("mimeType") == "text/html" and partie.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(partie["body"]["data"] + "===").decode("utf-8", errors="ignore")
    return next((h for p in partie.get("parts", []) if (h := _html(p))), "")


def _offres_du_mail(brut: str) -> dict[str, str]:
    """{id LinkedIn: « Intitulé Entreprise · Lieu »} pour chaque offre listée dans le mail."""
    brut = re.sub(r"(?s)<style.*?</style>", "", brut)
    brut = re.sub(r'<a [^>]*href="https://www\.linkedin\.com/comm/jobs/view/(\d+)[^"]*"[^>]*>', r" @@\1@@ ", brut)
    texte = html.unescape(re.sub(r"<[^>]+>", " ", brut))
    texte = re.sub(r"[\s͏‌\xa0]+", " ", texte)
    offres = {}
    morceaux = re.split(r"@@(\d+)@@", texte)
    for i in range(1, len(morceaux) - 1, 2):
        segment = morceaux[i + 1].strip()
        if segment and morceaux[i] not in offres:
            offres[morceaux[i]] = segment[:160]
    return offres


def nouvelles_pistes(jours: int = 30, maximum: int = 100) -> list[dict]:
    """Offres des alertes reçues ces `jours` derniers, pas encore traitées, hors autres contrats."""
    deja = set(json.loads(TRAITEES.read_text())) if TRAITEES.exists() else set()
    session = AuthorizedSession(identifiants())
    ids = session.get(API, params={"q": f"{EXPEDITEURS} newer_than:{jours}d", "maxResults": 200}).json()
    pistes = {}
    for m in ids.get("messages", []):
        d = session.get(f"{API}/{m['id']}", params={"format": "full"}).json()
        for id_li, texte in _offres_du_mail(_html(d.get("payload", {}))).items():
            if id_li in deja or id_li in pistes or hors_contrat(texte):
                continue
            pistes[id_li] = texte
    return [{"ref": k, "offre": v} for k, v in list(pistes.items())[:maximum]]


def marquer_traitees(refs: list[str]) -> None:
    deja = set(json.loads(TRAITEES.read_text())) if TRAITEES.exists() else set()
    TRAITEES.write_text(json.dumps(sorted(deja | set(refs))))
