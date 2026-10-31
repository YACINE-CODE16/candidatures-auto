"""Production du CV et de la lettre à partir des modèles .docx du candidat.

CV : on ne régénère jamais le CV (voir profil/GUIDE_AGENT_CV.md). Le rédacteur écrit une
liste d'opérations (edits.json) ; outils/cv_edit.py les applique au .docx de référence,
le convertit en PDF avec LibreOffice et refuse tout CV de plus d'une page.

Lettre : même police que le .docx du CV et mise en page d'une
lettre française classique : expéditeur en haut à gauche, destinataire en haut à droite,
lieu et date, objet, appel, 3 paragraphes, formule de politesse, nom en bas à droite.

Les deux fonctions sont exposées au rédacteur comme outils (@tool) par agents/dossier.py.
Chacune écrit aussi la version texte du PDF produit (cv.txt, lettre.txt) : c'est ce texte,
réellement présent dans les PDF, que le vérificateur contrôle.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from outils.config import candidat
from outils.lancement import RACINE

MODELES = RACINE / "profil" / "modeles"
# Catégorie de CV → modèle .docx (profil/candidat.json, clé « modeles_cv »).
CV_MODELES = {categorie: MODELES / f"{fichier}.docx" for categorie, fichier in candidat()["modeles_cv"].items()}
COORDONNEES = RACINE / "profil" / "coordonnees.json"
MENTION_PROJET = {"fr": "Projet personnel", "en": "Personal project"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def modele_cv(categorie: str, langue: str) -> Path:
    """Modèle .docx d'une catégorie, en français ou en anglais (<modèle>_EN.docx)."""
    base = CV_MODELES[categorie]
    return base.with_name(base.stem + "_EN.docx") if langue == "en" else base
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
LONGUEUR_MAX_PUCE = 220  # ≈ 2 lignes de CV (guide : 1 ligne idéalement, 2 au maximum)


def nom_fichier(entreprise: str, document: str) -> str:
    """CV_<Prénom>_<Nom>_<Entreprise>.pdf (ex. CV_Sam_Martin_Acme.pdf)."""
    def propre(texte: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", unicodedata.normalize("NFKD", texte)
                      .encode("ascii", "ignore").decode()).strip("_")
    c = candidat()
    return f"{document}_{propre(c['prenom'])}_{propre(c['nom'].capitalize())}_{propre(entreprise) or 'Entreprise'}.pdf"


def _xml(docx: Path) -> str:
    with zipfile.ZipFile(docx) as z:
        return z.read("word/document.xml").decode("utf-8")


def _texte_pdf(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout


def _en_pdf(docx: Path) -> tuple[Path, int]:
    """Convertit un .docx en PDF avec LibreOffice ; renvoie (pdf, nombre de pages)."""
    sortie = Path(tempfile.mkdtemp())
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(sortie), str(docx)],
                   check=True, capture_output=True, timeout=180)
    pdf = sortie / (docx.stem + ".pdf")
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    return pdf, int(re.search(r"Pages:\s+(\d+)", info).group(1))


# --------------------------------------------------------------------------
# CV
# --------------------------------------------------------------------------

