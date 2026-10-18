#!/usr/bin/env bash
# Redeploy a head of the fork to the VM: stage it on localhost:8001 and check it,
# then cut the live app over to it, seed, and run the public checks from here. Run
# from a machine that holds the VM's ssh key. Every step is safe to rerun.
#
#   deploy/redeploy.sh stage    <head>   # design 9 step 2: /opt/fitcheck-next on 127.0.0.1:8001
#   deploy/redeploy.sh cutover  <head>   # step 3: swap into /opt/fitcheck, restart, seed, check
#   deploy/redeploy.sh all      <head>   # stage, then cutover if the stage checks pass
#   deploy/redeploy.sh rollback          # put the previous checkout (and Caddyfile) back
#   deploy/redeploy.sh check             # rerun the public checks only (no seed)
#
# <head> is a commit on the fork's main (a full hash is best). The VM's environment
# lives in /opt/fitcheck/.env, written by hand and never by this script; stage copies
# it into the new checkout and cutover copies it again just before the swap, so an
# edit made in between is kept. A failed public check rolls back by itself.
#
# Public checks (design 9.1): always against http://<VM IP>, the plain-HTTP door that
# needs no DNS; then against https://fitcheck.care only when that name resolves here
# (getent ahostsv4) to the VM's IP. A connection or TLS failure on the name (Caddy still
# waiting for its certificate) is a warning, not a rollback; a bad status or page is.
#
# Layout on the VM:
#   /opt/fitcheck        live checkout; unit `fitcheck` runs it on 127.0.0.1:8000
#   /opt/fitcheck-next   staged checkout; transient unit `fitcheck-stage`, 127.0.0.1:8001
#   /opt/fitcheck-prev   the checkout the last cutover replaced, kept for rollback
#   /etc/caddy/Caddyfile       deploy/Caddyfile; Caddy owns ports 80 and 443
#   /etc/caddy/Caddyfile.prev  the file the last cutover replaced, only if it replaced one
#
# Overrides: FITCHECK_HOST (opc@147.224.201.81), FITCHECK_KEY (~/.ssh/id_fitcheck_vm),
# FITCHECK_REPO (the fork), MODEL_RETRIES (3), MODEL_RETRY_WAIT (60 seconds),
# FITCHECK_PUBLIC_URL (checked in the hostname's place, with no resolve gate).
set -euo pipefail

HOST="${FITCHECK_HOST:-opc@147.224.201.81}"
KEY="${FITCHECK_KEY:-$HOME/.ssh/id_fitcheck_vm}"
REPO="${FITCHECK_REPO:-https://github.com/CodeManEthan/hacktn-fit-check.git}"
RETRIES="${MODEL_RETRIES:-3}"
RETRY_WAIT="${MODEL_RETRY_WAIT:-60}"
VM_ADDR="${HOST#*@}"             # 147.224.201.81 (a test may add :port)
VM_IP="${VM_ADDR%:*}"
IP_URL="http://$VM_ADDR"
PUBLIC_NAME="fitcheck.care"      # as written in deploy/Caddyfile; a test keeps them equal
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

usage() { sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }

