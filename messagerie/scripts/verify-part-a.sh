#!/bin/sh
set -eu

compose="docker compose --env-file .env.example"

$compose config -q
$compose ps --status running synapse synapse-db element caddy >/dev/null

element_status="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H 'Host: chat.localhost' http://127.0.0.1/)"
matrix_status="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H 'Host: matrix.localhost' http://127.0.0.1/_matrix/client/versions)"
admin_status="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H 'Host: matrix.localhost' http://127.0.0.1/_synapse/admin/v1/users)"

[ "$element_status" = "200" ] || {
    echo "Element returned HTTP $element_status" >&2
    exit 1
}
[ "$matrix_status" = "200" ] || {
    echo "Matrix returned HTTP $matrix_status" >&2
    exit 1
}
[ "$admin_status" = "403" ] || {
    echo "Admin API returned HTTP $admin_status, expected 403" >&2
    exit 1
}

echo "Part A routes and public admin protection: OK"
