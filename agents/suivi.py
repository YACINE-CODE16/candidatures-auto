"""Agent Suivi : lit les réponses dans Gmail (lecture seule) et met le Sheet à jour.

Le code récupère les mails récents qui ressemblent à des réponses de recruteurs, et la liste
des candidatures « Envoyée ». L'agent (haiku) associe chaque mail à une candidature et le classe :
  - « Entretien » : invitation à un entretien, un test technique, un appel ;
  - « Refus » : réponse négative ;
  - « Réponse reçue » : accusé de réception ou autre réponse.
Chaque test ou entretien à venir est aussi noté dans l'onglet « Tests & entretiens » du Sheet, même
sans candidature correspondante, et le candidat en reçoit le récapitulatif par mail (à lui seul).
Les transitions de statut restent contrôlées par outils/sheet.py, et un mail déjà traité n'est
jamais relu (logs/suivi_mails_traites.json).

Lancement :
    python -m agents.suivi                 # mails des 3 derniers jours
    python -m agents.suivi --jours 30      # premier passage
"""

import argparse
import asyncio
import json
import re

from dotenv import load_dotenv

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from outils.config import nom_complet
from outils.gmail import chercher, envoyer_a_moi
from outils.lancement import RACINE, lancer_agent

TRAITES = RACINE / "logs" / "suivi_mails_traites.json"
STATUTS_SUIVI = ("Envoyée", "Réponse reçue", "Entretien")
REPONSES = ("Réponse reçue", "Entretien", "Refus")
BUDGET_USD = 1.00
ONGLET_RDV = "Tests & entretiens"
COLONNES_RDV = ["Reçu le", "Entreprise", "Poste", "Type", "État", "Échéance / date", "Lien du test / RDV",
                "Résumé", "ID offre", "Offre dans le Sheet", "Lieu", "Lien de l'offre"]
TYPES_RDV = ("Test technique", "Test en ligne", "Étude de cas", "Entretien")
ETATS_RDV = ("À faire", "Passé", "À planifier")
MAILS_MAX = 200   # au-delà de 50, la fenêtre de quelques jours n'était pas couverte
LOT = 40          # mails par appel d'agent : le contexte et le budget restent petits

SYSTEME = f"""Tu fais le suivi des candidatures de {nom_complet()}.
On te donne des mails reçus et la liste de ses candidatures en cours. Pour chaque mail qui répond
à une candidature (même entreprise, et même poste quand il est cité), appelle
enregistrer_reponse(id, statut, resume) avec :
  - « Entretien » : invitation à un entretien (téléphone, visio, sur place) avec une personne ;
  - « Refus » : réponse négative ;
  - « Réponse reçue » : test en ligne ou évaluation à passer, demande
    d'information, réponse personnalisée.
Un accusé de réception automatique (« Votre candidature a bien été reçue », « Nous avons bien reçu
votre dossier »…) N'EST PAS une réponse : n'enregistre rien, la candidature reste « Envoyée ».
Un même test peut valoir pour plusieurs postes d'une entreprise : associe-le à toutes les candidatures citées.
resume : une phrase factuelle avec la date du mail (ex. « 28/10 : invitation à un entretien RH »).
Ignore les mails sans lien avec une candidature (publicités, newsletters, alertes d'offres).
Dans le doute sur l'association, n'enregistre rien.
EN PLUS, pour chaque mail qui demande au candidat de passer un test (technique, en ligne, codage,
logique, HackerRank, Codility, CodinGame, HireVue, vidéo différée…), une étude de cas ou un
entretien, appelle noter_rendez_vous, MÊME si aucune candidature ne correspond (id vide) :
  type parmi « Test technique », « Test en ligne », « Étude de cas », « Entretien » ;
  etat : « À faire » (test à passer, entretien fixé), « Passé » (le mail confirme que le test est fait,
  ex. « Vous venez de passer un test ») ou « À planifier » (entretien sans date) ;
  id : l'id de la candidature, ou plusieurs ids séparés par des virgules quand un même test vaut pour
  plusieurs postes (note alors UNE ligne par poste cité dans le mail, avec son id) ;
  echeance : date limite du test ou date et heure de l'entretien telles qu'écrites, sinon « non précisée » ;
  lien : l'adresse pour passer le test ou rejoindre l'entretien, sinon vide.
N'invente rien : seulement ce qui est écrit dans le mail. Un même test n'est noté qu'une fois.
Termine par un bilan d'une ligne par mail retenu."""


def _mails_recents(jours: int) -> list[dict]:
    requete = (f"newer_than:{jours}d -from:me -category:promotions "
               "(candidature OR application OR stage OR internship OR alternance OR entretien OR interview OR recrutement "
               "OR test OR assessment OR évaluation OR HackerRank OR Codility OR CodinGame OR HireVue)")
    deja = set(json.loads(TRAITES.read_text())) if TRAITES.exists() else set()
    return [m for m in chercher(requete, MAILS_MAX) if m["id"] not in deja]


def _sans_traceurs(texte: str) -> str:
    """Raccourcit les liens de suivi géants (sendgrid…) qui cachaient le contenu du mail à l'agent."""
    return re.sub(r"\[(https?://[^\]\s]{60})[^\]\s]{40,}\]", r"[\1…]", texte)


def _onglet_rdv(sheet):
    classeur = sheet.feuille.spreadsheet
    try:
        return classeur.worksheet(ONGLET_RDV)
    except Exception:   # gspread.WorksheetNotFound : premier passage
        onglet = classeur.add_worksheet(ONGLET_RDV, rows=200, cols=len(COLONNES_RDV))
        onglet.append_row(COLONNES_RDV)
        return onglet


