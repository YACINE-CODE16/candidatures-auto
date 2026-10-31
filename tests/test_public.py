"""Le dépôt est public : aucune donnée personnelle dans le code, plafonds et paramètres lus depuis le profil."""

import json
import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
# Écrit en morceaux : ce fichier ne doit pas se trouver lui-même.
INTERDITS = re.compile("|".join(["ya" + "cine", "es" + "iea", "al" + "lam", "bn" + "p", r"\bma" + r"ki\b", "cac" + " ?40",
                                 "pipeline candidatures " + "20"]), re.I)


def fichiers_du_depot() -> list[Path]:
    sortie = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=RACINE,
                            capture_output=True, text=True, check=True).stdout
    return [RACINE / f for f in sortie.splitlines() if (RACINE / f).is_file()]


def test_aucune_mention_personnelle_dans_le_code():
    trouves = [f"{f.relative_to(RACINE)}:{i}" for f in fichiers_du_depot()
               for i, ligne in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
               if INTERDITS.search(ligne)]
    assert trouves == []


def test_aucun_document_personnel_versionne():
    noms = [f.relative_to(RACINE).as_posix() for f in fichiers_du_depot()]
    assert not [n for n in noms if n.endswith((".docx", ".pdf", ".bin")) or n.endswith("/verite.md")
                or n.startswith(("profil/", "secrets/", "navigateur/")) or n == ".env"]
    assert not [n for n in noms if n.startswith(("dossiers/", "logs/")) and not n.endswith(".gitkeep")]


def test_gitignore_protege_les_donnees():
    ignorees = subprocess.run(["git", "check-ignore", "profil/x", "secrets/x", ".env", "dossiers/x", "logs/x", "navigateur/x"],
                              cwd=RACINE, capture_output=True, text=True).stdout.split()
    assert len(ignorees) == 6


def test_profil_exemple_fictif():
    c = json.loads((RACINE / "profil.exemple" / "candidat.json").read_text(encoding="utf-8"))
    assert (c["prenom"], c["nom"]) == ("Sam", "MARTIN") and c["email"].endswith("@example.com")
    for champ in ("contrat", "zone", "date_debut", "annee", "secteurs", "metiers", "langues", "abonnement", "nom_sheet", "dossier_drive"):
        assert champ in c, champ
    assert "geoId" in c["zone"] and "libelle" in c["zone"]


def test_rien_de_propre_a_un_candidat_en_dur():
    motifs = re.compile(r"2027|104246759|f_JT=I|pas_un_stage|UNIQUEMENT DES STAGES")
    code = [f for f in fichiers_du_depot() if f.suffix == ".py" and f.parent.name in ("agents", "outils")]
    assert [f.name for f in code if motifs.search(f.read_text(encoding="utf-8"))] == []


@pytest.mark.parametrize("abonnement,dossiers,redacteur,verificateur,chasseur", [("pro", 10, "haiku", "sonnet", 1),
                                                                                 ("max", 0, "sonnet", "sonnet", 0)])
def test_plafonds_selon_l_abonnement(monkeypatch, abonnement, dossiers, redacteur, verificateur, chasseur):
    import outils.plafonds as p
    monkeypatch.setattr(p, "candidat", lambda: {"abonnement": abonnement})
    for nom in ("DOSSIERS_JOUR", "MODELE_REDACTEUR", "MODELE_VERIFICATEUR", "CHASSEUR_PAR_JOUR"):
        monkeypatch.delenv(nom, raising=False)
    assert p.plafond("DOSSIERS_JOUR") == dossiers and p.plafond("CHASSEUR_PAR_JOUR") == chasseur
    assert p.plafond("MODELE_REDACTEUR") == redacteur and p.plafond("MODELE_VERIFICATEUR") == verificateur
    monkeypatch.setenv("DOSSIERS_JOUR", "3")   # surcharge par .env
    assert p.plafond("DOSSIERS_JOUR") == 3


