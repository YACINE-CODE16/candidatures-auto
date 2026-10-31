"""Agent Postuleur : remplit le formulaire de candidature des offres « Validée » et les envoie.

Garanties apportées par le code (pas seulement par la consigne) :
  - l'outil lister_offres ne renvoie QUE les offres au statut « Validée » (règle 2) ;
  - le passage en « Envoyée » suit la table de transitions de outils/sheet.py (Validée → Envoyée) ;
  - en --dry-run, dès que l'agent annonce le formulaire prêt (outil pret_a_envoyer), un hook
    refuse tout clic, toute touche et tout envoi de formulaire dans le navigateur : rien ne peut partir ;
  - un CAPTCHA n'est jamais résolu : l'offre passe en « Bloquée » avec le motif.

Comptes : l'agent crée le compte candidat avec l'adresse du candidat et un mot de passe généré
(outil identifiants_site, stocké dans secrets/comptes.json, sauvegardé aussitôt dans le coffre
chiffré), et lit les codes de vérification dans Gmail (lecture seule).

Lancement :
    python -m agents.postuleur --dry-run --ids OF-0a90fd30    # remplit tout, n'envoie rien
    python -m agents.postuleur --ids OF-0a90fd30              # envoi réel
    python -m agents.postuleur --limite 10                     # les 10 premières offres « Validée »
"""

import argparse
import asyncio
import fcntl
import json
import os
import secrets
import string
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, create_sdk_mcp_server, tool

from outils.config import candidat, email, en_cloud, nom_complet, prenom, sources_connectees
from outils.gmail import chercher
from outils.lancement import RACINE, lancer_agent

DOSSIERS = RACINE / "dossiers"
COMPTES = RACINE / "secrets" / "comptes.json"
# Plusieurs postuleurs en parallèle (WORKER=« i/n ») : un profil de navigateur chacun (Chromium le verrouille).
_WORKER = int(os.getenv("WORKER", "0/1").split("/")[0])
PROFIL_NAVIGATEUR = RACINE / "navigateur" / ("profil" if _WORKER == 0 else f"profil_{_WORKER}")
CAPTURES = RACINE / "logs" / "navigateur"
EMAIL = email()
PRENOM = prenom()
PLAYWRIGHT_MCP = "@playwright/mcp@0.0.83"
# Comptes personnels du candidat : jamais créés ni réinitialisés par l'agent, seulement ses identifiants .env.
SITES_PROTEGES = tuple(candidat().get("comptes_proteges", ["linkedin", "jobteaser"]))
# Règle du candidat : on postule sur le site de l'entreprise. Les plateformes (JobTeaser, LinkedIn,
# Welcome to the Jungle, HelloWork, Indeed) servent seulement à trouver les offres.
PLATEFORMES = ("jobteaser.com", "linkedin.com", "welcometothejungle.com", "hellowork.com", "indeed.")
# Mode assisté (sur le PC du candidat) : navigateur visible, l'agent appelle à l'aide au lieu de bloquer.
NAVIGATEUR_VISIBLE = os.getenv("NAVIGATEUR_VISIBLE") == "1" and not en_cloud()
AIDE_HUMAINE = """
MODE ASSISTÉ : {prenom} regarde ce navigateur, sur son écran. Devant un CAPTCHA, une vérification « je ne suis
pas un robot », un code reçu par SMS ou une connexion que tu ne peux pas faire : n'appelle PAS marquer_bloquee.
Appelle demander_aide(message) en disant précisément quoi faire, puis attends : browser_wait_for 20 secondes
et browser_snapshot, jusqu'à 15 fois. Dès que c'est réglé, continue la candidature. Ne bloque qu'après ces
15 attentes."""
CANDIDATURE_SIMPLIFIEE = os.getenv("CANDIDATURE_SIMPLIFIEE") == "1"   # désactivée par défaut
QUOTA_LINKEDIN_JOUR = 10     # candidatures simplifiées LinkedIn par jour (limite le risque de restriction)
ENVOIS_LINKEDIN = RACINE / "logs" / "linkedin_envois.json"
BUDGET_PAR_OFFRE_USD = 3.00   # à 2 $, des formulaires longs (Accenture, Thales) s'arrêtaient avant l'envoi

# Réponses de l'onglet « Profil candidat » remplacées par les règles actuelles du pipeline.
CHAMPS_OBSOLETES = {"Envoi final", "Mots de passe", "GitHub / portfolio"}

