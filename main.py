"""Chef d'orchestre du pipeline de candidatures, lancé une fois par jour.

Ordre fixe (du code Python, pas un agent) :
  1. Chasseur  : complète les offres « Qualifiée » sans texte, puis cherche de nouvelles offres.
  2. Dossier   : CV + lettre vérifiés pour les offres « Qualifiée » qui ont leur texte.
  3. Postuleur : envoie les offres « Validée » (ou remplit sans envoyer avec --dry-run).
  4. Suivi     : lit les réponses dans Gmail et met le Sheet à jour.
  5. Résumé    : offres trouvées, dossiers prêts, envois, blocages → logs/resume_<date>.md.

Une étape qui plante n'arrête pas les suivantes : l'erreur est notée dans le résumé.

Lancement :
    python main.py --dry-run                 # tout, sauf l'envoi réel des candidatures
    python main.py                           # journée normale
    python main.py --max-envois 20 --max-dossiers 20
    python main.py --etapes suivi,resume     # seulement certaines étapes
"""

import argparse
import asyncio
import json
import os
import socket
import traceback
from collections import Counter
from datetime import date
from urllib.parse import urlsplit

from dotenv import load_dotenv

from outils.config import sources_connectees
from outils.lancement import RACINE
from outils.plafonds import autorises, compter, plafond
from outils.sheet import priorite

DOSSIERS = RACINE / "dossiers"
AGE_MAX_JOURS = 4   # au-delà, une offre jamais postulée est supprimée au début du cycle


def ma_part(offres: list[dict]) -> list[dict]:
    """Avec plusieurs workers (WORKER=« i/n »), chacun ne traite que sa part des offres, sans recouvrement."""
    i, n = (int(x) for x in os.getenv("WORKER", "0/1").split("/"))
    return [o for o in offres if int(o["id"].removeprefix("OF-"), 16) % n == i]


FAIT_AUJOURDHUI = RACINE / "logs" / "chasseur_du_jour.json"


def _une_fois_par_jour(tache: str, args) -> bool:
    """Vrai la première fois de la journée (puis noté) : ces recherches ne valent pas d'être refaites à chaque cycle.
    Un test à blanc ne compte pas : la vraie journée doit encore chercher."""
    if args.dry_run:
        return True
    faites = json.loads(FAIT_AUJOURDHUI.read_text()) if FAIT_AUJOURDHUI.exists() else {}
    if faites.get(tache) == date.today().isoformat():
        return False
    faites[tache] = date.today().isoformat()
    FAIT_AUJOURDHUI.parent.mkdir(exist_ok=True)
    FAIT_AUJOURDHUI.write_text(json.dumps(faites))
    return True


ETAPES = ("chasseur", "lecteur", "dossier", "postuleur", "suivi", "resume")


async def etape_chasseur(sheet, args, journal):
    from agents.chasseur import chercher, completer, nouveau_bilan

    bilan = journal.setdefault("chasseur", nouveau_bilan())
    if plafond("CHASSEUR_PAR_JOUR") and not _une_fois_par_jour("chasseur", args):
        print("Chasseur déjà passé aujourd'hui (plafond de l'abonnement).")
        return
    if args.max_completer and _une_fois_par_jour("completer", args):
        await completer(sheet, args.max_completer, bilan)
    if args.max_nouvelles and _une_fois_par_jour("chercher", args):
        await chercher(sheet, args.max_nouvelles, bilan)
    if args.max_alertes and _une_fois_par_jour("alertes", args):   # alertes LinkedIn du Gmail → site de l'entreprise
        from agents.chasseur import pistes
        await pistes(sheet, args.max_alertes, bilan)
    if args.max_posts and _une_fois_par_jour("posts", args):   # posts LinkedIn listant des offres (via la recherche)
        from agents.chasseur import posts
        await posts(sheet, args.max_posts, bilan)
    if args.max_cibles:   # moisson des sites carrière des entreprises ciblées
        from agents.chasseur import cibler
        await cibler(sheet, args.max_cibles, bilan)


async def etape_lecteur(sheet, args, journal):
    from agents.lecteur import fouiller, lire, nouveau_bilan

    bilan = journal.setdefault("lecteur", nouveau_bilan())
    if sources_connectees() and args.max_lecteur and _une_fois_par_jour("fouille", args):   # listes LinkedIn et JobTeaser
        await fouiller(sheet, bilan)
    await lire(sheet, args.max_lecteur, bilan)


