# Décisions prises pendant la construction

Choix non couverts par le cahier des charges, ou qui s'en écartent parce que la réalité technique l'impose.

## Écarts au cahier des charges

- **COFFRE_CLE contient la clé Fernet et le jeton de rafraîchissement Google.** Le coffre est sur le Drive et le jeton dans le coffre :
  sur une machine neuve, rien ne permettrait de le télécharger. Il reste bien une seule variable secrète.
- **Deux tâches planifiées au lieu d'une.** Les tâches planifiées ne proposent pas deux horaires différents dans une même tâche.
- **Lancement par tranches.** Dans le cloud, une commande en arrière-plan ne dure qu'environ 30 min. Chaque tranche s'arrête de lancer des agents
  au bout de 18 min (chaque agent est limité à 10 min), sauvegarde et affiche « A SUIVRE » ; la tâche relance jusqu'à 4 fois.
- **`GOOGLE_CLIENT_JSON` et le réseau Full se règlent avant l'installation**, et non à l'étape 9 : l'étape Google et tout le reste en ont besoin.
- **Dépôt du CV : par fichier, ou par mail à soi-même.** L'envoi de fichier dans une session web n'est pas documenté. `--depuis-gmail` utilise le droit de lecture Gmail déjà accordé.
- **Test à blanc plus limité que la commande demandée** (`--max-dossiers 1`, moins de recherche) : sinon jusqu'à 10 dossiers sur un abonnement Pro, et bien plus de 15 min.
- **L'archive contient aussi `dossiers/`, sans les captures.** Une offre « Validée » attend ses PDF d'une session à l'autre.
- **Le coffre garde `.env` entier** (hors COFFRE_CLE et GOOGLE_CLIENT_JSON), pas seulement MDP_* et EMAIL_*. Dans le cloud, c'est le seul endroit où survivent les réglages (`VALIDATION_AUTO`, `PROJETS_EN_COURS`).
- **Sauvegarde du coffre à chaque compte créé**, pas seulement dans `enregistrer_mot_de_passe` : un compte créé passe par `identifiants_site`.
- **Historique du dépôt : un premier commit propre, puis un commit par étape**, tous vérifiés. Pas d'écrasement final en un seul commit.
- **Base de code : la copie locale de production, plus le dernier commit de GitHub.** Le verrou, `pas_un_stage` et la section 5 de la cartographie n'existent que dans des modifications non commitées de cette copie : le clone seul ne les avait pas. Seuls des fichiers de code ont été copiés.
- **Pas de `.claude/settings.json`.** Un fichier qui pré-autorise des commandes Bash n'a pas été ajouté (refus de l'outil de permissions).

## Choix simples

- **Identité git neutre** dans ce dépôt : l'identité globale contenait des données personnelles.
- **Pas de liste d'entreprises par défaut.** Le candidat donne les siennes ; liste vide, le mode « cibler » ne fait rien.
- **`intern` compte comme un stage seulement en mot entier** : « International » n'en est pas un.
- **LinkedIn n'a pas de filtre alternance** : pas de `f_JT`, le mot-clé trie. Les codes JobTeaser hors stage ne sont pas vérifiés (source désactivée par défaut).
- **Plafonds Pro comptés par jour**, dans `logs/compteurs_du_jour.json`, sauvegardé : deux lancements par jour ne les doublent pas. Le coordinateur du dossier prend le modèle du rédacteur.
- **Abonnement absent de `candidat.json` : réglages Pro**, les plus prudents.
- **Un envoi automatique reste un choix** posé à l'installation (`VALIDATION_AUTO`).
- **Le dernier à écrire garde le verrou** : prendre le verrou n'est pas atomique ; après 3 secondes, le pipeline relit et s'efface si un autre a écrit après lui.
- **Une candidature sans ses deux PDF est bloquée**, jamais envoyée.
- **La lettre reprend la police du CV**, au lieu d'une police fixe.
- **Export CSV des mots de passe supprimé** : fichier en clair, inutile dans le cloud.

## Limites connues

- **En mode Test, Google coupe l'autorisation au bout de 7 jours.** Le lancement échoue alors avec un message clair ; l'utilisateur doit reprendre l'autorisation et remplacer COFFRE_CLE. Solution : passer le client OAuth « En production » sans validation (avertissement à l'écran, 100 utilisateurs au plus).
- **Avant l'étape 8, une machine cloud effacée oblige à recommencer** : le coffre n'existe pas encore.
- **Windows non testé** : `fcntl` et `os.geteuid` n'y existent pas (cible secondaire).
- **Mot de passe d'un compte créé par l'agent** : il reste dans le coffre, sans affichage prévu. Pour finir à la main, utiliser « mot de passe oublié ».
