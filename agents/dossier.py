"""Agent Dossier : pour chaque offre « Qualifiée », un CV adapté et une lettre, sans rien inventer.

Organisation :
  - l'agent principal (sonnet) fait travailler deux sous-agents, sans rien écrire lui-même ;
  - le Rédacteur (sonnet) choisit le modèle de CV, écrit edits.json (modifications du .docx)
    et lettre.json, puis appelle les outils produire_cv / produire_lettre qui fabriquent les PDF ;
  - le Vérificateur (opus, Read seulement) compare chaque phrase des PDF produits à
    profil/verite.md et répond « VERDICT: OK » ou « VERDICT: REJET » avec les phrases fautives ;
  - l'agent principal relance le Rédacteur avec ces phrases, 3 rédactions au maximum.

Le statut n'est PAS écrit par un agent. Le code Python lit lui-même, dans le flux de
messages, la réponse du Vérificateur : l'offre est acceptée seulement si la dernière
vérification dit OK et qu'aucun fichier n'a été réécrit après elle. Sinon : « Bloquée ».

Entrée : dossiers/<ID>/offre.md (texte de l'offre, écrit par le chasseur).
Sortie : dossiers/<ID>/CV_<Prénom>_<Nom>_<Entreprise>.pdf et Lettre_<Prénom>_<Nom>_<Entreprise>.pdf,
copiés sur le Drive avec leurs liens dans le Sheet.

Lancement :
    python -m agents.dossier --ids OF-1234abcd OF-5678ef90
    python -m agents.dossier --limite 3        # les 3 premières offres « Qualifiée »
    python -m agents.dossier --ids OF-... --test   # Sheet et Drive jamais modifiés
"""

import argparse
import asyncio
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from outils.config import candidat, nom_complet, prenom
from outils.documents import CV_MODELES, nom_fichier, produire_cv, produire_lettre
from outils.drive import deposer_dossier
from outils.lancement import RACINE, lancer_agent

DOSSIERS = RACINE / "dossiers"
VERITE = RACINE / "profil" / "verite.md"
GUIDE_CV = RACINE / "profil" / "GUIDE_AGENT_CV.md"
BLOCS = {cat: chemin.with_suffix(".blocs.txt") for cat, chemin in CV_MODELES.items()}
BLOCS_EN = {cat: chemin.with_name(chemin.stem + "_EN.blocs.txt") for cat, chemin in CV_MODELES.items()}
MAX_REDACTIONS = 3
BUDGET_PAR_OFFRE_USD = 3.00
OUTILS_DOCUMENTS = ["mcp__documents__produire_cv", "mcp__documents__produire_lettre"]
NOM, PRENOM, C = nom_complet(), prenom(), candidat()