# LinkedIn et JobTeaser connectés : désactivés par défaut (SOURCES_CONNECTEES=0), aucun mot de passe LinkedIn stocké.
RECETTE_LINKEDIN = f"""- LinkedIn (identifiants_site("linkedin.com")) : connexion avec le compte de {PRENOM}, jamais de création
  ni de « mot de passe oublié ». Code par mail : lire_mails ; vérification par téléphone : bloque.
  Candidature simplifiée : réponds aux questions avec profil_candidat, joins le CV PDF de l'offre.
""" if sources_connectees() else ""
RECETTE_JOBTEASER = f"""- JobTeaser ({candidat().get("jobteaser", "<école>.jobteaser.com")}, identifiants_site("jobteaser.com")) : bouton « Candidature
  simplifiée » → combobox Genre (réponse de profil_candidat) → message (≤ 1000 caractères, tiré de la lettre) ou lettre PDF
  → « Postuler maintenant ». Succès = texte « bien été envoyée ». Dans le HTML de l'offre,
  hasApplied:true = déjà postulé (ne pas renvoyer : marque « Envoyée » avec cette preuve). Le site limite
  à environ 1 requête par seconde : évite les actions en rafale.
""" if sources_connectees() else ""

SYSTEME = f"""Tu es le postuleur : tu envoies la candidature de {nom_complet()} ({EMAIL}) à UNE offre,
dans un vrai navigateur (outils mcp__navigateur__*). Tu agis en son nom, avec son accord.

Procédure :
1. Appelle lister_offres pour obtenir l'offre (URL de candidature, chemins du CV et de la lettre),
   et profil_candidat pour les réponses types aux questions des formulaires.
2. Ouvre l'URL de candidature. Préfère toujours le site officiel de l'entreprise ou de son outil de
   recrutement (Workable, Greenhouse, Lever, SmartRecruiters, Teamtailor, Workday, SuccessFactors…) :
   Postule sur le site de l'entreprise uniquement : jamais par JobTeaser, LinkedIn, Welcome to the
   Jungle, HelloWork ni Indeed (ces sites servent seulement à trouver les offres).
   Offre en anglais : joins les PDF en anglais (fichiers_pdf), réponds en anglais.
3. LinkedIn et JobTeaser : n'y crée jamais de compte et n'y réinitialise jamais un mot de passe.
   Si un compte est nécessaire : appelle identifiants_site(site) pour obtenir l'email et le mot de
   passe à utiliser (connexion d'abord, création seulement si aucun compte n'existe). « site » est le
   domaine de la page de connexion ; pour un portail partagé entre entreprises (Workday,
   SuccessFactors, Oracle, Taleo, SmartRecruiters…), ajoute le nom de l'entreprise :
   « successfactors.eu essilorluxottica ». Ne crée jamais un second compte sur un site où un
   compte existe déjà. Si un code ou un lien de vérification est envoyé
   par mail, attends 20 à 30 secondes puis appelle lire_mails (ex. « newer_than:15m <nom du site> ») ;
   les liens apparaissent entre crochets, ouvre-les avec browser_navigate.
   Si un compte existe déjà avec cet email et que le mot de passe est refusé : utilise « mot de passe
   oublié », ouvre le lien reçu par mail et définis comme nouveau mot de passe celui de identifiants_site.
   Si un mail te donne le mot de passe d'un compte existant, utilise-le et enregistre-le avec
   enregistrer_mot_de_passe(site, mot_de_passe) ; de même pour tout mot de passe que tu définis.
   N'écris jamais un mot de passe dans tes messages.
   Agis comme un humain : page vide, erreur ou chargement bloqué → attends 5 secondes et recharge (jusqu'à
   3 fois) avant de bloquer pour « site en erreur ». Lien qui mène à une page générique (accueil carrière,
   espace candidat) → cherche l'offre par son intitulé avec la recherche du site.
4. Remplis le formulaire avec profil_candidat et les informations de l'offre. Joins le CV (PDF) et la
   lettre (PDF) avec browser_file_upload. Si le bouton d'envoi de fichier n'ouvre aucun sélecteur
   (browser_file_upload refusé), dépose le fichier directement dans le champ caché avec
   browser_run_code_unsafe : async (page) => {{ await page.locator('input[type=file]').first()
   .setInputFiles('<chemin du PDF>') }}, puis vérifie à l'écran que le fichier apparaît.
   Si un champ texte demande une lettre ou un message de
   motivation, colle le texte de la lettre (champ lettre_txt de lister_offres, sans l'en-tête ni la signature).
   Questions ouvertes « qu'est-ce qui vous motive », « quelle expérience se rapproche le plus du poste »,
   « pourquoi nous » : réponds en reprenant des phrases de lettre_txt (déjà vérifiées), sans rien y ajouter.
   Champs « parcours » (expériences, employeur, fonction, dates, formation, école) : recopie-les depuis
   cv_txt (CV vérifié). Une information absente de cv_txt et de profil_candidat reste une information manquante.
   Coche les cases obligatoires (CGU, RGPD) ; ne coche jamais les cases marketing ou newsletter.
5. ARRÊTE-TOI et appelle marquer_bloquee(id, motif) si tu rencontres :
   - un CAPTCHA, quel qu'il soit (« je ne suis pas un robot », images à sélectionner, puzzle,
     hCaptcha, reCAPTCHA, Cloudflare) : motif « CAPTCHA ». Ne tente JAMAIS de le résoudre ;
   - une question obligatoire sans réponse dans profil_candidat ni dans l'offre (prétentions
     salariales chiffrées, vidéo…) : motif « Information manquante : <question> » ;
   - une offre expirée ou un site en erreur : motif explicite.
   N'invente jamais une réponse.
6. Quand TOUT est rempli, juste avant le bouton final d'envoi : prends une capture
   (browser_take_screenshot, filename « dossiers/<id>/avant_envoi.png »), puis appelle pret_a_envoyer(id).
   - S'il répond « ENVOIE » : clique sur le bouton final, attends la page de confirmation, prends une
     capture « dossiers/<id>/apres_envoi.png » et appelle marquer_envoyee(id, preuve) avec le texte de confirmation.
   - S'il répond « MODE TEST » : arrête-toi là, sans cliquer.
Règles complémentaires :
- Lis les pages en TEXTE (browser_snapshot, browser_find). Pas de capture d'écran pour regarder ou vérifier
  une étape : les deux seules captures sont avant_envoi.png et apres_envoi.png (étape 6).
- Mot de passe refusé sur un compte qui existe déjà (souvent créé depuis une autre machine) : fais UNE seule
  réinitialisation par mail (« mot de passe oublié », lien lu avec lire_mails), définis le mot de passe donné
  par identifiants_site, appelle enregistrer_mot_de_passe, puis connecte-toi. Si ça échoue encore : marquer_bloquee.
- Bloqué (CAPTCHA, formulaire cassé, connexion toujours impossible) : marquer_bloquee tout de suite, sans
  réessayer en boucle.
- Avant de remplir quoi que ce soit, vérifie si {PRENOM} a DÉJÀ postulé à cette offre (bouton « Déjà
  postulé », espace candidat « Mes candidatures », message « vous avez déjà postulé »). Si oui, ne renvoie
  rien : appelle marquer_envoyee(id, preuve) avec ce que tu as vu, sans pret_a_envoyer.
- Cookies : « Tout refuser » (ou le minimum obligatoire).
{RECETTE_LINKEDIN}- Ne passe en « Envoyée » qu'après avoir VU la page ou le message de confirmation.
- Une donnée personnelle absente de profil_candidat (date ou pays de naissance, situation familiale,
  prétentions salariales chiffrées, vidéo) n'est jamais inventée : bloque avec le motif.
Recettes qui fonctionnent, par portail :
{RECETTE_JOBTEASER}- LVMH, Dior, Guerlain, Moët, 24S : https://www.lvmh.com/fr/nous-rejoindre/nos-offres/<REF>/candidature,
  formulaire dans une iframe « apply.html » ; CV dans « CV joint », lettre dans « Lettre de motivation » ;
  cocher seulement la notice obligatoire ; cliquer ENVOYER (vrai clic). Succès = « VOTRE CANDIDATURE A BIEN
  ÉTÉ PRISE EN COMPTE ».
- SmartRecruiters (Veolia…) : champs dans un shadow DOM ; le CV se dépose sur la zone de dépôt.
- Thales (Phenom/Workday) : la page peut se figer après « Suivant » ; recharge une fois, sinon bloque.
- Workday (Chanel, ArianeGroup, Banque de France, Thales…) : un compte par entreprise
  (identifiants_site("myworkdayjobs.com <entreprise>")).
Termine par une ligne « RÉSULTAT: ENVOYÉE », « RÉSULTAT: PRÊTE (test) » ou « RÉSULTAT: BLOQUÉE – <motif> »."""


