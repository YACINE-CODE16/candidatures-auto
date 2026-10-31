"""Autorisation OAuth de l'utilisateur : Drive (fichiers créés par le pipeline), Sheets, Gmail (lecture, et
envoi de rappels à lui-même). Le Sheet est créé dans son Drive : rien à partager, pas de compte de service.

Le client OAuth est fourni hors du dépôt, dans la variable GOOGLE_CLIENT_JSON (ou secrets/client_oauth.json).

Autorisation en deux temps (fonctionne sur une machine sans navigateur) :
    python -m outils.google_auth lien             # affiche le lien à ouvrir
    python -m outils.google_auth code "<URL>"     # colle l'adresse de la page d'arrivée
Sur un ordinateur avec navigateur :
    python -m outils.google_auth local
"""

import json
import os
import sys
from urllib.parse import parse_qs, urlparse

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

from outils.lancement import RACINE

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",      # seulement les fichiers créés par le pipeline
    "https://www.googleapis.com/auth/spreadsheets",    # le Sheet de suivi
    "https://www.googleapis.com/auth/gmail.readonly",  # lecture ; jamais de suppression
    "https://www.googleapis.com/auth/gmail.send",      # seulement outils.gmail.envoyer_a_moi (à l'utilisateur)
]
CLIENT = RACINE / "secrets" / "client_oauth.json"
JETON = RACINE / "secrets" / "token_google.json"
_EN_ATTENTE = RACINE / "secrets" / ".oauth_en_attente.json"  # state + code_verifier entre les deux étapes
REDIRECTION = "http://localhost"
EXPIREE = ("Autorisation Google expirée ou retirée. Dans une session Claude Code, écris « reprends l'installation » : "
           "il faudra autoriser à nouveau Google, puis remplacer COFFRE_CLE dans les réglages de l'environnement cloud.")


def _fichier_client() -> str:
    """Chemin du client OAuth, écrit depuis GOOGLE_CLIENT_JSON s'il n'est pas encore sur la machine."""
    if not CLIENT.exists():
        if not os.getenv("GOOGLE_CLIENT_JSON"):
            raise RuntimeError("GOOGLE_CLIENT_JSON absente : ajoute la valeur reçue aux variables de l'environnement cloud.")
        CLIENT.parent.mkdir(exist_ok=True)
        CLIENT.write_text(os.environ["GOOGLE_CLIENT_JSON"])
        CLIENT.chmod(0o600)
    return str(CLIENT)


def client_oauth() -> dict:
    """{client_id, client_secret, token_uri…} du client OAuth."""
    brut = json.loads(open(_fichier_client()).read())
    return brut.get("installed") or brut.get("web") or brut


def identifiants() -> Credentials:
    """Identifiants de l'utilisateur, rafraîchis automatiquement quand ils expirent."""
    if not JETON.exists():
        raise RuntimeError("Jeton Google absent : lancer « python -m outils.google_auth lien ».")
    creds = Credentials.from_authorized_user_file(str(JETON))
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as erreur:
            raise RuntimeError(EXPIREE) from erreur
        JETON.write_text(creds.to_json())
    return creds


def _enregistrer(creds: Credentials) -> None:
    JETON.parent.mkdir(exist_ok=True)
    JETON.write_text(creds.to_json())
    JETON.chmod(0o600)
    print(f"Autorisation enregistrée dans {JETON.relative_to(RACINE)}.")


def droits_manquants(accordes) -> list[str]:
    """Google laisse décocher des cases sur son écran d'accord : le pipeline a besoin de toutes."""
    return [s for s in SCOPES if s not in (accordes or [])]


def lien() -> None:
    flow = Flow.from_client_secrets_file(_fichier_client(), scopes=SCOPES, redirect_uri=REDIRECTION,
                                         autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    _EN_ATTENTE.write_text(json.dumps({"state": state, "code_verifier": flow.code_verifier}))
    _EN_ATTENTE.chmod(0o600)
    print(url)


def code(url_retour: str) -> None:
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"   # une case décochée est signalée ci-dessous, pas par une exception obscure
    attente = json.loads(_EN_ATTENTE.read_text())
    params = parse_qs(urlparse(url_retour).query)
    if params.get("state", [None])[0] != attente["state"]:
        raise ValueError("Le paramètre « state » ne correspond pas : relancer l'étape « lien ».")
    flow = Flow.from_client_secrets_file(_fichier_client(), scopes=SCOPES, redirect_uri=REDIRECTION,
                                         state=attente["state"], code_verifier=attente["code_verifier"])
    flow.fetch_token(code=params["code"][0])
    if manquants := droits_manquants(flow.credentials.granted_scopes or flow.credentials.scopes):
        sys.exit("ERREUR : toutes les cases de l'écran Google doivent être cochées. Manquant : "
                 + ", ".join(s.rsplit("/", 1)[1] for s in manquants) + ". Relancer l'étape « lien ».")
    _enregistrer(flow.credentials)
    _EN_ATTENTE.unlink()


def local() -> None:
    _enregistrer(InstalledAppFlow.from_client_secrets_file(_fichier_client(), SCOPES).run_local_server(port=0))


if __name__ == "__main__":
    etape = sys.argv[1] if len(sys.argv) > 1 else "local"
    {"lien": lien, "local": local}.get(etape, lambda: code(sys.argv[2]))()