PROMPT_REDACTEUR = f"""Tu prépares le CV et la lettre de motivation de {NOM} pour UNE offre de stage.

À lire d'abord : l'offre (offre.md), la source de vérité ({VERITE}), le guide CV ({GUIDE_CV}).

RÈGLE ABSOLUE : zéro invention. Chaque fait sur {PRENOM} (mission, chiffre, date, outil, compétence,
diplôme, projet, langue) doit figurer dans verite.md. Tu reformules et tu choisis les mots-clés de
l'offre, mais tu n'ajoutes jamais un fait, tu ne gonfles jamais un chiffre et tu n'utilises jamais
un élément marqué [À CONFIRMER]. Une compétence demandée par l'offre mais absente de verite.md est
laissée de côté, sans la mentionner (ni pour l'affirmer, ni pour dire qu'elle manque).

CV — on ne régénère jamais le CV : on modifie le texte du modèle .docx (voir le guide).
1. Choisis le modèle le plus proche de l'offre (Finance Quant, Data Scientist, Recherche IA) et lis
   la liste de ses blocs de texte :
{chr(10).join(f"   - {cat} : {chemin}" for cat, chemin in BLOCS.items())}
   Un « old » ou un « after » doit être recopié exactement depuis UN SEUL bloc (séparés par « ⟂ »).
   OFFRE EN ANGLAIS : utilise le modèle anglais de la même catégorie, avec ses blocs :
{chr(10).join(f"   - {cat} : {chemin}" for cat, chemin in BLOCS_EN.items())}
   et mets "langue": "en" dans edits.json ET dans lettre.json ; CV, lettre et ligne cible
   (« Final-year internship: <short title> ») en anglais ; projet créé : « Personal project – In progress ».
   Sinon "langue": "fr".
2. Écris edits.json dans le dossier : {{"categorie": "...", "langue": "fr", "operations": [ ... ]}} avec les opérations
   « replace », « add_bullet », « remove_para » décrites dans le guide, et une quatrième :
   {{"op": "replace_in_para", "contains": "<texte unique du paragraphe>", "old": "...", "new": "..."}}
   pour un texte présent plusieurs fois (ex. « Projet personnel » : vise le paragraphe par le titre du
   projet ; les opérations s'appliquent dans l'ordre, donc utilise le titre tel qu'il est à ce moment-là).
   Ne supprime jamais le titre d'un projet ou d'une expérience pour contourner un blocage.
3. Respecte le guide : ligne cible « Stage de fin d'études <intitulé court de 3 à 6 mots> » (le reste de
   la ligne inchangé) ; mêmes nombres de puces par expérience, reformulées avec les mots-clés de l'offre ;
   puces de 1 à 2 lignes ; pas de tiret « — » ; ne touche jamais au nom, aux coordonnées, aux sections,
   à l'Éducation, aux dates, aux intitulés de poste, aux langues, certifications, engagement et centres
   d'intérêt.
   PROJETS, à adapter à CHAQUE offre (ne jamais laisser ceux du modèle tels quels) :
   a. Classe tous les projets de la section « Projets » de verite.md selon leur proximité avec l'offre
      et garde les 2 meilleurs. Si un projet retenu n'est pas dans le modèle, remplace le titre, la ligne
      de technos et les 2 puces du projet sortant par les siens (un « replace » par bloc).
   b. Réécris les 2 puces de chaque projet retenu pour mettre en avant l'angle qui sert l'offre
      (vocabulaire de l'offre : évaluation de modèles, métriques, qualité des données, risque…),
      sans rien ajouter qui ne soit dans verite.md. Le titre peut aussi être reformulé.
   c. Tu peux aussi CRÉER un projet conçu pour l'offre (règle « Projets créés pour une offre » de
      verite.md), si aucun projet de la banque n'est vraiment proche : remplace le titre, la ligne de
      technos et les 2 puces d'un projet du modèle, et remplace son « Projet personnel » par
      « Projet personnel – En cours ». Puces : objectif et démarche, aucun résultat obtenu.
      CHIFFRÉ : chaque puce porte un chiffre de périmètre ou d'objectif choisi pour le projet
      (« 3 modèles comparés », « 5 ans de données journalières », « objectif : F1 ≥ 0,85 ») ; jamais une
      statistique du dataset que tu ne peux pas vérifier, jamais un chiffre présenté comme atteint.
      RÉALISTE : un projet d'étudiant de M2, faisable seul en 1 à 2 semaines sur un ordinateur portable,
      dataset public connu, méthodes classiques (régression, arbres/boosting, clustering, CNN simple,
      RAG avec un petit LLM…). Interdit : plateforme complète, mise en production, gros modèle entraîné
      from scratch, mots grandiloquents (« révolutionner », « de bout en bout », « à grande échelle »).
      Stack : uniquement des compétences de verite.md.
      Pour chaque projet créé, écris aussi projets_crees.json dans le dossier :
      [{{"titre": "...", "objectif": "...", "dataset": "<jeu de données public, avec son lien>",
         "stack": "...", "etapes": ["...", "..."], "resultat_a_mesurer": "...",
         "duree_estimee": "<ex. 8 jours>"}}]
      Ne parle jamais d'un projet créé dans la lettre.
   d. Dans ta réponse finale, indique les 2 projets retenus (ou créés) et pourquoi.
4. Appelle l'outil produire_cv. S'il renvoie une ERREUR (texte introuvable, plus d'une page…),
   corrige edits.json et rappelle-le jusqu'à obtenir OK.

LETTRE — une page, dans la langue de l'offre. Son but : décrocher un entretien. Elle doit être
personnelle, précise et enthousiaste, propre à CETTE entreprise : aucune phrase qui pourrait servir
telle quelle pour une autre offre, aucune formule creuse ou générique, aucune formule négative.
5. Prépare d'abord, pour toi, un brouillon d'idées :
   - ce que l'entreprise fait et vise (mission, produits, équipe, projet du stage), tiré de offre.md ;
   - ses besoins précis pour ce stage (missions, compétences demandées) ;
   - pour chaque besoin, l'élément de verite.md qui y répond le mieux (expérience, chiffre, cours, projet) ;
   - ce que {PRENOM} pourra concrètement apporter au projet décrit.
6. Rédige 4 paragraphes (250 à 350 mots au total), selon la règle « vous, moi, nous » :
   § 1 VOUS (l'entreprise). Commence normalement, sans anecdote ni effet de style : une phrase qui
       présente {PRENOM} et sa candidature, sur ce modèle : « {C['phrase_ouverture']}, je vous adresse
       ma candidature pour le {C['type_recherche']} « <intitulé> », d'une durée de {C['disponibilite']}. »
       Puis parle de l'entreprise :
       ce qu'elle fait, l'équipe et le projet du stage décrits dans offre.md (cite-les précisément),
       et ce qui attire {PRENOM} dans cette mission.
   § 2 MOI. Sa formation et 2 ou 3 réalisations précises (expériences, projets, cours de verite.md),
       chacune explicitement reliée à une mission ou une compétence demandée
       par l'offre, avec les vrais chiffres.
   § 3 NOUS. Ce que {PRENOM} et l'équipe feront ensemble : la valeur ajoutée concrète qu'il apportera
       au projet décrit, et ce que ce stage lui permettra de développer à leurs côtés.
   § 4 Conclusion : enthousiasme réaffirmé, proposition d'un entretien pour en discuter.
   Écris lettre.json dans le dossier :
   {{"destinataire": ["<Entreprise>", "<service ou équipe cité dans l'offre, sinon Service Recrutement>",
                     "<ville de l'offre>"],
     "objet": "Candidature au stage de fin d'études <intitulé> (réf. <référence si l'offre en donne une>)",
     "appel": "Madame, Monsieur," (en anglais : "Dear Hiring Manager,"),
     "langue": "fr" ou "en",
     "paragraphes": ["§ 1", "§ 2", "§ 3", "§ 4"],
     "politesse": "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées."
                  (en anglais : "Yours sincerely,")}}
   Les coordonnées, la date et la signature sont ajoutées automatiquement : ne les écris pas.
   Sur l'entreprise, n'utilise que ce que dit offre.md ; n'invente ni adresse ni nom de recruteur.
   Relis-toi : orthographe, grammaire, enchaînements logiques entre les paragraphes.
6. Appelle l'outil produire_lettre, et corrige jusqu'à obtenir OK.

Si on te transmet des phrases rejetées par le vérificateur, corrige-les toutes (supprime ou reformule
à partir de verite.md), puis rappelle produire_cv et produire_lettre.
Termine par une phrase indiquant le modèle de CV choisi."""

