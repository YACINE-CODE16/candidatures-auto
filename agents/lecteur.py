"""Agent Lecteur : lit dans le navigateur connecté les offres hébergées par JobTeaser, LinkedIn et
Welcome to the Jungle, que le chasseur (simple lecteur HTTP) ne peut pas lire.

Pour chaque offre « Qualifiée » sans texte (ou bloquée par le chasseur faute de page officielle) :
  1. ouvre l'offre dans le navigateur, connecté aux comptes du candidat (JobTeaser école, LinkedIn) ;
  2. copie le texte de l'offre (outil enregistrer_offre, qui écrit dossiers/<ID>/offre.md) ;
  3. trouve le lien pour postuler sur le site de l'entreprise (règle du candidat : les plateformes
     servent seulement à trouver les offres) ;
  4. ou écarte l'offre si elle est expirée / hors critères.

Lancement :
    python -m agents.lecteur --limite 10
"""

import argparse
import asyncio
import os
from datetime import date
from urllib.parse import urlsplit

from dotenv import load_dotenv

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from agents.chasseur import CRITERES, SEUIL
from outils.config import CONTRATS, contrat, hors_contrat, mots_cles, zone
from agents.postuleur import CAPTURES, NAVIGATEUR_VISIBLE, PLAYWRIGHT_MCP, PROFIL_NAVIGATEUR, identifiants
from outils.config import candidat, nom_complet, prenom
from outils.sheet import age_offre, priorite
from outils.lancement import RACINE, lancer_agent

DOSSIERS = RACINE / "dossiers"
PLATEFORMES = ("jobteaser.com", "linkedin.com", "welcometothejungle.com")
BUDGET_PAR_OFFRE_USD = 0.80
AGE_MAX_JOURS = 4   # au-delà, l'offre est trop ancienne pour valoir une lecture (et le nettoyage la supprime)

SYSTEME = f"""Tu lis des offres pour {nom_complet()} dans un vrai navigateur (outils mcp__navigateur__*),
connecté à ses comptes. Tu ne postules PAS : tu lis et tu notes.

{CRITERES}

Pour l'offre donnée :
1. Ouvre son URL. Si une connexion est demandée (JobTeaser : compte école ; LinkedIn), appelle
   identifiants_site(<domaine>) et connecte-toi. Ne crée jamais de compte et ne réinitialise jamais
   un mot de passe ici. Si une vérification par code mail apparaît, utilise lire_mails. Si une
   vérification par téléphone ou un CAPTCHA apparaît : bloquer_offre avec le motif. Refuse les cookies.
   SI LA PLATEFORME BLOQUE (JobTeaser, Welcome to the Jungle : test « Just a moment », Cloudflare, 403,
   CAPTCHA) : ne bloque pas l'offre. Prends l'entreprise et l'intitulé donnés ci-dessous, va directement
   sur le site de recrutement de l'entreprise (WebSearch « <entreprise> carrières » ou « <entreprise>
   recrutement », puis ouvre-le dans le navigateur), cherche l'offre avec le moteur de recherche
   du site (mots de l'intitulé), connecte-toi si le site le demande
   (identifiants_site), et lis l'offre là-bas (étapes 2 et 3). Ne bloque que si l'offre est introuvable
   sur le site de l'entreprise. Une « Piste de {prenom()} » dans les notes est un lien à essayer en premier.
   AGIS COMME UN HUMAIN : page vide, erreur, 403 ou chargement bloqué → attends 5 secondes et recharge
   (jusqu'à 3 fois) avant de conclure. Page générique (accueil carrière, espace candidat) → cherche
   l'offre de l'entreprise par son intitulé avec la recherche du site. Un CAPTCHA à résoudre reste bloquant.
2. Récupère le texte complet de l'offre avec browser_evaluate (document.body.innerText de la zone de
   l'offre) et recopie-le tel quel, sans résumer ni reformuler.
3. Trouve le lien pour postuler SUR LE SITE DE L'ENTREPRISE : bouton « Postuler sur le site de
   l'entreprise », lien vers son site carrière ou son outil de recrutement (Workday, SuccessFactors,
   Workable, Greenhouse, Lever, SmartRecruiters, Teamtailor…). Si la page n'en donne pas, cherche
   l'offre sur le site carrière de l'entreprise avec WebSearch. On ne postule jamais par la plateforme.
4. Appelle UN outil :
   - enregistrer_offre(id, texte, url_candidature, mode, score, raison_score) avec
     url_candidature = la page de l'offre sur le site de l'entreprise et mode = « site entreprise » ;
   - ecarter_offre(id, raison) : offre expirée, déjà pourvue, ou hors critères ;
   - bloquer_offre(id, raison) : page impossible à lire, ou aucun site d'entreprise trouvé
     (motif « Site de l'entreprise introuvable »).
Termine par une ligne : « RÉSULTAT: <mode ou écartée ou bloquée> »."""


