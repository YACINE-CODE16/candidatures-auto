"""Agent Chasseur : trouve les offres, applique les critères du candidat (profil/candidat.json), les note et les dédoublonne.

Deux missions, par lots :
  1. COMPLÉTER les offres « Qualifiée » du Sheet qui n'ont pas encore de texte (souvent trouvées
     sur LinkedIn ou Welcome to the Jungle, que l'on ne lit pas) : retrouver la page officielle de
     l'offre (site carrière ou outil de recrutement de l'entreprise), vérifier qu'elle est en ligne,
     la noter. Offre retirée ou hors critères → « Écartée » ; page officielle introuvable → « Bloquée ».
  2. CHERCHER de nouvelles offres sur des sources légales (sites carrière, Workable, Greenhouse,
     Lever, SmartRecruiters, Teamtailor, Welcome to the Jungle exclu car il refuse les robots…).

Garanties apportées par le code :
  - le texte de l'offre est téléchargé par le code depuis l'URL officielle (outils/web.py), jamais
    écrit par l'agent : impossible d'inventer le contenu d'une offre ;
  - LinkedIn et Indeed ne sont jamais lus, robots.txt est respecté ;
  - le seuil de qualification (score ≥ SEUIL) est appliqué par le code ;
  - le dédoublonnage (URL normalisée, ou entreprise + intitulé) est fait par outils/sheet.py.

Lancement :
    python -m agents.chasseur --completer 10        # complète 10 offres « Qualifiée »
    python -m agents.chasseur --chercher 10         # cherche jusqu'à 10 nouvelles offres
"""

import argparse
import asyncio
import json
import os
import re
from datetime import date

from dotenv import load_dotenv

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from outils.config import candidat, consigne_contrat, criteres, hors_contrat, libelle_contrat, metiers, mots_cles, nom_complet, zone
from outils.lancement import RACINE, lancer_agent
from outils.web import AccesRefuse, autorise, lire, workday_chercher

DOSSIERS = RACINE / "dossiers"
SEUIL = 60                    # score minimal pour qu'une offre soit « Qualifiée »
TAILLE_LOT = 5                # offres à compléter par lancement d'agent
BUDGET_PAR_LOT_USD = 1.50

CRITERES = f"""Critères de {nom_complet()} ({candidat()['presentation']}) :
{criteres()}"""

SYSTEME = f"""Tu es le chasseur d'offres de {nom_complet()}.

{CRITERES}

Outils : WebSearch pour trouver des pages ; lire_page pour lire une page (le code refuse LinkedIn,
Indeed et les pages interdites aux robots : passe alors à une autre source, ne cherche pas à contourner).

Pour COMPLÉTER une offre du Sheet :
1. Cherche la page officielle de CETTE offre : site carrière de l'entreprise ou son outil de recrutement
   (myworkdayjobs, successfactors, apply.workable.com, jobs.lever.co, boards.greenhouse.io,
   jobs.smartrecruiters.com, teamtailor, careers.<entreprise>…). Exemple de recherche :
   « <entreprise> {libelle_contrat()} <intitulé> {candidat().get('annee', '')} ». Vérifie avec lire_page que c'est bien la même offre
   (même intitulé, même entreprise) et qu'elle est toujours en ligne.
2. Puis appelle UN de ces outils :
   - enregistrer_offre(id, url_officielle, score, raison_score, date_debut, duree) : offre trouvée ;
   - ecarter_offre(id, raison) : offre retirée, expirée, ou hors critères (score < {SEUIL}) ;
   - bloquer_offre(id, raison) : page officielle introuvable après 3 recherches sérieuses.
Pour CHERCHER de nouvelles offres :
1. Cherche des offres récentes correspondant aux critères, sur des sites carrière ou outils de
   recrutement (jamais LinkedIn ni Indeed). Lis chaque offre avec lire_page.
2. Pour chaque offre lue et pertinente, appelle ajouter_offre(entreprise, intitule, lieu, url_officielle,
   score, raison_score, date_publication, date_debut, duree, source). Le code dédoublonne.
{consigne_contrat()}
raison_score : une phrase courte et factuelle. N'invente aucune information : laisse vide ce que la
page ne dit pas. Termine par un bilan d'une ligne par offre traitée."""


def _ecrire_offre_md(id_offre: str, url: str, texte: str, entreprise: str, intitule: str) -> None:
    dossier = DOSSIERS / id_offre
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "offre.md").write_text(
        f"# {entreprise} — {intitule}\nSource : {url}\nLue le : {date.today().isoformat()}\n\n{texte}\n",
        encoding="utf-8")