def _profil_candidat(sheet) -> list[list[str]]:
    lignes = sheet.feuille.spreadsheet.worksheet("Profil candidat").get_all_values()
    return [l[:2] for l in lignes[1:] if l and l[0].strip() and l[0] not in CHAMPS_OBSOLETES]


def _mot_de_passe() -> str:
    """Mot de passe fort : 16 caractères (certains portails, dont SuccessFactors, refusent au-delà de 18)
    avec majuscules, minuscules, chiffres et symboles."""
    alphabet = string.ascii_letters + string.digits + "!@#%*-_"
    while True:
        mdp = "".join(secrets.choice(alphabet) for _ in range(16))
        if (any(c.islower() for c in mdp) and any(c.isupper() for c in mdp)
                and any(c.isdigit() for c in mdp) and any(c in "!@#%*-_" for c in mdp)):
            return mdp


def variable_env(site: str) -> str:
    """Nom de la variable .env d'un site : « emploi.cea.fr » → MDP_EMPLOI_CEA_FR."""
    return "MDP_" + "".join(c if c.isalnum() else "_" for c in site.upper())


def identifiants_fournis(site: str) -> dict | None:
    """Identifiants déjà connus pour ce site dans .env (MDP_<SITE>, avec EMAIL_<SITE> facultatif), sans rien créer.
    Une variable MDP_<MOT> s'applique si <MOT> est un des mots du site (MDP_THALESGROUP → careers.thalesgroup.com) ;
    la plus précise l'emporte. Seule voie permise pour les comptes protégés (LinkedIn, JobTeaser)."""
    mots = f"_{variable_env(site)[4:]}_"
    candidates = [n for n, v in os.environ.items() if n.startswith("MDP_") and v and f"_{n[4:]}_" in mots]
    if not candidates:
        return None
    cle = max(candidates, key=len)
    return {"site": site, "email": os.getenv(cle.replace("MDP_", "EMAIL_", 1), EMAIL),
            "mot_de_passe": os.environ[cle], "source": ".env"}


