"""Coffre à secrets chiffré (Fernet), stocké sur le Drive de l'utilisateur.

Le coffre (coffre.bin) contient secrets/ (jeton Google, client OAuth, drive.json, comptes.json) et les
valeurs de .env (MDP_*, EMAIL_*, réglages). Côté cloud, il reste une seule variable secrète : COFFRE_CLE.

COFFRE_CLE contient la clé Fernet ET le jeton de rafraîchissement Google : sur une machine neuve, le jeton
est nécessaire pour télécharger le coffre, il ne peut donc pas se trouver seulement dedans.

    python -m outils.coffre cle           # crée COFFRE_CLE une seule fois (dans .env) et l'affiche
    python -m outils.coffre sauvegarder   # envoie le coffre sur le Drive
    python -m outils.coffre restaurer     # récupère le coffre et l'archive du profil
"""

import base64
import io
import json
import os
import sys
import tarfile

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values

from outils.lancement import RACINE

NOM_COFFRE = "coffre.bin"
SECRETS = RACINE / "secrets"
ENV = RACINE / ".env"
API = "https://www.googleapis.com/drive/v3/files"
API_ENVOI = "https://www.googleapis.com/upload/drive/v3/files"
JAMAIS_DANS_LE_COFFRE = {".oauth_en_attente.json", "comptes.verrou", ".restaure"}
VARIABLES_DE_LANCEMENT = ("COFFRE_CLE=", "GOOGLE_CLIENT_JSON=")   # déjà dans les réglages du cloud


class CoffreErreur(Exception):
    """Problème de coffre, avec un message que l'utilisateur peut comprendre."""


def message_erreur(erreur: Exception) -> str:
    """Texte affiché à la fin d'une commande en échec (lancer_cloud.sh s'arrête avec un code non nul)."""
    from google.auth.exceptions import RefreshError

    from outils.google_auth import EXPIREE
    return f"ERREUR : {EXPIREE if isinstance(erreur, RefreshError) else erreur}"


# --------------------------------------------------------------------------
# Clé
# --------------------------------------------------------------------------

def composer_cle(cle_fernet: str, jeton_rafraichissement: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"k": cle_fernet, "r": jeton_rafraichissement}).encode()).decode()


def _brut() -> str | None:
    return os.getenv("COFFRE_CLE") or (dotenv_values(ENV).get("COFFRE_CLE") if ENV.exists() else None)


def lire_cle() -> tuple[str, str]:
    """(clé Fernet, jeton de rafraîchissement) lus dans COFFRE_CLE."""
    brut = _brut()
    if not brut:
        raise CoffreErreur("COFFRE_CLE absente : ajoute-la aux variables de l'environnement cloud, "
                           "ou lance « python -m outils.coffre cle » sur la machine d'installation.")
    try:
        d = json.loads(base64.urlsafe_b64decode(brut))
        Fernet(d["k"])
        return d["k"], d["r"]
    except (ValueError, KeyError, TypeError) as erreur:
        raise CoffreErreur("COFFRE_CLE illisible : recopie-la en entier, sans espace ni retour à la ligne.") from erreur


def creer_cle() -> str:
    """Crée la clé une seule fois ; ensuite, seul le jeton Google est remis à jour (après une nouvelle autorisation)."""
    jeton = json.loads((SECRETS / "token_google.json").read_text())["refresh_token"]
    cle = composer_cle(lire_cle()[0] if _brut() else Fernet.generate_key().decode(), jeton)
    lignes = [l for l in ENV.read_text(encoding="utf-8").splitlines() if not l.startswith("COFFRE_CLE=")] if ENV.exists() else []
    ENV.write_text("\n".join(lignes + [f"COFFRE_CLE={cle}"]) + "\n", encoding="utf-8")
    ENV.chmod(0o600)
    os.environ["COFFRE_CLE"] = cle
    return cle


# --------------------------------------------------------------------------
# Chiffrement
# --------------------------------------------------------------------------

def chiffrer(donnees: bytes, cle_fernet: str) -> bytes:
    return Fernet(cle_fernet).encrypt(donnees)


def dechiffrer(donnees: bytes, cle_fernet: str) -> bytes:
    try:
        return Fernet(cle_fernet).decrypt(donnees)
    except InvalidToken as erreur:
        raise CoffreErreur("Déchiffrement impossible : COFFRE_CLE ne correspond pas à ce coffre (mauvaise clé "
                           "ou clé d'une autre installation), ou le fichier est abîmé.") from erreur


