#!/bin/sh
set -eu

if [ ! -f /data/homeserver.yaml ]; then
    python3 - "$@" <<'PY'
import os
from pathlib import Path

template = Path("/templates/homeserver.yaml.template").read_text()
values = {
    "DOMAIN": os.environ["DOMAIN"],
    "PUBLIC_SCHEME": os.environ["PUBLIC_SCHEME"],
    "SERVER_NAME": os.environ["SERVER_NAME"],
    "SYNAPSE_PASSWORD_LOGIN": os.environ["SYNAPSE_PASSWORD_LOGIN"].lower(),
    "OIDC_SYNAPSE_SECRET": os.environ["OIDC_SYNAPSE_SECRET"],
    "SYNAPSE_REGISTRATION_SHARED_SECRET": os.environ["SYNAPSE_REGISTRATION_SHARED_SECRET"],
    "POSTGRES_USER": os.environ["POSTGRES_USER"],
    "POSTGRES_PASSWORD": os.environ["POSTGRES_PASSWORD"],
    "POSTGRES_DB": os.environ["POSTGRES_DB"],
}
for key, value in values.items():
    template = template.replace("${" + key + "}", value)
Path("/data/homeserver.yaml").write_text(template)
PY
fi

if [ ! -f /data/signing.key ]; then
    python3 -m synapse.app.homeserver \
        --generate-keys \
        --config-path /data/homeserver.yaml
fi

exec python3 -m synapse.app.homeserver \
    --config-path /data/homeserver.yaml
