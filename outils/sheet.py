"""Accès au Google Sheet « Pipeline Candidatures 2027 », la mémoire partagée du pipeline.

Chaque agent lit les lignes qui ont un certain statut, fait son travail, puis écrit
le statut suivant. Tout ce qui ne demande pas de jugement est ici, en Python :
  - lire les offres par statut ;
  - ajouter une offre en évitant les doublons ;
  - changer un statut, uniquement selon les transitions autorisées.

Les colonnes sont retrouvées par leur **nom d'en-tête**, jamais par leur position :
on peut réordonner les colonnes du Sheet sans casser le code.
"""

import csv
import hashlib
import os
import re
import time
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# --------------------------------------------------------------------------
# Schéma
# --------------------------------------------------------------------------

# Nom logique (utilisé dans le code) → en-tête exact de la colonne dans le Sheet.
# Les colonnes calculées par formule (« Relance prévue », « Jours depuis candidature »)
# n'y figurent pas : le code ne les écrit jamais.
CHAMPS = {
    "id": "ID",
    "date_publication": "Publiée le",
    "date_ajout": "Date d'ajout",
    "entreprise": "Entreprise",
    "intitule": "Intitulé",
    "lieu": "Lieu",
    "duree": "Durée",
    "date_debut": "Début",
    "url": "Lien",
    "source": "Source",
    "nb_candidats": "Candidats (visible)",
    "score": "Score",
    "raison_score": "Raison du score",
    "cv": "CV recommandé",
    "chemin_dossier": "Chemin du dossier",
    "lien_cv": "Lien CV",
    "lien_lettre": "Lien lettre",
    "canal": "Canal (email/formulaire)",
    "statut": "Statut",
    "validee": "Validée",
    "date_envoi": "Date candidature",
    "motif_blocage": "Motif de blocage",
    "derniere_reponse": "Dernière réponse",
    "priorite": "Priorité",
    "notes": "Notes",
}

STATUTS = [
    "Nouvelle", "Qualifiée", "Écartée", "À valider", "Validée",
    "Envoyée", "Bloquée", "Réponse reçue", "Entretien", "Refus",
]

# Transitions qu'un agent a le droit de faire. Toi, tu restes libre de modifier
# le Sheet à la main ; cette règle ne s'applique qu'au code.
TRANSITIONS = {
    "Nouvelle": {"Qualifiée", "Écartée"},
    "Qualifiée": {"À valider", "Validée", "Bloquée", "Écartée"},  # « Validée » directe si VALIDATION_AUTO=1 ;
                                                                  # « Écartée » si l'offre a expiré
    "À valider": {"Validée"},                 # via ta case « Validée »
    "Validée": {"Envoyée", "Bloquée"},
    "Bloquée": {"Qualifiée", "Validée"},      # remise dans la file (outils/debloquer.py), sinon à la main
    "Envoyée": {"Réponse reçue", "Entretien", "Refus"},
    "Réponse reçue": {"Entretien", "Refus"},
    "Entretien": {"Refus"},
    "Écartée": set(),
    "Refus": set(),
}

POSTULEES = ("Envoyée", "Réponse reçue", "Entretien", "Refus")   # gardées pour la trace
SUPPRIMEES = Path(__file__).resolve().parent.parent / "logs" / "offres_supprimees.csv"
DOSSIERS = Path(__file__).resolve().parent.parent / "dossiers"

VRAI = "TRUE"  # valeur d'une case cochée lue par l'API Google Sheets

# Paramètres d'URL de pistage, retirés avant de comparer deux URL.
_PARAMS_PISTAGE = re.compile(r"^(utm_.*|ref|refid|src|source|trk.*|origin|from|gh_src|lever-source.*)$", re.I)
# Mentions de genre retirées des intitulés : « (H/F) », « F/H », « (H/F/X) », « (W/M) »...
_MENTION_GENRE = re.compile(r"\(?\b[hfmwx](\s*/\s*[hfmwx]){1,2}\b\)?", re.I)


class TransitionInterdite(Exception):
    """Un agent a tenté un changement de statut non prévu par TRANSITIONS."""


