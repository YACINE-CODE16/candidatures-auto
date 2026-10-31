"""Tests de outils/sheet.py, sur un faux Sheet en mémoire (aucun appel réseau).

Lancement : python -m pytest -q
"""

import re

import pytest

from outils.sheet import (
    CHAMPS,
    PipelineSheet,
    TransitionInterdite,
    calculer_id,
    cle_offre,
    normaliser_url,
)

# En-têtes dans un ordre volontairement différent de CHAMPS, avec une colonne
# calculée en plus : le code doit les retrouver par leur nom.
ENTETES = ["Entreprise", "Intitulé", "Relance prévue"] + [
    e for e in CHAMPS.values() if e not in ("Entreprise", "Intitulé")
]


class FauxOnglet:
    """Imite les méthodes de gspread.Worksheet utilisées par PipelineSheet."""

    def __init__(self, lignes=None):
        self.cellules = [list(ENTETES)] + [list(l) for l in (lignes or [])]
        self.row_count = 1000
        self.nb_appels_ecriture = 0

    def get_all_values(self):
        return [list(l) for l in self.cellules]

    def batch_update(self, maj, value_input_option=None):
        self.nb_appels_ecriture += 1
        for m in maj:
            lettres, ligne = re.fullmatch(r"([A-Z]+)(\d+)", m["range"]).groups()
            col = 0
            for c in lettres:
                col = col * 26 + ord(c) - 64
            ligne = int(ligne)
            while len(self.cellules) < ligne:
                self.cellules.append([""] * len(ENTETES))
            self.cellules[ligne - 1][col - 1] = m["values"][0][0]

    def add_rows(self, n):
        self.row_count += n

    def valeur(self, ligne, entete):
        return self.cellules[ligne - 1][ENTETES.index(entete)]


def ligne(**valeurs):
    """Construit une ligne brute du Sheet à partir de noms logiques."""
    l = [""] * len(ENTETES)
    for nom, v in valeurs.items():
        l[ENTETES.index(CHAMPS[nom])] = v
    return l


OFFRE = {
    "entreprise": "Acme",
    "intitule": "Stage - Data Scientist (H/F)",
    "url": "https://jobs.lever.co/acme/123?utm_source=x",
    "source": "Lever",
}


# ---- normalisation -------------------------------------------------------

def test_normaliser_url_retire_pistage_et_slash():
    assert normaliser_url("http://WWW.Site.com/offre/42/?utm_source=li&id=7#top") == \
        normaliser_url("https://site.com/offre/42?id=7")


def test_normaliser_url_garde_les_parametres_utiles():
    assert normaliser_url("https://bpi.tzportal.io/fr/apply?job=9993-39") != \
        normaliser_url("https://bpi.tzportal.io/fr/apply?job=9993-40")


def test_cle_offre_ignore_casse_accents_et_mention_de_genre():
    assert cle_offre("Société Générale", "Stage Data Scientist (H/F)") == \
        cle_offre("SOCIETE GENERALE", "Stage Data Scientist F/H")


def test_id_stable():
    assert calculer_id(OFFRE["url"]) == calculer_id("https://jobs.lever.co/acme/123")
    assert re.fullmatch(r"OF-[0-9a-f]{8}", calculer_id(OFFRE["url"]))


# ---- ajout et dédoublonnage ---------------------------------------------

def test_ajout_offre_nouvelle():
    onglet = FauxOnglet()
    id_offre = PipelineSheet(onglet).ajouter_offre(OFFRE)
    assert id_offre is not None
    assert onglet.valeur(2, "Statut") == "Nouvelle"
    assert onglet.valeur(2, "ID") == id_offre
    assert onglet.valeur(2, "Validée") == "FALSE"
    assert onglet.valeur(2, "Relance prévue") == ""  # colonne calculée jamais écrite
    assert onglet.nb_appels_ecriture == 1             # une seule requête par ligne


def test_doublon_meme_url():
    onglet = FauxOnglet()
    sheet = PipelineSheet(onglet)
    sheet.ajouter_offre(OFFRE)
    assert sheet.ajouter_offre({**OFFRE, "intitule": "Autre titre", "url": OFFRE["url"] + "&utm_medium=mail"}) is None


def test_doublon_meme_offre_sur_un_autre_site():
    onglet = FauxOnglet([ligne(entreprise="Acme", intitule="STAGE - DATA SCIENTIST F/H",
                               url="https://ecole.jobteaser.com/fr/job-offers/abc", statut="Envoyée")])
    assert PipelineSheet(onglet).ajouter_offre(OFFRE) is None


