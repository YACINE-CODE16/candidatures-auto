"""Préparation du CV : blocs, brouillon de verite.md, formats refusés, conversion réelle si LibreOffice est là."""

import base64
import shutil
import zipfile

import pytest

import outils.preparer_cv as p
import outils.traduire_modeles as t

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
TYPES = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
         'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
RELS = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')
CV_SAM = [["Sam MARTIN"], ["sam.martin@example.com | +33 6 00 00 00 00"], ["FORMATION"],
          ["Université Exemple", " - Master Analyse de données"], ["EXPÉRIENCES PROFESSIONNELLES"],
          ["ACME - Stagiaire Data Analyst, 2025"], ["Automatisation de 12 rapports mensuels pour 3 équipes"],
          ["PROJETS"], ["Prévision des ventes en Python"], ["INFORMATIONS COMPLÉMENTAIRES"],
          ["Langues: français, anglais B2"], ["Centres d'intérêt: course à pied"]]


def fabriquer_docx(chemin, paragraphes):
    corps = "".join("<w:p>" + "".join(f'<w:r><w:t xml:space="preserve">{r}</w:t></w:r>' for r in runs) + "</w:p>"
                    for runs in paragraphes)
    with zipfile.ZipFile(chemin, "w") as z:
        z.writestr("[Content_Types].xml", TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{corps}<w:sectPr/></w:body></w:document>')
    return chemin


@pytest.fixture
def profil(tmp_path, monkeypatch):
    for attribut, valeur in (("PROFIL", tmp_path), ("VERITE", tmp_path / "verite.md"), ("MODELES", tmp_path / "modeles"),
                             ("A_TRADUIRE", tmp_path / "a_faire.json"), ("RACINE", tmp_path)):
        monkeypatch.setattr(p, attribut, valeur)
    monkeypatch.setattr(p, "TRADUCTIONS_CANDIDAT", tmp_path / "traductions_cv.json")
    monkeypatch.setattr(t, "TRADUCTIONS_CANDIDAT", tmp_path / "traductions_cv.json")
    (tmp_path / "profil.exemple").mkdir()
    (tmp_path / "profil.exemple" / "GUIDE_AGENT_CV.md").write_text("guide")
    return tmp_path


def test_blocs_meme_decoupage_que_cv_edit(tmp_path):
    from outils.cv_edit import apply
    docx = fabriquer_docx(tmp_path / "cv.docx", [["Stage ", "Data &amp; IA"], ["Puce unique"]])
    assert t.blocs(docx) == "Stage ⟂Data & IA\nPuce unique\n"
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode()
    # un bloc recopié tel quel est accepté par cv_edit, y compris avec un « & »
    assert "Data &amp; Machine" in apply(xml, {"op": "replace", "old": "Data & IA", "new": "Data & Machine"})


def test_brouillon_classe_sans_valider_ni_inventer():
    texte = p.brouillon_verite(["".join(l) for l in CV_SAM])
    assert "# Source de vérité — Sam MARTIN" in texte
    assert "BROUILLON" in texte and "Validé par" not in texte
    assert "- sam.martin@example.com" in texte
    formation = texte.split("## Formation")[1].split("##")[0]
    assert "Université Exemple" in formation and "ACME" not in formation
    assert "Automatisation de 12 rapports" in texte.split("## Expériences professionnelles")[1].split("##")[0]
    assert "Prévision des ventes" in texte.split("## Projets")[1].split("##")[0]
    assert "français, anglais B2" in texte.split("## Langues")[1].split("##")[0]
    assert "course à pied" in texte.split("## Engagement et centres d'intérêt")[1].split("##")[0]
    assert "12 rapports" in texte.split("## Chiffres relevés")[1]
    assert "ACME - Stagiaire Data Analyst, 2025" not in texte.split("## Chiffres relevés")[1]   # une année n'est pas un chiffre à vérifier


def test_brouillon_n_ouvre_pas_le_garde_fou(tmp_path, monkeypatch):
    import agents.dossier as dossier
    monkeypatch.setattr(dossier, "VERITE", tmp_path / "v.md")
    (tmp_path / "v.md").write_text(p.brouillon_verite(["Sam MARTIN"]), encoding="utf-8")
    assert not dossier.verite_validee()


def test_titres_et_lignes_ordinaires():
    assert p._section("EXPÉRIENCES PROFESSIONNELLES") == "Expériences professionnelles"
    assert p._section("Formation") == "Formation"
    assert p._section("Mes compétences") == "Compétences"
    assert p._section("Développement de 12 pipelines") is None
    assert p._section("Expérience de 3 ans chez ACME dans la finance de marché et les risques") is None


@pytest.mark.parametrize("nom,message", [("cv.pdf", "format PDF"), ("cv.odt", "que les fichiers .docx")])
def test_formats_refuses(profil, nom, message):
    with pytest.raises(SystemExit, match=message):
        p.preparer(profil / nom)


def test_faux_docx_refuse(profil):
    (profil / "cv.docx").write_text("pas un zip")
    with pytest.raises(SystemExit, match="pas un vrai fichier Word"):
        p.preparer(profil / "cv.docx")


def test_verite_existante_jamais_ecrasee(profil, monkeypatch):
    monkeypatch.setattr(p, "verifier", lambda docx: (1, ""))
    (profil / "verite.md").write_text("Validé par Sam le 01/11/2026")
    p.preparer(fabriquer_docx(profil / "cv.docx", CV_SAM))
    assert (profil / "verite.md").read_text() == "Validé par Sam le 01/11/2026"


def test_cv_de_deux_pages_refuse(profil, monkeypatch):
    monkeypatch.setattr(p, "verifier", lambda docx: (2, ""))
    with pytest.raises(SystemExit, match="fait 2 pages"):
        p.preparer(fabriquer_docx(profil / "cv.docx", CV_SAM))


def test_morceaux_restant_a_traduire(tmp_path):
    docx = fabriquer_docx(tmp_path / "cv.docx", [["PROJETS"], ["Prévision des ventes"], ["2025"]])
    assert p.blocs_sans_traduction(docx) == ["Prévision des ventes"]


def test_cv_dans_gmail(monkeypatch):
    import outils.gmail as gmail

    class Session:
        def __init__(self, _creds):
            pass

        def get(self, url, params=None):
            contenu = {"messages": [{"id": "m1"}]} if url == gmail.API else (
                {"data": base64.urlsafe_b64encode(b"octets").decode()} if url.endswith("/attachments/a1") else
                {"payload": {"parts": [{"filename": "photo.png", "body": {"attachmentId": "a0"}},
                                       {"parts": [{"filename": "Mon CV.DOCX", "body": {"attachmentId": "a1"}}]}]}})
            return type("R", (), {"json": lambda self: contenu, "raise_for_status": lambda self: None})()
    monkeypatch.setattr(gmail, "AuthorizedSession", Session)
    monkeypatch.setattr(gmail, "identifiants", lambda: None)
    assert gmail.derniere_piece_jointe("x", (".docx", ".pdf")) == ("Mon CV.DOCX", b"octets")


@pytest.mark.skipif(not (shutil.which("soffice") and shutil.which("pdfinfo")), reason="LibreOffice ou poppler absent")
def test_preparation_complete_avec_libreoffice(profil):
    p.preparer(fabriquer_docx(profil / "CV Sam.docx", CV_SAM))
    assert (profil / "modeles" / "principal.docx").exists() and (profil / "modeles" / "principal_EN.docx").exists()
    assert "Prévision des ventes" in (profil / "modeles" / "principal.blocs.txt").read_text()
    assert "PROJECTS" in (profil / "modeles" / "principal_EN.blocs.txt").read_text()
    assert "BROUILLON" in (profil / "verite.md").read_text() and (profil / "GUIDE_AGENT_CV.md").exists()
    assert "Prévision des ventes" in (profil / "a_faire.json").read_text()
