import hashlib
import hmac
import json

import httpx
import respx

from synapse_token import obtenir_jeton

SYN = "http://syn"
SECRET = "secret-partage"


def mock_register(*reponses):
    respx.get(f"{SYN}/_synapse/admin/v1/register").respond(json={"nonce": "n0nce"})
    return respx.post(f"{SYN}/_synapse/admin/v1/register").mock(side_effect=list(reponses))


@respx.mock
def test_jeton_conserve_et_reutilise(tmp_path):
    fichier = tmp_path / "jeton"
    fichier.write_text("jeton-existant")
    respx.get(f"{SYN}/_matrix/client/v3/account/whoami").respond(json={"user_id": "@console-admin:x"})
    register = mock_register()

    with httpx.Client() as http:
        assert obtenir_jeton(http, SYN, SECRET, fichier) == "jeton-existant"
    assert not register.called


@respx.mock
def test_compte_de_service_cree_avec_le_secret_partage(tmp_path):
    fichier = tmp_path / "jeton"
    register = mock_register(httpx.Response(200, json={"access_token": "nouveau"}))

    with httpx.Client() as http:
        assert obtenir_jeton(http, SYN, SECRET, fichier) == "nouveau"

    corps = json.loads(register.calls.last.request.content)
    assert corps["username"] == "console-admin" and corps["admin"] is True
    # Le code prouve la connaissance du secret, au format documenté par Synapse
    message = b"\x00".join(p.encode() for p in ("n0nce", "console-admin", corps["password"], "admin"))
    assert corps["mac"] == hmac.new(SECRET.encode(), message, hashlib.sha1).hexdigest()
    assert fichier.read_text() == "nouveau"
    assert fichier.stat().st_mode & 0o777 == 0o600


@respx.mock
def test_jeton_perdu_reprise_avec_un_compte_suffixe(tmp_path):
    fichier = tmp_path / "jeton"
    fichier.write_text("jeton-perime")
    respx.get(f"{SYN}/_matrix/client/v3/account/whoami").respond(401)
    register = mock_register(httpx.Response(400, json={"errcode": "M_USER_IN_USE"}),
                             httpx.Response(200, json={"access_token": "repris"}))

    with httpx.Client() as http:
        assert obtenir_jeton(http, SYN, SECRET, fichier) == "repris"

    second = json.loads(register.calls.last.request.content)["username"]
    assert second.startswith("console-admin-")
