---
name: installer-pipeline
description: Installe le pipeline de candidatures pour une personne qui n'a jamais ouvert un terminal, ou reprend une installation commencée. À utiliser quand l'utilisateur écrit « installe-moi le pipeline », « reprends l'installation », ou quand une autorisation Google a expiré.
---

# Installer le pipeline

Tu es le Claude de l'utilisateur. Tu exécutes tout ; lui clique, dépose un fichier et répond. Une étape à la fois.
Objectif : 15 minutes. Ne montre pas les commandes ni leurs sorties techniques : dis ce qui se passe, en une phrase.

- Questions fermées : outil de questions à choix s'il est disponible, sinon une liste numérotée. Questions ouvertes : une à la fois.
- Dans ce guide, `python` veut dire `.venv/bin/python`.
- Si une commande échoue, explique simplement le problème et ce que l'utilisateur peut faire. N'invente jamais de contournement.
- Une valeur secrète (COFFRE_CLE) ne s'affiche qu'à l'étape 9, une seule fois, pour être copiée dans les réglages.

## Reprise, à faire en premier

```
bash lancer_cloud.sh --dependances
python -m outils.etat_installation
```

Le résultat indique ce qui est fait et `prochaine_etape`. Si tout n'est pas à `false`, dis en une phrase ce qui est déjà prêt, et
reprends à cette étape : `reseau` → prévenir (voir Accueil) ; `google` → étape 2 ; `cv` → 3 ; `criteres` → 4 ; `verite` → 5 ;
`sheet` → 6 ; `test_a_blanc` → 7 ; `coffre` → 8 ; rien (`null`) → 9. Une étape finie n'est jamais refaite.

Une machine cloud peut être effacée : avant l'étape 8, si les fichiers ont disparu, il faut reprendre au début. Après l'étape 8,
`python -m outils.coffre restaurer` récupère tout, sans rien redemander.

## 1. Accueil

Dis, en 3 phrases : je vais brancher ton Google, ton CV et tes critères, puis le pipeline cherchera des offres et postulera pour
toi deux fois par jour, ordinateur éteint, avec un suivi dans ton Google Sheet. Il n'invente rien : chaque phrase est vérifiée
contre une fiche que tu valideras. Deux choses restent à toi : valider cette fiche, et finir à la main les candidatures bloquées
par un CAPTCHA ou une connexion.

Si `reseau` est `false` : l'environnement n'a pas accès à Internet. Fais faire : sur claude.ai/code, sélecteur d'environnement sous
la zone de message → Cloud → survoler l'environnement → icône de réglages → **Network access : Full**, puis ouvrir une nouvelle session.
Si `GOOGLE_CLIENT_JSON` est absente de l'environnement (`test -n "$GOOGLE_CLIENT_JSON"`) : même écran, ligne **Environment variables**,
coller `GOOGLE_CLIENT_JSON=<la valeur reçue>` sur une seule ligne, puis nouvelle session.

## 2. Google

1. `python -m outils.google_auth lien`
2. Donne le lien : « Ouvre ce lien, choisis ton compte. Si Google affiche un avertissement, clique sur **Paramètres avancés**, puis sur
   **Accéder à…** : c'est le pipeline de ton ami. **Coche toutes les cases**, puis valide. La page d'arrivée affiche une erreur :
   c'est normal. Copie l'adresse complète de cette page, celle qui commence par `http://localhost`, et colle-la ici. »
3. `python -m outils.google_auth code "<adresse collée>"`

« access_denied » ou « accès bloqué » : son adresse n'est pas encore autorisée, il doit prévenir la personne qui lui a donné ce projet.

## 3. CV

Demande son CV au format Word (`.docx`). Il le dépose ici s'il le peut ; sinon : « Envoie-toi ton CV par mail, à toi-même, en pièce
jointe, depuis ton Gmail, puis dis-moi quand c'est fait. »

- Fichier reçu : `python -m outils.preparer_cv "<chemin>"`
- Par mail : `python -m outils.preparer_cv --depuis-gmail`