def test_offres_differentes_acceptees():
    sheet = PipelineSheet(FauxOnglet())
    assert sheet.ajouter_offre(OFFRE)
    assert sheet.ajouter_offre({**OFFRE, "intitule": "Stage - Quant Analyst", "url": "https://jobs.lever.co/acme/456"})


# ---- lecture par statut --------------------------------------------------

def test_lire_par_statut():
    onglet = FauxOnglet([
        ligne(id="OF-1", entreprise="A", intitule="x", statut="Qualifiée"),
        ligne(id="OF-2", entreprise="B", intitule="y", statut="Validée"),
        ligne(id="OF-3", entreprise="C", intitule="z", statut="À valider"),
    ])
    sheet = PipelineSheet(onglet)
    assert [o["id"] for o in sheet.lire_par_statut("Qualifiée")] == ["OF-1"]
    assert [o["id"] for o in sheet.lire_validees()] == ["OF-2"]
    with pytest.raises(ValueError):
        sheet.lire_par_statut("Envoyé")  # ancien vocabulaire refusé


# ---- changements de statut ----------------------------------------------

def test_transition_autorisee_et_date_envoi_auto():
    onglet = FauxOnglet([ligne(id="OF-1", entreprise="A", intitule="x", statut="Validée")])
    PipelineSheet(onglet).changer_statut("OF-1", "Envoyée")
    assert onglet.valeur(2, "Statut") == "Envoyée"
    assert onglet.valeur(2, "Date candidature") != ""


def test_transition_interdite():
    # Règle 2 : rien ne peut passer en « Envoyée » sans être passé par « Validée ».
    onglet = FauxOnglet([ligne(id="OF-1", entreprise="A", intitule="x", statut="À valider")])
    with pytest.raises(TransitionInterdite):
        PipelineSheet(onglet).changer_statut("OF-1", "Envoyée")
    assert onglet.valeur(2, "Statut") == "À valider"


def test_bloquee_exige_un_motif():
    onglet = FauxOnglet([ligne(id="OF-1", entreprise="A", intitule="x", statut="Validée")])
    sheet = PipelineSheet(onglet)
    with pytest.raises(ValueError):
        sheet.changer_statut("OF-1", "Bloquée")
    sheet.changer_statut("OF-1", "Bloquée", motif_blocage="Création de compte demandée")
    assert onglet.valeur(2, "Motif de blocage") == "Création de compte demandée"


def test_valider_cases_cochees():
    onglet = FauxOnglet([
        ligne(id="OF-1", entreprise="A", intitule="x", statut="À valider", validee="TRUE"),
        ligne(id="OF-2", entreprise="B", intitule="y", statut="À valider", validee="FALSE"),
        ligne(id="OF-3", entreprise="C", intitule="z", statut="Qualifiée", validee="TRUE"),
    ])
    assert PipelineSheet(onglet).valider_cases_cochees() == ["OF-1"]
    assert onglet.valeur(3, "Statut") == "À valider"
    assert onglet.valeur(4, "Statut") == "Qualifiée"


def test_colonne_manquante_detectee():
    onglet = FauxOnglet()
    onglet.cellules[0] = [e for e in ENTETES if e != "ID"]
    with pytest.raises(ValueError, match="ID"):
        PipelineSheet(onglet).lire_par_statut("Nouvelle")


def test_validation_automatique_qualifiee_vers_validee():
    # Choix du candidat : un dossier approuvé par le vérificateur peut passer directement en « Validée ».
    onglet = FauxOnglet([ligne(id="OF-1", entreprise="A", intitule="x", statut="Qualifiée")])
    PipelineSheet(onglet).changer_statut("OF-1", "Validée", validee="TRUE", lien_cv="https://drive/cv")
    assert onglet.valeur(2, "Statut") == "Validée"
    assert onglet.valeur(2, "Lien CV") == "https://drive/cv"


def test_candidature_deja_envoyee_meme_offre_titre_voisin():
    onglet = FauxOnglet([
        ligne(id="OF-1", entreprise="Renault", statut="Envoyée", url="https://renault.wd3/job/1",
              intitule="CS27 - Bac+5 Stage Deep Reinforcement Learning pour la Prise de Décision en Conduite Autonome (H/F)"),
    ])
    sheet = PipelineSheet(onglet)
    assert sheet.candidature_deja_envoyee({"id": "OF-2", "entreprise": "Renault", "url": "https://linkedin.com/x",
        "intitule": "Stage Deep Reinforcement Learning pour la prise de décision en conduite autonome"}) == "OF-1"
    assert sheet.candidature_deja_envoyee({"id": "OF-3", "entreprise": "Renault", "url": "https://renault.wd3/job/2",
        "intitule": "CS27 - Bac+5 Data Scientist modèles multimodaux"}) is None


