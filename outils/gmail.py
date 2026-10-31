"""Boîte Gmail du candidat : lecture (codes de vérification, liens de confirmation, réponses, CV envoyé à soi-même) et un seul
envoi possible, un rappel adressé au candidat lui-même (envoyer_a_moi). Aucune suppression.
"""

import base64
import html
import re
import time
from email.message import EmailMessage

from google.auth.transport.requests import AuthorizedSession

from outils.config import candidat
from outils.google_auth import identifiants

API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
ENVOI = "https://www.googleapis.com/auth/gmail.send"


def message_pour_moi(objet: str, texte: str) -> EmailMessage:
    """Le destinataire est toujours l'adresse du candidat : aucun paramètre ne permet d'en choisir un autre."""
    m = EmailMessage()
    m["To"] = m["From"] = candidat()["email"]
    m["Subject"] = objet
    m.set_content(texte)
    return m


def envoyer_a_moi(objet: str, texte: str) -> bool:
    """Envoie un rappel au candidat. Renvoie False si le jeton n'a pas encore le droit d'envoi."""
    creds = identifiants()
    if ENVOI not in (creds.scopes or []):
        print("Rappel par mail non envoyé : relancer « python -m outils.google_auth lien » pour autoriser l'envoi.")
        return False
    brut = base64.urlsafe_b64encode(message_pour_moi(objet, texte).as_bytes()).decode()
    AuthorizedSession(creds).post(f"{API}/send", json={"raw": brut}).raise_for_status()
    return True


def _texte(partie: dict) -> str:
    """Texte lisible d'un message, liens compris (préfère la version HTML, qui contient les liens)."""
    if partie.get("mimeType", "").startswith("multipart/"):
        morceaux = {p.get("mimeType"): _texte(p) for p in partie.get("parts", [])}
        return morceaux.get("text/html") or next((m for m in morceaux.values() if m.strip()), "")
    donnees = partie.get("body", {}).get("data")
    if not donnees:
        return ""
    brut = base64.urlsafe_b64decode(donnees + "===").decode("utf-8", errors="ignore")
    if partie.get("mimeType") == "text/html":
        brut = re.sub(r"(?s)<(style|script).*?</\1>", "", brut)
        # Les liens (confirmation, réinitialisation) sont gardés : « texte [https://...] ».
        brut = re.sub(r"""(?is)<a\s[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", r" \2 [\1] ", brut)
        brut = html.unescape(re.sub(r"<[^>]+>", " ", brut))
    return re.sub(r"\s+", " ", brut).strip()


def _pieces_jointes(partie: dict) -> list[tuple[str, str]]:
    trouvees = [(partie["filename"], partie["body"]["attachmentId"])] if partie.get("filename") and partie.get("body", {}).get("attachmentId") else []
    return trouvees + [t for p in partie.get("parts", []) for t in _pieces_jointes(p)]


def derniere_piece_jointe(requete: str, extensions: tuple[str, ...]) -> tuple[str, bytes] | None:
    """(nom, contenu) de la pièce jointe la plus récente ; `extensions` par ordre de préférence dans un même mail."""
    session = AuthorizedSession(identifiants())
    for m in session.get(API, params={"q": requete, "maxResults": 10}).json().get("messages", []):
        payload = session.get(f"{API}/{m['id']}", params={"format": "full"}).json().get("payload", {})
        pieces = _pieces_jointes(payload)
        for extension in extensions:
            for nom, id_piece in pieces:
                if nom.lower().endswith(extension):
                    r = session.get(f"{API}/{m['id']}/attachments/{id_piece}")
                    r.raise_for_status()
                    return nom, base64.urlsafe_b64decode(r.json()["data"] + "===")
    return None


def chercher(requete: str, maximum: int = 5) -> list[dict]:
    """Messages correspondant à une recherche Gmail (ex. « newer_than:1h workable »), les plus récents d'abord."""
    session = AuthorizedSession(identifiants())
    ids = session.get(API, params={"q": requete, "maxResults": maximum}).json().get("messages", [])
    messages = []
    for m in ids:
        for essai in range(5):   # quota Gmail par minute dépassé : 429, ou 403 « Quota exceeded »
            r = session.get(f"{API}/{m['id']}", params={"format": "full"})
            quota = r.status_code == 429 or (r.status_code == 403 and "uota" in r.text)
            if not quota and r.status_code < 500:
                break
            time.sleep(4 * 2 ** essai)
        if r.status_code != 200:
            continue   # mail illisible : pas renvoyé, donc pas marqué comme lu, il sera repris plus tard
        d = r.json()
        entetes = {h["name"].lower(): h["value"] for h in d.get("payload", {}).get("headers", [])}
        messages.append({
            "id": m["id"],
            "date": entetes.get("date", ""),
            "de": entetes.get("from", ""),
            "objet": entetes.get("subject", ""),
            "texte": _texte(d.get("payload", {}))[:3000],
        })
    return messages