def identifiants(site: str, mot_de_passe_connu: str | None = None) -> dict:
    """Identifiants du compte candidat sur un site, dans cet ordre :
      1. le mot de passe donné dans .env ;
      2. celui déjà enregistré dans secrets/comptes.json ;
      3. sinon un mot de passe créé UNE seule fois pour ce site, puis toujours réutilisé.
    `mot_de_passe_connu` remplace le mot de passe enregistré (compte existant retrouvé par mail)."""
    if not mot_de_passe_connu and (fourni := identifiants_fournis(site)):
        return fourni
    COMPTES.parent.mkdir(exist_ok=True)
    with open(COMPTES.with_suffix(".verrou"), "w") as verrou:   # postuleurs parallèles : un seul mot de passe par site
        fcntl.flock(verrou, fcntl.LOCK_EX)
        return _identifiants_enregistres(site, mot_de_passe_connu)


def _identifiants_enregistres(site: str, mot_de_passe_connu: str | None) -> dict:
    comptes = json.loads(COMPTES.read_text()) if COMPTES.exists() else {}
    if site not in comptes or mot_de_passe_connu:
        comptes[site] = {"email": EMAIL, "mot_de_passe": mot_de_passe_connu or _mot_de_passe(),
                         "cree_le": datetime.now().isoformat(timespec="seconds")}
        COMPTES.write_text(json.dumps(comptes, indent=2, ensure_ascii=False))
        COMPTES.chmod(0o600)
        _copier_dans_env(site, comptes[site]["mot_de_passe"])
        _sauvegarder_le_compte(site)
    return {"site": site, **comptes[site]}


def _sauvegarder_le_compte(site: str) -> None:
    """Un compte créé ne doit jamais être perdu : coffre envoyé aussitôt sur le Drive."""
    from outils import coffre
    try:
        coffre.sauvegarder_coffre()
    except Exception as erreur:   # le compte reste dans secrets/ et repartira avec la sauvegarde de fin
        print(f"ATTENTION : compte {site} créé, mais coffre non sauvegardé pour l'instant ({erreur.__class__.__name__}).")


