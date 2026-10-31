"""Répare l'onglet « Tableau de bord » et la colonne « Relance prévue » (N) de « Offres ».

- Colonne N : la formule ARRAYFORMULA de N1 calcule seule les dates de relance ; des dates
  saisies à la main en dessous la bloquaient (#REF!). On efface ces saisies (la formule les recalcule).
- Tableau de bord : ses formules comptaient les anciens statuts (« Candidature envoyée »,
  « À postuler »…). Elles comptent maintenant les statuts du pipeline.

    python -m outils.reparer_tableau_de_bord
"""

from dotenv import load_dotenv

from outils.config import candidat
from outils.lancement import RACINE
from outils.sheet import ouvrir_pipeline

ENVOYEES = '{"Envoyée";"Réponse reçue";"Entretien";"Refus"}'
STATUTS = ["Qualifiée", "Validée", "Envoyée", "Réponse reçue", "Entretien", "Refus",
           "Bloquée", "Écartée", "Nouvelle", "À valider"]


def reparer() -> None:
    offres = ouvrir_pipeline().feuille
    classeur = offres.spreadsheet

    # 1. Colonne N et O : on ne garde que les formules d'en-tête.
    for colonne in ("N", "O"):
        valeurs = offres.get(f"{colonne}2:{colonne}{offres.row_count}", value_render_option="FORMULA")
        saisies = [i + 2 for i, ligne in enumerate(valeurs) if ligne and str(ligne[0]).strip()]
        if saisies:
            offres.batch_clear([f"{colonne}{n}" for n in saisies])
        print(f"Colonne {colonne} : {len(saisies)} saisie(s) manuelle(s) effacée(s).")

    # 2. Tableau de bord.
    tdb = classeur.worksheet("Tableau de bord")
    maj = [
        ("B4:H4", [["Offres suivies", "À postuler", "Candidatures envoyées", "Entretiens", "Refus",
                    "Taux de réponse", "Relances à faire"]]),
        ("B5:H5", [[
            "=COUNTA(Offres!R2:R)",
            '=COUNTIF(Offres!J2:J;"Qualifiée")+COUNTIF(Offres!J2:J;"Validée")',
            f"=SUMPRODUCT(COUNTIF(Offres!J2:J;{ENVOYEES}))",
            '=COUNTIF(Offres!J2:J;"Entretien")',
            '=COUNTIF(Offres!J2:J;"Refus")',
            '=IFERROR((COUNTIF(Offres!J2:J;"Réponse reçue")+E5+F5)/D5;0)',
            '=COUNTIFS(Offres!N2:N;"<="&TODAY();Offres!J2:J;"Envoyée")',
        ]]),
        ("B10:D19", [[s, f"=COUNTIF(Offres!$J$2:$J;B{10 + i})", f"=IFERROR(C{10 + i}/$B$5;0)"]
                     for i, s in enumerate(STATUTS)]),
        (f"G10:H{9 + min(3, len(candidat()['modeles_cv']))}", [[f'=COUNTIF(Offres!$H$2:$H;"*"&F{r}&"*")',
                      f"=SUMPRODUCT(ISNUMBER(SEARCH(F{r};Offres!$H$2:$H))"
                      f"*ISNUMBER(MATCH(Offres!$J$2:$J;{ENVOYEES};0)))"] for r in range(10, 10 + min(3, len(candidat()["modeles_cv"])))]),
        ("B22", [['=IFERROR(QUERY(Offres!A2:M;"select A, B, J, D where M = \'Haute\' and J <> \'Refus\' '
                  'and J <> \'Écartée\' label A \'Entreprise\', B \'Intitulé\', J \'Statut\', D \'Début\'";0);'
                  '"Aucune offre en priorité haute")']]),
    ]
    tdb.batch_update([{"range": plage, "values": valeurs} for plage, valeurs in maj],
                     value_input_option="USER_ENTERED")
    print("Tableau de bord : formules mises à jour.")
    print("Chiffres :", tdb.get("B4:H5"))


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    reparer()
