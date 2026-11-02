"""Révocation d'un agent : coupe son identité (Keycloak), puis son accès à la messagerie (Synapse).

Aucune cryptographie ici : uniquement des appels aux API d'administration de Keycloak et de Synapse.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class Config:
    keycloak_url: str
    keycloak_realm: str
    keycloak_client_id: str
    keycloak_client_secret: str
    synapse_url: str
    synapse_admin_token: str
    server_name: str


class KeycloakAdmin:
    """API d'administration de Keycloak, appelée avec le compte de service de la console."""

    def __init__(self, cfg: Config, http: httpx.Client):
        self._cfg = cfg
        self._http = http
        self._base = f"{cfg.keycloak_url}/admin/realms/{cfg.keycloak_realm}"
        self._jeton: str | None = None
        self._expiration = 0.0

    def _headers(self) -> dict[str, str]:
        # Flux OAuth2 « client_credentials » : la console s'authentifie en tant qu'application
        # (identifiant et secret du client console-admin) et reçoit un jeton limité aux rôles
        # view-users et manage-users. Le jeton est réutilisé jusqu'à son expiration.
        if self._jeton is None or time.monotonic() >= self._expiration:
            r = self._http.post(
                f"{self._cfg.keycloak_url}/realms/{self._cfg.keycloak_realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._cfg.keycloak_client_id,
                    "client_secret": self._cfg.keycloak_client_secret,
                },
            )
            r.raise_for_status()
            corps = r.json()
            self._jeton = corps["access_token"]
            # 10 s de marge pour ne jamais présenter un jeton sur le point d'expirer
            self._expiration = time.monotonic() + corps.get("expires_in", 60) - 10
        return {"Authorization": f"Bearer {self._jeton}"}

    def find_user(self, username: str) -> dict | None:
        r = self._http.get(f"{self._base}/users", params={"username": username, "exact": "true"},
                           headers=self._headers())
        r.raise_for_status()
        return next((u for u in r.json() if u["username"] == username), None)

    def disable_user(self, user_id: str) -> None:
        r = self._http.put(f"{self._base}/users/{user_id}", json={"enabled": False}, headers=self._headers())
        r.raise_for_status()

    def logout(self, user_id: str) -> None:
        r = self._http.post(f"{self._base}/users/{user_id}/logout", headers=self._headers())
        r.raise_for_status()

    def create_user(self, representation: dict) -> str:
        """Crée le compte et renvoie son identifiant Keycloak (dernier segment de l'en-tête Location)."""
        r = self._http.post(f"{self._base}/users", json=representation, headers=self._headers())
        r.raise_for_status()
        return r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def delete_user(self, user_id: str) -> None:
        r = self._http.delete(f"{self._base}/users/{user_id}", headers=self._headers())
        r.raise_for_status()

    def group_id(self, group: str) -> str | None:
        r = self._http.get(f"{self._base}/groups", params={"search": group, "exact": "true"},
                           headers=self._headers())
        r.raise_for_status()
        return next((g["id"] for g in r.json() if g["name"] == group), None)

    def add_to_group(self, user_id: str, group_id: str) -> None:
        r = self._http.put(f"{self._base}/users/{user_id}/groups/{group_id}", headers=self._headers())
        r.raise_for_status()

    def group_members(self, group: str) -> list[dict]:
        groupe = self.group_id(group)
        if groupe is None:
            return []
        r = self._http.get(f"{self._base}/groups/{groupe}/members",
                           params={"briefRepresentation": "false", "max": 1000}, headers=self._headers())
        r.raise_for_status()
        return r.json()


