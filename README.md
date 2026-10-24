# Fit-Check

A two-sided platform for medical equipment donations. Clinics state their real
conditions once. Donors post what they hold. Every offer is screened against the
receiving clinic before anyone books a container, and only what can actually
work is ranked by distance.

Built for HackTN, Track 1: From Surplus to Social Good.

**Live:** https://fitcheck.care

## The pipeline

1. **Filter.** Open offers of the requested type, with stock, not past expiry.
2. **Screen.** Fifteen constraint checks per candidate. A `won't work` offer is
   set aside here, before distance is considered.
3. **Rank.** Known verdicts first, then the fewest conditions (things that must
   ship alongside), then offers with a location, then great-circle distance.
   Each key is compared in turn, so no number of kilometres buys off a
   condition.
4. **Verify.** "Ask this donor" holds the stock against the request and gives
   the donor a link asking whether it still exists (shown on the page; a real
   deployment would email it). A "no" releases the stock.

Compatibility is a predicate, not a cost: a 60 Hz motor either runs on a 50 Hz
grid or it does not. Allocation comes after, when everything left fits and only
distance and quantity remain. Nothing ranks on a score that blends the two.

## The model researches, code judges

1. **The model reads.** It names a device from a nameplate photo or pasted
   text and, when the device library has no entry, copies facts out of what
   the donor supplied. It is told to return `null` rather than guess. Every
   fact on the page carries its source, such as `nameplate`, `spec sheet`,
   `model knowledge`, `IEC 60601-1 default`, `FDA GUDID`,
   `recorded by the nonprofit` or `unknown`.
2. **Code judges.** No model is called in `app/checks/`. Given the same facts,
   the verdict is the same every time.

The supply questions (are consumables sold in the country, can spare parts be
bought there, is the model still supported) are not answered from model
memory. They come from a live FDA GUDID lookup, which can only affirm, and from
answers the nonprofit records on `/supply`. Anything else stays unknown.

So the verdict cannot be hallucinated, only its inputs, and the UI shows where
each input came from. A missing fact becomes a question, never a guess. The
model also writes the packet's handover card and the `/impact` paragraph, each
gated by code against its source.

## Checks

Fifteen, from the WHO guidance *Medical device donations: considerations for
solicitation and provision* (Annex 1), IEC 60601-1 and Uganda's donated
equipment guidelines. All fifteen are code: fourteen in `ALL_CHECKS` and one
route check (shelf life) in `app/checks/runner.py`.

Supply frequency, supply voltage, operating temperature, humidity, grid
reliability, medical oxygen, distilled water, compressed air, consumables,
spare parts, age and manufacturer support, manual language, training and
repair, import and registration, shelf life on arrival.

Each returns `pass`, `mitigable`, `needs info` or `fail`. Any `fail` makes the
verdict **won't work**; else any `needs info` makes it **needs info**; else any
`mitigable` makes it **works if you also send X**, X being the add-ons; else
it **works**. A manual with no climate range gets the IEC 60601-1 default of
5-40 C and up to 93 % humidity, so a 44 C ward is flagged, not left blank.

## Run it locally

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m scripts.seed_demo
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/. If you pull new commits, rerun the `pip install`
line; a venv built before `openpyxl` was added fails on spreadsheet uploads
and in the tests.

With `DATABASE_URL` blank (the default) the app uses the SQLite file
`fitcheck.db` in the checkout. `scripts.seed_demo` fills it with the three
clinics, the demo offers and an open Kayunga request, and it can be rerun.

Settings, all listed in `.env.example` and read in `app/config.py`, except
`GUDID_LOOKUP`, which `app/reference/gudid.py` reads:

| Variable | Default | Needed for |
|---|---|---|
| `OCI_AUTH` | `api_key` | how OCI calls are signed: `~/.oci/config`, or `instance_principal` on the VM |
| `OCI_CONFIG_PROFILE`, `OCI_CONFIG_PATH` | `DEFAULT`, `~/.oci/config` | the API key profile |
| `OCI_REGION` | `us-chicago-1` | OCI Generative AI and OCI Language |
| `OCI_COMPARTMENT_ID` | tenancy from `~/.oci/config` | required under `instance_principal` |
| `REASONING_MODEL`, `VISION_MODEL` | `openai.gpt-oss-120b`, `meta.llama-4-maverick-17b-128e-instruct-fp8` | model overrides |
| `DATABASE_URL` | SQLite `fitcheck.db` | MySQL on the VM |
| `GUDID_LOOKUP` | on | `off` stops the live FDA lookup |

