#!/bin/sh
set -eu

if [ "${SYNAPSE_PASSWORD_LOGIN:-true}" != "true" ]; then
    echo "SYNAPSE_PASSWORD_LOGIN must be true in autonomous mode." >&2
    exit 1
fi

for user in awa.test issa.test; do
    docker compose --env-file .env.example exec -T synapse register_new_matrix_user \
        --config /data/homeserver.yaml \
        --user "$user" \
        --password "motdepasse-test" \
        --no-admin \
        --exists-ok \
        http://localhost:8008
done

echo "Demo users ready: @awa.test and @issa.test"
