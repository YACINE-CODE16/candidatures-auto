"""Lancement d'un agent et journalisation de son coût.

Tous les agents du projet passent par `lancer_agent()`. Cela garantit deux règles
du projet à un seul endroit :
  - chaque lancement a un plafond de dépense (`max_budget_usd`, fixé par l'appelant) ;
  - chaque lancement écrit son coût réel (`total_cost_usd`) dans logs/couts.csv.
"""

import asyncio
import csv
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

RACINE = Path(__file__).resolve().parent.parent
FICHIER_COUTS = RACINE / "logs" / "couts.csv"
COMPTES = RACINE / "secrets" / "comptes.json"
DUREE_MAX_S = 15 * 60   # le plus long des agents (un dossier) dure ~4 min ; au-delà, il est figé
AVANT_AGENT: list[Callable[[], None]] = []   # appelés avant chaque agent (ex. rafraîchir le verrou du pipeline)


def _masquer(texte: str) -> str:
    """Remplace les mots de passe des comptes candidats par *** avant tout affichage."""
    if COMPTES.exists():
        for compte in json.loads(COMPTES.read_text()).values():
            texte = texte.replace(compte["mot_de_passe"], "***")
    for nom, valeur in os.environ.items():   # mots de passe donnés par le candidat dans .env
        if nom.startswith("MDP_") and valeur:
            texte = texte.replace(valeur, "***")
    return texte


async def lancer_agent(
    nom: str,
    prompt: str,
    options: ClaudeAgentOptions,
    observateur: Callable[[Message], None] | None = None,
) -> ResultMessage:
    """Fait tourner un agent jusqu'au bout et renvoie son message de résultat.

    Affiche au fil de l'eau ce que l'agent dit et les outils qu'il appelle,
    pour que l'on voie la boucle de l'agent tourner. `observateur`, s'il est
    fourni, reçoit chaque message du flux (y compris ceux des sous-agents) :
    c'est ainsi que le code Python contrôle ce que les agents ont réellement fait.
    """
    if options.max_budget_usd is None:
        raise ValueError(f"L'agent « {nom} » doit avoir un max_budget_usd.")
    for rappel in AVANT_AGENT:
        rappel()

    resultat: ResultMessage | None = None
    async with asyncio.timeout(DUREE_MAX_S):   # un agent figé (page qui n'arrive jamais) ne bloque plus le pipeline
        async for message in query(prompt=prompt, options=options):
            if observateur:
                observateur(message)
            if isinstance(message, AssistantMessage):
                # Les messages d'un sous-agent portent l'ID de l'appel qui l'a lancé.
                prefixe = f"{nom}/sous-agent" if message.parent_tool_use_id else nom
                for bloc in message.content:
                    if isinstance(bloc, TextBlock):
                        print(_masquer(f"[{prefixe}] {bloc.text}"))
                    elif isinstance(bloc, ToolUseBlock):
                        print(_masquer(f"[{prefixe}] → outil {bloc.name} {str(bloc.input)[:200]}"))
            elif isinstance(message, ResultMessage):
                resultat = message

    if resultat is None:
        raise RuntimeError(f"L'agent « {nom} » s'est arrêté sans message de résultat.")

    _journaliser_cout(nom, resultat)
    return resultat


def _journaliser_cout(nom: str, resultat: ResultMessage) -> None:
    """Ajoute une ligne au journal des coûts (crée le fichier et l'en-tête au besoin)."""
    FICHIER_COUTS.parent.mkdir(exist_ok=True)
    nouveau = not FICHIER_COUTS.exists()
    with FICHIER_COUTS.open("a", newline="", encoding="utf-8") as f:
        ecrivain = csv.writer(f)
        if nouveau:
            ecrivain.writerow(["date", "agent", "statut", "tours", "duree_s", "cout_usd"])
        ecrivain.writerow([
            datetime.now().isoformat(timespec="seconds"),
            nom,
            resultat.subtype,  # "success", "error_max_budget_usd", ...
            resultat.num_turns,
            round(resultat.duration_ms / 1000, 1),
            f"{resultat.total_cost_usd or 0:.4f}",
        ])
