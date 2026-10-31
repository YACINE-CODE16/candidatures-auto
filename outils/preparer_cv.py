"""Prépare le CV de l'utilisateur à partir d'un seul .docx.

    python -m outils.preparer_cv chemin/CV.docx
    python -m outils.preparer_cv --depuis-gmail     # le CV que l'utilisateur s'est envoyé à lui-même en pièce jointe

Copie le CV dans profil/modeles/principal.docx, écrit la liste de ses blocs, vérifie qu'il tient sur 1 page,
fabrique la version anglaise, et un BROUILLON de profil/verite.md que l'utilisateur corrige puis valide.
Pas de conversion PDF → docx : un CV en PDF seul est refusé, avec un message.
"""

import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from outils.lancement import RACINE
from outils.traduire_modeles import MODELES, TRADUCTIONS_CANDIDAT, blocs, traduire, traductions, verifier

PROFIL = RACINE / "profil"
VERITE = PROFIL / "verite.md"
A_TRADUIRE = PROFIL / "traductions_a_faire.json"
PAS_DE_PDF = ("Ton CV est au format PDF : je ne peux pas le modifier proprement, seulement un fichier Word. "
              "Peux-tu me donner le .docx ? (Word ou Google Docs : Fichier → Télécharger → Microsoft Word.)")

# Mots qui annoncent une section du CV → section de verite.md. Le premier qui correspond l'emporte.
SECTIONS = (
    ("Formation", r"formation|[ée]ducation|[ée]tudes|dipl[ôo]mes?|academic"),
    ("Expériences professionnelles", r"exp[ée]riences?|parcours professionnel|employment|work history"),
    ("Projets", r"projets?|projects?|r[ée]alisations"),
    ("Certifications", r"certifications?"),
    ("Langues", r"langues?|languages?"),
    ("Compétences", r"comp[ée]tences|skills|informatique|outils|logiciels|langages"),
    ("Engagement et centres d'intérêt", r"engagements?|associati\w+|centres? d.int[ée]r[êe]t|int[ée]r[êe]ts|loisirs|hobbies|activit[ée]s"),
    ("À classer", r"informations? compl[ée]mentaires?|additional information|divers|autres"),
)
ENTETE = """# Source de vérité — {nom}

> **Statut du document : BROUILLON, en attente de validation par l'utilisateur.**
> Aucun agent ne doit l'utiliser tant que cette ligne n'a pas été remplacée par la mention de validation.

## Règles d'usage (pour les agents)

1. Ce fichier est la **seule** source de faits autorisée pour un CV ou une lettre.
2. Les chiffres se recopient **à l'identique**.
3. Reformuler est permis tant que le sens reste le même.
4. Un élément marqué **[À CONFIRMER]** ne doit pas être utilisé.
5. Les certifications « en cours » ne sont jamais présentées comme obtenues.

## Identité et contact
{contact}
"""


def _lignes(docx: Path) -> list[str]:
    return [l.replace("⟂", "") for l in blocs(docx).splitlines() if l.strip()]


def _section(ligne: str) -> str | None:
    """Un titre de section : une ligne courte faite presque seulement du mot annonciateur."""
    propre = re.sub(r"[^\w' ’]", " ", ligne).strip()
    if len(propre) > 40 or re.search(r"\d", propre):
        return None
    return next((nom for nom, motif in SECTIONS if re.fullmatch(rf"(mes |my |[\w']+ )?({motif})( [\w']+){{0,2}}", propre, re.I)), None)


def _sous_ligne(ligne: str) -> str | None:
    """« Langues : français, anglais » dans une rubrique fourre-tout : la ligne va dans sa propre section."""
    return next((nom for nom, motif in SECTIONS if re.match(rf"({motif})\s*(\([^)]*\))?\s*:", ligne, re.I)), None)


def _a_un_chiffre(ligne: str) -> bool:
    sans_dates = re.sub(r"\b(19|20)\d{2}\b|\b\d{1,2}/\d{2,4}\b", "", ligne)
    return bool(re.search(r"\d", sans_dates))


