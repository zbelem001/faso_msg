"""Console d'administration de la messagerie : enrôlement, révocation en un clic, journal d'audit.

Elle n'est jamais exposée directement : oauth2-proxy impose la connexion Keycloak (avec second
facteur) et lui transmet le jeton d'accès de l'administrateur, dont require_admin vérifie la signature.
"""
import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints

from auth import AccesRefuse, verifier_jeton
from enrolement import EnrolementRefuse, NouvelAgent, enroler_agent, est_reserve
from revocation import Config, KeycloakAdmin, SynapseAdmin, revoke_agent
from synapse_token import obtenir_jeton

KEYCLOAK_URL = os.environ["KEYCLOAK_URL"]
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "administration")
KEYCLOAK_ISSUER = os.environ["KEYCLOAK_ISSUER"]
CONSOLE_ORIGIN = os.environ["CONSOLE_ORIGIN"]
OIDC_AUDIENCE = os.environ.get("OIDC_AUDIENCE", "oauth2-proxy")  # aussi l'identifiant du client oauth2-proxy
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "admin-messagerie")
AGENTS_GROUP = os.environ.get("AGENTS_GROUP", "agents")
SYNAPSE_URL = os.environ["SYNAPSE_URL"]
SYNAPSE_SERVER_NAME = os.environ["SYNAPSE_SERVER_NAME"]
SYNAPSE_ADMIN_TOKEN_FILE = Path(os.environ.get("SYNAPSE_ADMIN_TOKEN_FILE", "/data/synapse_admin_token"))
AUDIT_DB = os.environ.get("AUDIT_DB", "/data/audit.db")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Console d'administration", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Clés publiques de Keycloak, téléchargées par le réseau interne et mises en cache par PyJWT
_jwks = jwt.PyJWKClient(f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs")
_verrou_synapse = threading.Lock()

with closing(sqlite3.connect(AUDIT_DB)) as base:
    base.execute("""CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY, horodatage TEXT, administrateur TEXT, action TEXT, agent TEXT,
        motif TEXT, succes INTEGER, rapport TEXT)""")
    base.commit()


def config(jeton_synapse: str = "") -> Config:
    return Config(KEYCLOAK_URL, KEYCLOAK_REALM, os.environ["KEYCLOAK_CLIENT_ID"], os.environ["KEYCLOAK_CLIENT_SECRET"],
                  SYNAPSE_URL, jeton_synapse, SYNAPSE_SERVER_NAME)


def jeton_synapse() -> str:
    """Jeton du compte de service Synapse (contrat C2), obtenu seul au premier besoin.

    Si Synapse est injoignable, la révocation coupe quand même l'identité ; l'étape Synapse
    apparaît alors en échec dans le rapport.
    """
    with _verrou_synapse, httpx.Client(timeout=10) as http:
        try:
            return obtenir_jeton(http, SYNAPSE_URL, os.environ["SYNAPSE_REGISTRATION_SHARED_SECRET"],
                                 SYNAPSE_ADMIN_TOKEN_FILE)
        except (httpx.HTTPError, RuntimeError):
            return ""


def journaliser(admin: dict, action: str, agent: str, motif: str, succes: bool, rapport: dict) -> None:
    with closing(sqlite3.connect(AUDIT_DB)) as base:
        base.execute("INSERT INTO journal (horodatage, administrateur, action, agent, motif, succes, rapport) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), admin.get("preferred_username"),
                      action, agent, motif, succes, json.dumps(rapport, ensure_ascii=False)))
        base.commit()


def require_admin(x_forwarded_access_token: str | None = Header(default=None)) -> dict:
    """Laisse passer uniquement un jeton Keycloak authentique d'un membre de admin-messagerie."""
    if not x_forwarded_access_token:
        raise HTTPException(401, "Connexion requise")
    try:
        cle = _jwks.get_signing_key_from_jwt(x_forwarded_access_token).key
        return verifier_jeton(x_forwarded_access_token, cle, KEYCLOAK_ISSUER, OIDC_AUDIENCE, ADMIN_GROUP)
    except jwt.PyJWTError:
        raise HTTPException(401, "Jeton invalide ou expiré")
    except AccesRefuse:
        raise HTTPException(403, "Réservé aux administrateurs de la messagerie")


