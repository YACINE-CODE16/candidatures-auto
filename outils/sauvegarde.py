"""Sauvegarde et restauration de tout ce qui n'est pas sur GitHub, pour que le pipeline survive
à l'effacement de la machine cloud.

- Sur GitHub : le code.
- Sur le Drive du candidat (un fichier créé par le pipeline, donc accessible avec drive.file) :
  profil/ (verite.md, modèles de CV), projets/, mails déjà traités par le suivi. AUCUN secret.
- Dans les réglages de l'environnement cloud (variables d'environnement) : les secrets, c'est-à-dire
  PIPELINE_CLE (les trois fichiers d'accès Google) et les MDP_* des sites.

    python -m outils.sauvegarde sauvegarder
    python -m outils.sauvegarde restaurer
    python -m outils.sauvegarde cle > fichier   # valeur à coller dans PIPELINE_CLE
"""

import base64
import io
import json
import os
import sys
import tarfile

from outils.lancement import RACINE

NOM_ARCHIVE = "Pipeline candidatures - sauvegarde (privé, ne pas partager).tar.gz"
# Aucun secret ici (ni .env, ni mots de passe, ni accès Google) : ils restent dans les réglages de
# l'environnement cloud (variables d'environnement), jamais sur le Drive.
A_SAUVEGARDER = ["profil", "projets", "secrets/drive.json", "logs/suivi_mails_traites.json"]
FICHIERS_CLE = {"service_account": "secrets/service_account.json", "client_oauth": "secrets/client_oauth.json",
                "token_google": "secrets/token_google.json"}
API = "https://www.googleapis.com/drive/v3/files"
API_ENVOI = "https://www.googleapis.com/upload/drive/v3/files"


def _session():
    from google.auth.transport.requests import AuthorizedSession

    from outils.google_auth import identifiants
    return AuthorizedSession(identifiants())


def _id_archive(session) -> str | None:
    r = session.get(API, params={"q": f"name = '{NOM_ARCHIVE}' and trashed = false", "fields": "files(id)"})
    fichiers = r.json().get("files", [])
    return fichiers[0]["id"] if fichiers else None


def sauvegarder() -> None:
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w:gz") as archive:
        for chemin in A_SAUVEGARDER:
            if (RACINE / chemin).exists():
                archive.add(RACINE / chemin, arcname=chemin)
    session, contenu = _session(), tampon.getvalue()
    id_archive = _id_archive(session)
    if id_archive:
        r = session.patch(f"{API_ENVOI}/{id_archive}", params={"uploadType": "media"}, data=contenu,
                          headers={"Content-Type": "application/gzip"})
    else:
        r = session.post(f"{API_ENVOI}?uploadType=multipart", files={
            "metadata": ("metadata", json.dumps({"name": NOM_ARCHIVE}), "application/json; charset=UTF-8"),
            "file": (NOM_ARCHIVE, contenu, "application/gzip")})
    r.raise_for_status()
    print(f"Sauvegarde envoyée sur le Drive ({len(contenu) // 1024} Ko).")


def restaurer() -> None:
    """Recrée les fichiers d'accès depuis PIPELINE_CLE, puis récupère l'archive du Drive."""
    cle = os.getenv("PIPELINE_CLE")
    if not cle:
        sys.exit("PIPELINE_CLE absente : impossible de restaurer les accès Google.")
    for nom, contenu in json.loads(base64.b64decode(cle)).items():
        chemin = RACINE / FICHIERS_CLE[nom]
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(contenu)
        chemin.chmod(0o600)
    session = _session()
    id_archive = _id_archive(session)
    if not id_archive:
        sys.exit("Aucune sauvegarde trouvée sur le Drive.")
    r = session.get(f"{API}/{id_archive}", params={"alt": "media"})
    r.raise_for_status()
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as archive:
        archive.extractall(RACINE, filter="data")
    print("Restauration terminée : profil, projets, .env et comptes récupérés.")


def cle() -> str:
    """Valeur de PIPELINE_CLE : les 3 fichiers d'accès Google, en base64."""
    contenu = {nom: (RACINE / chemin).read_text() for nom, chemin in FICHIERS_CLE.items()}
    return base64.b64encode(json.dumps(contenu).encode()).decode()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "sauvegarder":
        sauvegarder()
    elif action == "restaurer":
        restaurer()
    elif action == "cle":
        print(cle())
    else:
        sys.exit(__doc__)
