"""Test d'acceptation du contrat C2 contre un vrai Synapse, par le réseau interne Docker.

    docker compose exec console-admin python -m pytest -q

Ignoré si SYNAPSE_URL ou SYNAPSE_REGISTRATION_SHARED_SECRET ne sont pas définis. Keycloak est
simulé : seul Synapse est réel. Les deux comptes de test (préfixe test-c2-) sont désactivés à la fin.
"""
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from revocation import Config, SynapseAdmin, revoke_agent
from synapse_token import obtenir_jeton

SYNAPSE_URL = os.environ.get("SYNAPSE_URL")
SECRET = os.environ.get("SYNAPSE_REGISTRATION_SHARED_SECRET")
pytestmark = pytest.mark.skipif(not (SYNAPSE_URL and SECRET), reason="Synapse réel non configuré")


class AnnuaireSimule:
    """Keycloak simulé : la révocation côté identité est couverte par test_revocation.py."""

    def find_user(self, username):
        return {"id": username, "username": username}

    def disable_user(self, user_id):
        pass

    def logout(self, user_id):
        pass


def auth(jeton):
    return {"Authorization": f"Bearer {jeton}"}


def creer_agent(http, nom) -> str:
    """Crée un compte non administrateur et renvoie le jeton de son appareil."""
    nonce = http.get(f"{SYNAPSE_URL}/_synapse/admin/v1/register").json()["nonce"]
    mot_de_passe = secrets.token_urlsafe(16)
    message = b"\x00".join(p.encode() for p in (nonce, nom, mot_de_passe, "notadmin"))
    r = http.post(f"{SYNAPSE_URL}/_synapse/admin/v1/register",
                  json={"nonce": nonce, "username": nom, "password": mot_de_passe, "admin": False,
                        "mac": hmac.new(SECRET.encode(), message, hashlib.sha1).hexdigest()})
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture
def jeton_admin(tmp_path):
    # Dans le conteneur, on réutilise le jeton de la console plutôt que de créer un compte de service.
    fichier = Path(os.environ.get("SYNAPSE_ADMIN_TOKEN_FILE", "/data/synapse_admin_token"))
    if not os.access(fichier.parent, os.W_OK):
        fichier = tmp_path / "jeton"
    with httpx.Client(timeout=10) as http:
        return obtenir_jeton(http, SYNAPSE_URL, SECRET, fichier)


def test_revocation_reelle(jeton_admin):
    with httpx.Client(base_url=SYNAPSE_URL, timeout=10) as http:
        moi = http.get("/_matrix/client/v3/account/whoami", headers=auth(jeton_admin)).json()["user_id"]
        serveur = moi.split(":", 1)[1]
        suffixe = secrets.token_hex(3)
        awa, issa = f"test-c2-awa-{suffixe}", f"test-c2-issa-{suffixe}"
        jeton_awa, jeton_issa = creer_agent(http, awa), creer_agent(http, issa)

        salon = http.post("/_matrix/client/v3/createRoom", headers=auth(jeton_awa),
                          json={"preset": "private_chat", "invite": [f"@{issa}:{serveur}"]}).json()["room_id"]
        http.post(f"/_matrix/client/v3/join/{quote(salon)}", headers=auth(jeton_issa), json={}).raise_for_status()
        chiffrement = http.get(f"/_matrix/client/v3/rooms/{quote(salon)}/state/m.room.encryption",
                               headers=auth(jeton_awa))
        assert chiffrement.json()["algorithm"] == "m.megolm.v1.aes-sha2"  # salon chiffré par défaut

        synapse = SynapseAdmin(Config("", "", "", "", SYNAPSE_URL, jeton_admin, serveur), http)
        rapport = revoke_agent(issa, AnnuaireSimule(), synapse, serveur)

        assert rapport.success, rapport.to_dict()
        assert rapport.rooms_left == [salon]
        # L'appareil déjà connecté d'Issa est coupé immédiatement
        r = http.get("/_matrix/client/v3/account/whoami", headers=auth(jeton_issa))
        assert r.status_code == 401 and r.json()["errcode"] == "M_UNKNOWN_TOKEN"
        # Issa a quitté le salon, Awa y est toujours
        membres = http.get(f"/_matrix/client/v3/rooms/{quote(salon)}/joined_members",
                           headers=auth(jeton_awa)).json()["joined"]
        assert f"@{awa}:{serveur}" in membres and f"@{issa}:{serveur}" not in membres

        synapse.deactivate(f"@{awa}:{serveur}")


def test_inscription_libre_refusee():
    r = httpx.post(f"{SYNAPSE_URL}/_matrix/client/v3/register",
                   json={"username": "intrus", "password": "mot-de-passe-intrus", "auth": {"type": "m.login.dummy"}})
    assert r.status_code == 403
