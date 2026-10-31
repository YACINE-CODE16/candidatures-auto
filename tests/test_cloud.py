"""Mode cloud : lancer_cloud.sh (avec de faux outils), blocage sans écran, arrêt propre, reprise de l'installation."""

import asyncio
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

RACINE = Path(__file__).resolve().parent.parent
FAUX_PYTHON = """#!/usr/bin/env bash
echo "$*" >> "$JOURNAL"
case "$*" in
  *"coffre restaurer"*) mkdir -p secrets; exit "${CODE_RESTAURER:-0}";;
  *"sauvegarde sauvegarder"*) exit "${CODE_SAUVEGARDE:-0}";;
  main.py*) echo "CHROMIUM=$CHROMIUM_PATH CLOUD=$PIPELINE_CLOUD" >> "$JOURNAL"; exit "${CODE_MAIN:-0}";;
esac
exit 0
"""


@pytest.fixture
def cloud(tmp_path):
    """Un clone avec le vrai script, et de faux python, soffice, pdfinfo, pdftotext, npx et Chromium."""
    depot = tmp_path / "depot"
    (depot / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(RACINE / "lancer_cloud.sh", depot)
    faux = depot / ".venv" / "bin" / "python"
    faux.write_text(FAUX_PYTHON)
    faux.chmod(faux.stat().st_mode | stat.S_IEXEC)
    outils = tmp_path / "outils"
    outils.mkdir()
    for nom in ("soffice", "pdfinfo", "pdftotext", "npx"):
        (outils / nom).write_text("#!/bin/sh\nexit 0\n")
        (outils / nom).chmod(0o755)
    chrome = tmp_path / "home" / ".cache" / "ms-playwright" / "chromium-1" / "chrome-linux" / "chrome"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("")
    journal = tmp_path / "journal.txt"

    def lancer(*args, **env):
        reponse = subprocess.run(["bash", "lancer_cloud.sh", *args], cwd=depot, capture_output=True, text=True,
                                 env={"PATH": f"{outils}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "JOURNAL": str(journal), **env})
        return reponse, journal.read_text() if journal.exists() else ""
    return lancer


def test_ordre_normal(cloud):
    r, journal = cloud()
    lignes = journal.splitlines()
    assert r.returncode == 0
    assert [l.split()[0] if l.startswith("main.py") else l for l in lignes if "import" not in l and "CHROMIUM" not in l] == [
        "-m outils.coffre restaurer", "main.py", "-m outils.sauvegarde sauvegarder"]
    assert "chrome-linux/chrome" in journal and "CLOUD=1" in journal


def test_coffre_non_restaure_rien_n_est_lance(cloud):
    r, journal = cloud(CODE_RESTAURER="1")
    assert r.returncode != 0 and "coffre non restauré" in r.stdout
    assert "main.py" not in journal and "sauvegarder" not in journal


def test_relance_ne_restaure_pas_deux_fois(cloud):
    cloud()
    _, journal = cloud()
    assert journal.count("restaurer") == 1 and journal.count("main.py") == 2


def test_tranche_incomplete_annonce_la_suite(cloud):
    r, journal = cloud(CODE_MAIN="75")
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "A SUIVRE" and "sauvegarder" in journal


def test_echec_du_pipeline_sauvegarde_quand_meme(cloud):
    r, journal = cloud(CODE_MAIN="3")
    assert r.returncode == 3 and "sauvegarder" in journal and "A SUIVRE" not in r.stdout


def test_sauvegarde_en_echec_se_voit(cloud):
    r, _ = cloud(CODE_MAIN="75", CODE_SAUVEGARDE="1")
    assert r.returncode != 0 and "sauvegarde impossible" in r.stdout and "A SUIVRE" not in r.stdout


def test_dependances_seules(cloud):
    r, journal = cloud("--dependances")
    assert r.returncode == 0 and "main.py" not in journal and "restaurer" not in journal


def test_outil_manquant_est_nomme(tmp_path, cloud):
    os.unlink(tmp_path / "outils" / "pdftotext")
    r, _ = cloud("--dependances")
    if shutil.which("pdftotext", path="/usr/bin:/bin"):
        pytest.skip("pdftotext présent sur cette machine")
    assert r.returncode == 1 and "pdftotext" in r.stdout


# --------------------------------------------------------------------------
# Sans écran : l'offre passe en « Bloquée », avec le lien
# --------------------------------------------------------------------------

class FauxSheet:
    def __init__(self, *offres):
        self.offres, self.changements = list(offres), []

    def _charger(self):
        return self.offres

    def candidature_deja_envoyee(self, _offre):
        return None

    def changer_statut(self, id_offre, statut, **champs):
        self.changements.append((id_offre, statut, champs))


def offre(**champs):
    return {"id": "OF-1", "statut": "Validée", "url": "https://carrieres.acme.fr/offre/1", "motif_blocage": "", **champs}


@pytest.fixture
def dossier_pret(tmp_path, monkeypatch):
    import agents.postuleur as postuleur
    monkeypatch.setattr(postuleur, "DOSSIERS", tmp_path)
    (tmp_path / "OF-1").mkdir()
    (tmp_path / "OF-1" / "CV.pdf").write_text("x")
    (tmp_path / "OF-1" / "Lettre.pdf").write_text("x")
    return tmp_path / "OF-1"


def test_jamais_de_candidature_sans_pdf(dossier_pret, monkeypatch):
    import agents.postuleur as postuleur
    (dossier_pret / "Lettre.pdf").unlink()
    sheet = FauxSheet(offre())
    issue, _ = postuleur.controle_prealable(sheet, "OF-1", False)
    assert "absent" in issue and sheet.changements[0][1] == "Bloquée"


def site_a_captcha():
    return offre(id="OF-2", statut="Bloquée", motif_blocage="Postuleur : CAPTCHA", url="https://carrieres.acme.fr/offre/2")


def test_site_a_captcha_dans_le_cloud_bloque_avec_le_lien(dossier_pret, monkeypatch):
    import agents.postuleur as postuleur
    monkeypatch.setenv("PIPELINE_CLOUD", "1")
    sheet = FauxSheet(offre(), site_a_captcha())
    issue, _ = postuleur.controle_prealable(sheet, "OF-1", False)
    id_offre, statut, champs = sheet.changements[0]
    assert (id_offre, statut) == ("OF-1", "Bloquée") and "action humaine requise" in issue
    assert "Action humaine requise" in champs["motif_blocage"] and "https://carrieres.acme.fr/offre/1" in champs["motif_blocage"]


def test_site_a_captcha_sur_ordinateur_attend_le_mode_assiste(dossier_pret, monkeypatch):
    import agents.postuleur as postuleur
    monkeypatch.delenv("PIPELINE_CLOUD", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    sheet = FauxSheet(offre(), site_a_captcha())
    issue, _ = postuleur.controle_prealable(sheet, "OF-1", False)
    assert "Reportée" in issue and sheet.changements == []


def outil(sheet, offre_, garde, nom):
    import agents.postuleur as postuleur
    outils = {}
    original = postuleur.create_sdk_mcp_server
    postuleur.create_sdk_mcp_server = lambda name, tools: outils.update({t.name: t for t in tools})
    try:
        postuleur.serveur_postuleur(sheet, offre_, garde)
    finally:
        postuleur.create_sdk_mcp_server = original
    return outils[nom].handler


def test_demander_aide_dans_le_cloud_ne_touche_pas_a_macos(monkeypatch, dossier_pret):
    import agents.postuleur as postuleur
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setattr(postuleur.subprocess, "run", lambda *a, **k: pytest.fail("aucune commande système dans le cloud"))
    sheet, garde = FauxSheet(offre()), postuleur.Garde(False)
    asyncio.run(outil(sheet, offre(), garde, "demander_aide")({"message": "un CAPTCHA à résoudre"}))
    _, statut, champs = sheet.changements[0]
    assert statut == "Bloquée" and champs["motif_blocage"].startswith("Action humaine requise : un CAPTCHA")
    assert "https://carrieres.acme.fr/offre/1" in champs["motif_blocage"] and garde.issue.startswith("Bloquée")


def test_blocage_du_postuleur_donne_le_lien(dossier_pret):
    import agents.postuleur as postuleur
    sheet, garde = FauxSheet(offre()), postuleur.Garde(False)
    asyncio.run(outil(sheet, offre(), garde, "marquer_bloquee")({"id": "OF-1", "motif": "CAPTCHA"}))
    assert sheet.changements[0][2]["motif_blocage"] == "Postuleur : CAPTCHA → https://carrieres.acme.fr/offre/1"


def test_mode_assiste_refuse_dans_le_cloud(monkeypatch):
    import outils.assiste as assiste
    monkeypatch.setenv("PIPELINE_CLOUD", "1")
    monkeypatch.setattr("sys.argv", ["assiste"])
    with pytest.raises(SystemExit, match="demande un écran"):
        asyncio.run(assiste.main())


# --------------------------------------------------------------------------
# Arrêt propre
# --------------------------------------------------------------------------

def test_arret_demande_finit_proprement_et_garde_le_resume(monkeypatch, tmp_path):
    import main
    from outils.lancement import ArretDemande
    faites = []

    async def dossier(*_):
        faites.append("dossier")
        raise ArretDemande("temps écoulé")

    async def autre(nom):
        faites.append(nom)

    async def chasseur(*_):
        await autre("chasseur")

    async def postuleur(*_):
        await autre("postuleur")

    async def suivi(*_):
        await autre("suivi")

    monkeypatch.setattr(main, "etape_chasseur", chasseur)
    monkeypatch.setattr(main, "etape_lecteur", chasseur)
    monkeypatch.setattr(main, "etape_dossier", dossier)
    monkeypatch.setattr(main, "etape_postuleur", postuleur)
    monkeypatch.setattr(main, "etape_suivi", suivi)
    monkeypatch.setattr(main, "resume", lambda *a: "RÉSUMÉ\n")
    monkeypatch.setattr(main, "RACINE", tmp_path)
    monkeypatch.setattr("outils.sauvegarde.sauvegarder", lambda: faites.append("sauvegarde"))
    monkeypatch.setenv("VALIDATION_AUTO", "1")
    sheet = SimpleNamespace(supprimer_anciennes=lambda _j: [])
    args = SimpleNamespace(etapes="chasseur,lecteur,dossier,postuleur,suivi,resume", cycles=3, dry_run=False)
    journal = {"erreurs": []}
    asyncio.run(main._journee(sheet, journal, args))
    assert faites == ["chasseur", "chasseur", "dossier", "sauvegarde"]   # plus aucun agent après l'arrêt
    assert journal["a_suivre"] and journal["erreurs"] == []
    assert list((tmp_path / "logs").glob("resume_*.md"))[0].read_text() == "RÉSUMÉ\n\n"


def test_arret_demande_n_est_pas_l_echec_d_une_offre():
    from outils.lancement import ArretDemande
    assert not issubclass(ArretDemande, Exception)


# --------------------------------------------------------------------------
# Reprise de l'installation
# --------------------------------------------------------------------------

def test_reprise_a_la_bonne_etape():
    from outils.etat_installation import ETAPES, prochaine
    tout = dict.fromkeys(ETAPES, True)
    assert prochaine(tout) is None
    assert prochaine({**tout, "sheet": False, "coffre": False}) == "sheet"
    assert prochaine({**tout, "google": False}) == "google"


def test_etat_d_une_installation_a_moitie_faite(tmp_path, monkeypatch):
    import json

    import outils.etat_installation as e
    import outils.google_auth as g
    monkeypatch.setattr(e, "PROFIL", tmp_path / "profil")
    monkeypatch.setattr(e, "RACINE", tmp_path)
    monkeypatch.setattr(e, "_reseau", lambda: True)
    monkeypatch.setattr(g, "JETON", tmp_path / "absent.json")
    monkeypatch.delenv("COFFRE_CLE", raising=False)
    assert e.prochaine(e.etat()) == "google"
    monkeypatch.setattr(e, "_google", lambda: True)
    (tmp_path / "profil" / "modeles").mkdir(parents=True)
    (tmp_path / "profil" / "modeles" / "principal.blocs.txt").write_text("x")
    assert e.prochaine(e.etat()) == "criteres"
    (tmp_path / "profil" / "candidat.json").write_text(json.dumps({"prenom": "Sam"}))
    (tmp_path / "profil" / "coordonnees.json").write_text("{}")
    (tmp_path / "profil" / "verite.md").write_text("BROUILLON : à remplacer par « Validé par Sam le JJ/MM/AAAA »")
    assert e.prochaine(e.etat()) == "verite"
    (tmp_path / "profil" / "verite.md").write_text("Validé par Sam le 31/10/2026")
    assert e.prochaine(e.etat()) == "sheet"


def test_droits_google_manquants():
    from outils.google_auth import SCOPES, droits_manquants
    assert droits_manquants(SCOPES) == []
    assert droits_manquants(SCOPES[:-1]) == [SCOPES[-1]]
    assert len(SCOPES) == 4 and all("drive" in s or "spreadsheets" in s or "gmail" in s for s in SCOPES)
    assert not [s for s in SCOPES if s.endswith("/drive")]   # jamais tout le Drive
