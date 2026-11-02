"""Vérification des jetons d'accès Keycloak présentés à la console.

La console ne fait pas confiance à un simple en-tête : elle vérifie la signature du jeton avec
la clé publique de Keycloak. La vérification est faite par PyJWT : aucune cryptographie écrite
par l'équipe.
"""
import jwt


class AccesRefuse(Exception):
    """Jeton authentique, mais son porteur n'est pas administrateur de la messagerie."""


def verifier_jeton(jeton: str, cle_publique, emetteur: str, audience: str, groupe_admin: str) -> dict:
    """Renvoie les revendications du jeton s'il est signé par Keycloak, non expiré, émis par notre
    realm pour la console, et si son porteur appartient au groupe des administrateurs.

    Lève jwt.PyJWTError si le jeton n'est pas valable, AccesRefuse s'il manque le groupe.
    """
    claims = jwt.decode(
        jeton,
        cle_publique,
        algorithms=["RS256"],  # l'algorithme est imposé, jamais lu dans le jeton
        issuer=emetteur,
        audience=audience,
        options={"require": ["exp", "iss", "aud"]},
    )
    if groupe_admin not in claims.get("groups", []):
        raise AccesRefuse(f"{claims.get('preferred_username')} n'appartient pas au groupe {groupe_admin}")
    return claims
