#!/bin/sh
# Run inside a private dbus-run-session; secrets arrive through systemd credentials.
set -eu
umask 077
test -n "${CREDENTIALS_DIRECTORY:-}"
/usr/bin/gnome-keyring-daemon --unlock --components=secrets < "$CREDENTIALS_DIRECTORY/vault-password" >/dev/null
/opt/vox-agent/venv/bin/python - <<'PY'
import os
from pathlib import Path
import keyring
if type(keyring.get_keyring()).__module__ != 'keyring.backends.SecretService':
    raise SystemExit('OS vault unavailable')
root = Path(os.environ['CREDENTIALS_DIRECTORY'])
for account, name in [('feishu-app', 'vox-app'), ('vox-bootstrap', 'vox-bootstrap')]:
    value = (root / name).read_text().strip()
    if not value:
        raise SystemExit('Empty service credential')
    keyring.set_password('vox-agent-backup', account, value)
print('OS vault ready', flush=True)
PY
exec /opt/vox-agent/venv/bin/python -m engine.backup_runtime --config /etc/vox-team/runtime.json --host 127.0.0.1 --port 8766