class SynapseAdmin:
    """API d'administration de Synapse, accessible uniquement par le réseau interne Docker."""

    def __init__(self, cfg: Config, http: httpx.Client):
        self._http = http
        self._base = f"{cfg.synapse_url}/_synapse/admin"
        self._headers = {"Authorization": f"Bearer {cfg.synapse_admin_token}"}

    def joined_rooms(self, mxid: str) -> list[str]:
        r = self._http.get(f"{self._base}/v1/users/{quote(mxid, safe='')}/joined_rooms", headers=self._headers)
        r.raise_for_status()
        return r.json()["joined_rooms"]

    def devices(self, mxid: str) -> list[dict]:
        r = self._http.get(f"{self._base}/v2/users/{quote(mxid, safe='')}/devices", headers=self._headers)
        r.raise_for_status()
        return r.json()["devices"]

    def deactivate(self, mxid: str) -> bool:
        """Jetons invalidés, appareils et clés supprimés, retrait de tous les salons.

        erase=False conserve l'historique pour les autres membres (archivage). Renvoie False
        si l'agent n'a jamais ouvert de compte messagerie.
        """
        r = self._http.post(f"{self._base}/v1/deactivate/{quote(mxid, safe='')}", json={"erase": False},
                            headers=self._headers)
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class RevocationReport:
    username: str
    mxid: str
    success: bool = False
    duration_ms: int = 0
    rooms_left: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def revoke_agent(username: str, kc: KeycloakAdmin, syn: SynapseAdmin, server_name: str,
                 poll_interval_s: float = 0.5, verify_timeout_s: float = 10.0) -> RevocationReport:
    """Révoque un agent sur tous ses appareils et rend compte de chaque étape.

    L'ordre compte : on coupe d'abord l'identité, pour empêcher toute reconnexion
    pendant qu'on nettoie la messagerie.
    """
    debut = time.monotonic()
    username = username.lower()
    report = RevocationReport(username=username, mxid=f"@{username}:{server_name}")

    ok, user = _executer(report, "Recherche de l'agent (Keycloak)", lambda: kc.find_user(username))
    if ok and user is None:
        report.steps[-1] = Step("Recherche de l'agent (Keycloak)", False, "Agent introuvable dans l'annuaire")
    if user is None:
        return _terminer(report, debut)

    ok, _ = _executer(report, "Compte d'identité désactivé", lambda: kc.disable_user(user["id"]))
    if not ok:
        return _terminer(report, debut)

    # Un échec ici n'arrête pas la révocation : le compte désactivé ne peut de toute façon
    # plus renouveler ses jetons Keycloak.
    _executer(report, "Sessions SSO fermées", lambda: kc.logout(user["id"]))

    # Photo des salons avant la désactivation, pour le rapport et le journal d'audit
    try:
        report.rooms_left = syn.joined_rooms(report.mxid)
    except httpx.HTTPError:
        report.rooms_left = []

    ok, compte_existe = _executer(report, "Compte messagerie désactivé", lambda: syn.deactivate(report.mxid))
    if not ok:
        return _terminer(report, debut)
    if not compte_existe:
        report.steps[-1].detail = "Aucun compte messagerie : l'agent ne s'était jamais connecté"
        return _terminer(report, debut)

    # Synapse retire l'agent des salons en tâche de fond : on vérifie jusqu'à verify_timeout_s.
    appareils, salons = None, None
    limite = time.monotonic() + verify_timeout_s
    while True:
        try:
            appareils = syn.devices(report.mxid)
            salons = syn.joined_rooms(report.mxid)
        except httpx.HTTPError:
            pass
        if (appareils == [] and salons == []) or time.monotonic() >= limite:
            break
        time.sleep(poll_interval_s)

    report.steps.append(Step("Appareils et clés supprimés", appareils == [],
                             "" if appareils == [] else "Appareils encore présents après vérification"))
    report.steps.append(Step(f"Retiré de {len(report.rooms_left)} salon(s)", salons == [],
                             "" if salons == [] else "Salons encore rejoints après vérification"))
    return _terminer(report, debut)


def _executer(report: RevocationReport, nom: str, action: Callable[[], Any]) -> tuple[bool, Any]:
    """Exécute une étape et l'inscrit dans le rapport. Renvoie (réussite, résultat)."""
    try:
        resultat = action()
    except httpx.HTTPError as erreur:
        report.steps.append(Step(nom, False, _decrire(erreur)))
        return False, None
    report.steps.append(Step(nom, True))
    return True, resultat


def _decrire(erreur: httpx.HTTPError) -> str:
    if isinstance(erreur, httpx.HTTPStatusError):
        return f"HTTP {erreur.response.status_code} : {erreur.response.text[:200]}"
    return str(erreur) or type(erreur).__name__


def _terminer(report: RevocationReport, debut: float) -> RevocationReport:
    report.success = all(etape.ok for etape in report.steps)
    report.duration_ms = int((time.monotonic() - debut) * 1000)
    return report