def serveur_suivi(sheet, bilan: dict, rdv: list):
    def texte(t: str) -> dict:
        return {"content": [{"type": "text", "text": t}]}

    @tool("enregistrer_reponse", "Enregistre la réponse d'un recruteur à une candidature.",
          {"id": str, "statut": str, "resume": str})
    async def enregistrer_reponse(args):
        if args["statut"] not in REPONSES:
            return texte(f"ERREUR : statut à choisir parmi {REPONSES}.")
        offre = next((o for o in sheet._charger() if o["id"] == args["id"]), None)
        if offre is None or offre["statut"] not in STATUTS_SUIVI:
            return texte("ERREUR : candidature inconnue ou pas en cours.")
        if offre["statut"] == args["statut"]:
            sheet.mettre_a_jour(args["id"], derniere_reponse=args["resume"][:300])
        else:
            sheet.changer_statut(args["id"], args["statut"], derniere_reponse=args["resume"][:300])
        bilan[args["statut"]] = bilan.get(args["statut"], 0) + 1
        return texte("Réponse enregistrée.")

    @tool("noter_rendez_vous", "Note un test ou un entretien à passer dans l'onglet « Tests & entretiens ».",
          {"id": str, "entreprise": str, "poste": str, "type": str, "etat": str, "recu_le": str,
           "echeance": str, "lien": str, "resume": str})
    async def noter_rendez_vous(args):
        if args["type"] not in TYPES_RDV or args["etat"] not in ETATS_RDV:
            return texte(f"ERREUR : type parmi {TYPES_RDV}, etat parmi {ETATS_RDV}.")
        ids = [i.strip() for i in args["id"].split(",") if i.strip()]
        offres = [o for o in sheet._charger() if o["id"] in ids]   # détail de l'offre, tel qu'au Sheet
        detail = [" ; ".join(o[k] for o in offres) for k in ("intitule", "lieu", "url")]
        ligne = [args[k] for k in ("recu_le", "entreprise", "poste", "type", "etat", "echeance", "lien",
                                   "resume", "id")] + detail
        onglet = await asyncio.to_thread(_onglet_rdv, sheet)
        cle = lambda l: tuple(c.strip().lower() for c in l[:4])   # reçu le, entreprise, poste, type
        if any(cle(l) == cle(ligne) for l in await asyncio.to_thread(onglet.get_all_values)):
            return texte("Déjà noté.")
        await asyncio.to_thread(onglet.append_row, ligne, value_input_option="RAW")
        rdv.append(args)
        bilan[args["type"]] = bilan.get(args["type"], 0) + 1
        return texte("Noté.")

    return create_sdk_mcp_server(name="suivi", tools=[enregistrer_reponse, noter_rendez_vous])


def recapitulatif(rdv: list) -> tuple[str, str]:
    """Objet et texte du mail de rappel envoyé au candidat."""
    objet = f"[Candidatures] {len(rdv)} test(s) / entretien(s) à préparer"
    lignes = [f"- [{r['etat']}] {r['type']} — {r['entreprise']} ({r['poste'] or 'poste non précisé'}) : échéance "
              f"{r['echeance']}\n  {r['resume']}" + (f"\n  Lien : {r['lien']}" if r["lien"] else "") for r in rdv]
    return objet, "\n".join(lignes) + f"\n\nTout est aussi dans l'onglet « {ONGLET_RDV} » du Sheet."


async def suivre(sheet, jours: int, bilan: dict) -> None:
    mails = await asyncio.to_thread(_mails_recents, jours)
    if not mails:
        print("Suivi : aucun nouveau mail de recruteur.")
        return
    rdv = []
    candidatures = [{k: o[k] for k in ("id", "entreprise", "intitule", "date_envoi", "statut")}
                    for o in sheet._charger() if o["statut"] in STATUTS_SUIVI]
    try:
        for debut in range(0, len(mails), LOT):
            lot = mails[debut:debut + LOT]
            prompt = ("Candidatures en cours :\n" + json.dumps(candidatures, ensure_ascii=False) +
                      "\n\nMails reçus :\n" + json.dumps(
                          [{k: m[k] for k in ("date", "de", "objet")} | {"texte": _sans_traceurs(m["texte"])[:2500]}
                           for m in lot], ensure_ascii=False, indent=1))
            options = ClaudeAgentOptions(
                model="haiku", system_prompt=SYSTEME, tools=[],
                mcp_servers={"suivi": serveur_suivi(sheet, bilan, rdv)}, allowed_tools=["mcp__suivi"],
                permission_mode="dontAsk", setting_sources=[], cwd=str(RACINE), max_turns=15,
                extra_args={"strict-mcp-config": None},
                max_budget_usd=BUDGET_USD,
            )
            await lancer_agent("suivi", prompt, options)
            deja = set(json.loads(TRAITES.read_text())) if TRAITES.exists() else set()
            TRAITES.parent.mkdir(exist_ok=True)
            TRAITES.write_text(json.dumps(sorted(deja | {m["id"] for m in lot})))
    finally:   # ce qui a été noté est signalé même si l'agent s'arrête en route
        if rdv:
            await asyncio.to_thread(envoyer_a_moi, *recapitulatif(rdv))


async def main() -> None:
    from outils.sheet import ouvrir_pipeline

    parser = argparse.ArgumentParser(description="Met à jour le Sheet avec les réponses reçues par mail.")
    parser.add_argument("--jours", type=int, default=3, help="ancienneté maximale des mails lus")
    args = parser.parse_args()
    bilan = {}
    await suivre(ouvrir_pipeline(), args.jours, bilan)
    print("Bilan du suivi :", bilan)


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    asyncio.run(main())