async def etape_dossier(sheet, args, journal):
    from agents.dossier import preparer_dossier

    from agents.postuleur import CANDIDATURE_SIMPLIFIEE, PLATEFORMES, _lien_candidature, cle_site, sites_captcha

    toutes = sheet._charger()
    captcha = sites_captcha(toutes)
    # Pas de dossier (≈ 2,50 $) pour une offre qu'on ne pourra pas envoyer : lien d'une plateforme seulement,
    # ou site où un CAPTCHA a déjà bloqué (ces offres-là passent par le mode assisté).
    envoyable = lambda o: ((CANDIDATURE_SIMPLIFIEE or not any(p in _lien_candidature(o) for p in PLATEFORMES))
                           and cle_site(_lien_candidature(o)) not in captcha
                           and urlsplit(_lien_candidature(o)).netloc.lower() not in captcha)
    pretes = [o for o in toutes if o["statut"] == "Qualifiée" and (DOSSIERS / o["id"] / "offre.md").exists()]
    pretes = sorted((o for o in ma_part(pretes) if envoyable(o)), key=priorite)   # ≤ 48 h d'abord, puis score
    resultats = journal.setdefault("dossiers", [])
    for offre in pretes[: autorises("dossiers", args.max_dossiers)]:
        try:
            statut = await preparer_dossier(offre, sheet)
        except Exception as erreur:   # un dossier en échec n'arrête pas les suivants
            statut = f"Erreur technique ({erreur.__class__.__name__})"
        compter("dossiers")
        resultats.append((offre["id"], offre["entreprise"], offre["intitule"], statut))


async def etape_postuleur(sheet, args, journal):
    from agents.postuleur import postuler

    resultats = journal.setdefault("envois", [])
    for offre in sorted(ma_part(sheet.lire_validees()), key=priorite)[: autorises("envois", args.max_envois)]:   # ≤ 48 h d'abord
        issue = await postuler(sheet, offre, args.dry_run)
        if issue == "Envoyée":
            compter("envois")
        resultats.append((offre["id"], offre["entreprise"], offre["intitule"], issue, ""))


async def etape_suivi(sheet, args, journal):
    from agents.suivi import suivre

    bilan = {}
    await suivre(sheet, args.jours_suivi, bilan)
    journal["suivi"] = bilan


def resume(sheet, args, journal) -> str:
    """Résumé quotidien en Markdown : ce qui s'est passé aujourd'hui et l'état du pipeline."""
    offres = sheet._charger()
    statuts = Counter(o["statut"] for o in offres)
    aujourd_hui = date.today().strftime("%d/%m/%Y")
    l = [f"# Résumé du {aujourd_hui}" + (" (dry-run : aucun envoi réel)" if args.dry_run else ""), ""]

    if "chasseur" in journal:
        b = journal["chasseur"]
        l += ["## Offres trouvées",
              f"- {b['nouvelles']} nouvelles offres ({b['qualifiees']} qualifiées, {b['doublons']} doublons ignorés)",
              f"- {b['completees']} offres du Sheet complétées par leur page officielle",
              f"- {b['ecartees']} écartées, {b['bloquees']} bloquées", ""]
    if "lecteur" in journal:
        b = journal["lecteur"]
        l += ["## Offres JobTeaser / LinkedIn / WTTJ lues dans le navigateur",
              f"- {b['lues']} lues et prêtes, {b['ecartees']} écartées, {b['bloquees']} bloquées", ""]
    if "dossiers" in journal:
        l += [f"## Dossiers préparés ({len(journal['dossiers'])})"]
        l += [f"- {i} – {e} – {t} : **{s}**" for i, e, t, s in journal["dossiers"]] or ["- aucun"]
        l.append("")
    if "envois" in journal:
        l += [f"## Candidatures ({len(journal['envois'])})"]
        l += [f"- {i} – {e} – {t} : **{s}**" + (f" ({m})" if m else "") for i, e, t, s, m in journal["envois"]]
        l += [] if journal["envois"] else ["- aucune offre « Validée »"]
        l.append("")
    if "suivi" in journal:
        l += ["## Réponses reçues"]
        l += [f"- {n} × {s}" for s, n in journal["suivi"].items()] or ["- aucune nouvelle réponse"]
        l.append("")

    a_faire = [o for o in offres if o["statut"] == "Bloquée"]
    l += [f"## À faire à la main ({len(a_faire)} offres bloquées)"]
    from agents.postuleur import _lien_candidature
    humain = ("captcha", "information manquante", "connexion", "erreur", "403", "anti-bot", "cloudflare")
    a_faire.sort(key=lambda o: not any(m in o["motif_blocage"].lower() for m in humain))   # d'abord ce que le candidat peut débloquer
    l += [f"- {o['id']} – {o['entreprise']} – {o['intitule'][:60]} : {o['motif_blocage'] or 'motif non précisé'}"
          f" → {_lien_candidature(o)}"
          for o in a_faire[:40]]
    if len(a_faire) > 40:
        l.append(f"- … et {len(a_faire) - 40} autres (filtre « Bloquée » dans le Sheet)")
    l.append("")
    entretiens = [o for o in offres if o["statut"] == "Entretien"]
    if entretiens:
        l += ["## Entretiens et tests en cours"]
        l += [f"- {o['entreprise']} – {o['intitule'][:60]} : {o['derniere_reponse']}" for o in entretiens]
        l.append("")
    l += ["## État du pipeline"]
    l += [f"- {s} : {statuts.get(s, 0)}" for s in ("Nouvelle", "Qualifiée", "À valider", "Validée", "Envoyée",
                                                   "Bloquée", "Réponse reçue", "Entretien", "Refus", "Écartée")]
    if journal.get("erreurs"):
        l += ["", "## Erreurs du lancement"] + [f"- {e}" for e in journal["erreurs"]]
    return "\n".join(l) + "\n"


