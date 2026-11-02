"""Jeton d'administration Synapse de la console (contrat C2).

La console l'obtient seule : au premier besoin, elle crée un compte de service administrateur avec
le secret d'enregistrement partagé de Synapse, puis conserve le jeton dans son volume. Aucune copie
à la main : l'installation reste en une commande.
"""
import hashlib
import hmac
import secrets
from pathlib import Path

import httpx

COMPTE = "console-admin"


def jeton_valide(http: httpx.Client, synapse_url: str, jeton: str) -> bool:
    r = http.get(f"{synapse_url}/_matrix/client/v3/account/whoami", headers={"Authorization": f"Bearer {jeton}"})
    return r.status_code == 200


def enregistrer_compte_service(http: httpx.Client, synapse_url: str, secret: str) -> str:
    """Crée un compte administrateur Synapse et renvoie son jeton d'accès.

    API d'enregistrement par secret partagé : le code HMAC-SHA1, calculé avec la bibliothèque
    standard, prouve que l'on connaît le secret (format fixé par la documentation de Synapse).
    Si le compte existe déjà (jeton perdu), un compte suffixé est créé : le préfixe console-admin
    reste réservé et n'est jamais révocable depuis la console.
    """
    for nom in (COMPTE, f"{COMPTE}-{secrets.token_hex(3)}"):
        r = http.get(f"{synapse_url}/_synapse/admin/v1/register")
        r.raise_for_status()
        nonce = r.json()["nonce"]
        mot_de_passe = secrets.token_urlsafe(32)  # jamais utilisé : la connexion se fait par Keycloak
        message = b"\x00".join(p.encode() for p in (nonce, nom, mot_de_passe, "admin"))
        code = hmac.new(secret.encode(), message, hashlib.sha1).hexdigest()
        r = http.post(f"{synapse_url}/_synapse/admin/v1/register",
                      json={"nonce": nonce, "username": nom, "password": mot_de_passe, "admin": True, "mac": code})
        if r.status_code == 400 and r.json().get("errcode") == "M_USER_IN_USE":
            continue
        r.raise_for_status()
        return r.json()["access_token"]
    raise RuntimeError("Impossible de créer le compte de service de la console dans Synapse")


def obtenir_jeton(http: httpx.Client, synapse_url: str, secret: str, fichier: Path) -> str:
    """Réutilise le jeton conservé s'il est encore accepté par Synapse, sinon en crée un nouveau."""
    if fichier.exists():
        jeton = fichier.read_text().strip()
        if jeton and jeton_valide(http, synapse_url, jeton):
            return jeton
    jeton = enregistrer_compte_service(http, synapse_url, secret)
    fichier.write_text(jeton)
    fichier.chmod(0o600)
    return jeton