def meme_origine(origin: str | None = Header(default=None)) -> None:
    """Refuse une action envoyée par une page d'une autre origine que la console (protection CSRF)."""
    if origin is not None and origin != CONSOLE_ORIGIN:
        raise HTTPException(403, "Origine de la requête refusée")


Admin = Annotated[dict, Depends(require_admin)]


def texte(longueur_max: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=longueur_max)]


class Revocation(BaseModel):
    motif: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]


class Enrolement(BaseModel):
    identifiant: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=3, max_length=64,
                                                  pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")]
    prenom: texte(100)
    nom: texte(100)
    ministere: texte(200)
    fonction: texte(200)


def statut(membre: dict) -> str:
    if not membre.get("enabled", False):
        return "revoque"
    # Mot de passe provisoire non changé ou TOTP pas encore configuré
    return "en_attente" if membre.get("requiredActions") else "actif"


@app.get("/")
def page():
    return FileResponse(STATIC / "index.html")


@app.get("/api/moi")
def moi(admin: Admin):
    # Déconnexion complète : cookie de la console (oauth2-proxy), puis session Keycloak
    fin_session = (f"{KEYCLOAK_ISSUER}/protocol/openid-connect/logout?client_id={OIDC_AUDIENCE}"
                   f"&post_logout_redirect_uri={quote(CONSOLE_ORIGIN + '/', safe='')}")
    return {"identifiant": admin.get("preferred_username"), "nom": admin.get("name"), "serveur": SYNAPSE_SERVER_NAME,
            "deconnexion": f"/oauth2/sign_out?rd={quote(fin_session, safe='')}"}


@app.get("/api/agents")
def agents(admin: Admin):
    with httpx.Client(timeout=10) as http:
        membres = KeycloakAdmin(config(), http).group_members(AGENTS_GROUP)
    attribut = lambda m, nom: (m.get("attributes", {}).get(nom) or [""])[0]
    return sorted(({"identifiant": m["username"],
                    "nom": f"{m.get('firstName', '')} {m.get('lastName', '')}".strip(),
                    "ministere": attribut(m, "ministere"),
                    "fonction": attribut(m, "fonction"),
                    "statut": statut(m)} for m in membres),
                  key=lambda a: a["nom"])


@app.post("/api/agents", status_code=201, dependencies=[Depends(meme_origine)])
def enroler(demande: Enrolement, admin: Admin):
    try:
        with httpx.Client(timeout=10) as http:
            resultat = enroler_agent(NouvelAgent(**demande.model_dump()), KeycloakAdmin(config(), http),
                                     AGENTS_GROUP, SYNAPSE_SERVER_NAME)
    except EnrolementRefuse as refus:
        raise HTTPException(409, str(refus))
    except httpx.HTTPStatusError as erreur:
        raise HTTPException(502, f"Keycloak a refusé l'enrôlement (HTTP {erreur.response.status_code})")
    except httpx.HTTPError:
        raise HTTPException(502, "Keycloak est injoignable")
    # Le mot de passe provisoire n'est jamais journalisé : il n'est affiché qu'une fois.
    journaliser(admin, "enrolement", resultat["identifiant"], "", True, {"mxid": resultat["mxid"]})
    return resultat


@app.post("/api/agents/{username}/revoke", dependencies=[Depends(meme_origine)])
def revoquer(username: str, demande: Revocation, admin: Admin):
    username = username.lower()
    if username == admin.get("preferred_username") or est_reserve(username):
        raise HTTPException(400, "Ce compte ne peut pas être révoqué depuis la console")
    cfg = config(jeton_synapse())
    with httpx.Client(timeout=10) as http:
        rapport = revoke_agent(username, KeycloakAdmin(cfg, http), SynapseAdmin(cfg, http), cfg.server_name)
    journaliser(admin, "revocation", username, demande.motif, rapport.success, rapport.to_dict())
    return rapport.to_dict()


@app.get("/api/journal")
def journal(admin: Admin):
    with closing(sqlite3.connect(AUDIT_DB)) as base:
        base.row_factory = sqlite3.Row
        lignes = base.execute("SELECT * FROM journal ORDER BY id DESC LIMIT 100").fetchall()
    return [{**dict(ligne), "succes": bool(ligne["succes"]), "rapport": json.loads(ligne["rapport"])}
            for ligne in lignes]