PROMPT_VERIFICATEUR = f"""Tu es le vérificateur, strict et indépendant, des candidatures de {NOM}.

Lis {VERITE}, l'offre (offre.md), puis le texte RÉELLEMENT produit : cv.txt et lettre.txt dans le dossier
indiqué (c'est le texte extrait des PDF). Contrôle CHAQUE phrase et CHAQUE puce :
  - tout fait sur {PRENOM} (expérience, poste, dates, chiffre, outil, compétence, diplôme, projet, langue,
    certification, engagement, centre d'intérêt) doit être présent dans verite.md ;
  - les chiffres doivent être identiques (pas d'arrondi vers le haut, pas de « plus de » ajouté) ;
  - aucun élément marqué [À CONFIRMER] dans verite.md ne doit être utilisé ;
  - un projet absent de la banque de verite.md n'est accepté que s'il respecte TOUTES les conditions
    « Projets créés pour une offre » : mention « En cours », aucun résultat présenté comme atteint
    (les chiffres de périmètre ou d'objectif sont attendus), projet réaliste pour un étudiant (1 à 2 semaines,
    sans plateforme complète ni mise en production ni mot grandiloquent), stack dans les compétences,
    fiche présente dans projets_crees.json, absent de la lettre ;
  - aucun fait sur l'entreprise qui ne vienne pas de offre.md (ni adresse ni nom de recruteur inventés) ;
  - aucune phrase qui affirme ou souligne une lacune de {PRENOM} ;
  - une reformulation est acceptée si elle ne change pas le sens et n'ajoute rien ;
  - l'expression d'un intérêt, d'une motivation ou d'une intention (« ce projet m'attire »,
    « je souhaite contribuer à… ») est acceptée, tant qu'elle n'affirme aucun fait non prouvé.
Les coordonnées, l'adresse, la date et la signature de la lettre sont ajoutées par le code : ne les
contrôle pas.

Ne corrige rien toi-même. Ta réponse finale se termine OBLIGATOIREMENT par l'un de ces formats :

VERDICT: OK

ou

VERDICT: REJET
- « phrase fautive exacte » → raison courte
- ..."""

