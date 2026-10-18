#!/usr/bin/env bash
# Redeploy a head of the fork to the VM: stage it on localhost:8001 and check it,
# then cut port 80 over to it, seed, and check again from here. Run from a machine
# that holds the VM's ssh key. Every step is safe to rerun.
#
#   deploy/redeploy.sh stage    <head>   # design 9 step 2: /opt/fitcheck-next on 127.0.0.1:8001
#   deploy/redeploy.sh cutover  <head>   # step 3: swap into /opt/fitcheck, restart, seed, check
#   deploy/redeploy.sh all      <head>   # stage, then cutover if the stage checks pass
#   deploy/redeploy.sh rollback          # put the previous checkout back on port 80
#   deploy/redeploy.sh check             # rerun the port-80 checks only (no seed)
#
# <head> is a commit on the fork's main (a full hash is best). The VM's environment
# lives in /opt/fitcheck/.env, written by hand and never by this script; stage copies
# it into the new checkout and cutover copies it again just before the swap, so an
# edit made in between is kept. A failed port-80 check rolls back by itself.
#
# Layout on the VM:
#   /opt/fitcheck        live checkout; unit `fitcheck` runs it on port 80
#   /opt/fitcheck-next   staged checkout; transient unit `fitcheck-stage`, 127.0.0.1:8001
#   /opt/fitcheck-prev   the checkout the last cutover replaced, kept for rollback
#
# Overrides: FITCHECK_HOST (opc@147.224.201.81), FITCHECK_KEY (~/.ssh/id_fitcheck_vm),
# FITCHECK_REPO (the fork), MODEL_RETRIES (3), MODEL_RETRY_WAIT (60 seconds).
set -euo pipefail

HOST="${FITCHECK_HOST:-opc@147.224.201.81}"
KEY="${FITCHECK_KEY:-$HOME/.ssh/id_fitcheck_vm}"
REPO="${FITCHECK_REPO:-https://github.com/CodeManEthan/hacktn-fit-check.git}"
RETRIES="${MODEL_RETRIES:-3}"
RETRY_WAIT="${MODEL_RETRY_WAIT:-60}"
PUBLIC_URL="http://${HOST#*@}"
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

usage() { sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }

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
sudo systemd-run --quiet --unit="$UNIT" -p User="$(id -un)" -p WorkingDirectory="$NEXT" \
  -p EnvironmentFile="$NEXT/.env" \
  "$NEXT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
for _ in $(seq 1 40); do
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break
  sleep 1
done
fail=0
for path in / /offer /impact; do
  code=$(curl -s -m 120 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$path")
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
if [ "$fail" != 0 ]; then
  echo "!! stage checks failed; the live site is untouched. Journal:"
  sudo journalctl -u "$UNIT" --no-pager -n 30 || true
  exit 1
fi
echo "stage OK at $(git -C "$NEXT" rev-parse HEAD)"
REMOTE
}

# --- port-80 checks, from this machine ----------------------------------------------
# $1: the seed's output, which names the request and offer ids. Without it the ids
# are read (not written) on the VM by deploy/demo_ids.py.
check_live() {
  local seed_out="${1:-}" fail=0 code path
  for path in / /offer /impact; do
    code=$(curl -s -m 60 -o /dev/null -w '%{http_code}' "$PUBLIC_URL$path")
    echo "GET $PUBLIC_URL$path -> $code"
    [ "$code" = 200 ] || fail=1
  done
  if [ -z "$seed_out" ]; then
    seed_out=$(vm 'cd /opt/fitcheck && ./.venv/bin/python -' < "$HERE/demo_ids.py") || true
  fi
  python3 "$HERE/check_live.py" "$PUBLIC_URL" <<<"$seed_out" || fail=1
  return "$fail"
}

# --- step 3: cut over port 80 --------------------------------------------------------
cutover() {
  local head="$1" seed_out
  echo "==> cut over port 80 to $head"
  vm bash -s -- "$head" <<'REMOTE'
set -euo pipefail
HEAD="$1"; NEXT=/opt/fitcheck-next; LIVE=/opt/fitcheck; PREV=/opt/fitcheck-prev
want() { git -C "$1" rev-parse --verify --quiet "$HEAD^{commit}" || echo none; }
if [ ! -d "$NEXT/.git" ] && [ "$(git -C "$LIVE" rev-parse HEAD)" = "$(want "$LIVE")" ]; then
  echo "live is already at $HEAD and nothing is staged: restart only"
  sudo systemctl restart fitcheck
else
  [ -d "$NEXT/.git" ] || { echo "!! nothing staged at $NEXT; run stage first"; exit 1; }
  [ "$(git -C "$NEXT" rev-parse HEAD)" = "$(want "$NEXT")" ] \
    || { echo "!! $NEXT is not at $HEAD; run stage again"; exit 1; }
  sudo systemctl stop fitcheck-stage >/dev/null 2>&1 || true
  install -m 600 "$LIVE/.env" "$NEXT/.env"
  sudo systemctl stop fitcheck
  sudo rm -rf /opt/fitcheck-prev
  sudo mv "$LIVE" "$PREV"
  sudo mv "$NEXT" "$LIVE"
  sudo systemctl start fitcheck
fi
for _ in $(seq 1 40); do
  curl -fs -o /dev/null http://127.0.0.1:80/ && break
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
    echo "!! port-80 checks failed; rolling back"
    rollback
    return 1
  fi
  echo "cutover OK"
}

rollback() {
  echo "==> rollback: the previous checkout back on port 80"
  vm bash -s <<'REMOTE'
set -euo pipefail
LIVE=/opt/fitcheck; PREV=/opt/fitcheck-prev; FAILED=/opt/fitcheck-failed
[ -d "$PREV" ] || { echo "!! no $PREV to roll back to"; exit 1; }
sudo systemctl stop fitcheck
sudo rm -rf /opt/fitcheck-failed
sudo mv "$LIVE" "$FAILED"
sudo mv "$PREV" "$LIVE"
sudo systemctl start fitcheck
for _ in $(seq 1 40); do
  curl -fs -o /dev/null http://127.0.0.1:80/ && break
  sleep 1
done
echo "live: $(git -C "$LIVE" log --oneline -1); unit $(systemctl is-active fitcheck); failed checkout kept at $FAILED"
REMOTE
}

cmd="${1:-}"
head="${2:-}"
case "$cmd" in
  stage)    [ -n "$head" ] || usage; stage "$head" ;;
  cutover)  [ -n "$head" ] || usage; cutover "$head" ;;
  all)      [ -n "$head" ] || usage; stage "$head" && cutover "$head" ;;
  rollback) rollback ;;
  check)    check_live ;;
  *)        usage ;;
esac