def serveur_chasseur(sheet, bilan: dict):
    """Outils du chasseur. `bilan` compte ce qui a été fait (pour le résumé quotidien)."""

    def texte(t: str) -> dict:
        return {"content": [{"type": "text", "text": t}]}

    def offre_du_sheet(id_offre: str) -> dict | None:
        return next((o for o in sheet.lire_par_statut("Qualifiée") if o["id"] == id_offre), None)

    @tool("lire_page", "Texte d'une page web (offre d'emploi, site carrière). Respecte robots.txt.",
          {"url": str})
    async def lire_page(args):
        if hors_contrat(args["url"]):
            return texte(f"REFUS : l'URL indique un autre contrat que celui recherché ({libelle_contrat()}).")
        try:
            return texte(await asyncio.to_thread(lire, args["url"]))
        except AccesRefuse as refus:
            return texte(f"REFUS : {refus}")

    @tool("enregistrer_offre", "Enregistre la page officielle d'une offre « Qualifiée » du Sheet et sa note.",
          {"id": str, "url_officielle": str, "score": int, "raison_score": str, "date_debut": str, "duree": str})
    async def enregistrer_offre(args):
        offre = offre_du_sheet(args["id"])
        if offre is None:
            return texte("ERREUR : cette offre n'est pas « Qualifiée » dans le Sheet.")
        try:
            contenu = await asyncio.to_thread(lire, args["url_officielle"])   # le code lit, pas l'agent
        except AccesRefuse as refus:
            return texte(f"ERREUR : {refus}. Trouve une autre page officielle ou bloque l'offre.")
        champs = {"score": args["score"], "raison_score": args["raison_score"]}
        champs.update({k: args[k] for k in ("date_debut", "duree") if args.get(k)})
        if args["score"] < SEUIL:
            sheet.changer_statut(args["id"], "Écartée", **champs)
            bilan["ecartees"] += 1
            return texte(f"Score {args['score']} < {SEUIL} : offre écartée.")
        _ecrire_offre_md(args["id"], args["url_officielle"], contenu, offre["entreprise"], offre["intitule"])
        sheet.mettre_a_jour(args["id"], **champs)
        bilan["completees"] += 1
        return texte("Offre enregistrée.")

    @tool("ecarter_offre", "Écarte une offre « Qualifiée » (retirée, expirée ou hors critères).",
          {"id": str, "raison": str})
    async def ecarter_offre(args):
        sheet.changer_statut(args["id"], "Écartée", raison_score=args["raison"][:300])
        bilan["ecartees"] += 1
        return texte("Offre écartée.")

    @tool("bloquer_offre", "Bloque une offre dont la page officielle est introuvable.",
          {"id": str, "raison": str})
    async def bloquer_offre(args):
        sheet.changer_statut(args["id"], "Bloquée", motif_blocage=f"Chasseur : {args['raison']}"[:500])
        bilan["bloquees"] += 1
        return texte("Offre bloquée.")

    @tool("ajouter_offre", "Ajoute une nouvelle offre (dédoublonnée par le code) et la qualifie selon son score.",
          {"entreprise": str, "intitule": str, "lieu": str, "url_officielle": str, "score": int,
           "raison_score": str, "date_publication": str, "date_debut": str, "duree": str, "source": str})
    async def ajouter_offre(args):
        if hors_contrat(args["intitule"]) or hors_contrat(args["url_officielle"]):
            return texte(f"REFUS : autre contrat que celui recherché ({libelle_contrat()}) : rien n'est ajouté.")
        permis, raison = autorise(args["url_officielle"])
        if not permis:
            return texte(f"REFUS : {raison}")
        try:
            contenu = await asyncio.to_thread(lire, args["url_officielle"])
        except AccesRefuse as refus:
            return texte(f"REFUS : {refus}")
        champs = {k: args.get(k, "") for k in ("entreprise", "intitule", "lieu", "date_publication",
                                               "date_debut", "duree", "source", "score", "raison_score")}
        champs["url"] = args["url_officielle"]
        id_offre = sheet.ajouter_offre(champs)
        if id_offre is None:
            bilan["doublons"] += 1
            return texte("Doublon : cette offre est déjà dans le Sheet.")
        bilan["nouvelles"] += 1
        if args["score"] >= SEUIL:
            _ecrire_offre_md(id_offre, args["url_officielle"], contenu, args["entreprise"], args["intitule"])
            sheet.changer_statut(id_offre, "Qualifiée")
            bilan["qualifiees"] += 1
            return texte(f"Ajoutée et qualifiée ({id_offre}).")
        sheet.changer_statut(id_offre, "Écartée")
        bilan["ecartees"] += 1
        return texte(f"Ajoutée puis écartée, score {args['score']} < {SEUIL} ({id_offre}).")

    @tool("rechercher_workday", "Liste les offres d'un site carrière Workday (…myworkdayjobs.com/<site>) "
          "pour des mots-clés : titre, lieu, date, URL.", {"url_site": str, "mots_cles": str})
    async def rechercher_workday(args):
        import json
        try:
            offres = await asyncio.to_thread(workday_chercher, args["url_site"], args["mots_cles"], 60)
        except AccesRefuse as refus:
            return texte(f"REFUS : {refus}")
        return texte(json.dumps(offres, ensure_ascii=False) if offres else "Aucune offre pour ces mots-clés.")

    return create_sdk_mcp_server(name="chasseur", tools=[
        lire_page, rechercher_workday, enregistrer_offre, ecarter_offre, bloquer_offre, ajouter_offre])


