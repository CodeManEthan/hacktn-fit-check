#!/usr/bin/env bash
# Run ON the OCI Compute instance (Oracle Linux). Idempotent.
#   curl -fsSL https://raw.githubusercontent.com/SakxamShrestha/hacktn-fit-check/main/deploy/bootstrap.sh | bash
set -euo pipefail

REPO="https://github.com/SakxamShrestha/hacktn-fit-check.git"
APP_DIR="/opt/fitcheck"
SERVICE="fitcheck"
PORT="8000"

echo "==> packages"
sudo dnf install -y git python3.12 python3.12-pip >/dev/null 2>&1 || sudo yum install -y git python3 >/dev/null

echo "==> code"
if [ -d "$APP_DIR/.git" ]; then
  sudo git -C "$APP_DIR" pull --ff-only
else
  sudo git clone "$REPO" "$APP_DIR"
fi
sudo chown -R "$(whoami)":"$(whoami)" "$APP_DIR"

echo "==> virtualenv"
cd "$APP_DIR"
python3.12 -m venv .venv 2>/dev/null || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

echo "==> environment"
if [ ! -f "$APP_DIR/.env" ]; then
  echo "!! $APP_DIR/.env is missing. Create it with DATABASE_URL before starting."
  echo "   DATABASE_URL=mysql+pymysql://USER:PASSWORD@10.0.1.106:3306/fitcheck"
  exit 1
fi

echo "==> firewall"
sudo firewall-cmd --permanent --add-port=${PORT}/tcp >/dev/null 2>&1 || true
sudo firewall-cmd --permanent --add-service=http >/dev/null 2>&1 || true
sudo firewall-cmd --reload >/dev/null 2>&1 || true

echo "==> service"
sudo tee /etc/systemd/system/${SERVICE}.service >/dev/null <<UNIT
[Unit]
Description=Fit-Check donation screening
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now ${SERVICE}
sleep 3
sudo systemctl --no-pager --lines=5 status ${SERVICE} || true

echo "==> seeding reference and demo data"
cd "$APP_DIR" && ./.venv/bin/python -m scripts.seed_demo || echo "(seed skipped)"

echo
echo "Done. http://$(curl -s -m 5 ifconfig.me || echo '<public-ip>'):${PORT}/"
