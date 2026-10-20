#!/bin/sh
# Run as root inside CT 107. Installs the Nextcloud MCP server (pinned version)
# in its own venv and user, loopback only (docs/nextcloud.md, ADR 0005).
# Idempotent; the env file with the app password is never overwritten.
#
#   sh server/install-nextcloud-mcp.sh
set -eu
B=/opt/servitor-voice
V=$B/nextcloud-mcp
VERSION=0.198.5
SRC=$(cd "$(dirname "$0")" && pwd)
ENV=/etc/servitor-nextcloud-mcp.env

id servitor-mcp >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin servitor-mcp

if [ ! -x "$V/bin/nextcloud-mcp-server" ] || \
   [ "$("$V/bin/python" -c 'import importlib.metadata as m; print(m.version("nextcloud-mcp-server"))' 2>/dev/null)" != "$VERSION" ]; then
  python3 -m venv "$V"
  "$V/bin/pip" install --quiet --upgrade pip
  "$V/bin/pip" install --quiet "nextcloud-mcp-server==$VERSION"
fi

if [ ! -f "$ENV" ]; then
  umask 077
  cat > "$ENV" <<'EOF'
MCP_DEPLOYMENT_MODE=single_user_basic
NEXTCLOUD_HOST=https://cloud.obivan.org
NEXTCLOUD_USERNAME=
# Own app password (Nextcloud → Einstellungen → Sicherheit, Name "Proximus MCP")
NEXTCLOUD_PASSWORD=
EOF
  echo "Fill in $ENV (username, app password), then: systemctl enable --now servitor-nextcloud-mcp"
fi
chmod 600 "$ENV"

install -m 0644 "$SRC/servitor-nextcloud-mcp.service" /etc/systemd/system/
systemctl daemon-reload
echo "Then set SERVITOR_NEXTCLOUD_MCP_URL=http://127.0.0.1:8770/mcp in /etc/servitor-voice.env"