SYSTEME_DOSSIER = f"""Tu coordonnes la préparation d'un dossier de candidature avec deux sous-agents :
« redacteur » et « verificateur ». Tu n'écris rien et ne produis rien toi-même.

Procédure, en attendant à chaque fois la réponse du sous-agent avant de continuer :
1. Appelle « redacteur » en lui donnant le chemin du dossier et celui de offre.md, et en lui demandant
   de produire le CV et la lettre avec ses outils produire_cv et produire_lettre.
2. Appelle « verificateur » avec le chemin du dossier.
3. Si le verdict est REJET, rappelle « redacteur » en recopiant mot pour mot la liste des phrases
   rejetées, puis rappelle « verificateur ». {MAX_REDACTIONS} rédactions au maximum.
4. Arrête-toi dès qu'un verdict est OK, ou après la {MAX_REDACTIONS}e vérification.
Termine par une ligne : « RÉSULTAT: OK » ou « RÉSULTAT: ÉCHEC »."""


def serveur_documents(dossier: Path, entreprise: str):
    """Outils du rédacteur, liés à UN dossier : ils ne peuvent produire nulle part ailleurs."""

    def reponse(texte: str) -> dict:
        return {"content": [{"type": "text", "text": texte}], "is_error": texte.startswith("ERREUR")}

    @tool("produire_cv", "Applique edits.json au modèle .docx choisi et produit le PDF (1 page).", {})
    async def outil_cv(_args):
        return reponse(await asyncio.to_thread(produire_cv, dossier, entreprise))

    @tool("produire_lettre", "Met en page lettre.json (même police que le CV) et produit le PDF (1 page).", {})
    async def outil_lettre(_args):
        categorie_txt = dossier / "categorie.txt"
        categorie = categorie_txt.read_text().strip() if categorie_txt.exists() else "Data Scientist"
        return reponse(await asyncio.to_thread(produire_lettre, dossier, entreprise, categorie))

    return create_sdk_mcp_server(name="documents", tools=[outil_cv, outil_lettre])


class Suivi:
    """Lit le flux de messages pour savoir, sans faire confiance à l'agent principal,
    ce que les sous-agents ont réellement fait."""

    ECRITURES = {"Write", "Edit", *OUTILS_DOCUMENTS}

    def __init__(self):
        self.appels = {}         # id de l'appel Agent → nom du sous-agent
        self.evenements = []     # suite ordonnée : ("redaction"|"verification"|"ecriture", détail)
        self.verdicts = {}       # id de l'appel vérificateur → texte de sa réponse

    def __call__(self, message):
        if isinstance(message, AssistantMessage):
            for bloc in message.content:
                if isinstance(bloc, ToolUseBlock) and bloc.name in ("Agent", "Task"):
                    type_agent = bloc.input.get("subagent_type", "")
                    self.appels[bloc.id] = type_agent
                    self.evenements.append(("redaction" if type_agent == "redacteur" else "verification", bloc.id))
                elif isinstance(bloc, ToolUseBlock) and bloc.name in self.ECRITURES:
                    self.evenements.append(("ecriture", bloc.name))
                elif isinstance(bloc, TextBlock) and self.appels.get(message.parent_tool_use_id) == "verificateur":
                    self.verdicts[message.parent_tool_use_id] = bloc.text
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            # Le résultat de l'outil Agent contient le rapport final du sous-agent.
            for bloc in message.content:
                if isinstance(bloc, ToolResultBlock) and self.appels.get(bloc.tool_use_id) == "verificateur":
                    contenu = bloc.content if isinstance(bloc.content, str) else " ".join(
                        p.get("text", "") for p in bloc.content or [] if isinstance(p, dict))
                    self.verdicts[bloc.tool_use_id] = contenu

    def nb_redactions(self) -> int:
        return sum(1 for e, _ in self.evenements if e == "redaction")

    def conclusion(self) -> tuple[bool, str]:
        """(dossier accepté ?, explication). Accepté seulement si la dernière vérification
        dit OK et qu'aucune écriture de fichier n'a eu lieu après elle."""
        verifs = [i for i, (e, _) in enumerate(self.evenements) if e == "verification"]
        if not verifs:
            return False, "aucune vérification n'a eu lieu"
        derniere = verifs[-1]
        if any(e == "ecriture" for e, _ in self.evenements[derniere:]):
            return False, "des fichiers ont été modifiés après la dernière vérification"
        texte = self.verdicts.get(self.evenements[derniere][1], "")
        trouves = re.findall(r"VERDICT\s*:\s*(OK|REJET)", texte)
        if not trouves:
            return False, "réponse du vérificateur illisible"
        if trouves[-1] != "OK":
            return False, "rejet du vérificateur : " + texte.split("VERDICT")[-1][:400]
        if self.nb_redactions() > MAX_REDACTIONS:
            return False, f"plus de {MAX_REDACTIONS} rédactions"
        return True, "vérification OK"


