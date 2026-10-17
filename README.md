# Fit-Check

A two-sided platform for medical equipment donations. Clinics state their real
conditions once. Donors post what they hold. Every offer is screened against the
receiving facility before anyone books a container, and only what can actually
work is ranked by distance.

Built for HackTN, Track 1: From Surplus to Social Good.

## The pipeline

1. **Filter** — offers of the right type, with stock, not past their expiry.
2. **Screen** — fourteen constraint checks per candidate. Anything blocked is
   dropped here, before distance is ever considered.
3. **Rank** — survivors ordered by how little must ship alongside them, then by
   great-circle distance.
4. **Verify** — the donor is asked whether the stock still exists. Stock is held
   against the request until they answer, and released if they say no.

Compatibility and allocation are deliberately separate stages. Compatibility is
a predicate: a 60 Hz motor either runs on a 50 Hz grid or it does not, which
cannot be expressed as a cost inside a linear program. Allocation is the
opposite: everything already fits, so only distance and quantity remain, and no
model is involved. Nothing ever ranks on a score that blends the two.

## The design that matters

The verdict is produced in two halves, and the split is the point.

1. **The model researches.** It reads a nameplate photo or a pasted spec sheet
   and returns structured facts. Every fact carries a provenance label:
   `nameplate`, `spec sheet`, `model knowledge`, `IEC 60601-1 default` or
   `unknown`. It is told to return `null` rather than guess.
2. **Code judges.** Frequency, voltage, temperature, humidity, outages, age and
   utilities are arithmetic once the facts exist. No model is called in
   `app/checks/`. Given the same facts the verdict is identical every time.

The model only decides the three questions that genuinely need world knowledge:
are the consumables sold in that country, can spare parts be sourced there, and
is the model still supported by its maker.

So the verdict cannot be hallucinated. Only the inputs can, and the UI shows
where each input came from.

A missing fact becomes a follow-up question on the verdict page, never a guess.

## Checks

Thirteen, derived from the WHO guidance *Medical device donations:
considerations for solicitation and provision* (Annex 1) and IEC 60601-1.

| Check | Decided by |
|---|---|
| Supply frequency | code |
| Supply voltage | code |
| Operating temperature | code |
| Humidity | code |
| Grid reliability | code |
| Medical oxygen | code |
| Distilled water | code |
| Compressed air | code |
| Age and manufacturer support | code |
| Manual language | code |
| Training and repair | code |
| Consumables in country | model |
| Spare parts in country | model |

Each returns `pass`, `fail`, `mitigable` or `needs info`. One `fail` makes the
whole verdict **won't work**. Any `mitigable` makes it **works if you also
send X**, and the X list is the fixes.

When a device's manual states no climate range, the checks fall back to the
IEC 60601-1 default of 5-40 C and 15-93 % humidity. That is not a guess: a
device with no stated range is only required to work inside that range, so a
44 C ward is a flag rather than a blank.

## Running it

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env            # defaults work as-is
./.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000. Three pages: add a device, pick a clinic, read the
verdict. The verdict gets its own URL you can share.

Requires an OCI API signing key at `~/.oci/config`. The key is never read by
this repo's code directly; the OCI SDK loads it.

### Tests

```bash
./.venv/bin/python -m pytest
```

26 tests, all against the deterministic half. The model is never called in the
suite, which is why it runs in under a tenth of a second.

## Layout

```
app/
  domain/       frozen dataclasses: DeviceFacts, FacilityProfile, Verdict
  checks/       the fourteen checks and the rollup. No model calls.
  ai/           OCI Generative AI client, extraction, country research
  data/         SQLAlchemy schema, repository, seeded clinic profiles
  web/          routes, Jinja templates, one stylesheet
```

## Deploying to the OCI VM

The only change is the database. Set `DATABASE_URL` in `.env`:

```
DATABASE_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/fitcheck
```

Then run uvicorn behind nginx on the VM. Once the app runs on OCI Compute,
switch authentication from the API key to instance principals so no key file
sits on the server.

## Seeded clinics

Three profiles with real grid and climate figures:

- **Kayunga District Hospital, Uganda** - 240 V, 50 Hz, no air conditioning,
  six outages a week, no biomedical technician.
- **Hopital Communautaire de Pignon, Haiti** - 110 V, 60 Hz, so US equipment
  plugs straight in, but nothing is stocked locally.
- **Bardiya Primary Health Centre, Nepal** - 230 V, 50 Hz, ward temperatures
  past 42 C, stabiliser but no generator.

Haiti is the interesting one: a US device passes every power check there and
still fails on supply chain.

## Sources

- World Health Organization, *Medical device donations: considerations for
  solicitation and provision*, second edition. Annex 1 is the acceptance
  checklist these checks follow.
- WHO, *Guidelines for medicine donations*, for the expectation that a donated
  consignment lands with at least a year of shelf life left. The device
  guidance sets no equivalent figure, so the shelf-life check borrows the
  medicines one rather than inventing a number.
- IEC 60601-1, clause 7.2 marking requirements and the default environmental
  conditions in 7.9.3.1.
- National Academy of Medicine, *Access to Medical Devices in Low-Income
  Countries*, 2018.