def test_offres_de_moins_de_48_h_en_priorite():
    from datetime import date
    from outils.sheet import jours_depuis_publication, priorite
    j = date(2026, 10, 29)
    assert jours_depuis_publication({"date_publication": "Posted Yesterday (≈ 28/10/2026)"}, j) == 1
    assert jours_depuis_publication({"date_publication": "2026-10-20"}, j) == 9
    assert jours_depuis_publication({"date_publication": "Posted 30+ Days Ago"}, j) == 30
    assert jours_depuis_publication({"date_publication": "Non indiquée sur la page"}, j) is None
    offres = [{"date_publication": "2026-10-01", "score": "95"},
              {"date_publication": date.today().isoformat(), "score": "70"},
              {"date_publication": "", "score": "80"}]
    assert [o["score"] for o in sorted(offres, key=priorite)] == ["70", "95", "80"]


def test_nettoyage_garde_les_candidatures_et_supprime_les_vieilles_offres(tmp_path, monkeypatch):
    import outils.sheet as module
    from datetime import date, timedelta
    monkeypatch.setattr(module, "SUPPRIMEES", tmp_path / "supprimees.csv")
    monkeypatch.setattr(module, "DOSSIERS", tmp_path)
    (tmp_path / "OF-5").mkdir()
    for nom in ("cv.pdf", "lettre.pdf"):   # dossier prêt : gardé malgré son âge
        (tmp_path / "OF-5" / nom).write_text("x")
    module._cles_supprimees.cache_clear()
    vieux, recent = (date.today() - timedelta(days=10)).strftime("%d/%m/%Y"), date.today().strftime("%d/%m/%Y")
    onglet = FauxOnglet([
        ligne(id="OF-1", entreprise="A", intitule="Stage 1", statut="Envoyée", date_ajout=vieux),
        ligne(id="OF-2", entreprise="B", intitule="Stage 2", statut="Qualifiée", date_ajout=vieux, url="https://b.fr/2"),
        ligne(id="OF-3", entreprise="C", intitule="Stage 3", statut="Qualifiée", date_ajout=recent),
        ligne(id="OF-4", entreprise="D", intitule="Stage 4", statut="Bloquée"),   # aucune date : ligne migrée
        ligne(id="OF-5", entreprise="E", intitule="Stage 5", statut="Bloquée", date_ajout=vieux),
    ])
    requetes = []
    onglet.id = 0
    onglet.spreadsheet = type("Classeur", (), {"batch_update": lambda self, corps: requetes.extend(corps["requests"])})()
    sheet = PipelineSheet(onglet)
    assert [o["id"] for o in sheet.supprimer_anciennes(4)] == ["OF-2", "OF-4"]
    assert [r["deleteDimension"]["range"]["startIndex"] for r in requetes] == [4, 2]   # du bas vers le haut
    assert "OF-2" in (tmp_path / "supprimees.csv").read_text()
    assert sheet.ajouter_offre({"entreprise": "B", "intitule": "Stage 2", "url": "https://b.fr/2"}) is None


def test_verrou_un_seul_pipeline_a_la_fois():
    from outils.sheet import prendre_verrou, rendre_verrou

    class Onglet:
        valeur = ""
        def acell(self, _): return type("C", (), {"value": self.valeur})()
        def update_acell(self, _, v): self.valeur = v
    onglet = Onglet()
    sheet = type("S", (), {"feuille": type("F", (), {"spreadsheet": type("K", (), {"worksheet": lambda s, n: onglet})()})()})()
    assert prendre_verrou(sheet, "mac:1") is None
    assert prendre_verrou(sheet, "cloud:2").startswith("mac:1")     # refusé tant que le Mac tourne
    assert prendre_verrou(sheet, "mac:1") is None                   # le Mac rafraîchit son propre verrou
    rendre_verrou(sheet, "mac:1")
    assert prendre_verrou(sheet, "cloud:2") is None


def test_verrou_d_un_processus_mort_de_la_meme_machine_est_repris():
    import socket
    from outils.sheet import prendre_verrou

    class Onglet:
        valeur = f"{socket.gethostname()}:999999 | 2999-01-01T00:00:00+00:00"   # pid inexistant, verrou « récent »
        def acell(self, _): return type("C", (), {"value": self.valeur})()
        def update_acell(self, _, v): self.valeur = v
    sheet = type("S", (), {"feuille": type("F", (), {"spreadsheet": type("K", (), {"worksheet": lambda s, n: Onglet()})()})()})()
    assert prendre_verrou(sheet, "autre:1") is None