# --------------------------------------------------------------------------
# Normalisation et dédoublonnage (fonctions pures, faciles à tester)
# --------------------------------------------------------------------------

def normaliser_url(url: str) -> str:
    """Forme canonique d'une URL : sans pistage, sans ancre, sans « / » final, en minuscules pour l'hôte."""
    url = (url or "").strip()
    if not url:
        return ""
    morceaux = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(morceaux.query, keep_blank_values=True)
              if not _PARAMS_PISTAGE.match(k)]
    hote = morceaux.netloc.lower().removeprefix("www.")
    chemin = morceaux.path.rstrip("/")
    return urlunsplit(("https", hote, chemin, urlencode(sorted(params)), ""))


def _simplifier(texte: str) -> str:
    """Minuscules, sans accents, sans ponctuation, espaces réduits."""
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    texte = re.sub(r"[^a-z0-9]+", " ", texte.lower())
    return " ".join(texte.split())


def cle_offre(entreprise: str, intitule: str) -> str:
    """Clé « entreprise + intitulé » : repère la même offre publiée sur deux sites."""
    return f"{_simplifier(entreprise)}|{_simplifier(_MENTION_GENRE.sub(' ', intitule or ''))}"


def calculer_id(url: str, entreprise: str = "", intitule: str = "") -> str:
    """ID stable, dérivé de l'URL (ou de entreprise + intitulé si l'URL manque)."""
    base = normaliser_url(url) or cle_offre(entreprise, intitule)
    return "OF-" + hashlib.sha1(base.encode()).hexdigest()[:8]


# --------------------------------------------------------------------------
# Accès au Sheet
# --------------------------------------------------------------------------

def jours_depuis_publication(offre: dict, aujourd_hui: date | None = None) -> int | None:
    """Âge de l'offre en jours, lu dans « Publiée le » (formats variés selon les sites), sinon None."""
    aujourd_hui = aujourd_hui or date.today()
    texte = str(offre.get("date_publication", ""))
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", texte):
        return (aujourd_hui - date(int(m[1]), int(m[2]), int(m[3]))).days
    if m := re.search(r"(\d{2})/(\d{2})/(\d{4})", texte):
        return (aujourd_hui - date(int(m[3]), int(m[2]), int(m[1]))).days
    if re.search(r"today|aujourd", texte, re.I):
        return 0
    if re.search(r"yesterday|hier", texte, re.I):
        return 1
    if m := re.search(r"(\d+)\+? days? ago|il y a (\d+) jours?", texte, re.I):
        return int(m[1] or m[2])
    if re.search(r"(minute|heure|hour)s?\b", texte, re.I):   # « il y a 5 heures », « 3 hours ago »
        return 0
    if m := re.search(r"(\d+) (semaine|week)s?", texte, re.I):
        return 7 * int(m[1])
    return None


def age_offre(offre: dict, aujourd_hui: date | None = None) -> int | None:
    """Âge en jours : date de publication, sinon date d'ajout au Sheet. None si aucune des deux n'est lisible
    (lignes migrées des anciennes sessions, antérieures à l'installation)."""
    aujourd_hui = aujourd_hui or date.today()
    age = jours_depuis_publication(offre, aujourd_hui)
    if age is None and (m := re.search(r"(\d{2})/(\d{2})/(\d{4})", str(offre.get("date_ajout", "")))):
        age = (aujourd_hui - date(int(m[3]), int(m[2]), int(m[1]))).days
    return age


def priorite(offre: dict) -> tuple:
    """Clé de tri : d'abord les offres publiées il y a 48 h au plus (être parmi les premiers à postuler),
    puis le meilleur score."""
    age = jours_depuis_publication(offre)
    score = int(offre["score"]) if str(offre.get("score", "")).isdigit() else 0
    return (not (age is not None and age <= 2), -score)


