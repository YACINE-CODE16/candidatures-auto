# Tâches planifiées

Deux horaires par jour : il faut **deux tâches**, avec le même texte.

1. Ouvrir [claude.ai/code/routines](https://claude.ai/code/routines), puis **New task**.
2. Coller le texte ci-dessous. Dans **Advanced settings → General → Cloud environment**, choisir l'environnement du pipeline.
3. Horaire : chaque jour à **8 h 40**. Recommencer pour une seconde tâche à **17 h 50**.

## Texte de la tâche

```
Exécute `bash lancer_cloud.sh --dependances`, puis `bash lancer_cloud.sh`.
Lance chaque commande en arrière-plan, car elle peut durer jusqu'à 30 minutes, et attends qu'elle soit finie avant de continuer. Ne termine jamais ton tour pendant qu'une commande tourne.
Si la dernière ligne affichée par `bash lancer_cloud.sh` est « A SUIVRE », relance `bash lancer_cloud.sh`, jusqu'à 4 fois en tout.
Ne modifie aucun fichier versionné. Ne crée ni commit, ni branche, ni pull request.
À la fin, lis le fichier logs/resume_<date du jour>.md et résume en 5 lignes : offres trouvées, dossiers, envois, bloquées (avec liens), erreurs.
Si la commande échoue, recopie son message d'erreur tel quel.
```