def _copier_dans_env(site: str, mot_de_passe: str) -> None:
    """Recopie le mot de passe dans .env (MDP_<SITE>) pour que le candidat les retrouve tous au même endroit."""
    env, cle = RACINE / ".env", variable_env(site)
    lignes = [l for l in env.read_text(encoding="utf-8").splitlines() if not l.startswith(f"{cle}=")] if env.exists() else []
    env.write_text("\n".join(lignes + [f"{cle}={mot_de_passe}"]) + "\n", encoding="utf-8")
    os.environ[cle] = mot_de_passe


def _envois_linkedin_du_jour() -> int:
    donnees = json.loads(ENVOIS_LINKEDIN.read_text()) if ENVOIS_LINKEDIN.exists() else {}
    return donnees.get(datetime.now().date().isoformat(), 0)


def _compter_envoi_linkedin() -> None:
    donnees = json.loads(ENVOIS_LINKEDIN.read_text()) if ENVOIS_LINKEDIN.exists() else {}
    jour = datetime.now().date().isoformat()
    donnees[jour] = donnees.get(jour, 0) + 1
    ENVOIS_LINKEDIN.parent.mkdir(exist_ok=True)
    ENVOIS_LINKEDIN.write_text(json.dumps(donnees))


def _lien_candidature(offre: dict) -> str:
    """URL officielle notée par le chasseur dans offre.md (« Source : ... »), sinon le lien du Sheet."""
    texte = (DOSSIERS / offre["id"] / "offre.md").read_text(encoding="utf-8") if (DOSSIERS / offre["id"] / "offre.md").exists() else ""
    for ligne in texte.splitlines():
        if ligne.lower().startswith("source :"):
            return ligne.split(":", 1)[1].strip()
    return offre["url"]


def cle_site(url: str) -> str:
    """Site de candidature : domaine + premier segment (jobs.lever.co/entreprise), pour les CAPTCHA déjà vus."""
    morceaux = urlsplit(url)
    return f"{morceaux.netloc}/{morceaux.path.strip('/').split('/')[0]}".lower()


BLOCAGES_DE_SITE = ("captcha", "mot de passe refusé", "mot de passe fourni", "connexion workday", "identifiants refusés",
                    "connexion impossible")


def sites_captcha(offres: list[dict]) -> set[str]:
    """Sites où un CAPTCHA ou un compte refusé a déjà bloqué une candidature : inutile d'y relancer un agent
    (hors mode assisté) tant que le candidat n'a pas réglé le problème."""
    sites = set()
    for o in offres:
        motif = o["motif_blocage"].lower()
        if o["statut"] == "Bloquée" and any(m in motif for m in BLOCAGES_DE_SITE):
            lien = _lien_candidature(o)
            # un compte vaut pour tout le domaine ; un CAPTCHA, pour le site de l'entreprise sur l'outil partagé
            sites.add(urlsplit(lien).netloc.lower() if "captcha" not in motif else cle_site(lien))
    return sites


ASSISTE_EN_COURS = RACINE / "logs" / "assiste_en_cours.txt"   # offre traitée en ce moment par le mode assisté


def controle_prealable(sheet, id_offre: str, dry_run: bool) -> tuple[str | None, dict | None]:
    """Contrôles gratuits, en Python, avant tout agent. Renvoie (issue si l'offre est écartée, offre à jour)."""
    toutes = sheet._charger()
    o = next((o for o in toutes if o["id"] == id_offre and o["statut"] == "Validée"), None)
    if o is None:
        return "Ignorée : n'est plus « Validée »", None
    url = _lien_candidature(o)
    if not NAVIGATEUR_VISIBLE and ASSISTE_EN_COURS.exists() and ASSISTE_EN_COURS.read_text().strip() == id_offre:
        return "Reportée : en cours dans le mode assisté", o

    def bloquer(issue: str, motif: str):
        if not dry_run:
            sheet.changer_statut(id_offre, "Bloquée", motif_blocage=motif)
        return issue, o

    if deja := sheet.candidature_deja_envoyee({**o, "url": url}):
        return bloquer(f"Bloquée : doublon de {deja}", f"Doublon : candidature déjà envoyée pour la même offre ({deja})")
    if not CANDIDATURE_SIMPLIFIEE and any(p in url for p in PLATEFORMES):
        return bloquer("Bloquée : pas de lien vers le site de l'entreprise",
                       "Postuleur : seule une plateforme (JobTeaser/LinkedIn/WTTJ) est connue ; site de l'entreprise à trouver")
    bloques = sites_captcha(toutes)
    if not NAVIGATEUR_VISIBLE and (cle_site(url) in bloques or urlsplit(url).netloc.lower() in bloques):
        # pas de changement de statut : l'offre reste « Validée » pour le mode assisté, sans aller-retour
        return "Reportée : CAPTCHA ou compte refusé sur ce site, à faire en mode assisté", o
    if "linkedin.com" in url and _envois_linkedin_du_jour() >= QUOTA_LINKEDIN_JOUR:
        return f"Reportée (quota LinkedIn du jour atteint)", o
    return None, o


