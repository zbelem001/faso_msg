import json

import httpx
import pytest
import respx

from enrolement import EnrolementRefuse, NouvelAgent, enroler_agent
from revocation import Config, KeycloakAdmin

CFG = Config("http://kc", "administration", "console-admin", "secret",
             "http://syn", "jeton-admin", "messagerie.gov.bf")
KC = "http://kc/admin/realms/administration"
AGENT = NouvelAgent("Moussa.Kabore", "Moussa", "Kaboré", "Ministère de la Justice", "Greffier")


def mock_keycloak(utilisateurs_existants=()):
    respx.post("http://kc/realms/administration/protocol/openid-connect/token") \
        .respond(json={"access_token": "x"})
    respx.get(f"{KC}/users").respond(json=list(utilisateurs_existants))
    respx.get(f"{KC}/groups").respond(json=[{"id": "g-agents", "name": "agents"}])
    return respx.post(f"{KC}/users").respond(201, headers={"Location": f"{KC}/users/u-77"})


def enroler(agent=AGENT):
    with httpx.Client() as http:
        return enroler_agent(agent, KeycloakAdmin(CFG, http), "agents", CFG.server_name)


@respx.mock
def test_enrolement_complet():
    creation = mock_keycloak()
    groupe = respx.put(f"{KC}/users/u-77/groups/g-agents").respond(204)

    resultat = enroler()

    assert resultat["identifiant"] == "moussa.kabore"
    assert resultat["mxid"] == "@moussa.kabore:messagerie.gov.bf"
    assert groupe.called
    compte = json.loads(creation.calls.last.request.content)
    assert compte["username"] == "moussa.kabore"
    assert compte["attributes"] == {"ministere": ["Ministère de la Justice"], "fonction": ["Greffier"]}
    # Second facteur et mot de passe personnel imposés à la première connexion
    assert set(compte["requiredActions"]) == {"UPDATE_PASSWORD", "CONFIGURE_TOTP"}
    assert compte["credentials"] == [{"type": "password", "value": resultat["mot_de_passe_provisoire"],
                                      "temporary": True}]
    assert len(resultat["mot_de_passe_provisoire"]) >= 12


@respx.mock
def test_identifiant_deja_pris():
    creation = mock_keycloak([{"id": "1", "username": "moussa.kabore"}])
    with pytest.raises(EnrolementRefuse):
        enroler()
    assert not creation.called


@pytest.mark.parametrize("identifiant", ["console-admin-2", "service-account-console-admin", "awa demo",
                                         "awa..demo", "-awa", "awa_demo", "@awa:messagerie.gov.bf"])
@respx.mock
def test_identifiant_refuse(identifiant):
    creation = mock_keycloak()
    with pytest.raises(EnrolementRefuse):
        enroler(NouvelAgent(identifiant, "A", "B", "C", "D"))
    assert not creation.called


@respx.mock
def test_pas_de_compte_a_moitie_cree():
    mock_keycloak()
    respx.put(f"{KC}/users/u-77/groups/g-agents").respond(403)
    suppression = respx.delete(f"{KC}/users/u-77").respond(204)
    with pytest.raises(httpx.HTTPStatusError):
        enroler()
    assert suppression.called
