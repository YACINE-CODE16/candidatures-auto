"""Sauvegarde et restauration de tout ce qui n'est pas sur GitHub, pour survivre à l'effacement de la machine cloud.

- Sur GitHub : le code.
- Sur le Drive de l'utilisateur, chiffrés avec COFFRE_CLE : le coffre (outils/coffre.py) et l'archive
  (profil/, projets/, dossiers/, journaux utiles). Le profil contient verite.md et l'adresse : jamais en clair.

    python -m outils.sauvegarde sauvegarder
    python -m outils.sauvegarde restaurer
"""

import io
import sys
import tarfile
from collections import Counter

from outils.coffre import (CoffreErreur, chiffrer, deballer, dechiffrer, envoyer, lire_cle, message_erreur, recuperer,
                           restaurer_coffre, sauvegarder_coffre, session_drive)
from outils.lancement import RACINE

NOM_ARCHIVE = "archive.bin"
JOURNAUX_UTILES = ("couts.csv", "offres_supprimees.csv", "cibles_faites.json", "chasseur_du_jour.json", "compteurs_du_jour.json",
                   "suivi_mails_traites.json", "alertes_traitees.json", "linkedin_envois.json")
# dossiers/ est indispensable : une offre « Validée » attend ses PDF d'une session à l'autre.
A_SAUVEGARDER = ["profil", "projets", "dossiers", *(f"logs/{j}" for j in JOURNAUX_UTILES)]
LIBELLES = {"secrets": "accès Google et comptes", ".env": "mots de passe et réglages", "profil": "profil", "projets": "projets",
            "dossiers": "dossiers", "logs": "journaux"}


def emballer_archive() -> bytes:
    # shortcut: dossiers/ grandit sans purge (~200 Ko par offre) ; purger les offres closes s'il devient trop lourd.
    def sans_capture(info: tarfile.TarInfo):
        return None if info.name.endswith(".png") or info.name.endswith(".gitkeep") else info

    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w:gz") as archive:
        for chemin in A_SAUVEGARDER:
            if (RACINE / chemin).exists():
                archive.add(RACINE / chemin, arcname=chemin, filter=sans_capture)
    return tampon.getvalue()


def sauvegarder_archive() -> int:
    contenu = chiffrer(emballer_archive(), lire_cle()[0])
    envoyer(session_drive(), NOM_ARCHIVE, contenu)
    return len(contenu)


def sauvegarder() -> None:
    sauvegarder_coffre()
    taille = sauvegarder_archive()
    print(f"Coffre et archive chiffrés envoyés sur le Drive (archive : {taille // 1024} Ko).")


def bilan(noms: list[str]) -> str:
    """Ce qui a réellement été restauré, d'après le contenu des archives."""
    comptes = Counter(n.split("/")[0] for n in noms)
    return ", ".join(f"{LIBELLES.get(p, p)} ({n} fichier{'s' if n > 1 else ''})" for p, n in comptes.items()) or "rien"


def restaurer() -> list[str]:
    """Coffre d'abord (il rend le jeton Google utilisable), puis archive."""
    noms = restaurer_coffre()
    contenu = recuperer(session_drive(), NOM_ARCHIVE)
    if contenu is None:
        raise CoffreErreur(f"Coffre restauré, mais aucune {NOM_ARCHIVE} sur le Drive : profil et CV manquants.")
    noms += deballer(dechiffrer(contenu, lire_cle()[0]))
    print(f"Restauré : {bilan(noms)}.")
    return noms


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if action == "sauvegarder":
            sauvegarder()
        elif action == "restaurer":
            restaurer()
        else:
            sys.exit(__doc__)
    except Exception as erreur:   # jeton expiré, Drive injoignable, mauvaise clé : un message, pas une trace
        sys.exit(message_erreur(erreur))