**Without OCI credentials** these still work: the checks, the matches and
candidate pages, CSV screening on `/offer/csv`, spreadsheet uploads on
`/request/upload`, offers prefilled from the device library, `/review` and
`/supply`; seeding needs no model either. What needs OCI: naming a device from
a photo or pasted text, reading facts for a device the library lacks, the
packet's handover card and its translation. `/impact` falls back to a sentence
written by code. A typed wish-list on `/request/upload` is still saved without
OCI, but unparsed: it is split at line breaks, commas, semicolons and "and",
so "two monitors" becomes one line of that name rather than two monitors. A
missing setting shows as an error on the page, not a 500.

## Test

```
.venv/bin/python -m pytest -q
```

The live GUDID call is off under pytest unless `GUDID_LOOKUP=on` is set.

## Deploy

One OCI instance runs the app under systemd behind Caddy, which holds the
certificate for fitcheck.care. The database is MySQL HeatWave on a private
subnet, and the app signs OCI calls as an instance principal. To ship a commit
that is already on the fork's main, run `bash deploy/redeploy.sh all <hash>`.
To undo the last cutover, run `bash deploy/redeploy.sh rollback`.
`docs/deploy.md` covers the stack, auth, and what to do when the site stops
answering.

## Pages

Routers are included in `app/main.py` in the order csv, pages, marketplace,
legacy, so `/offer/csv` resolves before `/offer/{offer_id}`. The role bar
(`POST /role`) sends Donor to `/offer`, Clinic to `/clinic` and Nonprofit to
`/review`.

| Party | Path | What it is |
|---|---|---|
| Donor | `/offer` | post one device; photo, paste or a library pick fills the facts |
| Donor | `/offer/{offer_id}` | the posted offer |
| Donor | `/offer/csv` | upload an inventory spreadsheet, screen every row against one clinic, then post them |
| Donor | `/confirm/{token}` | the donor says whether held stock still exists |
| Clinic | `/clinic` | **start here**: register the clinic's conditions, then go to `/request/upload` |
| Clinic | `/request/upload` | a spreadsheet or a typed list becomes one request |
| Clinic | `/request` | the request form, for a clinic already registered |
| Clinic | `/request/{request_id}/matches` | ranked offers, the "won't work" list and the needs-info list |
| Clinic | `/request/{request_id}/offer/{offer_id}` | one offer's verdict, check by check, with sources |
| Clinic | `/match/{match_id}` | the request sent to the donor after "Ask this donor" (`POST /match`) |
| Clinic | `/packet/{offer_id}?facility=<slug>` | the handover packet: card, paperwork checklist, translation |
| Nonprofit | `/review`, `/review/{slug}` | check each clinic's claim against registry evidence; only a person moves its tier |
| Nonprofit | `/supply`, `/supply/{country}` | per country and device type: consumables sold, parts available, sold new, still supported |
| Shared | `/` | landing page |
| Shared | `/impact` | totals, failures by reason, recent verdicts |
| Shared | `/about`, `/help`, `/contact` | what Fit-Check checks, how to use it, who to reach |

## Seeded clinics

- **Kayunga District Hospital, Uganda** (`uganda-district`): 240 V, 50 Hz, no
  air conditioning, six outages a week, no biomedical technician.
- **Hopital Communautaire de Pignon, Haiti** (`haiti-community`): 110 V, 60 Hz.
- **Bardiya Primary Health Centre, Nepal** (`nepal-phc`): 230 V, 50 Hz, wards
  past 42 C, a stabilizer but no generator.

Only Uganda has import rules on file; elsewhere the import check asks.

## Docs

- `docs/checks.md`: the fifteen checks rule by rule, the rollup and ranking.
- `docs/ai-pipeline.md`: every model call, its prompt, and how code gates it.
- `docs/schema.md`: every table, who writes each row, what is never stored.
- `docs/deploy.md`: the live stack, auth, redeploy, rollback and recovery.
- `docs/demo.md`: the demo walk on the live host, screen by screen.

## Sources

- WHO, *Medical device donations: considerations for solicitation and
  provision*, second edition. Annex 1 is the checklist these checks follow.
- WHO, *Guidelines for medicine donations*, for the year of shelf life on
  arrival. The device guidance sets no figure, so the check borrows this one.
- IEC 60601-1, clause 7.2 marking requirements and the default environmental
  conditions in 7.9.3.1.
- Ministry of Health Uganda, *National Medical Equipment Guidelines*,
  November 2024, section 3.6, for the import and registration rules.
- National Academy of Medicine, *Access to Medical Devices in Low-Income
  Countries*, 2018.
