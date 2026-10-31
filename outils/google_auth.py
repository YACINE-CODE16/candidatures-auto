"""Autorisation OAuth du candidat (Drive : fichiers créés par l'application ; Gmail : lecture, et envoi de rappels au candidat seul).

Le compte de service suffit pour le Sheet, mais il ne peut ni déposer de fichiers sur un
Drive Gmail personnel (aucun espace de stockage) ni lire une boîte mail. Pour cela, le
pipeline agit au nom du candidat, avec un jeton enregistré dans secrets/token_google.json.

Autorisation en deux temps (fonctionne aussi sur une machine sans navigateur) :
    python -m outils.google_auth lien             # affiche le lien à ouvrir
    python -m outils.google_auth code "<URL>"     # colle l'adresse de la page d'arrivée
Sur un ordinateur avec navigateur :
    python -m outils.google_auth local
"""

import json
import sys
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

from outils.lancement import RACINE

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",      # seulement les fichiers créés par le pipeline
    "https://www.googleapis.com/auth/gmail.readonly",  # lecture ; jamais de suppression
    "https://www.googleapis.com/auth/gmail.send",      # seulement outils.gmail.envoyer_a_moi (au candidat)
]
CLIENT = RACINE / "secrets" / "client_oauth.json"
JETON = RACINE / "secrets" / "token_google.json"
_EN_ATTENTE = RACINE / "secrets" / ".oauth_en_attente.json"  # state + code_verifier entre les deux étapes
REDIRECTION = "http://localhost"


def identifiants() -> Credentials:
    """Identifiants du candidat, rafraîchis automatiquement quand ils expirent."""
    if not JETON.exists():
        raise RuntimeError("Jeton Google absent : lancer « python -m outils.google_auth lien ».")
    # Droits du jeton tels qu'accordés : un jeton plus ancien que l'ajout de gmail.send reste valable.
    creds = Credentials.from_authorized_user_file(str(JETON))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        JETON.write_text(creds.to_json())
    return creds


def _enregistrer(creds: Credentials) -> None:
    JETON.write_text(creds.to_json())
    JETON.chmod(0o600)
    print(f"Autorisation enregistrée dans {JETON.relative_to(RACINE)}.")


def lien() -> None:
    flow = Flow.from_client_secrets_file(str(CLIENT), scopes=SCOPES, redirect_uri=REDIRECTION,
                                         autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    _EN_ATTENTE.write_text(json.dumps({"state": state, "code_verifier": flow.code_verifier}))
    _EN_ATTENTE.chmod(0o600)
    print(url)


def code(url_retour: str) -> None:
    attente = json.loads(_EN_ATTENTE.read_text())
    params = parse_qs(urlparse(url_retour).query)
    if params.get("state", [None])[0] != attente["state"]:
        raise ValueError("Le paramètre « state » ne correspond pas : relancer l'étape « lien ».")
    flow = Flow.from_client_secrets_file(str(CLIENT), scopes=SCOPES, redirect_uri=REDIRECTION,
                                         state=attente["state"], code_verifier=attente["code_verifier"])
    flow.fetch_token(code=params["code"][0])
    _enregistrer(flow.credentials)
    _EN_ATTENTE.unlink()


def local() -> None:
    _enregistrer(InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES).run_local_server(port=0))


if __name__ == "__main__":
    etape = sys.argv[1] if len(sys.argv) > 1 else "local"
    {"lien": lien, "local": local}.get(etape, lambda: code(sys.argv[2]))()
