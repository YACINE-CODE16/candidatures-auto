"""Migration unique de l'onglet « Offres » vers le schéma du pipeline.

Ce que fait le script :
  1. ajoute à droite les colonnes manquantes (aucune colonne existante n'est déplacée,
     les formules du « Tableau de bord » continuent de pointer au bon endroit) ;
  2. donne un ID à chaque offre existante ;
  3. traduit les anciens statuts vers les nouveaux (table ANCIENS_STATUTS) et garde
     une trace de l'ancien statut dans « Notes » ;
  4. pose les listes déroulantes : « Statut » (nouveaux statuts) et « Validée » (case à cocher).

Lancement :
    python -m outils.migration_sheet --dry-run   # affiche ce qui changerait, n'écrit rien
    python -m outils.migration_sheet             # applique
"""

import argparse
from collections import Counter

from dotenv import load_dotenv

from outils.sheet import CHAMPS, STATUTS, _a1, calculer_id, ouvrir_pipeline

# Ancien statut → (nouveau statut, motif de blocage éventuel)
ANCIENS_STATUTS = {
    "À postuler": ("Qualifiée", None),
    "Prêt": ("Bloquée", "Ancien statut « Prêt » : vérifier à la main si l'envoi a déjà eu lieu"),
    "Envoyé": ("Envoyée", None),
    "Connexion requise": ("Bloquée", "Connexion ou compte candidat requis"),
    "Info manquante": ("Bloquée", "Information manquante (voir Notes)"),
    "Expirée": ("Écartée", None),
    "Offre expirée": ("Écartée", None),
    "Non pertinent": ("Écartée", None),
}

TAILLE_LOT = 500  # nombre de cellules par requête d'écriture


def migrer(appliquer: bool) -> None:
    sheet = ouvrir_pipeline()
    feuille = sheet.feuille
    valeurs = feuille.get_all_values()
    entetes = valeurs[0]

    # 1. Colonnes manquantes, ajoutées à droite.
    manquantes = [e for e in CHAMPS.values() if e not in entetes]
    print(f"Colonnes à ajouter : {manquantes or 'aucune'}")
    if appliquer and manquantes:
        if len(entetes) + len(manquantes) > feuille.col_count:
            feuille.add_cols(len(entetes) + len(manquantes) - feuille.col_count)
        feuille.batch_update(
            [{"range": _a1(1, len(entetes) + i + 1), "values": [[e]]} for i, e in enumerate(manquantes)],
            value_input_option="RAW",
        )
    entetes = entetes + manquantes
    col = {nom: entetes.index(e) + 1 for nom, e in CHAMPS.items()}

    # 2 et 3. ID + traduction des statuts, ligne par ligne.
    maj, bilan, inconnus, ids_vus = [], Counter(), Counter(), set()
    for numero, ligne in enumerate(valeurs[1:], start=2):
        ligne = ligne + [""] * (len(entetes) - len(ligne))
        val = {nom: ligne[c - 1] for nom, c in col.items()}
        if not val["entreprise"].strip():
            continue

        def ecrire(nom, valeur):
            maj.append({"range": _a1(numero, col[nom]), "values": [[valeur]]})

        # ID : deux anciennes lignes avec la même URL (doublons connus) reçoivent un suffixe.
        id_offre = val["id"] or calculer_id(val["url"], val["entreprise"], val["intitule"])
        base, n = id_offre, 2
        while id_offre in ids_vus:
            id_offre, n = f"{base}-{n}", n + 1
        ids_vus.add(id_offre)
        if id_offre != val["id"]:
            ecrire("id", id_offre)

        ancien = val["statut"].strip()
        if ancien in STATUTS:
            bilan[f"{ancien} (inchangé)"] += 1
        elif ancien in ANCIENS_STATUTS:
            nouveau, motif = ANCIENS_STATUTS[ancien]
            bilan[f"{ancien} → {nouveau}"] += 1
            ecrire("statut", nouveau)
            if motif:
                ecrire("motif_blocage", motif)
            trace = f"migration : ancien statut « {ancien} »"
            ecrire("notes", f"{val['notes']} ; {trace}" if val["notes"] else trace)
        else:
            inconnus[ancien or "(vide)"] += 1
        if not val["validee"]:
            ecrire("validee", "FALSE")

    print("\nTraduction des statuts :")
    for ligne_bilan, nb in sorted(bilan.items()):
        print(f"  {nb:4d}  {ligne_bilan}")
    if inconnus:
        print(f"\nStatuts non reconnus, laissés tels quels : {dict(inconnus)}")
    print(f"\n{len(maj)} cellules à écrire.")

    if not appliquer:
        print("Mode --dry-run : rien n'a été écrit.")
        return

    for i in range(0, len(maj), TAILLE_LOT):
        feuille.batch_update(maj[i:i + TAILLE_LOT], value_input_option="USER_ENTERED")

    # 4. Listes déroulantes, de la ligne 2 jusqu'en bas de l'onglet.
    def plage(nom):
        return {"sheetId": feuille.id, "startRowIndex": 1, "endRowIndex": feuille.row_count,
                "startColumnIndex": col[nom] - 1, "endColumnIndex": col[nom]}

    feuille.spreadsheet.batch_update({"requests": [
        {"setDataValidation": {"range": plage("statut"), "rule": {
            "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": s} for s in STATUTS]},
            "showCustomUi": True, "strict": False}}},
        {"setDataValidation": {"range": plage("validee"), "rule": {
            "condition": {"type": "BOOLEAN"}}}},
    ]})
    print("Migration appliquée.")


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="affiche les changements sans rien écrire")
    migrer(appliquer=not parser.parse_args().dry_run)
