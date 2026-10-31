"""Entreprises ciblées par le chasseur (mode « cibler ») : la liste du candidat, dans
profil/candidat.json (« entreprises_cibles »). Liste vide : ce mode ne fait rien.
"""

import json
from datetime import date, timedelta

from outils.config import candidat
from outils.lancement import RACINE

DEJA_CIBLEES = RACINE / "logs" / "cibles_faites.json"
DELAI_JOURS = 3            # une entreprise n'est re-ciblée qu'après ce délai (nouvelles offres entre-temps)
DELAI_SANS_OFFRE_JOURS = 14   # site visité sans aucune offre retenue (que d'autres contrats)
SANS_OFFRE = " sans offre"


def prochaines(nombre: int) -> list[str]:
    """Les `nombre` entreprises à cibler maintenant : jamais ciblées, ou pas depuis leur délai."""
    liste = candidat().get("entreprises_cibles", [])
    faites = json.loads(DEJA_CIBLEES.read_text()) if DEJA_CIBLEES.exists() else {}

    def due(entreprise: str) -> bool:
        vue = faites.get(entreprise, "")
        delai = DELAI_SANS_OFFRE_JOURS if vue.endswith(SANS_OFFRE) else DELAI_JOURS
        return vue[:10] < (date.today() - timedelta(days=delai)).isoformat()
    return [e for e in liste if due(e)][:nombre]


def marquer_ciblee(entreprise: str, sans_offre: bool = False) -> None:
    faites = json.loads(DEJA_CIBLEES.read_text()) if DEJA_CIBLEES.exists() else {}
    faites[entreprise] = date.today().isoformat() + (SANS_OFFRE if sans_offre else "")
    DEJA_CIBLEES.parent.mkdir(exist_ok=True)
    DEJA_CIBLEES.write_text(json.dumps(faites, ensure_ascii=False, indent=1))
