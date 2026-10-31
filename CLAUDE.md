# Pipeline de candidatures : consignes pour Claude

Ce dépôt automatise une recherche d'emploi : trouver des offres, préparer un CV et une lettre vérifiés
(zéro invention), postuler sur le site de l'entreprise, suivre les réponses dans Gmail, le tout visible dans
un Google Sheet. Tout ce qui est personnel vit dans `profil/`, `secrets/` et `.env`, jamais versionnés.

Parle à l'utilisateur en français simple, sans jargon, et tutoie-le. Il n'a peut-être jamais ouvert un terminal : c'est
toi qui exécutes tout. Lui ne fait que cliquer, déposer un fichier et répondre.

## Installation

Quand l'utilisateur écrit « installe-moi le pipeline » ou « reprends l'installation », suis
`.claude/skills/installer-pipeline/SKILL.md` du début à la fin.

## Utilisation quotidienne

Toutes les commandes s'exécutent avec `.venv/bin/python` (créé par `bash lancer_cloud.sh --dependances`).

- `bash lancer_cloud.sh` : lancement complet dans le cloud (voir `TACHE_PLANIFIEE.md`).
- `python main.py` : lancement sur un ordinateur. Options `--max-*` pour doser, `--dry-run` pour ne rien envoyer.
- `python -m outils.debloquer "<motif>" --appliquer` : remet dans la file les offres bloquées pour un motif résolu.
- `python -m outils.assiste` : sur un ordinateur seulement, navigateur visible pour les CAPTCHA et les connexions.
- `python -m outils.reparer_tableau_de_bord` : formules du tableau de bord.
- `python -m pytest -q` : tests.

## Règles à respecter (garanties en partie par le code)

- Zéro invention : un fait sur le candidat vient de `profil/verite.md`, sinon il n'apparaît pas. Ce fichier doit porter
  « Validé par <Prénom> le JJ/MM/AAAA » : n'écris cette ligne qu'après l'accord explicite de l'utilisateur.
- On postule sur le site de l'entreprise ; les plateformes servent seulement à trouver les offres.
- Jamais de CAPTCHA résolu, jamais de protection anti-robot contournée : l'offre passe en « Bloquée ».
- Les comptes LinkedIn et JobTeaser ne sont jamais créés ni réinitialisés. Ne demande jamais leur mot de passe.
- LinkedIn et Indeed ne sont jamais lus par le chasseur ; robots.txt est respecté.
- Un mail sortant ne part que vers l'utilisateur lui-même.
- Aucun secret dans git ni en clair sur le Drive. Ne commite jamais `profil/`, `secrets/`, `.env`, `dossiers/`, `logs/`.
