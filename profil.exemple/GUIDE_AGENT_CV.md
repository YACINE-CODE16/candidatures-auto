# Guide pour l'agent : adapter le CV du candidat sans casser la mise en forme

## 1. La règle d'or

**Ne jamais régénérer le CV** (pas de HTML → PDF, pas de docx-js, pas de LaTeX, pas de « réécriture » complète).
Le CV part TOUJOURS du fichier .docx de référence du candidat, et l'agent ne fait que **remplacer du texte
à l'intérieur** de ce fichier. La mise en forme (polices, filets, tabulations, puces, marges) est stockée
dans le fichier ; tant qu'on ne touche qu'au texte, elle reste identique.

## 2. Comment modifier

Le rédacteur écrit `edits.json` dans le dossier de l'offre, puis appelle l'outil `produire_cv` :

```json
{"categorie": "Principal", "langue": "fr", "operations": [
  {"op": "replace", "old": "texte exact d'un bloc", "new": "nouveau texte"},
  {"op": "add_bullet", "after": "texte exact d'une puce", "text": "Nouvelle puce…"},
  {"op": "remove_para", "contains": "texte unique de la puce à supprimer"}
]}
```

- `replace` : remplace un texte exact (il doit apparaître **une seule fois**).
- `add_bullet` : duplique une puce existante (même style) et y met le nouveau texte, juste après elle.
- `remove_para` : supprime le paragraphe qui contient ce texte.

`produire_cv` fabrique le PDF et **refuse tout CV de plus d'une page**.

## 3. Ce que l'agent a le droit de changer

- **La ligne cible** sous les coordonnées, s'il y en a une. L'intitulé fait **3 à 6 mots**.
- **La section Projets**, s'il y en a une : titre du projet, ligne de technos et puces, en gardant autant de
  projets et de puces que le modèle.
- **Les puces des expériences** : reformuler pour coller à l'offre (mots-clés), sans inventer de mission.
  Garder le même nombre de puces par expérience que le modèle.
- **La ligne des outils ou compétences** : réordonner, en restant sur **une seule ligne**.

## 4. Ce que l'agent ne doit JAMAIS changer

- Le nom, les coordonnées, les liens cliquables, l'ordre et le nom des sections.
- La formation, les dates, les lieux, les intitulés de poste.
- Les langues, certifications, engagements et centres d'intérêt, tels qu'écrits dans le modèle.
- Les polices, tailles, couleurs, marges, tabulations, filets.

## 5. Contraintes de longueur (pour rester sur 1 page)

- Une puce = **1 ligne idéalement, 2 lignes maximum** (≈ 110 caractères par ligne).
- Si on ajoute une ligne quelque part, en retirer une ailleurs.
- Pas de tiret cadratin « — » dans les puces ; utiliser « : » ou « , ».

## 6. Contrôle final obligatoire

1. Le PDF fait **1 page**.
2. Mêmes sections, mêmes polices que le modèle (sinon le CV a été régénéré au lieu d'être édité : refaire).
3. Aucune mission ni chiffre absent de `verite.md`.
4. Livrer en PDF, nommé `CV_<Prénom>_<Nom>_<Entreprise>.pdf`.
