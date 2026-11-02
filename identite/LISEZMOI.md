# Partie B : identité et gouvernance

Cette partie décide **qui entre** dans la messagerie et permet de **retirer un accès** en un clic.
Elle fournit le contrat C1 (connexion des agents par Keycloak) et consomme le contrat C2 (API
d'administration de Synapse). La partie A (`messagerie/`) n'est jamais modifiée par cette partie.

## 1. Services

| Service | Image | Rôle | Adresse publique |
|---|---|---|---|
| `keycloak` | `quay.io/keycloak/keycloak:26.8.1` | Annuaire des agents, mot de passe + TOTP, groupes | `auth.<DOMAIN>` |
| `keycloak-db` | `postgres:16-alpine` | Base de Keycloak | aucune |
| `oauth2-proxy` | `quay.io/oauth2-proxy/oauth2-proxy:v7.15.5` | Garde la console : connexion Keycloak et groupe `admin-messagerie` exigés | `admin.<DOMAIN>` |
| `console-admin` | construite depuis `console-admin/` | Enrôlement, révocation, journal d'audit | aucune (derrière oauth2-proxy) |

Aucun service n'expose de port : tout passe par Caddy. La console n'est joignable qu'à travers
oauth2-proxy, et l'API d'administration de Synapse qu'à travers le réseau interne Docker.

```text
identite/
├── compose.yml                          Services de la partie B
├── caddy.caddy                          Sites auth.* et admin.* (CSP stricte, en-tête de jeton nettoyé)
├── keycloak/import/
│   └── realm-administration.json        Realm importé au premier démarrage
├── oauth2-proxy/templates/error.html    Page d'erreur en français, sans ressource extérieure
├── totp-demo/                           QR codes TOTP des comptes fictifs (voir DEMO.md)
└── console-admin/
    ├── app.py                           API et page de la console (FastAPI)
    ├── auth.py                          Vérification de la signature des jetons Keycloak (PyJWT)
    ├── enrolement.py                    Création d'un agent dans Keycloak
    ├── revocation.py                    Révocation : Keycloak puis Synapse, avec rapport
    ├── synapse_token.py                 Jeton d'administration Synapse obtenu seul (contrat C2)
    ├── static/                          Interface (HTML, CSS, JS séparés pour la CSP)
    ├── tests/                           Tests unitaires et d'intégration
    └── Dockerfile  requirements.txt  requirements-tests.txt  pytest.ini
```

## 2. Démarrage

Depuis la racine du dépôt, pile complète :

```bash
cp .env.example .env
docker compose up -d
```

**Mode autonome** (sans la partie A, pour travailler sur l'identité seule) :

```bash
docker compose up -d caddy keycloak oauth2-proxy console-admin
```

La console démarre sans Synapse : la liste des agents et l'enrôlement fonctionnent. Une
révocation coupe alors l'identité, et l'étape « Compte messagerie désactivé » apparaît en échec.

| Adresse | Usage |
|---|---|
| http://admin.localhost | Console d'administration (compte `admin.demo`) |
| http://auth.localhost/admin | Console technique de Keycloak (compte `admin`, mot de passe `KEYCLOAK_ADMIN_PASSWORD`) |
| http://auth.localhost/realms/administration/account | Espace personnel d'un agent (mot de passe, TOTP) |

## 3. Le realm `administration`

Il est importé au premier démarrage (`--import-realm`). Les secrets et les adresses y sont injectés
depuis le `.env` (`${OIDC_SYNAPSE_SECRET}`, `${PUBLIC_SCHEME}`, `${DOMAIN}`…).

| Réglage | Valeur |
|---|---|
| Inscription libre | désactivée |
| Second facteur | **TOTP obligatoire pour tous** : flux de connexion `navigateur-totp`, où le code TOTP est *requis* (un compte sans TOTP doit en configurer un avant d'entrer) |
| Force brute | verrouillage temporaire après 5 échecs |
| Mot de passe | 12 caractères minimum, différent de l'identifiant |
| Groupes | `agents` (accès à la messagerie), `admin-messagerie` (accès à la console), `archiviste` (prévu) |
| Attributs | `ministere`, `fonction` (déclarés dans le profil utilisateur) |
| Langue | français par défaut |

| Client | Type | Usage |
|---|---|---|
| `synapse` | confidentiel, code d'autorisation | Contrat C1. Mappeur `groups` (sans chemin) dans l'ID token, le jeton d'accès et userinfo. Déconnexion back-channel vers `http://synapse:8008/_synapse/client/oidc/backchannel_logout` |
| `oauth2-proxy` | confidentiel, code d'autorisation + PKCE | Connexion à la console. Mappeurs `groups` et audience `oauth2-proxy` |
| `console-admin` | compte de service | API d'administration de Keycloak, rôles `view-users` et `manage-users` uniquement |

**Modifier le realm.** Il n'est importé que si le realm n'existe pas encore. Après une
modification du fichier, il faut repartir d'une base vide, **pour toute la pile** : Synapse lie
chaque compte à l'identifiant interne Keycloak de l'agent, qui change à chaque import.

```bash
docker compose down -v   # supprime aussi les messages et comptes de démonstration
docker compose up -d
```

## 4. La console d'administration

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/` | Interface |
| `GET` | `/api/moi` | Administrateur connecté, lien de déconnexion complète |
| `GET` | `/api/agents` | Membres du groupe `agents`, avec statut : actif, première connexion en attente, révoqué |
| `POST` | `/api/agents` | **Enrôlement** : identifiant, prénom, nom, ministère, fonction |
| `POST` | `/api/agents/{identifiant}/revoke` | **Révocation**, avec motif obligatoire |
| `GET` | `/api/journal` | Journal d'audit (100 dernières opérations) |

**Enrôlement.** L'agent est créé dans Keycloak, membre du groupe `agents`, avec un mot de passe
provisoire affiché une seule fois à l'administrateur (jamais journalisé). À sa première connexion,
Keycloak lui impose de choisir son mot de passe et de configurer son TOTP. Son compte de messagerie
est créé par Synapse à cette première connexion. Si l'ajout au groupe échoue, le compte est supprimé :
pas de compte à moitié créé.

**Révocation**, dans cet ordre :

1. compte Keycloak désactivé (plus aucune reconnexion possible) ;
2. sessions Keycloak fermées (et déconnexion back-channel vers Synapse) ;
3. photo des salons de l'agent ;
4. compte Synapse désactivé : jetons invalidés, appareils et clés supprimés, retrait de tous les salons ;
5. vérification : plus aucun appareil ni salon ; rapport étape par étape et entrée au journal.

**Garde-fous et sécurité de la console.**

- Un administrateur ne peut pas se révoquer lui-même ; les comptes de service (`console-admin*`,
  `service-account-*`) ne peuvent être ni créés ni révoqués.
- Chaque requête porte le jeton d'accès Keycloak de l'administrateur : la console en vérifie la
  signature, l'émetteur, l'audience, l'expiration et le groupe `admin-messagerie` (PyJWT, algorithme imposé).
- Caddy supprime tout en-tête `X-Forwarded-Access-Token` envoyé par le navigateur ; seul
  oauth2-proxy le fournit.
- Les actions (`POST`) venant d'une autre origine que la console sont refusées (protection CSRF),
  et le cookie de session est `SameSite=Lax`.
- Politique CSP `default-src 'self'` : aucun script en ligne, aucune ressource extérieure. La page
  d'erreur d'oauth2-proxy est remplacée, car celle par défaut charge sa feuille de style depuis cdnjs.
- Le jeton d'oauth2-proxy est renouvelé chaque minute : un administrateur révoqué perd l'accès à la console.
- Conteneur sans privilèges (utilisateur `console`), aucun port publié.

**Jeton Synapse (contrat C2).** Au premier besoin, la console crée elle-même un compte de service
administrateur `@console-admin` avec `SYNAPSE_REGISTRATION_SHARED_SECRET`, puis conserve son jeton dans
le volume `console_data`. Si ce jeton est perdu alors que le compte existe, elle crée un compte
`console-admin-xxxxxx`. Aucune copie de jeton à la main.

## 5. Tests

Dans le conteneur, pile démarrée (tests unitaires et tests d'intégration contre le vrai Synapse) :

```bash
docker compose exec console-admin python -m pytest -q
```

En local, sans Docker (les tests d'intégration sont alors ignorés) :

```bash
cd identite/console-admin
python -m venv .venv && .venv/bin/pip install -r requirements-tests.txt
.venv/bin/python -m pytest -q
```

| Fichier | Ce qui est vérifié |
|---|---|
| `test_auth.py` | Jeton administrateur accepté ; agent simple, signature forgée, jeton modifié, non signé, expiré, autre émetteur, autre audience : refusés |
| `test_revocation.py` | Ordre des étapes (identité coupée avant la messagerie) ; agent inconnu : rien n'est touché |
| `test_enrolement.py` | Actions requises (mot de passe, TOTP) ; identifiant pris, réservé ou invalide ; pas de compte à moitié créé |
| `test_synapse_token.py` | Code HMAC conforme à Synapse ; jeton réutilisé ; reprise avec un compte suffixé |
| `test_app.py` | Auto-révocation et comptes de service refusés ; autre origine refusée ; identifiant invalide refusé |
| `test_integration_synapse.py` | Contre le vrai Synapse : révocation complète, ancien jeton refusé (`M_UNKNOWN_TOKEN`), agent retiré du salon, l'autre membre toujours présent, salon chiffré par défaut, inscription libre refusée |

## 6. Dépannage

| Symptôme | Cause probable |
|---|---|
| « Invalid redirect uri » chez Keycloak | `DOMAIN` ou `PUBLIC_SCHEME` différents entre le `.env` et le realm déjà importé (réimporter, voir section 3) |
| Retour dans Element avec `invalid_scope` | Synapse demande un scope inconnu de Keycloak (ex. `groups`) : les groupes viennent du mappeur du client, pas d'un scope |
| Synapse : `"issuer" MUST use "https" scheme` | `AUTHLIB_INSECURE_TRANSPORT` absent du service `synapse` (appels internes en http) |
| « Cookie de redémarrage non trouvé » chez Keycloak | Démonstration en `http` sur une adresse autre que `*.localhost` : les navigateurs refusent alors les cookies sécurisés de Keycloak. Passer en `https` |
| Erreur `M_LIMIT_EXCEEDED` à la connexion | Limite de connexions par adresse de Synapse (`rc_login`) |
| Console : boucle de connexion | `OAUTH2_PROXY_COOKIE_SECURE=true` alors que la démonstration est en `http` |
| Console : 403 « Accès réservé » | Le compte n'est pas dans le groupe `admin-messagerie` |
| Erreur de secret au retour dans Element | `OIDC_SYNAPSE_SECRET` différent entre le `.env` et le realm déjà importé |

## 7. Limites

- La console technique de Keycloak (`auth.<DOMAIN>/admin`) reste accessible pour la démonstration ;
  en production, la réserver au réseau d'administration.
- La réintégration d'un agent révoqué se fait à la main (Keycloak et Synapse).
- La compromission de Keycloak équivaut à celle de toutes les identités : c'est le composant à
  protéger en priorité (sauvegardes, accès restreint, mises à jour).