class Garde:
    """Mémorise, pour le mode test, que le formulaire est prêt : plus aucun clic n'est permis ensuite."""

    # Après pret_a_envoyer, seuls ces outils de lecture restent permis ; tout le reste est refusé
    # (liste blanche : un nouvel outil du navigateur est bloqué par défaut).
    LECTURE_SEULE = ("browser_snapshot", "browser_take_screenshot", "browser_console_messages",
                     "browser_network_requests", "browser_find", "browser_tabs", "browser_close")

    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.formulaire_pret = False
        self.issue = "Inachevée (voir le journal)"   # ce qui s'est réellement passé, pour le résumé

    async def avant_outil(self, entree, _tool_use_id, _contexte):
        nom = entree.get("tool_name", "")
        if self.dry_run and self.formulaire_pret and not nom.endswith(self.LECTURE_SEULE):
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Mode test : le formulaire est prêt, aucune action n'est permise.",
            }}
        return {}


def serveur_postuleur(sheet, offre: dict, garde: Garde):
    """Outils du postuleur, limités à UNE offre « Validée »."""
    id_offre = offre["id"]
    dossier = DOSSIERS / id_offre

    def texte(t: str) -> dict:
        return {"content": [{"type": "text", "text": t}]}

    @tool("lister_offres", "L'offre « Validée » à traiter, avec ses fichiers.", {})
    async def lister_offres(_args):
        issue, o = await asyncio.to_thread(controle_prealable, sheet, id_offre, garde.dry_run)
        if issue:   # déjà vérifié avant le lancement ; revérifié ici au cas où le Sheet aurait changé
            garde.issue = issue
            return texte(f"{issue.upper()}. Ne fais rien d'autre, termine.")
        url = _lien_candidature(o)
        fichiers = {p.name: str(p) for p in dossier.glob("*.pdf")}
        return texte(json.dumps({
            "id": id_offre, "entreprise": o["entreprise"], "intitule": o["intitule"],
            "url_candidature": url, "fichiers_pdf": fichiers,
            # le texte lui-même : le postuleur n'a pas d'outil de lecture de fichiers (seulement le navigateur)
            "lettre_txt": (dossier / "lettre.txt").read_text(encoding="utf-8") if (dossier / "lettre.txt").exists() else "",
            # CV vérifié en texte : formation, expériences et dates pour les champs « parcours » des formulaires
            "cv_txt": (dossier / "cv.txt").read_text(encoding="utf-8") if (dossier / "cv.txt").exists() else "",
        }, ensure_ascii=False, indent=2))

    @tool("profil_candidat", "Réponses types du candidat pour les formulaires.", {})
    async def profil_candidat(_args):
        return texte("\n".join(f"- {champ} : {reponse}" for champ, reponse in _profil_candidat(sheet)))

    @tool("identifiants_site", "Email et mot de passe du compte candidat sur un site (créés si besoin).",
          {"site": str})
    async def identifiants_site(args):
        site = args["site"].lower().removeprefix("www.")
        if any(p in site for p in SITES_PROTEGES):
            compte = identifiants_fournis(site)   # jamais de création pour ces sites
            if compte is None:
                return texte("Compte personnel du candidat sans identifiants fournis : ne crée rien, bloque l'offre.")
            return texte(json.dumps(compte, ensure_ascii=False))
        return texte(json.dumps(identifiants(site), ensure_ascii=False))

    @tool("enregistrer_mot_de_passe", "Enregistre le mot de passe réel d'un compte candidat existant.",
          {"site": str, "mot_de_passe": str})
    async def enregistrer_mot_de_passe(args):
        if any(p in args["site"].lower() for p in SITES_PROTEGES):
            return texte("REFUS : le mot de passe de ce compte personnel ne doit jamais être modifié.")
        identifiants(args["site"].lower().removeprefix("www."), args["mot_de_passe"])
        return texte("Mot de passe enregistré.")

    @tool("lire_mails", "Recherche dans la boîte Gmail du candidat (lecture seule), ex. « newer_than:15m workable ».",
          {"recherche": str})
    async def lire_mails(args):
        messages = await asyncio.to_thread(chercher, args["recherche"], 5)
        return texte(json.dumps(messages, ensure_ascii=False, indent=2) if messages else "Aucun message.")

    @tool("pret_a_envoyer", "À appeler quand le formulaire est entièrement rempli, juste avant l'envoi.",
          {"id": str})
    async def pret_a_envoyer(_args):
        if garde.dry_run:
            garde.formulaire_pret = True
            garde.issue = "Prête (test, non envoyée)"
            return texte("MODE TEST : n'envoie pas. Arrête-toi ici.")
        return texte("ENVOIE : clique sur le bouton final d'envoi.")

    @tool("marquer_envoyee", "Passe l'offre en « Envoyée » après la page de confirmation.",
          {"id": str, "preuve": str})
    async def marquer_envoyee(args):
        if garde.dry_run:
            return texte("Mode test : statut inchangé.")
        anciennes = next((o["notes"] for o in sheet._charger() if o["id"] == id_offre), "")
        trace = f"{datetime.now():%d/%m} : envoyée par le postuleur ({args['preuve'][:300]})"
        sheet.changer_statut(id_offre, "Envoyée", notes=f"{anciennes} ; {trace}" if anciennes else trace)
        garde.issue = "Envoyée"
        if "linkedin.com" in _lien_candidature(offre):
            _compter_envoi_linkedin()
        return texte("Statut « Envoyée » enregistré.")

    @tool("marquer_bloquee", "Passe l'offre en « Bloquée » avec le motif.", {"id": str, "motif": str})
    async def marquer_bloquee(args):
        garde.issue = f"Bloquée : {args['motif'][:150]}"
        if garde.dry_run:
            return texte(f"Mode test : statut inchangé (motif noté : {args['motif']}).")
        sheet.changer_statut(id_offre, "Bloquée", motif_blocage=f"Postuleur : {args['motif']}"[:500])
        return texte("Statut « Bloquée » enregistré.")

    @tool("demander_aide", "Mode assisté : demande au candidat d'agir dans le navigateur visible.", {"message": str})
    async def demander_aide(args):
        print(f"\a\n{'=' * 70}\n>>> À TOI, {prenom().upper()} ({offre['entreprise']}) : {args['message']}\n{'=' * 70}\n",
              flush=True)
        for commande in (["notify-send", "Candidatures", args["message"]],
                         ["osascript", "-e", f'display notification "{args["message"][:120]}" with title "Candidatures"']):
            try:
                subprocess.run(commande, timeout=5, capture_output=True)
                break
            except (FileNotFoundError, subprocess.SubprocessError):
                pass
        return texte("Demande affichée. Attends maintenant que ce soit fait (browser_wait_for puis browser_snapshot).")

    return create_sdk_mcp_server(name="pipeline", tools=[
        demander_aide, lister_offres, profil_candidat, identifiants_site, enregistrer_mot_de_passe, lire_mails,
        pret_a_envoyer, marquer_envoyee, marquer_bloquee,
    ])


