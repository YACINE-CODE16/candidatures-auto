"""Lecture de pages web pour le chasseur, dans le respect des sites.

Garanties apportées par le code :
  - LinkedIn et Indeed ne sont jamais lus (leurs conditions interdisent l'accès automatisé) ;
  - le fichier robots.txt de chaque site est respecté ;
  - le robot se présente honnêtement (User-Agent explicite), sans se faire passer pour un navigateur ;
  - un site qui refuse (403, 429, protection anti-robot) est signalé, jamais contourné.
"""

import html
import re
from functools import lru_cache
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests


def _agent() -> str:
    from outils.config import email
    return f"PipelineCandidatures/1.0 (recherche d'emploi personnelle ; {email()})"


AGENT = _agent()
DOMAINES_INTERDITS = ("linkedin.com", "indeed.com", "indeed.fr")
TAILLE_MAX = 20000  # caractères de texte renvoyés à l'agent


class AccesRefuse(Exception):
    """Le site ne doit pas ou ne veut pas être lu automatiquement."""


def _domaine(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


@lru_cache(maxsize=256)
def _robots(base: str) -> RobotFileParser | None:
    robots = RobotFileParser()
    try:
        reponse = requests.get(f"{base}/robots.txt", headers={"User-Agent": AGENT}, timeout=15)
    except requests.RequestException:
        return None
    if reponse.status_code >= 400:
        return None  # pas de robots.txt : rien n'est interdit
    robots.parse(reponse.text.splitlines())
    return robots


def autorise(url: str) -> tuple[bool, str]:
    """(lecture permise ?, raison)."""
    domaine = _domaine(url)
    if any(domaine == d or domaine.endswith("." + d) for d in DOMAINES_INTERDITS):
        return False, f"{domaine} : accès automatisé interdit par ses conditions d'utilisation"
    morceaux = urlsplit(url)
    robots = _robots(f"{morceaux.scheme}://{morceaux.netloc}")
    if robots and not robots.can_fetch(AGENT, url):
        return False, f"{domaine} : page interdite aux robots par robots.txt"
    return True, ""


def texte_html(contenu: str) -> str:
    """Texte lisible d'une page HTML : sans scripts ni styles, liens conservés entre crochets."""
    contenu = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", contenu)
    contenu = re.sub(r'(?is)<a\s[^>]*href="(http[^"]+)"[^>]*>(.*?)</a>', r" \2 [\1] ", contenu)
    contenu = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|tr)>", "\n", contenu)
    contenu = html.unescape(re.sub(r"<[^>]+>", " ", contenu))
    contenu = re.sub(r"[ \t]+", " ", contenu)
    return re.sub(r"\n\s*\n+", "\n", contenu).strip()


def lire(url: str, json: bool = False):
    """Contenu d'une page (texte) ou d'une API publique (json=True). Lève AccesRefuse si interdit."""
    permis, raison = autorise(url)
    if not permis:
        raise AccesRefuse(raison)
    if "myworkdayjobs.com" in url and "/job/" in url and not json:
        return _workday_offre(url)
    try:
        reponse = requests.get(url, headers={"User-Agent": AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
                               timeout=30)
    except requests.RequestException as erreur:
        raise AccesRefuse(f"{_domaine(url)} : site injoignable ({erreur.__class__.__name__})") from erreur
    if reponse.status_code in (401, 403, 429) or reponse.status_code >= 500:
        raise AccesRefuse(f"{_domaine(url)} : accès refusé par le site (HTTP {reponse.status_code})")
    if reponse.status_code in (404, 410):
        raise AccesRefuse(f"{_domaine(url)} : page introuvable, offre probablement retirée (HTTP {reponse.status_code})")
    if json:
        return reponse.json()
    return texte_html(reponse.text)[:TAILLE_MAX]


# --------------------------------------------------------------------------
# Workday (Renault, Thales, Sanofi… ) : interface publique de recherche du site carrière
# --------------------------------------------------------------------------

def _workday(url: str) -> tuple[str, str, str]:
    """« https://<tenant>.wdN.myworkdayjobs.com/[fr-FR/]<site>/... » → (base, tenant, site)."""
    morceaux = urlsplit(url)
    chemin = [p for p in morceaux.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    return f"{morceaux.scheme}://{morceaux.netloc}", morceaux.netloc.split(".")[0], chemin[0]


def workday_chercher(url_site: str, mots_cles: str, limite: int = 60) -> list[dict]:
    """Offres d'un site carrière Workday correspondant aux mots-clés (titre, lieu, date, URL publique)."""
    permis, raison = autorise(url_site)
    if not permis:
        raise AccesRefuse(raison)
    base, tenant, site = _workday(url_site)
    offres = []
    for decalage in range(0, limite, 20):
        try:
            r = requests.post(f"{base}/wday/cxs/{tenant}/{site}/jobs", timeout=30,
                              headers={"User-Agent": AGENT, "Accept": "application/json"},
                              json={"appliedFacets": {}, "limit": 20, "offset": decalage, "searchText": mots_cles})
        except requests.RequestException as erreur:
            raise AccesRefuse(f"{tenant} : site Workday injoignable ({erreur.__class__.__name__})") from erreur
        if r.status_code != 200:
            raise AccesRefuse(f"{tenant} : recherche Workday refusée (HTTP {r.status_code})")
        lot = r.json().get("jobPostings", [])
        offres += [{"titre": o.get("title"), "lieu": o.get("locationsText"), "publiee": o.get("postedOn"),
                    "url": f"{base}/{site}{o['externalPath']}"} for o in lot if o.get("externalPath")]
        if len(lot) < 20:
            break
    return offres


def _workday_offre(url: str) -> str:
    """Texte d'une offre Workday (la page est en JavaScript ; son contenu est servi en JSON)."""
    base, tenant, site = _workday(url)
    chemin = url.split(f"/{site}", 1)[1]
    r = requests.get(f"{base}/wday/cxs/{tenant}/{site}{chemin}", timeout=30,
                     headers={"User-Agent": AGENT, "Accept": "application/json"})
    if r.status_code in (404, 410):
        raise AccesRefuse(f"{tenant} : offre introuvable, probablement retirée (HTTP {r.status_code})")
    if r.status_code != 200:
        raise AccesRefuse(f"{tenant} : accès refusé (HTTP {r.status_code})")
    info = r.json().get("jobPostingInfo", {})
    entete = "\n".join(f"{k} : {info[k]}" for k in ("title", "location", "postedOn", "startDate", "timeType",
                                                    "jobReqId") if info.get(k))
    return (entete + "\n\n" + texte_html(info.get("jobDescription", "")))[:TAILLE_MAX]