vm() { ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$HOST" "$@"; }

# --- step 2: stage on 127.0.0.1:8001 ------------------------------------------------
stage() {
  local head="$1"
  echo "==> stage $head on the VM (127.0.0.1:8001)"
  vm bash -s -- "$REPO" "$head" "$RETRIES" "$RETRY_WAIT" <<'REMOTE'
set -euo pipefail
REPO="$1"; HEAD="$2"; RETRIES="$3"; WAIT="$4"
NEXT=/opt/fitcheck-next; LIVE=/opt/fitcheck; PORT=8001; UNIT=fitcheck-stage
sudo systemctl stop "$UNIT" >/dev/null 2>&1 || true
sudo systemctl reset-failed "$UNIT" >/dev/null 2>&1 || true
if [ ! -d "$NEXT/.git" ]; then
  sudo mkdir -p "$NEXT"
  sudo chown "$(id -un)":"$(id -gn)" "$NEXT"
  git clone --quiet "$REPO" "$NEXT"
fi
git -C "$NEXT" remote set-url origin "$REPO"
git -C "$NEXT" fetch --quiet origin
git -C "$NEXT" merge-base --is-ancestor "$HEAD" origin/main 2>/dev/null \
  || { echo "!! $HEAD is not on the fork's main; the live site is untouched"; exit 1; }
git -C "$NEXT" checkout --quiet --detach "$HEAD"
echo "checkout: $(git -C "$NEXT" log --oneline -1)"
[ -x "$NEXT/.venv/bin/python" ] || python3.12 -m venv "$NEXT/.venv"
"$NEXT/.venv/bin/pip" install --quiet --upgrade pip
"$NEXT/.venv/bin/pip" install --quiet -r "$NEXT/requirements.txt"
echo "venv: $("$NEXT/.venv/bin/python" --version), oci $("$NEXT/.venv/bin/python" -c 'import oci; print(oci.__version__)')"
install -m 600 "$LIVE/.env" "$NEXT/.env"
# Our own leftover unit was stopped above, so anything on the port now is foreign.
# The new unit could not bind, and the checks would pass against the old process.
if [ -n "$(ss -Hltn "sport = :$PORT")" ]; then
  echo "!! something already listens on port $PORT; the live site is untouched. Stop it first:"
  sudo ss -Hltnp "sport = :$PORT" || true
  exit 1
fi
sudo systemd-run --quiet --unit="$UNIT" -p User="$(id -un)" -p WorkingDirectory="$NEXT" \
  -p EnvironmentFile="$NEXT/.env" \
  "$NEXT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
for _ in $(seq 1 40); do
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break
  sleep 1
done
fail=0
for path in / /offer /impact; do
  code=$(curl -s -m 120 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$path" || true)
  echo "GET 127.0.0.1:$PORT$path -> $code"
  [ "$code" = 200 ] || fail=1
done
# One model call. The naming step always asks the model, so rows back mean the
# instance principal reached Generative AI even when the library supplies the facts.
rows=0
for attempt in $(seq 1 "$RETRIES"); do
  out=$(curl -s -m 180 -X POST "http://127.0.0.1:$PORT/offer/extract" -F spec_text='Drive DeVilbiss 525DS' || true)
  summary=$(printf '%s' "$out" | "$NEXT/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("rows") or []), "|", (d.get("message") or "")[:240])' 2>/dev/null || echo "0 | unreadable: ${out:0:240}")
  rows="${summary%% *}"
  echo "POST 127.0.0.1:$PORT/offer/extract (try $attempt) -> rows $summary"
  if [ "$rows" -gt 0 ]; then break; fi
  if [ "$attempt" -lt "$RETRIES" ]; then sleep "$WAIT"; fi
done
[ "$rows" -gt 0 ] || fail=1
state=$(systemctl is-active "$UNIT" || true)
echo "unit $UNIT: $state"
[ "$state" = active ] || fail=1
if [ "$fail" != 0 ]; then
  echo "!! stage checks failed; the live site is untouched. Journal:"
  sudo journalctl -u "$UNIT" --no-pager -n 30 || true
  exit 1
fi
echo "stage OK at $(git -C "$NEXT" rev-parse HEAD)"
REMOTE
}

# --- public checks, from this machine ----------------------------------------------
# Both halves against one base URL: the curl loop, then deploy/check_live.py.
# $1: the base. $2: the seed's output, which names the request and offer ids.
check_base() {
  local base="$1" seed_out="$2" fail=0 code path
  for path in / /offer /impact; do
    code=$(curl -s -m 60 -o /dev/null -w '%{http_code}' "$base$path" || true)
    echo "GET $base$path -> $code"
    [ "$code" = 200 ] || fail=1
  done
  python3 "$HERE/check_live.py" "$base" <<<"$seed_out" || fail=1
  return "$fail"
}

# Probe the hostname's base before checking it. check_live.py exits 1 alike for a
# refused connection and for a bad page, so curl's exit code tells them apart first.
# Returns 1, after a warning naming the cause, on a connect, timeout or TLS failure:
# the certificate may not be issued yet, and rolling back the app cannot fix that.
# Returns 0 (run the full checks, every failure real) on any other outcome.
probe_name() {
  local base="$1" rc=0 msg cause
  msg=$(curl -sS -o /dev/null -m 20 "$base/" 2>&1) || rc=$?
  case "$rc" in
    0) return 0 ;;
    6) cause="the name does not resolve" ;;
    7) cause="nothing accepted the connection" ;;
    28) cause="the connection timed out" ;;
    35) cause="the TLS handshake failed (Caddy may not hold the certificate yet)" ;;
    51|60) cause="the certificate is not valid for the name (not issued yet?)" ;;
    *) echo "probe of $base/ ended with curl exit $rc; running the full checks"
       return 0 ;;
  esac
  echo "!! warning: $base not checked: $cause; curl exit $rc: ${msg:-no message}"
  echo "   not rolling back for this. On the VM: sudo journalctl -u caddy --no-pager -n 50"
  return 1
}

