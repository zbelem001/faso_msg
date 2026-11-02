# Démonstration

Tous les comptes, secrets et données de ce document sont **fictifs**.

## Avant de commencer

```bash
cp .env.example .env
docker compose up -d
```

Le premier démarrage prend environ deux minutes : Keycloak importe l'annuaire de démonstration et
Synapse génère sa clé de signature. Tout est prêt quand `docker compose ps` affiche `keycloak` et
`synapse` en `healthy`.

La démonstration fonctionne en `http` sur la machine locale, grâce aux adresses `*.localhost`. Pour
une démonstration depuis d'autres postes du réseau, passer en `https` : les navigateurs refusent
sinon les cookies sécurisés de Keycloak.

| Service | Adresse |
|---|---|
| Messagerie (Element) | http://chat.localhost |
| Console d'administration (enrôlement, révocation, journal d'audit) | http://admin.localhost (compte `admin.demo`) |
| Identité (console technique de Keycloak) | http://auth.localhost/admin (compte `admin`, mot de passe `KEYCLOAK_ADMIN_PASSWORD` du `.env`) |

## Comptes de démonstration

Mot de passe commun : **`Agent-Demo-2026`**

Le code TOTP est obligatoire. Le plus simple est de **scanner le QR code du compte** avec une
application d'authentification (Aegis, FreeOTP, ou toute application compatible RFC 6238). En
saisie manuelle : type « basé sur le temps », 6 chiffres, 30 secondes, SHA-1, et le secret ci-dessous.
L'heure du téléphone doit être exacte.

| Identifiant | Nom | Ministère, fonction | Groupes | Secret TOTP (fictif) | QR code |
|---|---|---|---|---|---|
| `awa.demo` | Awa Sawadogo | Santé, directrice des ressources humaines | `agents` | `KVTG6MKMKZWEM3ZQONBUORRYIY2TO43L` | [awa.demo.png](identite/totp-demo/awa.demo.png) |
| `issa.demo` | Issa Traoré | Économie et Finances, chef du service du budget | `agents` | `KEZWI42IG5WXKZ22PIYGOULZJRWUYZDD` | [issa.demo.png](identite/totp-demo/issa.demo.png) |
| `fatim.demo` | Fatimata Ouédraogo | Éducation nationale, conseillère technique | `agents` | `KRGTSWLGLJZDM3SMPFWGGN2DMZ3GM6TL` | [fatim.demo.png](identite/totp-demo/fatim.demo.png) |
| `admin.demo` | Salif Zongo | Transition digitale, administrateur de la messagerie | `agents`, `admin-messagerie` | `KV3GYMLQMZQUENLKHAYTIURVNE4HITRZ` | [admin.demo.png](identite/totp-demo/admin.demo.png) |
| `horsgroupe.demo` | Visiteur Hors groupe | Aucun | aucun | `GRFVEVLVJZXWO32VGE4VMQSKMRCTE4TQ` | [horsgroupe.demo.png](identite/totp-demo/horsgroupe.demo.png) |
| `jury.demo` | Membre du Jury | Jury du Hackathon 226 | `agents` | configuré avec le jury | |

- `horsgroupe.demo` existe dans l'annuaire mais n'est pas enrôlé comme agent : la messagerie le refuse.
- `jury.demo` n'a pas encore de TOTP : à sa première connexion, Keycloak affiche un QR code que le
  jury scanne avec son propre téléphone.
- Un code TOTP ne peut servir qu'une fois : pour une seconde connexion du même compte, attendre le code suivant.

## Scénario : échange chiffré

1. Dans un premier navigateur, ouvrir http://chat.localhost, cliquer sur « Compte agent de l'État »,
   se connecter avec `awa.demo` (mot de passe, puis code TOTP).
2. Dans une fenêtre privée ou un second navigateur, faire de même avec `issa.demo`.
3. Awa crée un salon (chiffré par défaut), invite `@issa.demo:messagerie.gov.bf` et envoie un message,
   un fichier et un message vocal.
4. Vérifier que la base ne contient que du contenu chiffré :
   ```bash
   docker compose exec synapse-db psql -U synapse -d synapse -c \
     "SELECT e.type, left(j.json::text, 120) FROM events e JOIN event_json j USING (event_id)
      WHERE e.type IN ('m.room.message', 'm.room.encrypted')
      ORDER BY e.stream_ordering DESC LIMIT 5;"
   ```
   On ne doit voir que des événements `m.room.encrypted`, avec un champ `ciphertext` illisible.

Les noms et sujets de salons ne sont pas chiffrés dans Matrix (métadonnée documentée) : utiliser des noms neutres.

## Scénario : révocation pendant une conversation de groupe

1. Awa, Issa et Fatimata sont dans un même salon et échangent des messages.
2. Dans un autre navigateur, ouvrir http://admin.localhost et se connecter avec `admin.demo`.
3. Cliquer sur « Révoquer » sur la ligne de Fatimata, saisir un motif, confirmer.
4. Le rapport s'affiche, toutes les étapes cochées, en une à deux secondes.
5. Fatimata est déconnectée et ne peut plus se reconnecter (« Compte désactivé »). Elle n'est plus
   membre du salon : Awa et Issa continuent sans elle, et les nouveaux messages sont chiffrés avec
   une nouvelle clé de groupe qu'elle ne reçoit pas.
6. L'onglet « Journal d'audit » garde la trace de l'opération : qui, quand, quel agent, quel motif.

## Scénario : enrôlement d'un nouvel agent

1. Dans la console, cliquer sur « Enrôler un agent », saisir prénom, nom, ministère et fonction ;
   l'identifiant est proposé (ex. `moussa.kabore`).
2. La console affiche une seule fois le mot de passe provisoire, à remettre à l'agent en main propre.
   L'agent apparaît « Première connexion en attente ».
3. L'agent se connecte sur http://chat.localhost : Keycloak lui impose de choisir son mot de passe,
   puis de configurer son code TOTP. Il entre ensuite dans la messagerie, en `@moussa.kabore:messagerie.gov.bf`.

## Scénario : intrus

| Tentative | Résultat attendu |
|---|---|
| Connexion avec `horsgroupe.demo` (compte existant, non enrôlé comme agent) | Refusée par la messagerie |
| Mot de passe correct, code TOTP faux | Refusée par Keycloak |
| Compte sans TOTP | Doit configurer un TOTP avant d'entrer |
| Agent révoqué | « Compte désactivé » |
| Agent simple (`issa.demo`) sur http://admin.localhost | Refusé : « Accès réservé » (403) |
| Inscription directe sur le serveur Matrix | `curl -X POST http://matrix.localhost/_matrix/client/v3/register -d '{}'` : 403, « Registration has been disabled » |
| API d'administration de Synapse depuis l'extérieur | `curl http://matrix.localhost/_synapse/admin/v1/server_version` : 403 |

## Tests automatisés

```bash
docker compose exec console-admin python -m pytest -q
```

Voir [identite/LISEZMOI.md](identite/LISEZMOI.md) (section 5) pour le détail des tests.
