"""Prépare un Google Sheet VIDE pour un nouveau candidat : onglets « Offres », « Profil candidat »
et « Tableau de bord », avec les colonnes, les listes déroulantes et les formules du pipeline.

Prérequis : le Sheet est partagé en « Éditeur » avec le compte de service, et SHEET_ID est dans .env.

    python -m outils.initialiser_sheet
"""

from dotenv import load_dotenv

from outils.config import candidat, email, nom_complet
from outils.lancement import RACINE
from outils.sheet import CHAMPS

# Colonnes de l'onglet « Offres » dans l'ordre du Sheet d'origine (les deux formules calculent la relance).
COLONNES = ["Entreprise", "Intitulé", "Lieu", "Début", "Durée", "Lien", "Source", "CV recommandé",
            "Canal (email/formulaire)", "Statut", "Date candidature", "Notes", "Priorité",
            '={"Relance prévue";ARRAYFORMULA(IF(K2:K="";"";K2:K+10))}',
            '={"Jours depuis candidature";ARRAYFORMULA(IF(K2:K="";"";TODAY()-K2:K))}',
            "Publiée le", "Candidats (visible)"] + [e for e in CHAMPS.values() if e not in (
                "Entreprise", "Intitulé", "Lieu", "Début", "Durée", "Lien", "Source", "CV recommandé",
                "Canal (email/formulaire)", "Statut", "Date candidature", "Notes", "Priorité", "Publiée le",
                "Candidats (visible)")]

A_COMPLETER = "<à compléter>"


def profil_candidat() -> list[list[str]]:
    """Réponses types aux formulaires : pré-remplies depuis profil/candidat.json, le reste à compléter."""
    c = candidat()
    return [
        ["Champ", "Réponse à utiliser dans les formulaires"],
        ["Prénom / Nom", f"{c['prenom']} / {c['nom']}"],
        ["Email", email()],
        ["Téléphone", A_COMPLETER],
        ["Adresse", A_COMPLETER],
        ["LinkedIn", A_COMPLETER],
        ["École / diplôme préparé", A_COMPLETER],
        ["Convention de stage", A_COMPLETER],
        ["Type de stage", c["type_recherche"]],
        ["Date de début", A_COMPLETER],
        ["Durée", c["disponibilite"]],
        ["Gratification souhaitée", "Selon la grille de l'entreprise"],
        ["Prétentions salariales", "Selon la grille de l'entreprise. Si un montant chiffré est obligatoire : " + A_COMPLETER],
        ["Autorisation de travail en France", A_COMPLETER],
        ["Langues", A_COMPLETER],
        ["Date de naissance", A_COMPLETER],
        ["Pays de naissance", A_COMPLETER],
        ["Situation familiale", A_COMPLETER],
        ["Genre", A_COMPLETER],
        ["Civilité", A_COMPLETER],
        ["Handicap / diversité", "Je préfère ne pas répondre"],
        ["Mobilité géographique (autre région)", A_COMPLETER],
        ["Cases obligatoires CGU / RGPD / notice d'information", "Toujours cocher (cases marketing/newsletter : ne pas cocher)"],
        ["Consentement outil d'IA de tri des candidatures", "J'accepte"],
        ["Comment avez-vous connu l'offre ?", "Site carrière / site de recrutement de l'entreprise (option la plus proche)"],
        ["Disponibilité", A_COMPLETER],
        ["Lettre / message de motivation", "Toujours en joindre une quand un champ existe ; ne rien forcer sinon"],
        ["Vidéo de présentation", "Non disponible : bloquer l'offre"],
    ]


def initialiser(classeur=None) -> None:
    """`classeur` : un gspread.Spreadsheet (tests) ; par défaut, le Sheet de SHEET_ID via le compte de service."""
    import os

    import gspread

    if classeur is None:
        client = gspread.service_account(filename=os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
        classeur = client.open_by_key(os.environ["SHEET_ID"])
    onglets = {f.title: f for f in classeur.worksheets()}

    # 1. Offres : le premier onglet (vide) est renommé, les en-têtes posés.
    if "Offres" not in onglets:
        offres = classeur.sheet1
        if any(c.strip() for ligne in offres.get_all_values() for c in ligne):
            raise SystemExit("Le premier onglet n'est pas vide : utiliser un Sheet neuf.")
        offres.update_title("Offres")
        offres.resize(rows=1000, cols=len(COLONNES))
        offres.update([COLONNES], "A1", value_input_option="USER_ENTERED")
        offres.freeze(rows=1)
        print("Onglet « Offres » créé.")
    # 2. Profil candidat.
    if "Profil candidat" not in onglets:
        profil = classeur.add_worksheet("Profil candidat", rows=60, cols=2)
        profil.update(profil_candidat(), "A1", value_input_option="RAW")
        print("Onglet « Profil candidat » créé : compléter les « <à compléter> ».")
    # 3. Tableau de bord (les formules sont posées par reparer_tableau_de_bord).
    if "Tableau de bord" not in onglets:
        tdb = classeur.add_worksheet("Tableau de bord", rows=40, cols=10, index=0)
        categories = list(candidat()["modeles_cv"])[:3]
        tdb.update([[f"Suivi des candidatures — {nom_complet()}"]], "B1")
        tdb.update([["Pipeline par statut"], ["Statut"]], "B8")
        tdb.update([["Par CV recommandé", "", ""], ["CV", "Offres", "Envoyées"]], "F8")
        tdb.update([[c] for c in categories], "F10")
        tdb.update([["Priorité haute à traiter"]], "B21")
        print("Onglet « Tableau de bord » créé.")

    # Listes déroulantes (Statut, case Validée) et formules du tableau de bord.
    from outils.migration_sheet import migrer
    from outils.reparer_tableau_de_bord import reparer
    migrer(appliquer=True)
    reparer()


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    initialiser()