def options_postuleur(sheet, offre: dict, garde: Garde) -> ClaudeAgentOptions:
    PROFIL_NAVIGATEUR.mkdir(parents=True, exist_ok=True)
    CAPTURES.mkdir(parents=True, exist_ok=True)
    navigateur = ["-y", PLAYWRIGHT_MCP, "--user-data-dir", str(PROFIL_NAVIGATEUR), "--output-dir", str(CAPTURES)]
    if not NAVIGATEUR_VISIBLE:
        navigateur.insert(2, "--headless")
    if os.getenv("CHROMIUM_PATH"):
        navigateur += ["--executable-path", os.environ["CHROMIUM_PATH"]]
    if os.geteuid() == 0:
        navigateur.append("--no-sandbox")   # conteneur lancé en root : Chromium l'exige (inutile sur un PC)
    return ClaudeAgentOptions(
        model=os.getenv("MODELE_POSTULEUR", "haiku"),   # remplissage de formulaire ; « sonnet » si haiku rate trop
        system_prompt=SYSTEME + (AIDE_HUMAINE.format(prenom=prenom()) if NAVIGATEUR_VISIBLE else ""),
        tools=[],                                    # aucun outil intégré : navigateur + outils du pipeline
        mcp_servers={
            "navigateur": {"command": "npx", "args": navigateur},
            "pipeline": serveur_postuleur(sheet, offre, garde),
        },
        allowed_tools=["mcp__navigateur", "mcp__pipeline"],
        permission_mode="dontAsk",
        hooks={"PreToolUse": [HookMatcher(matcher="mcp__navigateur__.*", hooks=[garde.avant_outil])]},
        setting_sources=[],
        cwd=str(RACINE),
        max_turns=60,                                # Haiku : ~30 tours par envoi réussi, 40 coupait des formulaires longs
        max_budget_usd=BUDGET_PAR_OFFRE_USD,
        extra_args={"strict-mcp-config": None},      # seulement les serveurs MCP ci-dessus, aucun connecteur
        max_buffer_size=20 * 1024 * 1024,           # captures et pages lourdes (défaut : 1 Mo)
    )


