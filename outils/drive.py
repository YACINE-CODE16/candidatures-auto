"""Dépôt des CV et lettres générés sur le Drive du candidat, pour la traçabilité.

Chaque dossier validé est copié dans « <dossier_drive de candidat.json> / <ID> – <Entreprise> – <Intitulé> / »,
et les liens vers le CV et la lettre sont écrits dans le Sheet, sur la ligne de l'offre.
L'accès utilise le jeton OAuth du candidat limité à drive.file : le pipeline ne voit que
les fichiers qu'il a lui-même créés.
"""

import json
import re
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession

from outils.config import candidat
from outils.google_auth import identifiants
from outils.lancement import RACINE

API = "https://www.googleapis.com/drive/v3/files"
API_ENVOI = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink"
MEMOIRE = RACINE / "secrets" / "drive.json"  # ID du dossier racine et du Sheet, pour ne pas les recréer


def memoire() -> dict:
    return json.loads(MEMOIRE.read_text()) if MEMOIRE.exists() else {}


def retenir(**valeurs) -> None:
    MEMOIRE.parent.mkdir(exist_ok=True)
    MEMOIRE.write_text(json.dumps(memoire() | valeurs))
    MEMOIRE.chmod(0o600)


def _session() -> AuthorizedSession:
    return AuthorizedSession(identifiants())


def _nom_propre(texte: str, longueur: int = 90) -> str:
    """Nom de fichier lisible : sans caractères interdits, sans espaces en trop."""
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]', " ", texte)).strip()[:longueur]


def _creer_dossier(session: AuthorizedSession, nom: str, parent: str | None = None) -> str:
    meta = {"name": nom, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        meta["parents"] = [parent]
    r = session.post(API, json=meta, params={"fields": "id"})
    r.raise_for_status()
    return r.json()["id"]


def _dossier_racine(session: AuthorizedSession) -> str:
    if id_dossier := memoire().get("dossier_racine"):
        r = session.get(f"{API}/{id_dossier}", params={"fields": "trashed"})
        if r.ok and not r.json().get("trashed"):
            return id_dossier
    id_dossier = _creer_dossier(session, candidat()["dossier_drive"])
    retenir(dossier_racine=id_dossier)
    return id_dossier


def _envoyer(session: AuthorizedSession, chemin: Path, nom: str, parent: str) -> str:
    meta = json.dumps({"name": nom, "parents": [parent]})
    r = session.post(API_ENVOI, files={
        "metadata": ("metadata", meta, "application/json; charset=UTF-8"),
        "file": (nom, chemin.read_bytes(), "application/pdf"),
    })
    r.raise_for_status()
    return r.json()["webViewLink"]


def deposer_dossier(offre: dict, cv_pdf: Path, lettre_pdf: Path) -> dict:
    """Copie le CV et la lettre sur le Drive. Renvoie {"lien_cv", "lien_lettre"}."""
    session = _session()
    titre = _nom_propre(f"{offre['id']} – {offre['entreprise']} – {offre['intitule']}")
    racine = _dossier_racine(session)
    # Un dossier régénéré remplace l'ancien : on met l'ancienne version à la corbeille.
    anciens = session.get(API, params={"q": f"'{racine}' in parents and name contains '{offre['id']}' "
                                            "and trashed = false", "fields": "files(id)"}).json()
    for ancien in anciens.get("files", []):
        session.patch(f"{API}/{ancien['id']}", json={"trashed": True})
    sous_dossier = _creer_dossier(session, titre, racine)
    return {
        "lien_cv": _envoyer(session, cv_pdf, cv_pdf.name, sous_dossier),
        "lien_lettre": _envoyer(session, lettre_pdf, lettre_pdf.name, sous_dossier),
    }
