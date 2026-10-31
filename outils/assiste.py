"""Mode assisté, à lancer sur le PC du candidat : le navigateur s'affiche à l'écran et, devant un CAPTCHA,
un code SMS ou une connexion, l'agent demande au candidat d'agir au lieu de bloquer l'offre.

    python -m outils.assiste            # reprend les offres bloquées par un CAPTCHA, un anti-robot, une panne
                                        # ou une connexion, puis postule avec le navigateur visible
    python -m outils.assiste --ids OF-1234abcd OF-5678ef90
"""

import argparse
import asyncio
import os
import socket
from pathlib import Path

from dotenv import load_dotenv

from outils.lancement import RACINE

MOTIFS = ("captcha", "anti-bot", "cloudflare", "403", "connexion", "site en erreur", "information manquante")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", nargs="*", help="offres précises à reprendre")
    parser.add_argument("--parallele", action="store_true",
                        help="pendant que main.py tourne : navigateur à part, une offre à la fois, sans verrou")
    args = parser.parse_args()
    os.environ["NAVIGATEUR_VISIBLE"] = "1"   # avant tout import de agents.postuleur
    if args.parallele:
        os.environ["WORKER"] = "1/2"         # profil de navigateur séparé de celui de main.py
    from outils.sheet import ouvrir_pipeline

    sheet = ouvrir_pipeline()
    from outils.sheet import prendre_verrou, rendre_verrou

    qui = f"{socket.gethostname()}:{os.getpid()}"
    if args.parallele:
        return await _assister_une_par_une(sheet, args)
    if autre := prendre_verrou(sheet, qui):   # pas pendant un autre pipeline (Mac ou cloud)
        print(f"Un autre pipeline tourne déjà ({autre}) : réessaie quand il aura fini.")
        return
    try:
        await _assister(sheet, args)
    finally:
        rendre_verrou(sheet, qui)


async def _assister(sheet, args) -> None:
    from agents.postuleur import postuler

    for o in sheet.lire_par_statut("Bloquée"):
        pret = list((RACINE / "dossiers" / o["id"]).glob("*.pdf"))
        voulu = o["id"] in args.ids if args.ids else any(m in o["motif_blocage"].lower() for m in MOTIFS)
        if voulu and pret:
            sheet.changer_statut(o["id"], "Validée", motif_blocage="")
    offres = [o for o in sheet.lire_validees() if not args.ids or o["id"] in args.ids]
    print(f"{len(offres)} candidature(s) à faire avec toi. Garde la fenêtre du navigateur visible.")
    for offre in offres:
        print(f"→ {offre['entreprise']} – {offre['intitule'][:60]} : {await postuler(sheet, offre, False)}")


async def _assister_une_par_une(sheet, args) -> None:
    """À côté de main.py : chaque offre bloquée n'est remise en « Validée » qu'au moment de la traiter, et
    signalée dans logs/assiste_en_cours.txt pour que le pipeline principal la laisse au mode assisté."""
    from agents.postuleur import ASSISTE_EN_COURS, postuler, sites_captcha, _lien_candidature, cle_site

    toutes = sheet._charger()
    sites = sites_captcha(toutes)
    choisies = [o for o in toutes if (o["id"] in args.ids if args.ids else (
        (o["statut"] == "Bloquée" and any(m in o["motif_blocage"].lower() for m in MOTIFS)
         and not o["motif_blocage"].startswith("Abandonnée"))
        or (o["statut"] == "Validée" and cle_site(_lien_candidature(o)) in sites)))
        and len(list((RACINE / "dossiers" / o["id"]).glob("*.pdf"))) >= 2]
    print(f"{len(choisies)} candidature(s) à faire avec toi, une par une. Garde la fenêtre du navigateur visible.")
    for o in choisies:
        ASSISTE_EN_COURS.write_text(o["id"])
        try:
            if o["statut"] == "Bloquée":
                sheet.changer_statut(o["id"], "Validée", motif_blocage="")
            print(f"→ {o['entreprise']} – {o['intitule'][:60]} : {await postuler(sheet, o, False)}", flush=True)
        finally:
            ASSISTE_EN_COURS.unlink(missing_ok=True)


if __name__ == "__main__":
    load_dotenv(Path(RACINE) / ".env")
    asyncio.run(main())