def brouillon_verite(lignes: list[str]) -> str:
    """Trie le texte du CV par section, sans rien ajouter. Ce n'est qu'un point de départ à faire corriger."""
    nom = lignes[0] if lignes else "<Prénom NOM>"
    contact, contenu, courante = [f"- Nom : {nom}"], {}, None
    for ligne in lignes[1:]:
        if titre := _section(ligne):
            courante = titre
            continue
        if courante is None and re.search(r"@|\+?\d[\d .-]{7,}|linkedin|github|https?://", ligne, re.I):
            contact.append(f"- {ligne}")
            continue
        contenu.setdefault(_sous_ligne(ligne) or courante or "À classer", []).append(f"- {ligne}")

    texte = ENTETE.format(nom=nom, contact="\n".join(contact))
    for section in (s for s, _ in SECTIONS):
        if section in contenu:
            texte += f"\n## {section}\n" + "\n".join(contenu[section]) + "\n"
    chiffres = [l for ls in contenu.values() for l in ls if _a_un_chiffre(l)]
    if chiffres:
        texte += "\n## Chiffres relevés dans le CV (à vérifier un par un)\n" + "\n".join(chiffres) + "\n"
    return texte


def blocs_sans_traduction(docx: Path) -> list[str]:
    """Morceaux de texte que les traductions connues ne couvrent pas : à faire traduire par l'assistant."""
    table = sorted(traductions().items(), key=lambda kv: -len(kv[0]))
    restants = {}
    for ligne in blocs(docx).splitlines():
        for morceau in ligne.split("⟂"):
            texte = morceau
            for fr, en in table:
                texte = texte.replace(fr, en)
            if texte == morceau and re.search(r"[^\W\d_]{3}", morceau):
                restants[morceau] = True
    return list(restants)


def preparer(source: Path) -> None:
    if source.suffix.lower() == ".pdf":
        sys.exit(PAS_DE_PDF)
    if source.suffix.lower() != ".docx":
        sys.exit(f"Je ne sais lire que les fichiers .docx (reçu : « {source.name} »). Word ou Google Docs peuvent l'enregistrer "
                 "dans ce format.")
    if not source.exists():
        sys.exit(f"Fichier introuvable : {source}")
    if not zipfile.is_zipfile(source):
        sys.exit(f"« {source.name} » n'est pas un vrai fichier Word (il est peut-être abîmé) : réenregistre-le en .docx.")

    MODELES.mkdir(parents=True, exist_ok=True)
    principal = MODELES / "principal.docx"
    if source.resolve() != principal.resolve():
        shutil.copy(source, principal)
    principal.with_suffix(".blocs.txt").write_text(blocs(principal), encoding="utf-8")
    lignes = _lignes(principal)
    if not lignes:
        sys.exit("Je ne trouve aucun texte dans ce CV (c'est peut-être une image dans un fichier Word) : "
                 "il me faut un CV dont le texte peut être sélectionné.")

    pages, _ = verifier(principal)
    if pages != 1:
        sys.exit(f"Ton CV fait {pages} pages une fois converti, et le pipeline exige 1 page. "
                 "Raccourcis-le, puis redonne-le-moi. (Une police absente de cette machine peut aussi l'allonger.)")

    if VERITE.exists():   # déjà corrigée ou validée : jamais écrasée
        print("profil/verite.md existe déjà : conservé tel quel.")
    else:
        VERITE.write_text(brouillon_verite(lignes), encoding="utf-8")
        print("Brouillon de profil/verite.md écrit, à faire corriger puis valider par l'utilisateur.")
    if not (PROFIL / "GUIDE_AGENT_CV.md").exists():
        shutil.copy(RACINE / "profil.exemple" / "GUIDE_AGENT_CV.md", PROFIL / "GUIDE_AGENT_CV.md")

    anglais = MODELES / "principal_EN.docx"
    traduire(principal, anglais)
    anglais.with_name("principal_EN.blocs.txt").write_text(blocs(anglais), encoding="utf-8")
    restants = blocs_sans_traduction(principal)
    A_TRADUIRE.write_text(json.dumps({r: "" for r in restants}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"CV prêt : 1 page, {len(lignes)} lignes de texte. Version anglaise créée ; {len(restants)} morceaux restent à traduire : "
          f"remplir {TRADUCTIONS_CANDIDAT.relative_to(RACINE)} d'après {A_TRADUIRE.relative_to(RACINE)}, "
          "puis lancer « python -m outils.traduire_modeles ».")


def recuperer_depuis_gmail() -> Path:
    from outils.gmail import derniere_piece_jointe

    trouve = derniere_piece_jointe("from:me has:attachment newer_than:7d", (".docx", ".pdf"))
    if trouve is None:
        sys.exit("Aucun mail récent avec un CV en pièce jointe : envoie-toi ton CV à toi-même depuis ton Gmail, puis préviens-moi.")
    nom, contenu = trouve
    cible = Path(tempfile.mkdtemp()) / Path(nom).name
    cible.write_bytes(contenu)
    return cible


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    preparer(recuperer_depuis_gmail() if sys.argv[1] == "--depuis-gmail" else Path(sys.argv[1]))