def options_dossier(dossier: Path, entreprise: str) -> ClaudeAgentOptions:
    relatif = dossier.relative_to(RACINE).as_posix()
    return ClaudeAgentOptions(
        model="sonnet",
        system_prompt=SYSTEME_DOSSIER,
        tools=["Agent", "Read", "Write"],    # Read/Write servent aux sous-agents (voir leurs définitions)
        mcp_servers={"documents": serveur_documents(dossier, entreprise)},
        disallowed_tools=["Agent(fork)"],    # pas de copie de l'agent principal
        # « Edit(chemin) » couvre aussi l'outil Write : écriture permise dans ce dossier seulement.
        allowed_tools=["Agent", "Read", f"Edit(./{relatif}/**)", *OUTILS_DOCUMENTS],
        permission_mode="dontAsk",           # tout le reste est refusé
        setting_sources=[],
        cwd=str(RACINE),
        max_turns=8,                         # journaux : 5 tours d'habitude, 7 au plus
        max_budget_usd=BUDGET_PAR_OFFRE_USD,
        extra_args={"strict-mcp-config": None},   # seulement le serveur « documents », aucun connecteur
        env={
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",     # les sous-agents n'en lancent pas d'autres
            "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1",  # seuls nos deux sous-agents existent
        },
        agents={
            "redacteur": AgentDefinition(
                description="Produit le CV (modifications du .docx) et la lettre d'une offre, à partir de verite.md.",
                prompt=PROMPT_REDACTEUR,
                tools=["Read", "Write", *OUTILS_DOCUMENTS],
                model="sonnet",
                background=False,
            ),
            "verificateur": AgentDefinition(
                description="Vérifie chaque phrase du CV et de la lettre produits contre verite.md. Lecture seule.",
                prompt=PROMPT_VERIFICATEUR,
                tools=["Read"],
                model="sonnet",  # Sonnet : économise le quota de l'abonnement
                background=False,
            ),
        },
    )


IDEES = RACINE / "projets" / "idees.md"


def noter_projets_crees(offre: dict, dossier: Path) -> None:
    """Ajoute à projets/idees.md la fiche des projets créés pour cette offre (affichés « En cours »)."""
    fichier = dossier / "projets_crees.json"
    if not fichier.exists():
        return
    import json

    if not IDEES.exists():
        IDEES.write_text("# Projets à réaliser\n\nProjets affichés « En cours » sur des CV envoyés : "
                         "à réaliser avant un éventuel entretien.\n", encoding="utf-8")
    # Un dossier régénéré remplace ses anciennes fiches au lieu de les dupliquer.
    blocs = IDEES.read_text(encoding="utf-8").split("\n## ")
    IDEES.write_text("\n## ".join(b for b in blocs if f"- Offre : {offre['id']} " not in b), encoding="utf-8")
    with IDEES.open("a", encoding="utf-8") as f:
        for p in json.loads(fichier.read_text(encoding="utf-8")):
            etapes = "\n".join(f"   {i}. {e}" for i, e in enumerate(p.get("etapes", []), 1))
            f.write(f"\n## {p.get('titre')}\n"
                    f"- Offre : {offre['id']} – {offre['entreprise']} – {offre['intitule']}\n"
                    f"- Objectif : {p.get('objectif')}\n- Dataset : {p.get('dataset')}\n"
                    f"- Stack : {p.get('stack')}\n- Étapes :\n{etapes}\n"
                    f"- Résultat à mesurer : {p.get('resultat_a_mesurer')}\n- Durée estimée : {p.get('duree_estimee', '?')}\n")


