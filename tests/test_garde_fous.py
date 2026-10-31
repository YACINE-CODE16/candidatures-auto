"""Tests des garde-fous qui ne dépendent pas d'un agent (aucun appel réseau ni modèle)."""

import asyncio

from agents.postuleur import Garde, identifiants
from outils.web import autorise, texte_html


def test_linkedin_et_indeed_jamais_lus():
    assert autorise("https://www.linkedin.com/jobs/view/123")[0] is False
    assert autorise("https://fr.indeed.com/viewjob?jk=1")[0] is False


def test_texte_html_garde_les_liens_et_retire_les_scripts():
    t = texte_html('<script>x()</script><p>Offre</p><a href="https://site.fr/postuler">Postuler</a>')
    assert "x()" not in t and "Offre" in t and "[https://site.fr/postuler]" in t


def test_mode_test_bloque_tout_sauf_la_lecture_une_fois_le_formulaire_pret():
    garde = Garde(dry_run=True)
    avant = asyncio.run(garde.avant_outil({"tool_name": "mcp__navigateur__browser_click"}, None, None))
    assert avant == {}                                   # remplir le formulaire reste permis
    garde.formulaire_pret = True
    for outil, refuse in [("browser_click", True), ("browser_run_code_unsafe", True),
                          ("browser_press_key", True), ("browser_take_screenshot", False)]:
        r = asyncio.run(garde.avant_outil({"tool_name": f"mcp__navigateur__{outil}"}, None, None))
        assert (r.get("hookSpecificOutput", {}).get("permissionDecision") == "deny") is refuse


def test_envoi_reel_non_bloque():
    garde = Garde(dry_run=False)
    garde.formulaire_pret = True
    assert asyncio.run(garde.avant_outil({"tool_name": "mcp__navigateur__browser_click"}, None, None)) == {}


def test_mot_de_passe_env_par_mot_cle(monkeypatch):
    monkeypatch.setenv("MDP_THALESGROUP", "secret")
    compte = identifiants("careers.thalesgroup.com")
    assert compte["mot_de_passe"] == "secret" and compte["source"] == ".env"


def test_rappel_mail_toujours_adresse_au_candidat():
    from outils.config import candidat
    from outils.gmail import message_pour_moi
    m = message_pour_moi("Objet", "texte")
    assert m["To"] == candidat()["email"] and m["From"] == candidat()["email"]


def test_recapitulatif_tests_et_entretiens():
    from agents.suivi import recapitulatif
    objet, texte = recapitulatif([{"type": "Test en ligne", "etat": "À faire", "entreprise": "ACME", "poste": "",
                                   "echeance": "05/11", "lien": "https://test.example/x", "resume": "test en ligne"}])
    assert "1 test" in objet and "Lien : https://test.example/x" in texte and "poste non précisé" in texte


def test_liens_de_suivi_raccourcis_pour_que_l_agent_lise_le_mail():
    from agents.suivi import _sans_traceurs
    t = _sans_traceurs("[https://u1.ct.sendgrid.net/ls/click?upn=" + "x" * 2000 + "] Bonjour, test avant le 1 nov.")
    assert len(t) < 150 and "Bonjour, test avant le 1 nov." in t


def test_chasseur_ne_lit_que_le_contrat_recherche():
    from outils.config import hors_contrat
    for titre in ("Lead, AI Engineering (111210)", "Alternance Data Analyst", "VIE Data Scientist",
                  "https://x.fr/jobs/cdi-data-engineer"):
        assert hors_contrat(titre, "stage")
    for titre in ("Stage - Assistant Product Manager", "Actuaire assurance vie / Data, IA (F/H)",
                  "Stage de fin d'études / Alternance - Finance"):
        assert not hors_contrat(titre, "stage")
    assert hors_contrat("Stage Data Analyst", "alternance") and not hors_contrat("Alternance Data Analyst", "alternance")
    assert hors_contrat("Stage Data Analyst", "cdi") and hors_contrat("Internship Data", "cdi")
    assert not hors_contrat("Senior Data Engineer", "cdi") and not hors_contrat("CDI Manager Data", "cdi")
    assert not hors_contrat("International Business Developer", "cdi")   # « intern » n'est pas un stage
    assert hors_contrat("https://x.fr/jobs/alternance-data", "cdi")


def test_consignes_generees_depuis_le_contrat(monkeypatch):
    import outils.config as config
    monkeypatch.setattr(config, "candidat", lambda: {"contrat": "alternance", "metiers": ["Comptable"], "annee": "2027"})
    assert "UNIQUEMENT DES ALTERNANCES" in config.consigne_contrat()
    assert "« alternance Comptable »" in config.mots_cles() and "apprenticeship 2027" in config.mots_cles()
    monkeypatch.setattr(config, "candidat", lambda: {"contrat": "cdi", "metiers": ["Comptable"], "zone": {"libelle": "Lyon"}})
    assert "UNIQUEMENT DES CDI" in config.consigne_contrat() and "zone : Lyon" in config.criteres()


def test_site_sans_offre_pas_revisite_avant_deux_semaines(tmp_path, monkeypatch):
    import json
    from datetime import date, timedelta
    import outils.cibles as cibles
    monkeypatch.setattr(cibles, "DEJA_CIBLEES", tmp_path / "faites.json")
    monkeypatch.setattr(cibles, "candidat", lambda: {"entreprises_cibles": ["Alpha", "Beta"]})
    il_y_a_5_jours = (date.today() - timedelta(days=5)).isoformat()
    (tmp_path / "faites.json").write_text(json.dumps({"Alpha": il_y_a_5_jours + " sans offre", "Beta": il_y_a_5_jours}))
    assert cibles.prochaines(5) == ["Beta"]