@lru_cache(maxsize=1)
def _cles_supprimees() -> frozenset:
    """Liens et clés entreprise + intitulé des offres supprimées par supprimer_anciennes."""
    if not SUPPRIMEES.exists():
        return frozenset()
    with SUPPRIMEES.open(encoding="utf-8") as f:
        return frozenset(c for o in csv.DictReader(f)
                         for c in (normaliser_url(o["url"]), cle_offre(o["entreprise"], o["intitule"])) if c)


def _a1(ligne: int, colonne: int) -> str:
    """(3, 2) → « B3 »."""
    lettres = ""
    while colonne:
        colonne, reste = divmod(colonne - 1, 26)
        lettres = chr(65 + reste) + lettres
    return f"{lettres}{ligne}"


class PipelineSheet:
    """Lecture et écriture de l'onglet des offres.

    `feuille` est un `gspread.Worksheet` (ou, dans les tests, un faux objet qui
    offre les mêmes méthodes : get_all_values, batch_update, row_count, add_rows).
    """

    def __init__(self, feuille):
        self.feuille = feuille

    # ---- lecture ---------------------------------------------------------

    def _charger(self) -> list[dict]:
        """Lit tout l'onglet et renvoie une liste de dicts {nom_logique: valeur, "_ligne": n}."""
        for essai in range(5):   # quota Google Sheets par minute (429) : deux pipelines lisent en même temps
            try:
                valeurs = self.feuille.get_all_values()
                break
            except Exception as erreur:
                if "429" not in str(erreur) or essai == 4:
                    raise
                time.sleep(20 * (essai + 1))
        if not valeurs:
            raise ValueError("L'onglet est vide : la ligne d'en-têtes est introuvable.")
        entetes = valeurs[0]
        manquantes = [e for e in CHAMPS.values() if e not in entetes]
        if manquantes:
            raise ValueError(f"Colonnes absentes du Sheet : {manquantes}. Lancer la migration du Sheet.")
        self._colonne = {nom: entetes.index(entete) + 1 for nom, entete in CHAMPS.items()}
        self._nb_lignes = len(valeurs)

        offres = []
        for numero, ligne in enumerate(valeurs[1:], start=2):
            ligne = ligne + [""] * (len(entetes) - len(ligne))
            if not any(cellule.strip() for cellule in ligne):
                continue
            offre = {nom: ligne[col - 1] for nom, col in self._colonne.items()}
            offre["_ligne"] = numero
            offres.append(offre)
        return offres

    def lire_par_statut(self, statut: str) -> list[dict]:
        """Toutes les offres qui ont exactement ce statut."""
        if statut not in STATUTS:
            raise ValueError(f"Statut inconnu : {statut!r}")
        return [o for o in self._charger() if o["statut"] == statut]

    def candidature_deja_envoyee(self, offre: dict) -> str | None:
        """ID d'une candidature déjà envoyée pour la même offre (même lien, ou intitulé très proche
        chez la même entreprise : une offre LinkedIn et sa page officielle ont souvent des titres voisins)."""
        mots = lambda t: set(_simplifier(_MENTION_GENRE.sub(" ", t or "")).split()) - {"stage", "h", "f", "intern", "internship"}
        url, titre = normaliser_url(offre.get("url", "")), mots(offre.get("intitule"))
        for o in self._charger():
            if o["id"] == offre.get("id") or o["statut"] not in ("Envoyée", "Réponse reçue", "Entretien", "Refus"):
                continue
            if url and normaliser_url(o["url"]) == url:
                return o["id"]
            if _simplifier(o["entreprise"]) == _simplifier(offre.get("entreprise")):
                autre = mots(o["intitule"])
                if titre and autre and len(titre & autre) / len(titre | autre) >= 0.6:
                    return o["id"]
        return None

    def lire_validees(self) -> list[dict]:
        """Seule lecture accessible au postuleur : les offres au statut « Validée », rien d'autre."""
        return self.lire_par_statut("Validée")

    # ---- écriture --------------------------------------------------------

    def _ecrire(self, ligne: int, champs: dict) -> None:
        """Écrit plusieurs cellules d'une même ligne en un seul appel à l'API."""
        inconnus = set(champs) - set(CHAMPS)
        if inconnus:
            raise ValueError(f"Champs inconnus : {sorted(inconnus)}")
        maj = [{"range": _a1(ligne, self._colonne[nom]), "values": [[valeur]]}
               for nom, valeur in champs.items()]
        self.feuille.batch_update(maj, value_input_option="USER_ENTERED")

    def ajouter_offre(self, offre: dict) -> str | None:
        """Ajoute une offre au statut « Nouvelle ».

        Renvoie son ID, ou None si elle existe déjà (même URL, ou même entreprise
        + même intitulé, quel que soit le site où elle a été trouvée).
        """
        if not offre.get("entreprise") or not offre.get("intitule"):
            raise ValueError("Une offre doit avoir au moins une entreprise et un intitulé.")

        existantes = self._charger()
        url = normaliser_url(offre.get("url", ""))
        cle = cle_offre(offre["entreprise"], offre["intitule"])
        if (url and url in _cles_supprimees()) or cle in _cles_supprimees():
            return None   # déjà vue puis supprimée (trop ancienne)
        for autre in existantes:
            if (url and normaliser_url(autre["url"]) == url) or cle_offre(autre["entreprise"], autre["intitule"]) == cle:
                return None

        id_offre = calculer_id(offre.get("url", ""), offre["entreprise"], offre["intitule"])
        ligne = self._nb_lignes + 1
        if ligne > self.feuille.row_count:
            self.feuille.add_rows(100)

        champs = {k: v for k, v in offre.items() if k in CHAMPS and v not in (None, "")}
        champs.update({
            "id": id_offre,
            "statut": "Nouvelle",
            "validee": "FALSE",
            "date_ajout": date.today().strftime("%d/%m/%Y"),
        })
        self._ecrire(ligne, champs)
        return id_offre

    def changer_statut(self, id_offre: str, nouveau: str, **champs) -> None:
        """Passe une offre à un nouveau statut et écrit d'autres champs en même temps.

        Exemple : changer_statut("OF-1a2b3c4d", "Bloquée", motif_blocage="Compte requis")
        """
        offre = next((o for o in self._charger() if o["id"] == id_offre), None)
        if offre is None:
            raise KeyError(f"Aucune offre avec l'ID {id_offre}")
        actuel = offre["statut"]
        if nouveau not in TRANSITIONS.get(actuel, set()):
            raise TransitionInterdite(f"{id_offre} : « {actuel} » → « {nouveau} » n'est pas autorisé.")
        if nouveau == "Bloquée" and not champs.get("motif_blocage"):
            raise ValueError("Passer en « Bloquée » exige un motif_blocage.")
        if nouveau == "Envoyée":
            champs.setdefault("date_envoi", date.today().strftime("%d/%m/%Y"))

        self._ecrire(offre["_ligne"], {"statut": nouveau, **champs})

    def mettre_a_jour(self, id_offre: str, **champs) -> None:
        """Écrit des champs d'une offre sans changer son statut (score, lien officiel, notes...)."""
        if "statut" in champs:
            raise ValueError("Pour changer un statut, utiliser changer_statut().")
        offre = next((o for o in self._charger() if o["id"] == id_offre), None)
        if offre is None:
            raise KeyError(f"Aucune offre avec l'ID {id_offre}")
        self._ecrire(offre["_ligne"], champs)

    def supprimer_anciennes(self, jours: int = 4) -> list[dict]:
        """Supprime les offres jamais postulées de plus de `jours` jours (ou sans aucune date), sauf celles dont
        le CV et la lettre sont déjà prêts (elles n'attendent qu'un envoi ou une réponse du candidat).
        Les candidatures envoyées restent, pour la trace. Chaque ligne supprimée est d'abord copiée dans
        logs/offres_supprimees.csv, qui sert aussi de mémoire anti-doublon (ajouter_offre).
        À n'appeler que quand aucun agent ne tourne : les écritures se font par numéro de ligne."""
        anciennes = [o for o in self._charger() if o["statut"] and o["statut"] not in POSTULEES   # lignes sans
                     and ((age := age_offre(o)) is None or age > jours)                           # statut : pas des offres
                     and len(list((DOSSIERS / o["id"]).glob("*.pdf"))) < 2]
        if not anciennes:
            return []
        neuf = not SUPPRIMEES.exists()
        SUPPRIMEES.parent.mkdir(exist_ok=True)
        with SUPPRIMEES.open("a", newline="", encoding="utf-8") as f:
            ecrivain = csv.DictWriter(f, fieldnames=["supprimee_le", *CHAMPS])
            if neuf:
                ecrivain.writeheader()
            for o in anciennes:
                ecrivain.writerow({"supprimee_le": date.today().isoformat(), **{k: o[k] for k in CHAMPS}})
        # Du bas vers le haut, pour que la suppression d'une ligne ne décale pas les suivantes.
        requetes = [{"deleteDimension": {"range": {"sheetId": self.feuille.id, "dimension": "ROWS",
                                                   "startIndex": o["_ligne"] - 1, "endIndex": o["_ligne"]}}}
                    for o in sorted(anciennes, key=lambda o: -o["_ligne"])]
        for debut in range(0, len(requetes), 50):   # par paquets : une seule grosse requête est refusée (503)
            self.feuille.spreadsheet.batch_update({"requests": requetes[debut:debut + 50]})
        _cles_supprimees.cache_clear()
        return anciennes

    def valider_cases_cochees(self) -> list[str]:
        """Passe en « Validée » les offres « À valider » dont tu as coché la case. Renvoie leurs ID."""
        validees = []
        for offre in self._charger():
            if offre["statut"] == "À valider" and offre["validee"].upper() == VRAI:
                self._ecrire(offre["_ligne"], {"statut": "Validée"})
                validees.append(offre["id"])
        return validees