async def postuler(sheet, offre: dict, dry_run: bool) -> str:
    """Traite une offre « Validée ». Renvoie ce qui s'est réellement passé (Envoyée, Bloquée : ..., Prête)."""
    garde = Garde(dry_run)
    issue, _ = await asyncio.to_thread(controle_prealable, sheet, offre["id"], dry_run)
    if issue:   # aucun agent lancé : 0 token
        print(f"→ {offre['id']} : {issue} (sans agent)")
        return issue
    prompt = (f"Candidature à l'offre {offre['id']} : {offre['entreprise']} — {offre['intitule']}."
              + (" (MODE TEST : remplis tout mais n'envoie rien.)" if dry_run else ""))
    try:
        resultat = await lancer_agent(f"postuleur {offre['id']}", prompt, options_postuleur(sheet, offre, garde))
    except Exception as erreur:   # une offre en échec n'arrête pas les suivantes
        print(f"→ {offre['id']} : erreur du postuleur ({erreur.__class__.__name__} : {str(erreur)[:300]}) ; "
              f"issue : {garde.issue}")
        _bloquer_si_plafond(sheet, offre, garde, str(erreur))
        return f"Erreur technique ({erreur.__class__.__name__}) ; {garde.issue}"
    if resultat.subtype != "success":
        _bloquer_si_plafond(sheet, offre, garde, resultat.subtype)
    print(f"→ {offre['id']} : {garde.issue} ({resultat.num_turns} tours)")
    return garde.issue


def _bloquer_si_plafond(sheet, offre: dict, garde: Garde, cause: str) -> None:
    """Agent arrêté par son plafond (tours ou budget) sans avoir conclu : l'offre passe en « Bloquée »
    au lieu d'être retentée à chaque cycle. Une limite de session Claude, elle, n'est pas la faute de l'offre."""
    plafond = any(m in cause for m in ("max_turns", "maximum budget", "max_budget", "maximum number of turns"))
    if plafond and garde.issue.startswith("Inachevée") and not garde.dry_run:
        sheet.changer_statut(offre["id"], "Bloquée", motif_blocage=(
            f"Postuleur : arrêté par son plafond ({cause[:80]}) avant l'envoi : formulaire long ou cassé, "
            "à faire en mode assisté"))
        garde.issue = "Bloquée : plafond atteint avant l'envoi"


async def main() -> None:
    from outils.sheet import ouvrir_pipeline

    parser = argparse.ArgumentParser(description="Envoie les candidatures des offres « Validée ».")
    parser.add_argument("--ids", nargs="*", help="ID précis à traiter")
    parser.add_argument("--limite", type=int, default=5, help="nombre maximal d'offres")
    parser.add_argument("--dry-run", action="store_true", help="remplit les formulaires sans rien envoyer")
    args = parser.parse_args()

    sheet = ouvrir_pipeline()
    offres = sheet.lire_validees()
    if args.ids:
        offres = [o for o in offres if o["id"] in args.ids]
    for offre in offres[: args.limite]:
        await postuler(sheet, offre, args.dry_run)


if __name__ == "__main__":
    load_dotenv(RACINE / ".env")
    asyncio.run(main())