def test_plafond_quotidien_compte_sur_la_journee(tmp_path, monkeypatch):
    import outils.plafonds as p
    monkeypatch.setattr(p, "COMPTEURS", tmp_path / "compteurs.json")
    monkeypatch.setattr(p, "candidat", lambda: {"abonnement": "pro"})
    monkeypatch.delenv("ENVOIS_JOUR", raising=False)
    assert p.autorises("envois", 15) == 10
    for _ in range(8):
        p.compter("envois")
    assert p.autorises("envois", 15) == 2 and p.autorises("dossiers", 15) == 10
    (tmp_path / "compteurs.json").write_text(json.dumps({"date": "2000-01-01", "envois": 10}))   # un autre jour
    assert p.autorises("envois", 15) == 10
    monkeypatch.setattr(p, "candidat", lambda: {"abonnement": "max"})
    assert p.autorises("envois", 15) == 15


def test_comptes_proteges_jamais_crees(tmp_path, monkeypatch):
    import agents.postuleur as postuleur
    monkeypatch.setattr(postuleur, "COMPTES", tmp_path / "comptes.json")
    for nom in list(__import__("os").environ):
        if nom.startswith("MDP_"):
            monkeypatch.delenv(nom)
    assert postuleur.identifiants_fournis("linkedin.com") is None
    assert not (tmp_path / "comptes.json").exists()


def test_sources_connectees_desactivees_par_defaut(monkeypatch):
    from agents.lecteur import offres_a_lire
    monkeypatch.delenv("SOURCES_CONNECTEES", raising=False)
    base = {"source": "", "date_publication": "", "date_ajout": "", "statut": "Qualifiée", "motif_blocage": ""}
    offres = [{**base, "id": "OF-1", "url": "https://www.linkedin.com/jobs/view/1"},
              {**base, "id": "OF-2", "url": "https://www.welcometothejungle.com/fr/jobs/2"}]
    faux = type("F", (), {"_charger": lambda self: offres})()
    assert [o["id"] for o in offres_a_lire(faux)] == ["OF-2"]
    monkeypatch.setenv("SOURCES_CONNECTEES", "1")
    assert [o["id"] for o in offres_a_lire(faux)] == ["OF-1", "OF-2"]


def test_verite_validee_exige_une_vraie_validation(tmp_path, monkeypatch):
    import agents.dossier as dossier
    fichier = tmp_path / "verite.md"
    monkeypatch.setattr(dossier, "VERITE", fichier)
    fichier.write_text((RACINE / "profil.exemple" / "verite.exemple.md").read_text(encoding="utf-8"), encoding="utf-8")
    assert not dossier.verite_validee()   # le modèle cite la phrase à écrire : il n'est pas validé pour autant
    fichier.write_text(f"Validé par {dossier.PRENOM} le 31/10/2026\n", encoding="utf-8")
    assert dossier.verite_validee()


def test_onglet_profil_rempli_depuis_les_reponses(monkeypatch):
    import outils.initialiser_sheet as i
    reponses = dict(i.profil_candidat())
    assert reponses["Téléphone"] == "+33 6 00 00 00 00" and reponses["Permis de conduire"] == "Non"
    assert reponses["Niveau d'anglais"] == "B2" and "Convention de stage" in reponses
    monkeypatch.setattr(i, "contrat", lambda: "cdi")
    assert "Convention de stage" not in dict(i.profil_candidat())


def test_recherches_linkedin_depuis_le_profil(monkeypatch):
    import agents.lecteur as lecteur
    monkeypatch.setattr(lecteur, "candidat", lambda: {"metiers": ["Comptable"], "zone": {"libelle": "Lyon", "geoId": "123"}})
    monkeypatch.setattr(lecteur, "contrat", lambda: "cdi")
    assert lecteur.recherches("linkedin") == ["https://www.linkedin.com/jobs/search/?keywords=Comptable&location=Lyon&geoId=123"
                                              "&f_JT=F&f_TPR=r86400&sortBy=DD"]