Seulement un PDF : le script le dit ; demande le `.docx`. Plus d'une page : demande de le raccourcir.
Puis, sans rien demander : traduis fidèlement en anglais chaque morceau de `profil/traductions_a_faire.json`, sans rien ajouter ni
retirer, et écris le résultat dans `profil/traductions_cv.json` (texte français exact → anglais). Laisse de côté les noms propres et les
adresses. Tu lanceras la traduction à la fin de l'étape 4.

## 4. Critères

Pose ces questions. Reprends du CV ce que tu peux, et fais-le simplement confirmer.

1. Contrat recherché (choix) : stage / alternance / CDI.
2. Métiers visés (2 à 4).
3. Secteurs préférés (ou « peu importe »).
4. Zone géographique.
5. Date de début, et durée si stage ou alternance.
6. Entreprises que tu vises en particulier (texte libre, ou aucune).
7. Pour les formulaires : adresse, téléphone, handicap à signaler ou « je préfère ne pas répondre » (choix), permis de conduire (choix),
   niveau d'anglais (choix : A2, B1, B2, C1, C2, natif).
8. Ton abonnement Claude (choix) : Pro / Max.
9. Projets « En cours » (choix, **Non** en premier et recommandé) : « Le pipeline peut ajouter à ton CV un projet pensé pour l'offre, affiché
   "En cours", que tu devras vraiment réaliser avant un entretien. Tu l'autorises ? »
10. Envoi (choix) : « Dès qu'un dossier est vérifié, je l'envoie » / « J'attends que tu coches la case "Validée" dans le Sheet ».

Écris alors, à partir de `profil.exemple/` :

- `profil/candidat.json` : `prenom`, `nom`, `email` (son Gmail), `presentation`, `phrase_ouverture` et `reponses_formulaires` tirés de ses réponses
  et du CV, sans rien inventer ; `contrat`, `type_recherche`, `zone` (`libelle`, et `geoId` LinkedIn seulement si tu le connais avec certitude, sinon
  vide), `date_debut`, `annee`, `disponibilite`, `disponibilite_en`, `metiers`, `secteurs`, `langues`, `abonnement` (`pro` ou `max`),
  `entreprises_cibles`, `nom_sheet` (« Candidatures <année> - <Prénom> »), `dossier_drive` (« Candidatures <année> »), `modeles_cv`
  (`{"Principal": "principal"}`), `comptes_proteges` (`["linkedin", "jobteaser"]`), `jobteaser` vide.
- `profil/coordonnees.json` : adresse pour la lettre.
- `.env` à partir de `.env.example` : `PROJETS_EN_COURS=1` seulement s'il a dit oui ; `VALIDATION_AUTO=1` s'il a choisi l'envoi direct, sinon `0`.
  Laisse `SOURCES_CONNECTEES=0`, et ne demande aucun mot de passe.

Puis `python -m outils.traduire_modeles`. S'il dépasse une page en anglais, raccourcis les traductions les plus longues.
Si `PROJETS_EN_COURS=1`, ajoute la règle « Projets créés pour une offre » à la section Projets de `verite.md` (voir `profil.exemple/verite.exemple.md`).

## 5. Fiche de vérité

Mets `profil/verite.md` au propre à partir du brouillon, en suivant `profil.exemple/verite.exemple.md`. Chaque fait une seule fois,
chiffres exacts, rien d'ajouté ; ce qui est douteux est marqué `[À CONFIRMER]`. Tout fait de `phrase_ouverture` et de `presentation` doit y figurer aussi, sinon le vérificateur rejette la lettre. Supprime la section « Chiffres relevés » une fois ses lignes vérifiées.

Affiche la fiche en entier. « Tout ce que le pipeline dira de toi vient de cette fiche, et seulement d'elle. Corrige ce qui est faux, ajoute ce qui
manque : un chiffre, une mission, une compétence. Rien ne sera envoyé sans ton accord sur cette fiche. » Applique ses corrections, ligne à ligne,
sans rien ajouter de toi-même.

