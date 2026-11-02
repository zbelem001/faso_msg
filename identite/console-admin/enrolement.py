"""Enrôlement d'un agent : création de son compte d'identité dans Keycloak.

L'agent reçoit un mot de passe provisoire ; à sa première connexion, Keycloak l'oblige à choisir
son mot de passe et à configurer son code TOTP. Le compte de messagerie n'est pas créé ici :
Synapse le crée à la première connexion de l'agent via Keycloak (contrat C1).
"""
from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass

import httpx

from revocation import KeycloakAdmin

# Contrat C1 : identifiant en minuscules, lettres, chiffres, point et tiret (ex. awa.demo)
FORMAT_IDENTIFIANT = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
# Comptes de service : jamais créés ni révoqués depuis la console (contrat C3)
PREFIXES_RESERVES = ("console-admin", "service-account-")


class EnrolementRefuse(Exception):
    """Demande valide en forme, mais refusée (identifiant réservé ou déjà pris)."""


@dataclass(frozen=True)
class NouvelAgent:
    identifiant: str
    prenom: str
    nom: str
    ministere: str
    fonction: str


def est_reserve(identifiant: str) -> bool:
    return identifiant.startswith(PREFIXES_RESERVES)


def mot_de_passe_provisoire(longueur: int = 16) -> str:
    """Tiré par le module secrets ; valable pour la seule première connexion."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(longueur))


def enroler_agent(agent: NouvelAgent, kc: KeycloakAdmin, groupe_agents: str, server_name: str) -> dict:
    """Crée l'agent dans Keycloak, membre du groupe des agents, avec mot de passe provisoire et
    actions requises. Renvoie son identifiant Matrix et le mot de passe provisoire à lui remettre.

    Lève EnrolementRefuse si l'identifiant est invalide, réservé ou déjà pris,
    et httpx.HTTPError si Keycloak refuse l'opération.
    """
    identifiant = agent.identifiant.lower()
    if not FORMAT_IDENTIFIANT.fullmatch(identifiant):
        raise EnrolementRefuse("Identifiant invalide : minuscules, chiffres, point et tiret uniquement")
    if est_reserve(identifiant):
        raise EnrolementRefuse("Identifiant réservé aux comptes de service")
    if kc.find_user(identifiant) is not None:
        raise EnrolementRefuse(f"L'identifiant {identifiant} est déjà utilisé")
    groupe = kc.group_id(groupe_agents)
    if groupe is None:
        raise EnrolementRefuse(f"Groupe {groupe_agents} introuvable dans l'annuaire")

    mot_de_passe = mot_de_passe_provisoire()
    user_id = kc.create_user({
        "username": identifiant,
        "enabled": True,
        "firstName": agent.prenom,
        "lastName": agent.nom,
        "attributes": {"ministere": [agent.ministere], "fonction": [agent.fonction]},
        "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"],
        "credentials": [{"type": "password", "value": mot_de_passe, "temporary": True}],
    })
    try:
        kc.add_to_group(user_id, groupe)
    except httpx.HTTPError:
        # Pas de compte à moitié créé : sans le groupe, l'agent ne pourrait de toute façon pas entrer.
        kc.delete_user(user_id)
        raise
    return {"identifiant": identifiant, "mxid": f"@{identifiant}:{server_name}",
            "mot_de_passe_provisoire": mot_de_passe}