def serveur_lecteur(sheet, offre: dict, bilan: dict):
    from outils.gmail import chercher

    def texte(t: str) -> dict:
        return {"content": [{"type": "text", "text": t}]}

    @tool("identifiants_site", "Identifiants du candidat sur un site (JobTeaser, LinkedIn…), sans création.",
          {"site": str})
    async def identifiants_site(args):
        compte = identifiants(args["site"].lower().removeprefix("www."))
        if compte.get("source") != ".env":
            return texte("Aucun identifiant fourni par le candidat pour ce site : ne t'y connecte pas.")
        return texte(f'{{"email": "{compte["email"]}", "mot_de_passe": "{compte["mot_de_passe"]}"}}')

    @tool("lire_mails", "Recherche Gmail (lecture seule), ex. « newer_than:10m linkedin code ».", {"recherche": str})
    async def lire_mails(args):
        import json
        return texte(json.dumps(await asyncio.to_thread(chercher, args["recherche"], 3), ensure_ascii=False))

    @tool("enregistrer_offre", "Enregistre le texte de l'offre et la façon d'y postuler.",
          {"id": str, "texte": str, "url_candidature": str, "mode": str, "score": int, "raison_score": str})
    async def enregistrer_offre(args):
        from agents.postuleur import PLATEFORMES
        if any(p in args["url_candidature"] for p in PLATEFORMES):
            return texte("ERREUR : url_candidature doit être sur le site de l'entreprise, pas sur une plateforme. "
                         "Trouve-la, ou bloque l'offre (« Site de l'entreprise introuvable »).")
        if len(args["texte"]) < 300:
            return texte("ERREUR : texte trop court, récupère le texte complet de l'offre.")
        if args["score"] < SEUIL:
            sheet.changer_statut(offre["id"], "Écartée", score=args["score"], raison_score=args["raison_score"][:300])
            bilan["ecartees"] += 1
            return texte(f"Score {args['score']} < {SEUIL} : offre écartée.")
        dossier = DOSSIERS / offre["id"]
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "offre.md").write_text(
            f"# {offre['entreprise']} — {offre['intitule']}\nSource : {args['url_candidature']}\n"
            f"Mode de candidature : {args['mode']}\nPage de l'offre : {offre['url']}\nLue le : {date.today().isoformat()}\n\n"
            f"{args['texte']}\n", encoding="utf-8")
        if offre["statut"] == "Bloquée":
            sheet.changer_statut(offre["id"], "Qualifiée", motif_blocage="")
        sheet.mettre_a_jour(offre["id"], score=args["score"], raison_score=args["raison_score"][:300],
                            canal=args["mode"])
        bilan["lues"] += 1
        return texte("Offre enregistrée.")

    @tool("ecarter_offre", "Écarte l'offre (expirée, pourvue, hors critères).", {"id": str, "raison": str})
    async def ecarter_offre(args):
        if offre["statut"] == "Bloquée":
            sheet.changer_statut(offre["id"], "Qualifiée")
        sheet.changer_statut(offre["id"], "Écartée", raison_score=args["raison"][:300])
        bilan["ecartees"] += 1
        return texte("Offre écartée.")

    @tool("bloquer_offre", "Bloque l'offre (page illisible, vérification impossible).", {"id": str, "raison": str})
    async def bloquer_offre(args):
        if offre["statut"] != "Bloquée":
            sheet.changer_statut(offre["id"], "Bloquée", motif_blocage=f"Lecteur : {args['raison']}"[:500])
        else:
            sheet.mettre_a_jour(offre["id"], motif_blocage=f"Lecteur : {args['raison']}"[:500])
        bilan["bloquees"] += 1
        return texte("Offre bloquée.")

    return create_sdk_mcp_server(name="lecteur", tools=[identifiants_site, lire_mails, enregistrer_offre,
                                                         ecarter_offre, bloquer_offre])


