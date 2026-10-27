#!/bin/sh
# Container entrypoint.
#
# Render mounts Secret Files root-owned under /etc/secrets, where the unprivileged app
# user cannot read them. When started as root, copy the YouTube cookies file into an
# app-owned private location, then drop to the app user (no capabilities, no new
# privileges) before running the server. When already started as a non-root user, just
# run the command.
set -eu

APP_USER="${APP_USER:-app}"
SECRETS_DIR="${APP_SECRETS_DIR:-/app/secrets}"
COOKIES_SRC="${YTDLP_COOKIES_FILE:-/etc/secrets/cookies.txt}"

if [ "$(id -u)" = "0" ]; then
    if [ -f "$COOKIES_SRC" ]; then
        install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$SECRETS_DIR"
        install -m 0600 -o "$APP_USER" -g "$APP_USER" "$COOKIES_SRC" "$SECRETS_DIR/cookies.txt"
        export YTDLP_COOKIES_FILE="$SECRETS_DIR/cookies.txt"
    fi
    HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
    export HOME
    exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups --no-new-privs -- "$@"
fi

exec "$@"
