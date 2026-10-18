#!/usr/bin/env bash
# First install on a fresh OCI Compute instance (Oracle Linux 9). Idempotent.
# Run ON the instance, after /opt/fitcheck/.env exists (see deploy/README.md):
#   curl -fsSL https://raw.githubusercontent.com/CodeManEthan/hacktn-fit-check/main/deploy/bootstrap.sh | bash
# Later heads go out with deploy/redeploy.sh from a machine holding the ssh key.
# This script writes no secrets: the environment file is written by hand.
set -euo pipefail

REPO="https://github.com/CodeManEthan/hacktn-fit-check.git"
APP_DIR="/opt/fitcheck"
ENV_FILE="${APP_DIR}/.env"
SERVICE="fitcheck"
PORT="80"
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

echo "==> firewall (port ${PORT})"
sudo firewall-cmd --permanent --add-service=http >/dev/null 2>&1 || true
sudo firewall-cmd --reload >/dev/null 2>&1 || true

echo "==> service"
# Port 80 is privileged; the capability lets the unprivileged user bind it.
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
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
AmbientCapabilities=CAP_NET_BIND_SERVICE
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

echo "==> demo seed (upserts; safe to rerun)"
(cd "$APP_DIR" && ./.venv/bin/python -m scripts.seed_demo) || echo "(seed failed; see above)"

echo
echo "Done. http://$(curl -s -m 5 ifconfig.me || echo '<public-ip>')/"
