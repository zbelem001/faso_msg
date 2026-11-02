"""Garde-fous de la console, vérifiés sans Keycloak ni Synapse."""
import os
import tempfile
from unittest import mock

import pytest
from fastapi.testclient import TestClient

DEFAUTS = {"KEYCLOAK_URL": "http://kc", "KEYCLOAK_ISSUER": "http://auth.test/realms/administration",
           "CONSOLE_ORIGIN": "http://admin.test", "KEYCLOAK_CLIENT_ID": "console-admin",
           "KEYCLOAK_CLIENT_SECRET": "secret", "SYNAPSE_URL": "http://syn", "SYNAPSE_SERVER_NAME": "messagerie.gov.bf"}

# Valeurs de test, sauf dans le conteneur où les vraies sont déjà définies. Le journal reste
# temporaire, et ces valeurs ne restent pas dans l'environnement des autres tests.
with mock.patch.dict(os.environ, {**DEFAUTS, **os.environ, "AUDIT_DB": os.path.join(tempfile.mkdtemp(), "audit.db")}):
    import app as console

ORIGINE = {"Origin": console.CONSOLE_ORIGIN}


@pytest.fixture
def client():
    console.app.dependency_overrides[console.require_admin] = lambda: {"preferred_username": "admin.demo"}
    yield TestClient(console.app)
    console.app.dependency_overrides.clear()


def test_sans_jeton_refuse():
    assert TestClient(console.app).get("/api/agents").status_code == 401


@pytest.mark.parametrize("cible", ["admin.demo", "ADMIN.DEMO", "console-admin", "console-admin-3f2a1b",
                                   "service-account-console-admin"])
def test_comptes_non_revocables(client, cible):
    r = client.post(f"/api/agents/{cible}/revoke", json={"motif": "essai"}, headers=ORIGINE)
    assert r.status_code == 400


def test_action_depuis_une_autre_origine_refusee(client):
    r = client.post("/api/agents/awa.demo/revoke", json={"motif": "essai"},
                    headers={"Origin": "http://site-malveillant.example"})
    assert r.status_code == 403


def test_identifiant_invalide_refuse_avant_keycloak(client):
    r = client.post("/api/agents", headers=ORIGINE, json={"identifiant": "awa demo", "prenom": "Awa", "nom": "Demo",
                                                          "ministere": "Santé", "fonction": "Directrice"})
    assert r.status_code == 422


def test_statut_des_agents():
    assert console.statut({"enabled": False, "requiredActions": []}) == "revoque"
    assert console.statut({"enabled": True, "requiredActions": ["CONFIGURE_TOTP"]}) == "en_attente"
    assert console.statut({"enabled": True, "requiredActions": []}) == "actif"
