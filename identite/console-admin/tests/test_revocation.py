import httpx
import respx

from revocation import Config, KeycloakAdmin, SynapseAdmin, revoke_agent

CFG = Config("http://kc", "administration", "console-admin", "secret",
             "http://syn", "jeton-admin", "messagerie.gov.bf")
KC = "http://kc/admin/realms/administration"
MXID = "%40awa.demo%3Amessagerie.gov.bf"
SYN = "http://syn/_synapse/admin"


def mock_keycloak(users):
    respx.post("http://kc/realms/administration/protocol/openid-connect/token") \
        .respond(json={"access_token": "x"})
    respx.get(f"{KC}/users").respond(json=users)
    respx.put(f"{KC}/users/42").respond(204)
    respx.post(f"{KC}/users/42/logout").respond(204)


@respx.mock
def test_revocation_complete():
    mock_keycloak([{"id": "42", "username": "awa.demo"}])
    respx.get(f"{SYN}/v1/users/{MXID}/joined_rooms").mock(side_effect=[
        httpx.Response(200, json={"joined_rooms": ["!salon:messagerie.gov.bf"]}),
        httpx.Response(200, json={"joined_rooms": []}),
    ])
    respx.get(f"{SYN}/v2/users/{MXID}/devices").respond(json={"devices": []})
    deactivate = respx.post(f"{SYN}/v1/deactivate/{MXID}").respond(json={})

    with httpx.Client() as http:
        report = revoke_agent("awa.demo", KeycloakAdmin(CFG, http),
                              SynapseAdmin(CFG, http), CFG.server_name,
                              poll_interval_s=0)

    assert report.success
    assert deactivate.called
    assert report.rooms_left == ["!salon:messagerie.gov.bf"]
    # L'identité est coupée AVANT la messagerie
    urls = [str(c.request.url) for c in respx.calls]
    assert urls.index(f"{KC}/users/42") < urls.index(f"{SYN}/v1/deactivate/{MXID}")


@respx.mock
def test_agent_inconnu_rien_n_est_touche():
    mock_keycloak([])
    deactivate = respx.post(f"{SYN}/v1/deactivate/{MXID}").respond(json={})

    with httpx.Client() as http:
        report = revoke_agent("awa.demo", KeycloakAdmin(CFG, http),
                              SynapseAdmin(CFG, http), CFG.server_name)

    assert not report.success
    assert not deactivate.called
