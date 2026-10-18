# Deploying Fit-Check to OCI

The app runs on a Compute instance and talks to MySQL HeatWave over the VCN's
private subnet. HeatWave is not reachable from outside the VCN, so the app must
run on the instance; a laptop cannot point at the cloud database. Generative AI
(the naming, reading and card steps, the impact paragraph) and Language (the
packet's translation) are called from the instance with its own identity, the
instance principal, so the VM holds no API key.

## The stack

One Resource Manager stack in the operator's tenancy, region us-chicago-1, made
these:

| Resource | Address |
|---|---|
| Compute instance `fitcheckfitcheck-web`, Oracle Linux, python3.12 | 147.224.201.81 public |
| MySQL HeatWave, schema `fitcheck`, user `admin` | 10.0.1.229:3306, private subnet |
| VCN, public and private subnets | ingress 22, 80 and 443 only |
| Caddy 2.11.7 on the instance, unit `caddy`, user `caddy` | ports 80 and 443; `https://fitcheck.care`, and plain HTTP on 147.224.201.81 |
| The app on the instance, unit `fitcheck`, user `opc` | 127.0.0.1:8000 |

The app listens on 127.0.0.1:8000 as user `opc`, behind Caddy. It binds no
public port, so the unit carries no capability line, and it runs with
`--proxy-headers --forwarded-allow-ips 127.0.0.1` (insurance: the app builds no
absolute URL). Caddy owns ports 80 and 443: it serves https://fitcheck.care with
a Let's Encrypt certificate it fetches and renews itself, redirects plain HTTP
for that name with a 308, and proxies to 127.0.0.1:8000. Its config is
`deploy/Caddyfile`, the one file that names the public host. The bare IP still
answers on plain HTTP, with no redirect, as the compatibility door for old links
and for the deploy checks; no doc or slide gives it out. Port 8001, where a new
head is staged, is bound to 127.0.0.1 and checked over ssh; the VCN does not
open it. On the VM:

| Path | What |
|---|---|
| `/opt/fitcheck` | the live checkout, run by the systemd unit `fitcheck` |
| `/opt/fitcheck/.env` | the environment, mode 600, written by hand |
| `/opt/fitcheck-next` | a staged head (only while one is staged) |
| `/opt/fitcheck-prev` | the checkout the last cutover replaced, for rollback |
| `/etc/caddy/Caddyfile` | Caddy's config, from `deploy/Caddyfile` |
| `/etc/caddy/Caddyfile.prev` | the file the last cutover replaced, only if it changed it |

## The environment file

`/opt/fitcheck/.env` holds the keys below. Nothing in this repository writes it,
and no value in it belongs in the repository.

```
DATABASE_URL=mysql+pymysql://admin:<password>@10.0.1.229:3306/fitcheck
OCI_REGION=us-chicago-1
OCI_AUTH=instance_principal
OCI_COMPARTMENT_ID=<the tenancy OCID from the console>
REASONING_MODEL=openai.gpt-oss-120b
VISION_MODEL=meta.llama-4-maverick-17b-128e-instruct-fp8
GUDID_LOOKUP=on
```

`OCI_AUTH` is `api_key` (the default, reads `~/.oci/config`) or
`instance_principal`. Under `instance_principal` there is no config file to read
a tenancy from, so `OCI_COMPARTMENT_ID` must be set; the instance sits in the
tenancy root, so it is the tenancy OCID (Console, profile menu, Tenancy, OCID).
`GUDID_LOOKUP=on` lets the matches page ask the FDA's device registry.

## Instance principals, once per tenancy

1. **Dynamic group** `fitcheck-instances`, matching rule
   `instance.id = '<the instance OCID from the console>'`.
2. **Policy** `fitcheck-instances-ai` in the tenancy root, with:
   ```
   Allow dynamic-group fitcheck-instances to use generative-ai-family in tenancy
   Allow dynamic-group fitcheck-instances to use ai-service-language-family in tenancy
   ```
3. Set `OCI_AUTH=instance_principal` and `OCI_COMPARTMENT_ID` in the `.env`
   and restart the unit (`sudo systemctl restart fitcheck`).
4. Check one model call answers, then delete `/home/opc/.oci/` from the VM.

Steps 1 and 2 are scripted. From a machine with the operator's API key in
`~/.oci/config` and the OCI Python SDK (the repo's venv has it):

```
.venv/bin/python deploy/instance_principals.py --public-ip 147.224.201.81
```

It finds the instance by its public IP, creates the group and the policy or
reuses them, and prints names only. A new policy can take a minute to apply.

## First install on a new instance

1. Write `/opt/fitcheck/.env` as above.
2. Create the schema once from the instance:
   `mysql -h 10.0.1.229 -u admin -p -e "CREATE DATABASE IF NOT EXISTS fitcheck;"`
3. Run the bootstrap on the instance. It installs git and python3.12, clones
   the fork into `/opt/fitcheck`, builds the venv, opens http and https in
   firewalld, writes the unit for 127.0.0.1:8000 and starts it, installs Caddy
   (the project's release binary, checked against its sha512 file, to
   `/usr/local/bin/caddy`, with a `caddy` system user and the project's reference
   unit), installs `deploy/Caddyfile` to `/etc/caddy/Caddyfile`, validates and
   starts Caddy, and runs the demo seed:
   ```
   curl -fsSL https://raw.githubusercontent.com/CodeManEthan/hacktn-fit-check/main/deploy/bootstrap.sh | bash
   ```
   `/opt` belongs to root, so the clone goes to a temporary directory under
   `$HOME` and is moved into `/opt/fitcheck`. On an instance that already has the
   checkout, a rerun fetches and checks out the fork's main (detached, as
   `redeploy.sh` leaves it).

The app creates any missing table at startup. No build adds a column to an
existing table, so an older database keeps working.

## Redeploying a new head

From a machine with the VM's ssh key (`~/.ssh/id_fitcheck_vm`), after the head
is on the fork's main:

```
deploy/redeploy.sh stage <head>
deploy/redeploy.sh cutover <head>
```

`stage` clones or fetches the fork into `/opt/fitcheck-next`, checks out the
head, builds its venv, copies the `.env`, runs it as the transient unit
`fitcheck-stage` on 127.0.0.1:8001, and checks `/`, `/offer`, `/impact` and one
model call through `/offer/extract` (retried twice a minute apart, for a policy
still applying). It says `stage OK` only if those pass and the unit is still
active; otherwise it prints the unit's journal. The live site is not touched.
It refuses, and exits 1, in two cases:

- `!! <head> is not on the fork's main`: the head is on another branch of the
  fork or not in it at all. Merge it to main first.
- `!! something already listens on port 8001`: a process other than
  `fitcheck-stage` holds the port (it prints which). Stop it, then rerun.

`cutover` copies the `.env` again, stops the stage, installs `deploy/Caddyfile`
if it differs from `/etc/caddy/Caddyfile` (`caddy validate` first, the old file
kept at `Caddyfile.prev`, then `systemctl reload-or-restart caddy`, all before the
app is stopped; when it is unchanged, any stale `.prev` is removed and Caddy is
left alone), moves `/opt/fitcheck` to `/opt/fitcheck-prev` and the staged
checkout into `/opt/fitcheck`, restarts the unit, runs `scripts/seed_demo.py`
once (it upserts, so a rerun leaves one of each), and runs the public checks
from your machine: `/`, `/offer`, `/impact` answer 200, and the Kayunga request's
matches page shows the 525KS shipping, the 525DS under "Won't work" and the
older unit needing info (`deploy/check_live.py`). A failed check rolls back by
itself.

The public checks always run against the bare IP over plain HTTP, so they need
no DNS. They run against https://fitcheck.care as well only when
`getent ahostsv4 fitcheck.care` on your machine gives the VM's address; otherwise one
`-- hostname check skipped` line says so. On the name, a bad status or page rolls
back; a refused connection, a timeout or a TLS failure (no certificate yet) prints
a warning and does not. `FITCHECK_PUBLIC_URL`, if set, is checked in the name's
place with the same rules.

If the live checkout is already at `<head>` (a rerun of `stage` and `cutover` on
the head that is live), `cutover` does not swap: it stops the stage, removes
`/opt/fitcheck-next` and only restarts the unit, so `/opt/fitcheck-prev` keeps
the checkout from before that head.

`deploy/redeploy.sh all <head>` does both; `rollback` puts `/opt/fitcheck-prev`
back, and `/etc/caddy/Caddyfile.prev` too when the last cutover left one;
`check` reruns the public checks without seeding. A path that does not
answer at all (refused or timed out) prints `-> 000` and the run goes on to the
next path, in `check` and in `stage`.

A rollback to a head older than instance-principal auth (the overnight round's
B0 merge added it; 9e00968, the head before that round, is older) needs the API
key back in `/home/opc/.oci/`, because that code reads only the key file.

## When something is wrong

- `sudo journalctl -u fitcheck -n 50` on the VM shows the app's log.
- `sudo journalctl -u caddy -n 50` shows Caddy's log and the access log,
  certificate attempts included. Without an A record for fitcheck.care it logs
  `NXDOMAIN` and "will retry" on its own back-off; do not restart it to hurry it
  (Let's Encrypt limits failed validations per hour).
- A model call that answers "OCI is not configured" names the missing key.
  "NotAuthorizedOrNotFound" means the dynamic group or policy is missing or
  still applying.
- If the VM is gone: make a new instance from the stack, add its OCID to the
  dynamic group's rule (rerun `instance_principals.py` with the new IP), write the
  `.env`, run the bootstrap.
