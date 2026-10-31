"""Crée la version anglaise des modèles de CV (<modèle>_EN.docx), pour les offres en anglais.

Même principe que cv_edit.py : on ne touche qu'au texte de word/document.xml, la mise en forme
(polices, filets, tabulations, liens) reste celle du modèle. Les traductions ci-dessous sont faites
une fois pour toutes : communes ici, propres au candidat dans profil/traductions_cv.json
(à faire rédiger par Claude à partir de ses CV), sans rien ajouter ni retirer.

    python -m outils.traduire_modeles
"""

import html
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from outils.config import candidat
from outils.lancement import RACINE

MODELES = RACINE / "profil" / "modeles"


# Traductions communes à tous les CV français (titres de sections, mentions, mois).
TRADUCTIONS_COMMUNES = {
    "Stage de fin d'études ": "Final-year internship: ",
    "ÉDUCATION": "EDUCATION",
    "EXPÉRIENCES PROFESSIONNELLES": "PROFESSIONAL EXPERIENCE",
    "EXPÉRIENCES": "PROFESSIONAL EXPERIENCE",
    "PROFESSIONNELLES": "",
    "PROJETS": "PROJECTS",
    "INFORMATIONS COMPLÉMENTAIRES": "ADDITIONAL INFORMATION",
    "Diplôme d'Ingénieur": "Master's-level Engineering Degree (Diplôme d'Ingénieur)",
    "Échange académique": "Academic exchange",
    "Présent": "Present",
    "Juil.": "Jul.",
    "Stagiaire": "Intern",
    "Projet personnel": "Personal project",
    "Langages de programmation:": "Programming languages:",
    "Langues:": "Languages:",
    "Outils & frameworks:": "Tools & frameworks:",
    "Certifications (en cours):": "Certifications (in progress):",
    "Engagement:": "Leadership:",
    "Centres d'intérêt:": "Interests:",
    "Janv.": "Jan.",
    "Févr.": "Feb.",
    "Mai ": "May ",
    "Avr.": "Apr.",
    "Sept.": "Sep.",
    "Déc.": "Dec.",
}
# Traductions propres au candidat (ses puces, ses projets, sa disponibilité) : profil/traductions_cv.json.
TRADUCTIONS_CANDIDAT = RACINE / "profil" / "traductions_cv.json"


def blocs(docx: Path) -> str:
    """Liste des blocs de texte, telle que l'attend cv_edit.py : un paragraphe par ligne, ses runs séparés par « ⟂ »."""
    # shortcut: un paragraphe contenant une zone de texte donne un bloc mêlé ; à séparer si des CV en utilisent beaucoup.
    with zipfile.ZipFile(docx) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    lignes = ["⟂".join(html.unescape(t) for t in re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", p))
              for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]
    return "".join(l + "\n" for l in lignes if l.strip())


def traductions() -> dict:
    c = candidat()
    toutes = {**TRADUCTIONS_COMMUNES, c.get("disponibilite", "\0"): c.get("disponibilite_en", c.get("disponibilite", "\0"))}
    if TRADUCTIONS_CANDIDAT.exists():
        toutes.update(json.loads(TRADUCTIONS_CANDIDAT.read_text(encoding="utf-8")))
    return toutes


def traduire(source: Path, cible: Path) -> None:
    with zipfile.ZipFile(source) as z:
        fichiers = {n: z.read(n) for n in z.namelist()}
    xml = fichiers["word/document.xml"].decode("utf-8")

    def traduire_texte(m: re.Match) -> str:
        texte = m.group(2)
        for fr, en in sorted(traductions().items(), key=lambda kv: -len(kv[0])):
            texte = texte.replace(escape(fr), escape(en))
        return m.group(1) + texte + m.group(3)

    xml = re.sub(r"(<w:t(?: [^>]*)?>)([^<]*)(</w:t>)", traduire_texte, xml)
    fichiers["word/document.xml"] = xml.encode("utf-8")
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_DEFLATED) as z:
        for nom, contenu in fichiers.items():
            z.writestr(nom, contenu)


def verifier(docx: Path) -> tuple[int, str]:
    sortie = tempfile.mkdtemp()
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", sortie, str(docx)],
                   check=True, capture_output=True, timeout=180)
    pdf = Path(sortie) / (docx.stem + ".pdf")
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                                             text=True).stdout).group(1))
    return pages, subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout


if __name__ == "__main__":
    for nom in candidat()["modeles_cv"].values():
        cible = MODELES / f"{nom}_EN.docx"
        traduire(MODELES / f"{nom}.docx", cible)
        cible.with_name(f"{nom}_EN.blocs.txt").write_text(blocs(cible), encoding="utf-8")
        pages, texte = verifier(cible)
        print(f"{cible.name} : {pages} page(s)")
        if pages != 1:
            sys.exit(f"ERREUR : {cible.name} dépasse une page.")
