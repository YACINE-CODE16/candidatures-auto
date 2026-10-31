# Guide pour l'agent : adapter le CV du candidat sans casser la mise en forme

## 1. La règle d'or

**Ne jamais régénérer le CV** (pas de HTML → PDF, pas de docx-js, pas de LaTeX, pas de « réécriture » complète).
Le CV part TOUJOURS d'un des 3 fichiers .docx de référence fournis, et l'agent ne fait que **remplacer du texte
à l'intérieur** de ce fichier. La mise en forme (polices, filets noirs, tabulations, puces, marges) est stockée
dans le fichier ; tant qu'on ne touche qu'au texte, elle reste identique.

Les 3 modèles :

| Fichier | À utiliser pour |
|---|---|
| `CV_le candidat_Finance_Quant.docx` | banque, asset management, hedge fund, assurance, quant / risque / marchés |
| `CV_le candidat_Data_Scientist.docx` | data science / data analyst généraliste, conseil, grands groupes  |
| `CV_le candidat_Recherche_IA.docx` | R&D, laboratoire, recherche IA, research scientist / engineer |

## 2. Comment modifier (méthode technique)

Un .docx est un zip. Tout le contenu est dans `word/document.xml`. L'outil fourni `cv_edit.py` fait tout :

```bash
python3 cv_edit.py CV_le candidat_Data_Scientist.docx sortie.docx edits.json
```

`edits.json` contient une liste d'opérations, appliquées dans l'ordre :

```json
[
  {"op": "replace", "old": "Stage de fin d'études Data Scientist / Data Analyst",
                    "new": "Stage de fin d'études Data Scientist – NLP"},
  {"op": "replace", "old": "Analyse prédictive S&P 500", "new": "Détection de fraude bancaire"},
  {"op": "add_bullet", "after": "Script Python (pandas)", "text": "Nouvelle puce…"},
  {"op": "remove_para", "contains": "texte unique de la puce à supprimer"}
]
```

- `replace` : remplace un texte exact (il doit apparaître **une seule fois**).
- `add_bullet` : duplique une puce existante (même style) et y met le nouveau texte, juste après elle.
- `remove_para` : supprime le paragraphe qui contient ce texte.

Le script produit le .docx **et** le .pdf (via LibreOffice) et **échoue si le CV dépasse 1 page**.
Prérequis : Python 3, `soffice` (LibreOffice) et `pdfinfo` (poppler-utils) installés, police **Garamond**
disponible (elle est embarquée dans le .docx ; si LibreOffice ne la trouve pas, installer Garamond ou EB Garamond).

Si l'agent ne peut pas utiliser le script : même principe à la main → dézipper, modifier uniquement le texte
entre les balises `<w:t>…</w:t>` dans `word/document.xml`, re-zipper, convertir en PDF, vérifier 1 page.

## 3. Ce que l'agent a le droit de changer

- **La ligne cible** sous les coordonnées (ex. « Stage de fin d'études Data Scientist | 6 mois à partir du
  8 février 2027 | Paris & Île-de-France »). Garder ce format court : `Stage de fin d'études <Intitulé court> | 6 mois à partir du 8 février 2027 | Paris & Île-de-France`. L'intitulé fait **3 à 6 mots maximum** (jamais le titre complet de l'offre) et la ligne tient sur **une seule ligne**.
- **La section PROJETS** : titre du projet, ligne de technos (texte normal sous le titre), et les 2 puces.
  Toujours garder **2 projets** avec **2 puces** chacun.
- **Les puces des expériences** : reformuler pour coller à l'offre (mots-clés), sans inventer de mission.
  Garder le même nombre de puces par expérience que le modèle.
- **La ligne « Outils & frameworks »** : réordonner / adapter, en restant sur **une seule ligne**.

## 4. Ce que l'agent ne doit JAMAIS changer

- Le nom, les coordonnées, le lien LinkedIn (cliquable), l'ordre et le nom des sections :
  ÉDUCATION → EXPÉRIENCES PROFESSIONNELLES → PROJETS → INFORMATIONS COMPLÉMENTAIRES.
- **Ne jamais supprimer la section PROJETS.**
- La section Éducation, les dates, les lieux, les intitulés de poste.
- Langues (telles qu'écrites dans le modèle), Certifications (liens cliquables),
  Engagement, Centres d'intérêt (tels qu'écrits dans le modèle).
- Les polices, tailles, couleurs, marges, tabulations, filets noirs.

## 5. Contraintes de longueur (pour rester sur 1 page)

- Une puce = **1 ligne idéalement, 2 lignes maximum** (≈ 110 caractères par ligne).
- Si on ajoute une ligne quelque part, en retirer une ailleurs.
- Le CV doit remplir la page sans déborder : **1 page exactement**, sans grand blanc en bas.
- Pas de tiret cadratin « — » dans les puces ; utiliser « : » ou « , ».

## 6. Référence de la mise en forme (pour vérification visuelle)

| Élément | Valeur |
|---|---|
| Format | A4, marges : haut 1,22 cm · gauche/droite 1,02 cm · bas 0,79 cm |
| Police | Garamond partout (puces : Arial) |
| Nom | Garamond gras 18 pt, centré |
| Coordonnées | 10,5 pt, centré, LinkedIn en lien bleu souligné |
| Ligne cible | Garamond gras 10,5 pt, bleu marine `#1F3864`, centrée |
| Titres de section | MAJUSCULES, gras, avec un filet noir pleine largeur dessous |
| Corps de texte | Garamond 10,5 pt, interligne simple |
| Entreprise / école | gras à gauche, dates en gras alignées à droite (tabulation droite à 10527 twips ≈ 18,57 cm) |
| Poste / diplôme | normal à gauche, ville/pays alignés à droite (même tabulation) |
| Puces | « • », retrait 485 twips |

## 7. Contrôle final obligatoire

1. Le PDF fait **1 page**.
2. Comparer visuellement avec le PDF de référence du même modèle : mêmes sections, mêmes filets, Garamond
   (si le PDF est en « Liberation Serif » ou « Times », c'est que le CV a été régénéré au lieu d'être édité : refaire).
3. Les liens (LinkedIn, certifications) sont toujours cliquables.
4. Aucune mission ni chiffre inventé par rapport au CV de référence, sauf si le candidat l'a validé.
5. Livrer en PDF, nommé `CV_<Prénom>_<Nom>_<Entreprise>.pdf`.