def verite_validee() -> bool:
    """verite.md n'est utilisable qu'une fois sa ligne de statut remplacée par « Validé par <prénom> le ... »."""
    return f"Validé par {PRENOM} le" in VERITE.read_text(encoding="utf-8")


async def preparer_dossier(offre: dict, sheet, test: bool = False) -> str:
    """Prépare le dossier d'une offre, le dépose sur le Drive et met le Sheet à jour.

    En mode test, le brouillon de verite.md est autorisé mais ni le Sheet ni le Drive ne sont
    modifiés : un dossier de test ne peut pas être validé, donc jamais envoyé.
    """
    id_offre, entreprise = offre["id"], offre["entreprise"]
    dossier = DOSSIERS / id_offre
    if not test and not verite_validee():
        raise RuntimeError(f"profil/verite.md n'est pas encore validé par {PRENOM}.")
    if not (dossier / "offre.md").exists():
        if not test:
            sheet.changer_statut(id_offre, "Bloquée", motif_blocage="Texte de l'offre absent (offre.md)")
        return "Bloquée"

    for ancien in dossier.iterdir():
        if ancien.name != "offre.md":
            ancien.unlink()

    prompt = (f"Prépare le dossier de l'offre {id_offre} : {entreprise} — {offre['intitule']}.\n"
              f"Dossier : {dossier}\nOffre : {dossier / 'offre.md'}")
    if test:
        prompt += (f"\n\nMODE TEST, autorisé par {PRENOM} : le brouillon de verite.md peut être utilisé "
                   "(toujours sans les éléments [À CONFIRMER]). Ce dossier sert à relire le résultat "
                   "et ne sera jamais envoyé. Transmets cette mention aux deux sous-agents.")
    suivi = Suivi()
    resultat = await lancer_agent(f"dossier {id_offre}", prompt, options_dossier(dossier, entreprise),
                                  observateur=suivi)

    accepte, raison = suivi.conclusion()
    cv_pdf, lettre_pdf = dossier / nom_fichier(entreprise, "CV"), dossier / nom_fichier(entreprise, "Lettre")
    if resultat.subtype != "success":
        accepte, raison = False, f"arrêt de l'agent ({resultat.subtype})"
    elif accepte and not (cv_pdf.exists() and lettre_pdf.exists()):
        accepte, raison = False, "PDF du CV ou de la lettre absent"

    # Validation automatique (choix du candidat) : un dossier approuvé par le vérificateur
    # passe directement en « Validée ». Sans VALIDATION_AUTO=1, il attend la case à cocher.
    statut_ok = "Validée" if os.getenv("VALIDATION_AUTO") == "1" else "À valider"
    statut = statut_ok if accepte else "Bloquée"
    if test:
        statut += " (test : Sheet et Drive non modifiés)"
    elif accepte:
        liens = await asyncio.to_thread(deposer_dossier, offre, cv_pdf, lettre_pdf)  # traçabilité
        champs = {"cv": (dossier / "categorie.txt").read_text().strip(),
                  "chemin_dossier": dossier.relative_to(RACINE).as_posix(), **liens}
        if statut_ok == "Validée":
            champs["validee"] = "TRUE"
        sheet.changer_statut(id_offre, statut_ok, **champs)
        noter_projets_crees(offre, dossier)
    else:
        sheet.changer_statut(id_offre, "Bloquée", motif_blocage=f"Dossier : {raison}"[:500])
    print(f"→ {id_offre} : {statut} ({raison}, {suivi.nb_redactions()} rédaction(s))")
    return statut


async def main() -> None:
    from outils.sheet import ouvrir_pipeline

    parser = argparse.ArgumentParser(description="Prépare les dossiers des offres « Qualifiée ».")
    parser.add_argument("--ids", nargs="*", help="ID précis à traiter")
    parser.add_argument("--limite", type=int, default=5, help="nombre maximal d'offres")
    parser.add_argument("--test", action="store_true",
                        help="autorise le brouillon de verite.md ; ne modifie ni le Sheet ni le Drive")
    args = parser.parse_args()

    sheet = ouvrir_pipeline()
    offres = sheet.lire_par_statut("Qualifiée")
    if args.ids:
        offres = [o for o in offres if o["id"] in args.ids]
    for offre in offres[: args.limite]:
        await preparer_dossier(offre, sheet, test=args.test)


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    asyncio.run(main())
