#!/bin/sh
# Als root auf Debian/Raspberry Pi OS:
#   sh deploy/journal-forward/install.sh server [LAN-IP ...]
#   sh deploy/journal-forward/install.sh client http://172.22.9.107:19532
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
role=${1:-}
case "$role" in
    server)
        shift
        [ "$#" -gt 0 ] || set -- 172.22.9.107
        python3 - "$@" <<'PY'
import ipaddress, sys
for value in sys.argv[1:]:
    address = ipaddress.IPv4Address(value)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise SystemExit('Der Empfänger braucht konkrete private LAN-IPv4-Adressen.')
PY
        target="$*"
        ;;
    client)
        [ "$#" -eq 2 ] || { echo 'client braucht die URL des Empfängers.' >&2; exit 2; }
        target=$2
        python3 - "$target" <<'PY'
import re, sys, urllib.parse
url = sys.argv[1]
parts = urllib.parse.urlsplit(url)
if (parts.scheme not in ('http', 'https') or not parts.hostname
        or parts.username or parts.password or parts.query or parts.fragment
        or parts.path not in ('', '/') or not re.fullmatch(r'[A-Za-z0-9.:/\[\]-]+', url)):
    raise SystemExit('Erwartet: http[s]://Rechner:Port ohne Zugangsdaten oder Pfad.')
if parts.port is not None and not 1 <= parts.port <= 65535:
    raise SystemExit('Ungültiger Port.')
PY
        ;;
    *) echo "Aufruf: $0 server [LAN-IP ...] | client http[s]://Rechner:19532" >&2; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo 'Bitte als root ausführen.' >&2; exit 1; }

# Beide Programme werden in Debian vom Paket systemd-journal-remote geliefert.
if [ ! -x /usr/lib/systemd/systemd-journal-remote ] \
        || [ ! -x /usr/lib/systemd/systemd-journal-upload ]; then
    apt-get update
    apt-get install -y --no-install-recommends systemd-journal-remote
fi

if [ "$role" = server ]; then
    getent passwd servitor >/dev/null
    install -d -o systemd-journal-remote -g systemd-journal-remote -m 2750 /var/log/journal/remote
    install -d -m 0755 /etc/systemd/system/systemd-journal-remote.socket.d \
        /etc/systemd/system/systemd-journal-remote.service.d \
        /etc/systemd/system/servitor-voice.service.d /etc/systemd/journal-remote.conf.d
    {
        printf '[Socket]\nListenStream=\n'
        for address in $target; do printf 'ListenStream=%s:19532\n' "$address"; done
    } > /etc/systemd/system/systemd-journal-remote.socket.d/60-proximus.conf
    cat > /etc/systemd/system/systemd-journal-remote.service.d/60-proximus.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/lib/systemd/systemd-journal-remote --listen-http=-3 --output=/var/log/journal/remote/
UMask=0027
EOF
    cat > /etc/systemd/journal-remote.conf.d/60-proximus.conf <<'EOF'
[Remote]
SplitMode=host
MaxUse=128M
KeepFree=256M
MaxFileSize=16M
MaxFiles=16
EOF
    # Die Remote-Gruppe gibt es erst nach Installation des Pakets. Daher ein
    # Drop-in, keine Voraussetzung in der allgemeinen Sprachdienst-Unit.
    cat > /etc/systemd/system/servitor-voice.service.d/60-journal-forward.conf <<'EOF'
[Service]
SupplementaryGroups=systemd-journal systemd-journal-remote
EOF
    systemctl daemon-reload
    systemctl stop systemd-journal-remote.service
    systemctl enable systemd-journal-remote.socket
    systemctl restart systemd-journal-remote.socket
    systemctl restart servitor-voice.service
    echo "Journal-Empfänger an $target, Port 19532 (nur LAN; HTTP ist unverschlüsselt)."
else
    install -d -m 0755 /usr/local/libexec
    install -m 0755 "$here/seed-cursor.py" /usr/local/libexec/proximus-journal-cursor
    install -d -m 0755 /etc/systemd/journal-upload.conf.d \
        /etc/systemd/system/systemd-journal-upload.service.d
    cat > /etc/systemd/journal-upload.conf.d/60-proximus.conf <<EOF
[Upload]
URL=$target
EOF
    cat > /etc/systemd/system/systemd-journal-upload.service.d/60-proximus.conf <<'EOF'
[Service]
SupplementaryGroups=systemd-journal
StateDirectory=
RuntimeDirectory=proximus-journal-upload
RuntimeDirectoryMode=0750
RuntimeDirectoryPreserve=yes
ExecStartPre=/usr/local/libexec/proximus-journal-cursor
ExecStart=
ExecStart=/usr/lib/systemd/systemd-journal-upload --save-state=/run/proximus-journal-upload/state --follow
Restart=always
RestartSec=5
EOF
    systemctl daemon-reload
    systemctl enable systemd-journal-upload.service
    systemctl restart systemd-journal-upload.service
    echo "Journal-Weiterleitung an $target aktiv; Cursor im RAM, keine laufenden SD-Schreibzugriffe."
    case "$target" in http:*) echo 'HTTP ist unverschlüsselt: nur im vertrauten LAN verwenden.' ;; esac
fi
