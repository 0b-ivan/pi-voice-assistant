#!/bin/sh
# Sicherheitsstop: Diese bisherige Weiterleitung transportiert komplette
# Systemjournale und darf nicht mehr eingerichtet werden.
# Vorhandene Instanzen per docs/web-client.md abschalten.
set -eu
echo 'Proximus: Voll-Journal-Weiterleitung aus Sicherheitsgründen deaktiviert.' >&2
echo 'Bestehende Upload-Dienste und Empfänger gemäß docs/web-client.md stoppen.' >&2
exit 2