def produire_cv(dossier: Path, entreprise: str) -> str:
    """Applique dossier/edits.json au modèle choisi. Renvoie un message pour le rédacteur."""
    try:
        demande = json.loads((dossier / "edits.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erreur:
        return f"ERREUR : edits.json illisible ({erreur})."
    categorie, operations = demande.get("categorie"), demande.get("operations", [])
    langue = demande.get("langue", "fr")
    if categorie not in CV_MODELES:
        return f"ERREUR : « categorie » doit être l'une de {list(CV_MODELES)}."
    if langue not in MENTION_PROJET:
        return "ERREUR : « langue » doit être « fr » ou « en »."

    for op in operations:
        nouveau = op.get("new") or op.get("text") or ""
        if "—" in nouveau:
            return f"ERREUR : pas de tiret cadratin « — » (guide) : « {nouveau[:80]} »."
        if len(nouveau) > LONGUEUR_MAX_PUCE:
            return f"ERREUR : texte trop long ({len(nouveau)} caractères, max {LONGUEUR_MAX_PUCE}) : « {nouveau[:80]}… »."

    docx = dossier / "cv.docx"
    operations_json = Path(tempfile.mkdtemp()) / "operations.json"  # cv_edit.py attend la liste seule
    operations_json.write_text(json.dumps(operations, ensure_ascii=False), encoding="utf-8")
    resultat = subprocess.run(
        [sys.executable, str(RACINE / "outils" / "cv_edit.py"), str(modele_cv(categorie, langue)), str(docx),
         str(operations_json)],
        capture_output=True, text=True, timeout=240,
    )
    if resultat.returncode != 0:
        return "ERREUR : " + (resultat.stderr or resultat.stdout).strip()[-600:]

    texte = _texte_pdf(docx.with_suffix(".pdf"))
    mention = MENTION_PROJET[langue]
    if _xml(docx).count(mention) != _xml(modele_cv(categorie, langue)).count(mention):   # 0 si le CV n'a pas cette mention
        return (f"ERREUR : le CV doit garder autant de projets que le modèle, chacun avec son titre et sa mention « {mention} ». "
                "Ne supprime aucun titre.")
    pdf = dossier / nom_fichier(entreprise, "CV")
    shutil.move(str(docx.with_suffix(".pdf")), pdf)
    (dossier / "cv.txt").write_text(texte, encoding="utf-8")
    (dossier / "categorie.txt").write_text(categorie, encoding="utf-8")
    (dossier / "langue.txt").write_text(langue, encoding="utf-8")
    return f"OK : {pdf.name} (1 page). Texte final du CV :\n\n{texte}"


# --------------------------------------------------------------------------
# Lettre
# --------------------------------------------------------------------------

def _paragraphe(texte: str = "", police: str = "Garamond", gras: bool = False, retrait_cm: float = 0,
                justifie: bool = False, apres: int = 0) -> str:
    """Un paragraphe Word en 11,5 pt, dans la police du CV."""
    retrait = f'<w:ind w:left="{int(retrait_cm * 567)}"/>' if retrait_cm else ""
    alignement = '<w:jc w:val="both"/>' if justifie else ""
    rpr = (f'<w:rPr><w:rFonts w:ascii="{police}" w:hAnsi="{police}" w:cs="{police}"/>'
           + ("<w:b/>" if gras else "") + '<w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr>')
    run = f'<w:r>{rpr}<w:t xml:space="preserve">{escape(texte)}</w:t></w:r>' if texte else ""
    return (f'<w:p><w:pPr>{retrait}{alignement}<w:spacing w:before="0" w:after="{apres}" '
            f'w:line="264" w:lineRule="auto"/>{rpr}</w:pPr>{run}</w:p>')


def produire_lettre(dossier: Path, entreprise: str, categorie: str) -> str:
    """Met en page dossier/lettre.json. Renvoie un message pour le rédacteur."""
    try:
        lettre = json.loads((dossier / "lettre.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erreur:
        return f"ERREUR : lettre.json illisible ({erreur})."
    manquants = [c for c in ("destinataire", "objet", "appel", "paragraphes", "politesse") if not lettre.get(c)]
    if manquants:
        return f"ERREUR : champs manquants dans lettre.json : {manquants}."
    if not 3 <= len(lettre["paragraphes"]) <= 5:
        return "ERREUR : la lettre doit avoir 3 à 5 paragraphes."

    moi = json.loads(COORDONNEES.read_text(encoding="utf-8"))
    aujourd_hui, anglais = date.today(), lettre.get("langue") == "en"
    if anglais:
        date_texte = f"{moi['ville_redaction']}, {MONTHS[aujourd_hui.month - 1]} {aujourd_hui.day}, {aujourd_hui.year}"
    else:
        date_texte = f"{moi['ville_redaction']}, le {aujourd_hui.day} {MOIS[aujourd_hui.month - 1]} {aujourd_hui.year}"

    # On part du .docx du CV : ses polices et ses styles, la lettre a donc le même style.
    with zipfile.ZipFile(CV_MODELES.get(categorie, next(iter(CV_MODELES.values())))) as z:
        fichiers = {n: z.read(n) for n in z.namelist()}
    xml = fichiers["word/document.xml"].decode("utf-8")
    police = m[1] if (m := re.search(r'w:rFonts w:ascii="([^"]+)"', xml)) else "Garamond"

    def para(texte: str = "", **options) -> str:
        return _paragraphe(texte, police, **options)

    corps = [para(moi["nom"], gras=True)]
    corps += [para(ligne) for ligne in [*moi["adresse"], moi["telephone"], moi["email"]]]
    corps.append(para())
    corps += [para(ligne, gras=(i == 0), retrait_cm=10) for i, ligne in enumerate(lettre["destinataire"])]
    corps += [para(), para(date_texte, retrait_cm=10), para(), para()]
    corps += [para(f"{'Subject:' if anglais else 'Objet :'} {lettre['objet']}", gras=True, apres=240)]
    corps += [para(lettre["appel"], apres=160)]
    corps += [para(p, justifie=True, apres=160) for p in lettre["paragraphes"]]
    corps += [para(lettre["politesse"], justifie=True, apres=480)]
    corps += [para(moi["nom"], retrait_cm=10)]

    section = re.search(r"<w:sectPr.*?</w:sectPr>", xml, re.S).group(0)
    section = re.sub(r"<w:footerReference[^>]*/>", "", section)
    section = re.sub(r"<w:pgMar [^>]*/>", '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
                                           'w:header="0" w:footer="0" w:gutter="0"/>', section)
    debut = xml.index("<w:body>") + len("<w:body>")
    fichiers["word/document.xml"] = (xml[:debut] + "".join(corps) + section + "</w:body></w:document>").encode("utf-8")

    docx = dossier / "lettre.docx"
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
        for nom, contenu in fichiers.items():
            z.writestr(nom, contenu)
    pdf_temp, pages = _en_pdf(docx)
    if pages != 1:
        return f"ERREUR : la lettre fait {pages} pages, elle doit tenir sur une seule. Raccourcis-la."
    pdf = dossier / nom_fichier(entreprise, "Lettre")
    shutil.move(str(pdf_temp), pdf)
    texte = _texte_pdf(pdf)
    (dossier / "lettre.txt").write_text(texte, encoding="utf-8")
    return f"OK : {pdf.name} (1 page). Texte final de la lettre :\n\n{texte}"