Quand il dit explicitement qu'elle est juste, remplace le bloc de statut par : `> **Validé par <Prénom> le JJ/MM/AAAA**` (date du jour). Jamais avant.

## 6. Sheet

`python -m outils.initialiser_sheet`. Donne le lien affiché par la commande : « Voici ton Google Sheet de suivi, garde-le en favori. »
S'il reste des `<à compléter>` utiles dans l'onglet « Profil candidat », propose de les remplir avec lui ; un champ vide bloque simplement l'offre qui le demande.

## 7. Test à blanc

Lance en arrière-plan, et préviens que cela prend environ 10 minutes :

```
python main.py --etapes chasseur,lecteur,dossier,postuleur --max-envois 1 --dry-run --cycles 1 --max-dossiers 1 --max-completer 0 --max-nouvelles 3 --max-cibles 0 --max-alertes 3 --max-posts 0 --max-lecteur 0
```

Aucune candidature ne part. Montre ensuite, depuis le Sheet : l'offre, et les liens Drive du CV et de la lettre (colonnes « Lien CV » et « Lien lettre »).
Demande-lui de les relire. Si rien n'est trouvé, relance une fois avec `--max-nouvelles 8`.
Préviens-le : « Ce dossier est prêt : il partira au prochain lancement. Si tu n'en veux pas, mets son statut sur "Écartée" dans le Sheet. »

Si une phrase est fausse, corrige `verite.md` avec lui avant de continuer.

## 8. Coffre

```
python -m outils.coffre cle > /dev/null
python -m outils.sauvegarde sauvegarder
```

« Tes accès et ta fiche sont maintenant chiffrés sur ton Drive. »

## 9. Lancement automatique

Tu ne peux pas créer ces réglages toi-même : guide-le, puis attends qu'il confirme. Affiche, prêt à copier, dans un bloc de code :
la valeur renvoyée par `python -m outils.coffre cle`, sous la forme `COFFRE_CLE=<valeur>` (sur une seule ligne), et `GOOGLE_CLIENT_JSON=` si elle n'est pas déjà réglée.

1. **Réglages** : claude.ai/code → sélecteur d'environnement sous la zone de message → Cloud → survoler l'environnement → icône de réglages. Ligne
   **Environment variables** : coller `COFFRE_CLE=…` et garder `GOOGLE_CLIENT_JSON=…`. **Network access : Full**. Ligne **Setup script** : coller la commande du README.
2. **Tâches** : claude.ai/code/routines → **New task** → coller le texte de `TACHE_PLANIFIEE.md` (affiche-le) → Advanced settings → General → Cloud environment :
   choisir cet environnement. Chaque jour à **8 h 40**.
3. **Seconde tâche** : la même, à **17 h 50**.

Rappelle : « Garde cette clé pour toi : elle ouvre tes accès. Sans elle, le pipeline ne peut plus rien récupérer. »

## 10. Fin

En 4 lignes :
- Dans ton Sheet : chaque offre, son statut, son CV et sa lettre, et les réponses reçues (onglets « Offres » et « Tests & entretiens »).
- Offre `Bloquée` : ouvre le lien de la colonne « Motif de blocage » et finis à la main. Offre `À valider` : relis le dossier, puis coche « Validée ».
- Le pipeline tourne seul à 8 h 40 et 17 h 50, ordinateur éteint. Le résumé de chaque lancement se trouve dans l'historique de la tâche.
- Pour lancer à la main : ouvre une session et écris « lance le pipeline » ; j'exécuterai `bash lancer_cloud.sh`.

## Si l'autorisation Google a expiré

Le lancement cite « Autorisation Google expirée », ou `google` est à `false` alors que la clé `COFFRE_CLE` est réglée. Rien n'est perdu :

1. Refais l'étape 2.
2. `python -m outils.coffre restaurer` : récupère tout, en gardant la nouvelle autorisation.
3. `python -m outils.coffre cle > /dev/null`, puis `python -m outils.sauvegarde sauvegarder`.
4. Affiche la valeur renvoyée par `python -m outils.coffre cle`, et fais-la remplacer dans **Environment variables** (étape 9, point 1).
