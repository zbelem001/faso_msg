import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from auth import AccesRefuse, verifier_jeton

EMETTEUR = "https://auth.localhost/realms/administration"
AUDIENCE = "oauth2-proxy"
GROUPE = "admin-messagerie"

# Paires de clés générées pour les tests : l'une joue Keycloak, l'autre un faussaire.
CLE_KEYCLOAK = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLE_FAUSSAIRE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jeton(cle=CLE_KEYCLOAK, **modifs):
    claims = {"iss": EMETTEUR, "aud": AUDIENCE, "exp": int(time.time()) + 300,
              "preferred_username": "admin.demo", "groups": ["agents", GROUPE]}
    claims.update(modifs)
    return jwt.encode(claims, cle, algorithm="RS256")


def verifier(j):
    return verifier_jeton(j, CLE_KEYCLOAK.public_key(), EMETTEUR, AUDIENCE, GROUPE)


def test_jeton_administrateur_accepte():
    assert verifier(jeton())["preferred_username"] == "admin.demo"


def test_agent_simple_refuse():
    with pytest.raises(AccesRefuse):
        verifier(jeton(groups=["agents"]))


def test_signature_d_un_faussaire_refusee():
    with pytest.raises(jwt.InvalidSignatureError):
        verifier(jeton(cle=CLE_FAUSSAIRE))


def test_jeton_modifie_refuse():
    # On remplace le contenu par celui d'un autre jeton en gardant l'ancienne signature
    entete, _, signature = jeton(groups=["agents"]).split(".")
    _, contenu_admin, _ = jeton().split(".")
    with pytest.raises(jwt.InvalidSignatureError):
        verifier(f"{entete}.{contenu_admin}.{signature}")


def test_jeton_non_signe_refuse():
    with pytest.raises(jwt.PyJWTError):
        verifier(jwt.encode({"iss": EMETTEUR, "aud": AUDIENCE, "exp": int(time.time()) + 300,
                             "groups": [GROUPE]}, None, algorithm="none"))


def test_jeton_expire_refuse():
    with pytest.raises(jwt.ExpiredSignatureError):
        verifier(jeton(exp=int(time.time()) - 10))


def test_autre_emetteur_refuse():
    with pytest.raises(jwt.InvalidIssuerError):
        verifier(jeton(iss="https://faux-keycloak.example/realms/administration"))


def test_jeton_destine_a_une_autre_application_refuse():
    with pytest.raises(jwt.InvalidAudienceError):
        verifier(jeton(aud="synapse"))