# $1: the seed's output. Without it the ids are read (not written) on the VM by
# deploy/demo_ids.py.
check_live() {
  local seed_out="${1:-}" fail=0 name_url addrs addr resolved=0
  if [ -z "$seed_out" ]; then
    seed_out=$(vm 'cd /opt/fitcheck && ./.venv/bin/python -' < "$HERE/demo_ids.py") || true
  fi
  check_base "$IP_URL" "$seed_out" || fail=1
  if [ -n "${FITCHECK_PUBLIC_URL:-}" ]; then
    name_url="${FITCHECK_PUBLIC_URL%/}"
  else
    # ahostsv4, not hosts: with an AAAA record, `getent hosts` prints only the IPv6
    # addresses and the VM's IPv4 never matches. One line of output whatever it prints.
    addrs=$(getent ahostsv4 "$PUBLIC_NAME" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' | sed 's/ *$//' || true)
    for addr in $addrs; do
      if [ "$addr" = "$VM_IP" ]; then resolved=1; fi
    done
    if [ "$resolved" != 1 ]; then
      echo "-- hostname check skipped: $PUBLIC_NAME does not resolve to $VM_IP from here (getent ahostsv4 gave: ${addrs:-nothing})"
      return "$fail"
    fi
    name_url="https://$PUBLIC_NAME"
  fi
  echo "-- hostname check against $name_url"
  if probe_name "$name_url"; then
    check_base "$name_url" "$seed_out" || fail=1
  fi
  return "$fail"
}

# --- step 3: cut over --------------------------------------------------------------
cutover() {
  local head="$1" seed_out
  echo "==> cut over to $head"
  # The Caddyfile travels as one base64 word, so the heredoc can stay the script on stdin.
  vm bash -s -- "$head" "$(base64 -w0 "$HERE/Caddyfile")" <<'REMOTE'
set -euo pipefail
HEAD="$1"; CADDYFILE_B64="$2"
NEXT=/opt/fitcheck-next; LIVE=/opt/fitcheck; PREV=/opt/fitcheck-prev; CADDY_DIR=/etc/caddy
want() { git -C "$1" rev-parse --verify --quiet "$HEAD^{commit}" || echo none; }
# Install deploy/Caddyfile only when it differs from the live one. Runs before the app
# is stopped, so a file that fails validation exits with the old head still live.
caddyfile() {
  local new="$CADDY_DIR/Caddyfile.new" cf="$CADDY_DIR/Caddyfile" prev="$CADDY_DIR/Caddyfile.prev"
  local caddy
  caddy="$(command -v caddy || echo /usr/local/bin/caddy)"
  if ! sudo test -d "$CADDY_DIR" || [ ! -x "$caddy" ]; then
    echo "!! Caddy is not installed on the VM (no $CADDY_DIR or no caddy binary): run deploy/bootstrap.sh first; nothing changed, the old head is still live"
    exit 1
  fi
  printf '%s' "$CADDYFILE_B64" | base64 -d | sudo tee "$new" >/dev/null
  sudo chmod 644 "$new"
  if sudo cmp -s "$new" "$cf"; then
    sudo rm -f "$new"
    if sudo test -e "$prev"; then
      sudo rm -f "$prev"
      echo "Caddyfile unchanged; removed a stale $prev"
    else
      echo "Caddyfile unchanged; Caddy left alone"
    fi
    return 0
  fi
  if ! sudo -u caddy "$caddy" validate --config "$new" --adapter caddyfile >/dev/null 2>"$HOME/caddy-validate.log"; then
    cat "$HOME/caddy-validate.log"
    sudo rm -f "$new"
    echo "!! the new Caddyfile does not validate; nothing changed, the old head is still live"
    exit 1
  fi
  if sudo test -e "$cf"; then sudo cp -p "$cf" "$prev"; else sudo rm -f "$prev"; fi
  sudo mv "$new" "$cf"
  if ! sudo systemctl reload-or-restart caddy; then
    if sudo test -e "$prev"; then sudo mv "$prev" "$cf"; fi
    sudo systemctl reload-or-restart caddy || true
    echo "!! Caddy would not take the new Caddyfile; the old one is back, the old head is still live"
    exit 1
  fi
  echo "Caddyfile: installed the new one and reloaded Caddy; the old one is at $prev"
}
if [ "$(git -C "$LIVE" rev-parse HEAD)" = "$(want "$LIVE")" ]; then
  # Swapping a copy of the live head in would push the real rollback out of $PREV.
  sudo systemctl stop fitcheck-stage >/dev/null 2>&1 || true
  if [ -e "$NEXT" ]; then
    sudo rm -rf /opt/fitcheck-next
    echo "removed the staged copy at $NEXT: it is the head already live"
  fi
  caddyfile
  echo "live is already at $HEAD: restart only; $PREV keeps the previous checkout"
  sudo systemctl restart fitcheck
else
  [ -d "$NEXT/.git" ] || { echo "!! nothing staged at $NEXT; run stage first"; exit 1; }
  [ "$(git -C "$NEXT" rev-parse HEAD)" = "$(want "$NEXT")" ] \
    || { echo "!! $NEXT is not at $HEAD; run stage again"; exit 1; }
  sudo systemctl stop fitcheck-stage >/dev/null 2>&1 || true
  install -m 600 "$LIVE/.env" "$NEXT/.env"
  caddyfile
  sudo systemctl stop fitcheck
  sudo rm -rf /opt/fitcheck-prev
  sudo mv "$LIVE" "$PREV"
  sudo mv "$NEXT" "$LIVE"
  sudo systemctl start fitcheck
fi
for _ in $(seq 1 40); do
  curl -fs -o /dev/null http://127.0.0.1:8000/ && break
  sleep 1
done
echo "live: $(git -C "$LIVE" log --oneline -1); unit $(systemctl is-active fitcheck)"
REMOTE
  echo "==> seed (scripts/seed_demo.py upserts; safe to rerun)"
  if ! seed_out=$(vm 'cd /opt/fitcheck && ./.venv/bin/python -m scripts.seed_demo'); then
    printf '%s\n' "$seed_out"
    echo "!! seed failed; rolling back"
    rollback
    return 1
  fi
  printf '%s\n' "$seed_out"
  echo "==> checks from here"
  if ! check_live "$seed_out"; then
    echo "!! public checks failed; rolling back"
    rollback
    return 1
  fi
  echo "cutover OK"
}

rollback() {
  echo "==> rollback: the previous checkout back live (rerun the public checks with: check)"
  vm bash -s <<'REMOTE'
set -euo pipefail
LIVE=/opt/fitcheck; PREV=/opt/fitcheck-prev; FAILED=/opt/fitcheck-failed; CADDY_DIR=/etc/caddy
[ -d "$PREV" ] || { echo "!! no $PREV to roll back to"; exit 1; }
sudo systemctl stop fitcheck
sudo rm -rf /opt/fitcheck-failed
sudo mv "$LIVE" "$FAILED"
sudo mv "$PREV" "$LIVE"
sudo systemctl start fitcheck
for _ in $(seq 1 40); do
  curl -fs -o /dev/null http://127.0.0.1:8000/ && break
  sleep 1
done
echo "live: $(git -C "$LIVE" log --oneline -1); unit $(systemctl is-active fitcheck); failed checkout kept at $FAILED"
# Only a Caddyfile that the last cutover replaced has a .prev; otherwise Caddy is left alone.
if sudo test -e "$CADDY_DIR/Caddyfile.prev"; then
  sudo mv "$CADDY_DIR/Caddyfile.prev" "$CADDY_DIR/Caddyfile"
  sudo systemctl reload-or-restart caddy
  echo "Caddyfile: the previous one is back and Caddy reloaded"
fi
REMOTE
}

cmd="${1:-}"
head="${2:-}"
case "$cmd" in
  stage)    [ -n "$head" ] || usage; stage "$head" ;;
  cutover)  [ -n "$head" ] || usage; cutover "$head" ;;
  all)      [ -n "$head" ] || usage; stage "$head" && cutover "$head" ;;
  rollback) rollback ;;
  check)    echo "==> public checks (no seed)"; check_live ;;
  *)        usage ;;
esac
