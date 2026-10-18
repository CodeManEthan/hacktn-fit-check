# Deploy, auth, and what to do when it breaks

This page answers one question: how does the live demo run, how does it sign in to OCI without a key, and what do you type if it stops answering just before the pitch?

It is written from `deploy/README.md`, `deploy/redeploy.sh`, `deploy/bootstrap.sh`, `deploy/check_live.py`, `app/ai/client.py`, `app/reference/gudid.py` and the B7 build report. Every command below is one you run yourself, one per line.

## The stack

One Resource Manager stack in the operator's tenancy, region `us-chicago-1`:

| Resource | Address |
|---|---|
| Compute instance `fitcheckfitcheck-web`, Oracle Linux, python3.12 | 147.224.201.81, public |
| MySQL HeatWave, schema `fitcheck`, user `admin` | 10.0.1.229:3306, private subnet |
| VCN, public and private subnets | ingress on 22, 80 and 443 only |

The app is a FastAPI process run by uvicorn as user `opc` on port 80, under the systemd unit `fitcheck`. The unit has `AmbientCapabilities=CAP_NET_BIND_SERVICE` so an unprivileged user can bind port 80, `Restart=always`, and it is enabled at boot. HeatWave is reachable only inside the VCN, so the app has to run on the instance. A laptop cannot point at the cloud database.

Paths on the VM:

| Path | What |
|---|---|
| `/opt/fitcheck` | the live checkout, with its own `.venv` |
| `/opt/fitcheck/.env` | the environment, mode 600, written by hand |
| `/opt/fitcheck-next` | a staged head, only while one is staged |
| `/opt/fitcheck-prev` | the checkout the last cutover replaced, kept for rollback |
| `/opt/fitcheck-failed` | the checkout a rollback took off port 80, if there was one |

**What is live.** The VM serves the fork's main (`https://github.com/CodeManEthan/hacktn-fit-check`). The lead's redeploy log is the record of which commit went out. To see the live head yourself:

```
ssh -i /home/jpluto/.ssh/id_fitcheck_vm opc@147.224.201.81 'git -C /opt/fitcheck log --oneline -1'
```

## Auth: instance principals, no key on the VM