def offres_a_lire(sheet) -> list[dict]:
    """Offres hébergées par une plateforme, sans texte : « Qualifiée », ou « Bloquée » par le chasseur."""
    def plateforme(o):
        return any(d in urlsplit(o["url"]).netloc for d in PLATEFORMES)
    return [o for o in sheet._charger()
            if (plateforme(o) or o["source"].startswith("Fouille")) and not (DOSSIERS / o["id"] / "offre.md").exists()
            and ((age := age_offre(o)) is None or age <= AGE_MAX_JOURS)
            and (o["statut"] == "Qualifiée"
                 or (o["statut"] == "Bloquée" and o["motif_blocage"].startswith("Chasseur")
                     and "introuvable" not in o["motif_blocage"].lower())   # déjà cherchée 3 fois en vain
                 or (o["statut"] == "Bloquée" and o["motif_blocage"].startswith("Lecteur")
                     and any(m in o["motif_blocage"].lower() for m in ("cloudflare", "403", "sécurité", "captcha"))
                     and "introuvable" not in o["motif_blocage"].lower()))]   # déjà cherchée sur le site de l'entreprise


async def lire(sheet, limite: int, bilan: dict) -> None:
    i, n = (int(x) for x in os.getenv("WORKER", "0/1").split("/"))   # lecteurs parallèles : chacun sa part
    a_lire = sorted((o for o in offres_a_lire(sheet) if int(o["id"].removeprefix("OF-"), 16) % n == i), key=priorite)
    for offre in a_lire[:limite]:   # les offres publiées depuis ≤ 48 h d'abord
        navigateur = _navigateur()
        options = ClaudeAgentOptions(
            model=os.getenv("MODELE_LECTEUR", "haiku"), system_prompt=SYSTEME, tools=["WebSearch"],   # extraction
            mcp_servers={"navigateur": {"command": "npx", "args": navigateur},
                         "lecteur": serveur_lecteur(sheet, offre, bilan)},
            allowed_tools=["WebSearch", "mcp__navigateur", "mcp__lecteur"], permission_mode="dontAsk",
            setting_sources=[], cwd=str(RACINE), max_turns=16, max_budget_usd=BUDGET_PAR_OFFRE_USD,   # 90 % sous 15
            max_buffer_size=20 * 1024 * 1024,
            disallowed_tools=["mcp__navigateur__browser_take_screenshot"],   # le texte suffit pour lire
            extra_args={"strict-mcp-config": None},
        )
        prompt = (f"Offre {offre['id']} : {offre['entreprise']} — {offre['intitule']} ({offre['lieu']}).\n"
                  f"URL : {offre['url']}" + (f"\nNotes du Sheet : {offre['notes']}" if offre.get("notes") else ""))
        try:
            await lancer_agent(f"lecteur {offre['id']}", prompt, options)
        except Exception as erreur:   # une offre en échec n'arrête pas les suivantes
            print(f"→ {offre['id']} : erreur du lecteur ({erreur.__class__.__name__})")