def emballer_secrets() -> bytes:
    """Archive en mémoire de secrets/ et de .env (sans les variables déjà dans les réglages du cloud)."""
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w:gz") as archive:
        for chemin in sorted(SECRETS.rglob("*")) if SECRETS.exists() else []:
            if chemin.is_file() and chemin.name not in JAMAIS_DANS_LE_COFFRE:
                archive.add(chemin, arcname=chemin.relative_to(RACINE).as_posix())
        if ENV.exists():
            contenu = "\n".join(l for l in ENV.read_text(encoding="utf-8").splitlines()
                                if not l.startswith(VARIABLES_DE_LANCEMENT)).encode()
            info = tarfile.TarInfo(".env")
            info.size = len(contenu)
            archive.addfile(info, io.BytesIO(contenu))
    return tampon.getvalue()


def deballer(contenu: bytes) -> list[str]:
    """Extrait une archive dans le dépôt et renvoie les chemins réellement restaurés."""
    gardees = [l for l in ENV.read_text(encoding="utf-8").splitlines() if l.startswith(VARIABLES_DE_LANCEMENT)] if ENV.exists() else []
    with tarfile.open(fileobj=io.BytesIO(contenu), mode="r:gz") as archive:
        noms = [m.name for m in archive.getmembers() if m.isfile()]
        archive.extractall(RACINE, filter="data")
    if ".env" in noms:
        ENV.write_text("\n".join(ENV.read_text(encoding="utf-8").splitlines() + gardees) + "\n", encoding="utf-8")
        ENV.chmod(0o600)
    for nom in noms:
        if nom.startswith("secrets/"):
            (RACINE / nom).chmod(0o600)
    return noms


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------

def session_drive():
    """Accès au Drive : le jeton local s'il existe, sinon (machine neuve) celui de COFFRE_CLE et GOOGLE_CLIENT_JSON."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    from outils.google_auth import JETON, client_oauth, identifiants

    if JETON.exists():
        return AuthorizedSession(identifiants())
    c = client_oauth()
    return AuthorizedSession(Credentials(None, refresh_token=lire_cle()[1], client_id=c["client_id"],
                                         client_secret=c["client_secret"], token_uri=c["token_uri"]))


def _id_fichier(session, nom: str) -> str | None:
    r = session.get(API, params={"q": f"name = '{nom}' and trashed = false", "fields": "files(id)"})
    r.raise_for_status()
    fichiers = r.json().get("files", [])
    return fichiers[0]["id"] if fichiers else None


def envoyer(session, nom: str, contenu: bytes) -> None:
    id_fichier = _id_fichier(session, nom)
    if id_fichier:
        r = session.patch(f"{API_ENVOI}/{id_fichier}", params={"uploadType": "media"}, data=contenu,
                          headers={"Content-Type": "application/octet-stream"})
    else:
        from outils.drive import _dossier_racine
        meta = {"name": nom, "parents": [_dossier_racine(session)]}
        r = session.post(f"{API_ENVOI}?uploadType=multipart", files={
            "metadata": ("metadata", json.dumps(meta), "application/json; charset=UTF-8"),
            "file": (nom, contenu, "application/octet-stream")})
    r.raise_for_status()


def recuperer(session, nom: str) -> bytes | None:
    id_fichier = _id_fichier(session, nom)
    if not id_fichier:
        return None
    r = session.get(f"{API}/{id_fichier}", params={"alt": "media"})
    r.raise_for_status()
    return r.content


def sauvegarder_coffre() -> None:
    envoyer(session_drive(), NOM_COFFRE, chiffrer(emballer_secrets(), lire_cle()[0]))


def restaurer_coffre() -> list[str]:
    session = session_drive()
    contenu = recuperer(session, NOM_COFFRE)
    if contenu is None:
        raise CoffreErreur(f"Aucun {NOM_COFFRE} sur le Drive : l'installation n'est pas allée jusqu'à l'étape du coffre.")
    return deballer(dechiffrer(contenu, lire_cle()[0]))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if action == "cle":
            print(creer_cle())
        elif action == "sauvegarder":
            sauvegarder_coffre()
            print("Coffre chiffré envoyé sur le Drive.")
        elif action == "restaurer":
            from outils.sauvegarde import restaurer
            restaurer()
        else:
            sys.exit(__doc__)
    except Exception as erreur:   # jeton expiré, Drive injoignable, mauvaise clé : un message, pas une trace
        sys.exit(message_erreur(erreur))