def options_chasseur(sheet, bilan: dict) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=os.getenv("MODELE_CHASSEUR", "haiku"),   # tri et extraction
        system_prompt=SYSTEME,
        tools=["WebSearch"],                     # lecture des pages : uniquement via lire_page (robots.txt)
        mcp_servers={"chasseur": serveur_chasseur(sheet, bilan)},
        allowed_tools=["WebSearch", "mcp__chasseur"],
        permission_mode="dontAsk",
        setting_sources=[],
        cwd=str(RACINE),
        max_turns=60,                            # journaux : cibler 90 % sous 32, chercher ≤ 61
        max_budget_usd=BUDGET_PAR_LOT_USD * 2,
        extra_args={"strict-mcp-config": None},  # seulement le serveur du chasseur, aucun connecteur
    )


def nouveau_bilan() -> dict:
    return {"completees": 0, "nouvelles": 0, "qualifiees": 0, "ecartees": 0, "bloquees": 0, "doublons": 0}


async def completer(sheet, nombre: int, bilan: dict) -> None:
    """Complète jusqu'à `nombre` offres « Qualifiée » sans texte, par lots de TAILLE_LOT."""
    a_faire = [o for o in sheet.lire_par_statut("Qualifiée") if not (DOSSIERS / o["id"] / "offre.md").exists()]
    i, n = (int(x) for x in os.getenv("WORKER", "0/1").split("/"))   # postuleurs parallèles : chacun sa part
    a_faire = [o for o in a_faire if int(o["id"].removeprefix("OF-"), 16) % n == i][:nombre]
    for debut in range(0, len(a_faire), TAILLE_LOT):
        lot = [{k: o[k] for k in ("id", "entreprise", "intitule", "lieu", "url", "source", "notes")}
               for o in a_faire[debut:debut + TAILLE_LOT]]
        prompt = ("COMPLÈTE ces offres du Sheet (une par une) :\n"
                  + json.dumps(lot, ensure_ascii=False, indent=2))
        await lancer_agent("chasseur (compléter)", prompt, options_chasseur(sheet, bilan))


async def chercher(sheet, nombre: int, bilan: dict) -> None:
    prompt = (f"CHERCHE jusqu'à {nombre} nouvelles offres qui correspondent aux critères, les plus récentes "
              "d'abord (idéalement publiées ces derniers jours ; une offre plus ancienne mais encore en ligne "
              "reste acceptable, avec un score de fraîcheur plus bas), en commençant par les secteurs "
              "prioritaires. Nous sommes le "
              f"{date.today().strftime('%d/%m/%Y')}.")
    await lancer_agent("chasseur (chercher)", prompt, options_chasseur(sheet, bilan))


async def cibler(sheet, nombre: int, bilan: dict) -> None:
    """Va chercher en masse, sur le site carrière de chaque entreprise ciblée, toutes les offres qui
    correspondent aux critères (mode « cibler »)."""
    from outils.cibles import marquer_ciblee, prochaines

    for entreprise in prochaines(nombre):
        prompt = (f"CIBLE l'entreprise {entreprise} : récupère TOUTES ses offres actuellement en ligne qui "
                  "correspondent aux critères, directement sur son site carrière officiel.\n"
                  "Trouve son site carrière officiel (WebSearch). S'il s'agit d'un Workday "
                  "(…myworkdayjobs.com/<site>), utilise rechercher_workday ; sinon utilise la recherche de son site "
                  f"carrière (ou WebSearch) avec PLUSIEURS mots-clés : {mots_cles()}. "
                  "Les grands groupes publient beaucoup d'offres : parcours toutes les pages de résultats.\n"
                  + f"Lis avec lire_page chaque offre dont le titre paraît pertinent ({libelle_contrat()}, {metiers()}), "
                    "puis appelle ajouter_offre pour chacune "
                    "(source : « Site carrière " + entreprise + " »). Au plus 25 offres. Nous sommes le "
                  + date.today().strftime("%d/%m/%Y") + ".")
        avant = (bilan["qualifiees"], bilan["nouvelles"] + bilan["doublons"])
        try:
            await lancer_agent(f"chasseur (cibler {entreprise})", prompt, options_chasseur(sheet, bilan))
            vues = bilan["nouvelles"] + bilan["doublons"] > avant[1]
            if not vues:   # site carrière en JavaScript, illisible sans navigateur : on le fouille dans le navigateur
                from agents.lecteur import fouiller_entreprise
                pistes = {}
                await fouiller_entreprise(sheet, pistes, entreprise)
                bilan["nouvelles"] += pistes.get("pistes", 0)
                bilan["qualifiees"] += pistes.get("pistes", 0)
                vues = pistes.get("pistes", 0) > 0
            # « sans stage » : offres vues mais aucune retenue. Site toujours illisible : délai normal.
            marquer_ciblee(entreprise, sans_stage=vues and bilan["qualifiees"] == avant[0])
        except Exception as erreur:   # une entreprise en échec n'arrête pas les suivantes
            print(f"→ {entreprise} : erreur du chasseur ({erreur.__class__.__name__})")