FOUILLE = """Tu fouilles une plateforme d'offres pour {nom}, dans un vrai navigateur connecté à ses comptes.
{criteres}

Pour chaque URL de recherche donnée : ouvre-la (connexion si demandée : identifiants_site(<domaine>), code par
mail : lire_mails ; ne crée jamais de compte, ne réinitialise jamais un mot de passe). Lis la LISTE des résultats
en texte (browser_snapshot), sans ouvrir chaque offre, sur 2 pages au plus. Pour chaque offre qui correspond aux
critères, appelle ajouter_piste(entreprise, intitule, lieu, url, date_publication) avec l'URL de l'offre sur la
plateforme et la date telle qu'affichée (« il y a 2 jours »). Ignore les autres types de contrat.
Au plus 40 pistes en tout.
Si on te donne un NOM D'ENTREPRISE au lieu d'une URL : trouve son site carrière officiel (WebSearch), ouvre-le
dans le navigateur et utilise SA recherche avec plusieurs mots-clés ({mots}), filtre sur la zone {zone},
et parcours toutes les pages de résultats (jusqu'à 5 par recherche). Au plus 25 pistes par entreprise.
ARRÊTE-TOI IMMÉDIATEMENT si la plateforme demande une vérification (CAPTCHA, « security check », code par
téléphone, compte restreint) : ne tente rien, termine en le disant. Agis lentement, comme un humain.
Termine par une ligne : « RÉSULTAT: <n> pistes »."""


def recherches(plateforme: str) -> list[str]:
    """URL de recherche du jour : offres de la zone publiées depuis 24 h (LinkedIn, fouille quotidienne), JobTeaser."""
    from urllib.parse import quote
    c, regle = candidat(), CONTRATS[contrat()]
    mots = [f"{m} {regle['fr']}" if not regle["f_jt"] else m for m in c.get("metiers", [])]   # sans filtre : le mot-clé trie
    if plateforme == "linkedin":
        z = c.get("zone", {})
        filtre = f"&f_JT={regle['f_jt']}" if regle["f_jt"] else ""
        return [f"https://www.linkedin.com/jobs/search/?keywords={quote(m)}&location={quote(z.get('libelle', ''))}"
                f"&geoId={z.get('geoId', '')}{filtre}&f_TPR=r86400&sortBy=DD" for m in mots]
    ecole = candidat().get("jobteaser", "")
    hote = ecole if ecole.endswith("jobteaser.com") else f"{ecole}.jobteaser.com"   # « monecole » ou « monecole.jobteaser.com »
    return [f"https://{hote}/fr/job-offers?q={quote(m)}&contract={regle['jobteaser']}" for m in mots] if ecole else []


def serveur_fouille(sheet, bilan: dict, source: str):
    from outils.gmail import chercher

    def texte(t: str) -> dict:
        return {"content": [{"type": "text", "text": t}]}

    @tool("identifiants_site", "Identifiants du candidat sur un site (JobTeaser, LinkedIn…), sans création.",
          {"site": str})
    async def identifiants_site(args):
        compte = identifiants(args["site"].lower().removeprefix("www."))
        if compte.get("source") != ".env":
            return texte("Aucun identifiant fourni par le candidat pour ce site : ne t'y connecte pas.")
        return texte(f'{{"email": "{compte["email"]}", "mot_de_passe": "{compte["mot_de_passe"]}"}}')

    @tool("lire_mails", "Recherche Gmail (lecture seule), ex. « newer_than:10m linkedin code ».", {"recherche": str})
    async def lire_mails(args):
        import json
        return texte(json.dumps(await asyncio.to_thread(chercher, args["recherche"], 3), ensure_ascii=False))

    @tool("ajouter_piste", "Ajoute une offre trouvée dans la liste de résultats (dédoublonné par le code).",
          {"entreprise": str, "intitule": str, "lieu": str, "url": str, "date_publication": str})
    async def ajouter_piste(args):
        if hors_contrat(args["intitule"]):
            return texte("REFUS : autre type de contrat que celui recherché.")
        id_offre = sheet.ajouter_offre({**{k: args[k] for k in ("entreprise", "intitule", "lieu", "url",
                                                                 "date_publication")}, "source": source})
        if id_offre is None:
            return texte("Déjà dans le Sheet.")
        sheet.changer_statut(id_offre, "Qualifiée")   # le lecteur la lira et trouvera le site de l'entreprise
        bilan["pistes"] = bilan.get("pistes", 0) + 1
        return texte(f"Ajoutée ({id_offre}).")

    return create_sdk_mcp_server(name="fouille", tools=[identifiants_site, lire_mails, ajouter_piste])


