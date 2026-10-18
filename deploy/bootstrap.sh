#!/usr/bin/env bash
# First install on a fresh OCI Compute instance (Oracle Linux 9). Idempotent.
# Run ON the instance, after /opt/fitcheck/.env exists (see deploy/README.md):
#   curl -fsSL https://raw.githubusercontent.com/CodeManEthan/hacktn-fit-check/main/deploy/bootstrap.sh | bash
# Later heads go out with deploy/redeploy.sh from a machine holding the ssh key.
# This script writes no secrets: the environment file is written by hand.
#
# Design 9.1: the app (unit `fitcheck`) listens on 127.0.0.1:8000 only; Caddy (unit
# `caddy`, user `caddy`) owns ports 80 and 443, serves https://fitcheck.care with a
# certificate it fetches itself, and proxies to the app. deploy/Caddyfile is its config.
set -euo pipefail

REPO="https://github.com/CodeManEthan/hacktn-fit-check.git"
APP_DIR="/opt/fitcheck"
ENV_FILE="${APP_DIR}/.env"
SERVICE="fitcheck"
BIND_HOST="127.0.0.1"
PORT="8000"
CADDY_VERSION="2.11.7"      # the Caddy project's binary release, linux_amd64
RUN_USER="$(id -un)"

echo "==> packages"
sudo dnf install -y git python3.12 python3.12-pip >/dev/null

echo "==> code"
if [ -d "$APP_DIR/.git" ]; then
  # redeploy.sh leaves the checkout detached, where pull refuses; move to main instead.
  git -C "$APP_DIR" fetch --quiet origin
  git -C "$APP_DIR" checkout --quiet --detach origin/main
else
  # The directory may already exist holding only .env. /opt is root's, so clone into
  # a temporary directory under $HOME, then move the checkout in.
  sudo mkdir -p "$APP_DIR"
  sudo chown "$RUN_USER":"$(id -gn)" "$APP_DIR"
  CLONE_TMP="$(mktemp -d "$HOME/fitcheck-clone.XXXXXX")"
  git clone --quiet "$REPO" "$CLONE_TMP/repo"
  shopt -s dotglob
  mv "$CLONE_TMP/repo"/* "$APP_DIR"/
  shopt -u dotglob
  rm -rf "$CLONE_TMP"
fi
echo "checkout: $(git -C "$APP_DIR" log --oneline -1)"

echo "==> virtualenv (python3.12)"
[ -x "$APP_DIR/.venv/bin/python" ] || python3.12 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> environment"
if [ ! -f "$ENV_FILE" ]; then
  echo "!! $ENV_FILE is missing. Write it first (deploy/README.md lists the keys):"
  echo "   DATABASE_URL=mysql+pymysql://<user>:<password>@<mysql private ip>:3306/fitcheck"
  echo "   OCI_AUTH=instance_principal"
  echo "   OCI_COMPARTMENT_ID=<the tenancy OCID from the console>"
  exit 1
fi
chmod 600 "$ENV_FILE"

echo "==> firewall (http and https, for Caddy)"
sudo firewall-cmd --permanent --add-service=http >/dev/null 2>&1 || true
sudo firewall-cmd --permanent --add-service=https >/dev/null 2>&1 || true
sudo firewall-cmd --reload >/dev/null 2>&1 || true

echo "==> service (${BIND_HOST}:${PORT}, behind Caddy)"
# Local bind, unprivileged port: no capability line. --proxy-headers is insurance (the
# app builds no absolute URL today): it trusts X-Forwarded-* from Caddy on 127.0.0.1 only.
sudo tee /etc/systemd/system/${SERVICE}.service >/dev/null <<UNIT
[Unit]
Description=Fit-Check donation screening
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host ${BIND_HOST} --port ${PORT} --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now ${SERVICE}
sudo systemctl restart ${SERVICE}
sleep 3
sudo systemctl --no-pager --lines=5 status ${SERVICE} || true

echo "==> caddy ${CADDY_VERSION}"
if [ "$(/usr/local/bin/caddy version 2>/dev/null | awk '{print $1}')" != "v${CADDY_VERSION}" ]; then
  CADDY_TMP="$(mktemp -d "$HOME/caddy-download.XXXXXX")"
  CADDY_URL="https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}"
  CADDY_TAR="caddy_${CADDY_VERSION}_linux_amd64.tar.gz"
  curl -fsSL -m 300 -o "$CADDY_TMP/$CADDY_TAR" "$CADDY_URL/$CADDY_TAR"
  curl -fsSL -m 60 -o "$CADDY_TMP/checksums.txt" "$CADDY_URL/caddy_${CADDY_VERSION}_checksums.txt"
  if ! (cd "$CADDY_TMP" && grep " ${CADDY_TAR}\$" checksums.txt | sha512sum -c --quiet -); then
    echo "!! $CADDY_TAR does not match the release's checksum file; Caddy not installed"
    exit 1
  fi
  tar -xzf "$CADDY_TMP/$CADDY_TAR" -C "$CADDY_TMP" caddy
  # install, not mv: a new file under /usr/local/bin gets that directory's SELinux label.
  sudo install -m 755 -o root -g root "$CADDY_TMP/caddy" /usr/local/bin/caddy
  rm -rf "$CADDY_TMP"
fi
/usr/local/bin/caddy version
getent group caddy >/dev/null || sudo groupadd --system caddy
id caddy >/dev/null 2>&1 || sudo useradd --system --gid caddy --create-home \
  --home-dir /var/lib/caddy --shell /usr/sbin/nologin --comment "Caddy web server" caddy
sudo install -d -m 755 /etc/caddy
sudo install -m 644 "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo -u caddy /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
# The Caddy project's reference unit (caddyserver/dist, init/caddy.service), with the
# binary at /usr/local/bin.
sudo tee /etc/systemd/system/caddy.service >/dev/null <<'UNIT'
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable caddy
sudo systemctl reload-or-restart caddy
sudo systemctl --no-pager --lines=5 status caddy || true

echo "==> demo seed (upserts; safe to rerun)"
(cd "$APP_DIR" && ./.venv/bin/python -m scripts.seed_demo) || echo "(seed failed; see above)"

echo
echo "Done. https://fitcheck.care/ answers once the name's A record points at this"
echo "instance and Caddy holds the certificate (sudo journalctl -u caddy)."
