# Candidatures automatiques

Un assistant qui cherche des offres pour toi, prépare un CV et une lettre vérifiés (il n'invente rien), postule sur le site des
entreprises et suit les réponses, deux fois par jour, ordinateur éteint, sur ton propre abonnement Claude. Tu suis tout dans
ton Google Sheet. Rien à installer sur ton ordinateur.

## Il te faut

- un abonnement Claude Pro ou Max ;
- un compte Google, dont l'adresse a été autorisée par la personne qui t'a donné ce projet ;
- la valeur `GOOGLE_CLIENT_JSON` qu'elle t'a envoyée ;
- ton CV d'une page au format Word (`.docx`).

## Installation (15 minutes)

1. Crée un compte sur [github.com](https://github.com/signup), si tu n'en as pas.
2. En haut de cette page : **Use this template → Create a new repository**, choisis **Private**, puis **Create repository**.
3. Va sur [claude.ai/code](https://claude.ai/code) et connecte ton GitHub. Dans le sélecteur d'environnement, sous la zone de message :
   **Cloud** → survole l'environnement → icône de réglages. Règle ces trois lignes, puis enregistre :
   - **Network access** : `Full`
   - **Environment variables** : `GOOGLE_CLIENT_JSON=` suivi de la valeur reçue, sur une seule ligne
   - **Setup script** :
     ```
     apt-get update -qq && apt-get install -y -qq libreoffice-writer poppler-utils fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea fonts-ebgaramond && npx -y playwright install --with-deps chromium
     ```
4. Ouvre une session sur ton nouveau dépôt, et écris :

   > installe-moi le pipeline

Claude s'occupe du reste et te guide : autorisation Google, CV, critères, fiche à valider, test à blanc, lancement automatique.

## Ce qui reste à toi

- **Valider ta fiche de vérité** : tout ce qui est dit de toi vient d'elle.
- **Finir à la main les offres `Bloquée`** : CAPTCHA ou connexion demandée. Le lien et le motif sont dans ton Sheet.

## Bon à savoir

- Ton dépôt doit rester **privé**. Tes données n'y sont jamais écrites : elles sont chiffrées sur ton Drive.
- Les candidatures partent en ton nom. Les conditions d'utilisation de chaque site s'appliquent à toi.
- Le pipeline consomme ton abonnement Claude : l'abonnement Pro est plafonné à 10 dossiers et 10 envois par jour.
- Pour reprendre une installation interrompue, écris « reprends l'installation ». Si l'autorisation Google a expiré, c'est la même phrase.

Le détail des choix techniques est dans `DECISIONS.md`.