The app calls OCI Generative AI (naming, reading, the handover card, the impact paragraph) and OCI Language (the packet's translation). On the VM it signs those calls as the instance itself. There is no API key on the VM. B7 deleted `/home/opc/.oci/`, and a search for `oci_api_key*.pem` on the VM's disk found none.

What makes that work:

- **Dynamic group** `fitcheck-instances`, matching rule `instance.id = '<the instance OCID from the console>'`.
- **Policy** `fitcheck-instances-ai` in the tenancy root, two statements:
  ```
  Allow dynamic-group fitcheck-instances to use generative-ai-family in tenancy
  Allow dynamic-group fitcheck-instances to use ai-service-language-family in tenancy
  ```
- **`.env` on the VM** sets `OCI_AUTH=instance_principal` and `OCI_COMPARTMENT_ID` to the tenancy OCID from the console (the instance sits in the tenancy root).

`deploy/instance_principals.py` creates or reuses the group and the policy. It runs from a machine with the operator's API key, finds the instance by public IP, and prints names only:

```
.venv/bin/python deploy/instance_principals.py --public-ip 147.224.201.81
```

In the code, `build_signer_and_config` in `app/ai/client.py` is the one place both the chat client and the Language client get credentials. Under `OCI_AUTH=api_key` (the default on a laptop) it reads `~/.oci/config`. Under `instance_principal` it builds `InstancePrincipalsSecurityTokenSigner()` with only the region as config. A missing `OCI_COMPARTMENT_ID` is a `ConfigError` that names the variable, shown on the page rather than as a 500.

The `.env` keys, from `deploy/README.md`. No value belongs in the repository:

```
DATABASE_URL=mysql+pymysql://admin:<password>@10.0.1.229:3306/fitcheck
OCI_REGION=us-chicago-1
OCI_AUTH=instance_principal
OCI_COMPARTMENT_ID=<the tenancy OCID from the console>
REASONING_MODEL=openai.gpt-oss-120b
VISION_MODEL=meta.llama-4-maverick-17b-128e-instruct-fp8
GUDID_LOOKUP=on
```

If a model call answers "NotAuthorizedOrNotFound", the dynamic group or the policy is missing or still applying. A new policy can take a minute.

## Redeploying a head

`deploy/redeploy.sh` runs on this machine and drives the VM over ssh with the key `~/.ssh/id_fitcheck_vm`. Run it from a checkout of the repo. The head must already be on the fork's main.

| Command | What it does |
|---|---|
| `bash deploy/redeploy.sh stage <head>` | Fetches the fork into `/opt/fitcheck-next`, checks out `<head>`, builds the venv, copies `.env`, runs it as the transient unit `fitcheck-stage` on 127.0.0.1:8001, and checks `/`, `/offer`, `/impact` and one model call through `/offer/extract`, retried 3 times a minute apart. Says `stage OK` only if all pass and the unit is still active. The live site is not touched. |
| `bash deploy/redeploy.sh cutover <head>` | Copies `.env` again, stops the stage, moves `/opt/fitcheck` to `/opt/fitcheck-prev` and the staged checkout into `/opt/fitcheck`, restarts `fitcheck`, runs `scripts/seed_demo.py` once, then checks from this machine. A failed seed or check rolls back by itself. |
| `bash deploy/redeploy.sh all <head>` | `stage`, then `cutover` if the stage passed. |
| `bash deploy/redeploy.sh check` | Reruns the port-80 checks. Does not seed. |
| `bash deploy/redeploy.sh rollback` | Puts `/opt/fitcheck-prev` back on port 80. |

The port-80 check (`deploy/check_live.py`) wants `/`, `/offer` and `/impact` to answer 200, and the Kayunga request's matches page to show the 525KS shipping first, the 525DS under "Won't work", and the older unit needing info.

`stage` refuses, and exits 1 without touching the live site, when the head is not on the fork's main (`!! <head> is not on the fork's main`) or when something else listens on port 8001.

If the live checkout is already at `<head>`, `cutover` does not swap. It removes the staged copy and only restarts the unit, so `/opt/fitcheck-prev` keeps the checkout from before that head.

The seed upserts. Running it again leaves one of each offer, answer and request, and rewrites the twenty history verdicts.

## The rollback

```
bash deploy/redeploy.sh rollback
```

It stops `fitcheck`, moves the live checkout to `/opt/fitcheck-failed`, moves `/opt/fitcheck-prev` into `/opt/fitcheck`, and starts the unit. Three things to know:

- **It works once.** After it runs, `/opt/fitcheck-prev` no longer exists. A second `rollback` prints `!! no /opt/fitcheck-prev to roll back to`. To go forward again, redeploy a head with `all`.
- **It does not reseed.** The database is shared by every checkout. No build this round changed an existing table, so an older checkout reads the newer rows.
- **Not older than B0.** A head older than instance-principal auth, such as 9e00968, reads only the key file, which is gone. Rolling back that far loses every model call until the key is copied back. After the lead's cutover of 385bc8f, `/opt/fitcheck-prev` should hold e64eba6 (B6), which has instance-principal auth. Check before you rely on it:

```
ssh -i /home/jpluto/.ssh/id_fitcheck_vm opc@147.224.201.81 'git -C /opt/fitcheck-prev log --oneline -1'
```

## When the VM stops answering at 12:45

Work down the list and stop at the first step that brings it back. Run everything from the main checkout on this machine:

```
cd /home/jpluto/projects/hospital-surplus
```

**1. See what is down.** If this prints `200`, the site is up and the problem is in the page, not the server:

```
curl -s -m 10 -o /dev/null -w '%{http_code}\n' http://147.224.201.81/
```

```
bash deploy/redeploy.sh check
```

If `check` prints `-> 000` for every path, the app is not answering. Go to step 2. If the pages answer but a hero check prints `FAIL`, see "If B is missing from the matches page" below.

**2. Restart the app.** Log in, read the log, restart:

```
ssh -i /home/jpluto/.ssh/id_fitcheck_vm opc@147.224.201.81
```

On the VM:

```
sudo systemctl status fitcheck --no-pager
```

```
sudo journalctl -u fitcheck -n 50 --no-pager
```

```
sudo systemctl restart fitcheck
```

```
exit
```

Back on this machine:

```
bash deploy/redeploy.sh check
```

**3. If the restart does not hold and the log shows a code error from the new head**, roll back:

```
bash deploy/redeploy.sh rollback
```

```
bash deploy/redeploy.sh check
```

**4. If ssh times out**, the VM itself is down, not the app. In the OCI console, open Compute, Instances, `fitcheckfitcheck-web`, and press Reboot. The `fitcheck` unit starts at boot. When ssh answers again, rerun `bash deploy/redeploy.sh check`.

**5. If the VM will not come back in time, run the demo on this laptop.** Rebuilding a VM (a new instance from the stack, `instance_principals.py` with the new IP, the `.env`, `bootstrap.sh`; the last section of `deploy/README.md`) takes longer than fifteen minutes. Do not start it at 12:45. The main checkout's `.env` leaves `DATABASE_URL` blank, so the app uses the SQLite file `fitcheck.db` in the checkout, and signs model calls with the API key in `~/.oci/config`:

```
.venv/bin/python -m scripts.seed_demo
```

```
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000/. The ids differ from the VM's. Read the hero offers' ids and the Kayunga request id from the seed's output (`offer N added` or `replaced`, `request N`), and build the demo URLs from them: `/request/<request>/matches`, `/request/<request>/offer/<offer>`, `/packet/<offer B>?facility=uganda-district`. I checked this path on a scratch SQLite file: `/`, `/offer`, the matches page, the candidate page and `/offer/csv` answered 200, and the matches page showed "Ship with", "Won't work at Kayunga District Hospital" and "Needs info".

### If B is missing from the matches page

Pressing "Ask this donor" on the 525KS reserves its only unit. The offer then leaves the matches page, and `check_live.py` fails "B ships". A reseed does not undo it: the seed keeps an offer's reserved quantity when it replaces the offer. To release the unit, open the match page the button led to, follow its `/confirm/<token>` link, and answer no. That withdraws the match and gives the unit back.

Posting the sample CSV adds a second 525KS to the Kayunga matches page, and a reseed does not remove it either. Post the sample last, or on a scratch database.

## What `GUDID_LOOKUP=off` does

`app/reference/gudid.py` asks the FDA's device registry whether a model is still in commercial distribution, at most 5 seconds per call. A cold matches page makes one call per offer model, one after another. Each answer is cached in `registry_lookups` for 7 days. A failure is cached for 10 minutes.

`live_enabled()` reads `GUDID_LOOKUP`. With `off` (or `0`, `false`, `no`):

- No new call goes to the FDA.
- A cached answer under 7 days old is still used. The cache is read before the switch.
- A model with no cached answer gets "not looked up", which leaves manufacturer support unknown.
- **No verdict changes.** The support check fails only on a known "no", and GUDID never says no. What you lose is the registry link on the 525DS's support row, if it was not cached yet.

So turn it off when the FDA is slow and the matches page hangs on a cold load. Leave it on otherwise. On the VM:

```
ssh -i /home/jpluto/.ssh/id_fitcheck_vm opc@147.224.201.81
```

```
sudo sed -i 's/^GUDID_LOOKUP=.*/GUDID_LOOKUP=off/' /opt/fitcheck/.env
```

```
sudo systemctl restart fitcheck
```

The next `stage` and `cutover` copy this `.env` forward, so the setting stays until you change it back.

The comment in `.env.example` says that with the lookup off "every support row then reads needs-info unless the nonprofit recorded an answer". That is wrong. The support row reads the build year, so a device with a known year still passes or fails on age.

## Rough edges

- `bootstrap.sh` was never run against the live VM. B7 checked it by reading, with `bash -n` and with tests that run its code step against a fake `/opt`. It rewrites the unit file, which is why it was not run on the pitch host.
- The CSV screening cache lives in the app process's memory. A restart, a rollback or a cutover drops every held screening, and the result links give a clean 404. Upload the file again.
- The impact paragraph is cached in memory too. After a restart the first `/impact` load makes one model call.
- During a cutover the site is down for the seconds between `systemctl stop` and the new process answering. The script waits up to 40 seconds for it.