async def main() -> None:
    from outils.sheet import ouvrir_pipeline

    parser = argparse.ArgumentParser(description="Pipeline quotidien de candidatures.")
    parser.add_argument("--dry-run", action="store_true", help="tout, sauf l'envoi réel des candidatures")
    parser.add_argument("--etapes", default=",".join(ETAPES), help=f"étapes à lancer parmi {ETAPES}")
    parser.add_argument("--max-completer", type=int, default=15, help="offres du Sheet à compléter")
    parser.add_argument("--max-nouvelles", type=int, default=10, help="nouvelles offres à chercher")
    parser.add_argument("--max-cibles", type=int, default=3,
                        help="entreprises ciblées dont on moissonne le site carrière, par cycle")
    parser.add_argument("--max-alertes", type=int, default=16, help="pistes des alertes LinkedIn du Gmail")
    parser.add_argument("--max-posts", type=int, default=10, help="offres tirées des posts LinkedIn")
    parser.add_argument("--max-dossiers", type=int, default=15, help="dossiers à préparer")
    parser.add_argument("--max-envois", type=int, default=15, help="candidatures à envoyer")
    parser.add_argument("--max-lecteur", type=int, default=15, help="offres de plateformes à lire")
    parser.add_argument("--jours-suivi", type=int, default=3, help="ancienneté des mails lus par le suivi")
    parser.add_argument("--cycles", type=int, default=None,
                        help="(défaut : selon l'abonnement) répète chasseur → lecteur → dossier → postuleur (s'arrête s'il n'y a plus rien à faire)")
    args = parser.parse_args()
    args.cycles = args.cycles or plafond("CYCLES")

    sheet, journal = ouvrir_pipeline(), {"erreurs": []}
    from outils.lancement import AVANT_AGENT
    from outils.sheet import prendre_verrou, rendre_verrou

    qui = f"{socket.gethostname()}:{os.getpid()}"
    if autre := prendre_verrou(sheet, qui):   # jamais deux pipelines (Mac et cloud) sur le même Sheet
        print(f"Un autre pipeline tourne déjà ({autre}) : arrêt. Onglet « Verrou » du Sheet.")
        return
    AVANT_AGENT.append(lambda: prendre_verrou(sheet, qui))
    try:
        await _journee(sheet, journal, args)
    finally:
        rendre_verrou(sheet, qui)


async def _journee(sheet, journal: dict, args) -> None:
    etapes = [e.strip() for e in args.etapes.split(",")]
    if os.getenv("VALIDATION_AUTO") != "1":
        sheet.valider_cases_cochees()   # tes cases « Validée » cochées depuis la veille

    async def lancer(nom, fonction):
        if nom in etapes:
            print(f"\n===== {nom.upper()} =====")
            try:
                await fonction(sheet, args, journal)
            except Exception as erreur:   # une étape en échec n'empêche pas les suivantes
                journal["erreurs"].append(f"{nom} : {erreur.__class__.__name__} : {erreur}")
                traceback.print_exc()

    for cycle in range(1, args.cycles + 1):
        avant = (len(journal.get("dossiers", [])), len(journal.get("envois", [])),
                 sum(journal.get("lecteur", {}).values()), sum(journal.get("chasseur", {}).values()))   # offre écartée ou bloquée = progrès aussi
        print(f"\n########## CYCLE {cycle}/{args.cycles} ##########")
        if not args.dry_run:   # offres jamais postulées de plus de 4 jours : supprimées (copie dans logs/)
            supprimees = await asyncio.to_thread(sheet.supprimer_anciennes, AGE_MAX_JOURS)
            print(f"Nettoyage : {len(supprimees)} offre(s) de plus de {AGE_MAX_JOURS} jours supprimée(s) du Sheet.")
        for nom, fonction in (("chasseur", etape_chasseur), ("lecteur", etape_lecteur),
                              ("dossier", etape_dossier), ("postuleur", etape_postuleur)):
            await lancer(nom, fonction)
        apres = (len(journal.get("dossiers", [])), len(journal.get("envois", [])),
                 sum(journal.get("lecteur", {}).values()), sum(journal.get("chasseur", {}).values()))   # offre écartée ou bloquée = progrès aussi
        if apres == avant:
            print("Plus rien à traiter : arrêt des cycles.")
            break
    await lancer("suivi", etape_suivi)

    if "resume" in etapes:
        texte = resume(sheet, args, journal)
        fichier = RACINE / "logs" / f"resume_{date.today().isoformat()}.md"
        fichier.parent.mkdir(exist_ok=True)
        fichier.write_text(texte, encoding="utf-8")
        print("\n" + texte + f"\n(Résumé enregistré dans {fichier.relative_to(RACINE)})")

    try:   # coffre et profil chiffrés sur le Drive, pour survivre à la machine
        from outils.sauvegarde import sauvegarder
        sauvegarder()
    except Exception as erreur:
        print(f"Sauvegarde Drive impossible : {erreur}")


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    asyncio.run(main())
