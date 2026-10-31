"""Remet dans la file (« Qualifiée ») les offres bloquées pour un motif donné.

Utile quand la cause du blocage a disparu, par exemple quand le postuleur sait désormais créer
les comptes candidats : les offres « Connexion requise » repassent par dossier puis postuleur.

    python -m outils.debloquer "Connexion"          # affiche les offres concernées
    python -m outils.debloquer "Connexion" --appliquer
"""

import argparse

from dotenv import load_dotenv

from outils.lancement import RACINE
from outils.sheet import ouvrir_pipeline

if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("motif", help="texte contenu dans le motif de blocage")
    parser.add_argument("--appliquer", action="store_true", help="sans cette option, rien n'est modifié")
    args = parser.parse_args()

    sheet = ouvrir_pipeline()
    offres = [o for o in sheet.lire_par_statut("Bloquée") if args.motif.lower() in o["motif_blocage"].lower()]
    for o in offres:
        print(f"{o['id']} – {o['entreprise']} – {o['intitule'][:60]} : {o['motif_blocage']}")
        if args.appliquer:
            # Dossier déjà prêt (CV et lettre en PDF) : directement « Validée », sinon retour à « Qualifiée ».
            pret = len(list((RACINE / "dossiers" / o["id"]).glob("*.pdf"))) >= 2
            sheet.changer_statut(o["id"], "Validée" if pret else "Qualifiée", motif_blocage="")
    print(f"\n{len(offres)} offre(s) {'remises dans la file' if args.appliquer else 'concernées (rien modifié)'}.")