async def pistes(sheet, nombre: int, bilan: dict) -> None:
    """Offres vues dans les alertes LinkedIn du Gmail : le chasseur les retrouve sur le site de l'entreprise."""
    from outils.alertes import marquer_traitees, nouvelles_pistes

    a_faire = nouvelles_pistes(maximum=nombre)
    for debut in range(0, len(a_faire), 8):
        lot = a_faire[debut:debut + 8]
        prompt = ("PISTES : offres vues dans les alertes emploi LinkedIn du candidat (« intitulé  entreprise · lieu », "
                  "suivi de mentions LinkedIn à ignorer). N'ouvre jamais LinkedIn. Pour chaque piste :\n"
                  f"- si l'intitulé ne correspond clairement pas aux critères (autre contrat, autre métier que {metiers()}, "
                  f"hors zone {zone()}), passe sans rien faire ;\n"
                  "- sinon, trouve CETTE offre sur le site carrière officiel de l'entreprise (WebSearch, "
                  "rechercher_workday si c'est un Workday), lis-la avec lire_page, puis ajouter_offre avec "
                  "l'URL officielle (source : « Alerte LinkedIn → site entreprise »). Introuvable après 2 recherches : "
                  "passe à la suivante.\n" + json.dumps(lot, ensure_ascii=False, indent=2)
                  + f"\nNous sommes le {date.today().strftime('%d/%m/%Y')}.")
        await lancer_agent("chasseur (alertes)", prompt, options_chasseur(sheet, bilan))
        marquer_traitees([p["ref"] for p in lot])


async def posts(sheet, nombre: int, bilan: dict) -> None:
    """Offres relayées dans des posts LinkedIn, trouvées par le moteur de recherche (sans ouvrir LinkedIn)."""
    prompt = (f"POSTS : des recruteurs et des candidats publient sur LinkedIn des posts qui listent des offres ({libelle_contrat()}). "
              f"Trouve-les avec WebSearch, avec « site:linkedin.com/posts » suivi de ces recherches : {mots_cles()}. "
              "N'ouvre JAMAIS une page LinkedIn : utilise seulement le texte des résultats de recherche, et les liens "
              "qu'ils citent hors LinkedIn (site carrière, Notion, Google Docs…) que tu peux lire avec lire_page. "
              "Pour chaque offre repérée (entreprise + intitulé), trouve-la sur le site carrière officiel de "
              "l'entreprise, lis-la, puis ajouter_offre avec l'URL officielle (source : « Post LinkedIn → site "
              f"entreprise »). Au plus {nombre} offres. Nous sommes le {date.today().strftime('%d/%m/%Y')}.")
    await lancer_agent("chasseur (posts)", prompt, options_chasseur(sheet, bilan))


async def main() -> None:
    from outils.sheet import ouvrir_pipeline

    parser = argparse.ArgumentParser(description="Trouve et qualifie les offres.")
    parser.add_argument("--completer", type=int, default=0, help="offres « Qualifiée » à compléter")
    parser.add_argument("--chercher", type=int, default=0, help="nouvelles offres à chercher")
    parser.add_argument("--alertes", type=int, default=0, help="pistes des alertes LinkedIn du Gmail à traiter")
    parser.add_argument("--posts", type=int, default=0, help="offres à tirer des posts LinkedIn (via la recherche)")
    parser.add_argument("--cibler", type=int, default=0, help="entreprises ciblées à moissonner")
    args = parser.parse_args()

    sheet, bilan = ouvrir_pipeline(), nouveau_bilan()
    if args.completer:
        await completer(sheet, args.completer, bilan)
    if args.chercher:
        await chercher(sheet, args.chercher, bilan)
    if args.alertes:
        await pistes(sheet, args.alertes, bilan)
    if args.posts:
        await posts(sheet, args.posts, bilan)
    if args.cibler:
        await cibler(sheet, args.cibler, bilan)
    print("Bilan du chasseur :", bilan)


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    asyncio.run(main())