async def fouiller(sheet, bilan: dict) -> None:
    """Fouille les listes d'offres de LinkedIn et JobTeaser (une fois par jour, appelée par main.py)."""
    for plateforme, source in (("linkedin", "Fouille LinkedIn"), ("jobteaser", "Fouille JobTeaser")):
        if urls := recherches(plateforme):
            await fouiller_urls(sheet, bilan, plateforme, urls, source)


async def fouiller_entreprise(sheet, bilan: dict, entreprise: str) -> None:
    """Site carrière en JavaScript, illisible pour le chasseur : on le parcourt dans le navigateur."""
    await fouiller_urls(sheet, bilan, entreprise, [f"ENTREPRISE : {entreprise}"],
                        f"Fouille site carrière {entreprise}", recherche_web=True)


async def fouiller_urls(sheet, bilan: dict, nom: str, urls: list[str], source: str,
                        recherche_web: bool = False) -> None:
    """Parcourt des pages de résultats dans le navigateur (plateforme, ou site carrière en JavaScript)."""
    web = ["WebSearch"] if recherche_web else []
    options = ClaudeAgentOptions(
        model=os.getenv("MODELE_LECTEUR", "haiku"),
        system_prompt=FOUILLE.format(nom=nom_complet(), criteres=CRITERES, mots=mots_cles(), zone=zone()), tools=web,
        mcp_servers={"navigateur": {"command": "npx", "args": _navigateur()},
                     "fouille": serveur_fouille(sheet, bilan, source)},
        allowed_tools=[*web, "mcp__navigateur", "mcp__fouille"], permission_mode="dontAsk",
        disallowed_tools=["mcp__navigateur__browser_take_screenshot"],
        setting_sources=[], cwd=str(RACINE), max_turns=60, max_budget_usd=1.50,
        max_buffer_size=20 * 1024 * 1024, extra_args={"strict-mcp-config": None},
    )
    try:
        await lancer_agent(f"fouille {nom}", "Recherches à parcourir :\n" + "\n".join(urls), options)
    except Exception as erreur:   # une source en échec n'arrête pas les autres
        print(f"→ fouille {nom} : erreur ({erreur.__class__.__name__} : {str(erreur)[:200]})")


def _navigateur() -> list[str]:
    navigateur = ["-y", PLAYWRIGHT_MCP] + ([] if NAVIGATEUR_VISIBLE else ["--headless"]) + [
        "--user-data-dir", str(PROFIL_NAVIGATEUR), "--output-dir", str(CAPTURES)]
    if os.getenv("CHROMIUM_PATH"):
        navigateur += ["--executable-path", os.environ["CHROMIUM_PATH"]]
    if os.geteuid() == 0:
        navigateur.append("--no-sandbox")
    return navigateur


def nouveau_bilan() -> dict:
    return {"lues": 0, "ecartees": 0, "bloquees": 0}


async def main() -> None:
    from outils.sheet import ouvrir_pipeline

    parser = argparse.ArgumentParser(description="Lit les offres JobTeaser, LinkedIn et WTTJ dans le navigateur.")
    parser.add_argument("--limite", type=int, default=10)
    args = parser.parse_args()
    bilan = nouveau_bilan()
    await lire(ouvrir_pipeline(), args.limite, bilan)
    print("Bilan du lecteur :", bilan)


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    asyncio.run(main())