def ouvrir_pipeline() -> PipelineSheet:
    """Connexion au vrai Sheet avec le compte de service (variables lues dans .env)."""
    import gspread

    client = gspread.service_account(filename=os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
    classeur = client.open_by_key(os.environ["SHEET_ID"])
    return PipelineSheet(classeur.worksheet(os.getenv("SHEET_ONGLET", "Offres")))


# --------------------------------------------------------------------------
# Verrou : un seul pipeline à la fois (Mac ou cloud), visible dans l'onglet « Verrou » du Sheet
# --------------------------------------------------------------------------

ONGLET_VERROU = "Verrou"
VERROU_PERIME_MIN = 90   # un verrou non rafraîchi depuis 90 min vient d'un pipeline arrêté brutalement


def _onglet_verrou(sheet):
    classeur = sheet.feuille.spreadsheet
    try:
        return classeur.worksheet(ONGLET_VERROU)
    except Exception:   # gspread.WorksheetNotFound : premier lancement
        return classeur.add_worksheet(ONGLET_VERROU, rows=2, cols=1)


def prendre_verrou(sheet, qui: str) -> str | None:
    """Pose (ou rafraîchit) le verrou. Renvoie le verrou d'un autre pipeline encore actif, sinon None."""
    from datetime import datetime, timedelta, timezone

    onglet = _onglet_verrou(sheet)
    actuel = onglet.acell("A1").value or ""
    if actuel and not actuel.startswith(qui + " | "):
        try:
            depuis = datetime.fromisoformat(actuel.split(" | ")[1])
        except (IndexError, ValueError):
            depuis = None
        if depuis and datetime.now(timezone.utc) - depuis < timedelta(minutes=VERROU_PERIME_MIN) \
                and not _processus_mort(actuel.split(" | ")[0]):
            return actuel
    onglet.update_acell("A1", f"{qui} | {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return None


def _processus_mort(proprietaire: str) -> bool:
    """Verrou laissé par un pipeline de CETTE machine qui s'est arrêté sans le rendre (« hôte:pid »)."""
    import socket

    hote, _, pid = proprietaire.rpartition(":")
    if hote != socket.gethostname() or not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:   # le processus existe, sous un autre utilisateur
        return False


def rendre_verrou(sheet, qui: str) -> None:
    onglet = _onglet_verrou(sheet)
    if (onglet.acell("A1").value or "").startswith(qui + " | "):
        onglet.update_acell("A1", "")
