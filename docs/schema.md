# Database schema

This page answers one question: what does Fit-Check store, in which table, who writes each row and when, and what does it never store?

Everything here was read from `app/data/db.py`, `app/data/marketplace_schema.py`, the two repository modules (`app/data/repository.py`, `app/data/marketplace_repo.py`) and the routes that call them. The row counts were taken by running `scripts/seed_demo.py` against an empty SQLite file at commit 385bc8f.

## The short version

- Twelve tables. SQLite on a laptop (`fitcheck.db` in the checkout when `DATABASE_URL` is blank), MySQL HeatWave on the VM.
- The app creates any missing table at startup (`repository.setup()` calls `METADATA.create_all`). It never alters a table that already exists.
- Eight tables predate this round. Four are new: `supply_answers`, `registry_lookups`, `packets` and `extractions`. They arrived in one commit, 386f383 ("feat(contracts): library, import rules and the round's new tables").
- No column was added to, removed from or changed in any of the eight older tables this round. That is the rule in design §4.4 and §9: new facts travel inside the existing JSON columns, new records get new tables. I checked it by diffing every `Table(` and `Column(` line in both schema files against 9e00968, the head before the round. Only the four new tables differ.
- Most facts live in JSON text columns, not in typed columns. A device's facts, a clinic's profile and a verdict are each one JSON blob.

## Tables in `app/data/db.py`

These three come from the original single-device screener. They back the legacy `/donor` → `/check` → `/verdict/<id>` path and the impact page.

### `facilities`

One row per staged clinic: `slug` (key), `name`, `country`, and `profile_json`.

- Filled by `repository.setup()` at every startup, but only when the table is empty. It copies the three profiles in `app/data/seed.py`.
- Nothing reads it. The legacy `/check` route looks clinics up in the in-code list `FACILITIES_BY_SLUG`, and every newer route reads `facilities_v2`. It is a leftover. It was left in place because dropping it would change a table on the operator's MySQL.

### `devices`

One row per device screened on the legacy path: `id`, `label` (manufacturer and model as one string), `raw_input` (what the donor typed), `facts_json`, `created_at`.

- Written by `POST /donor` after extraction, and by the seed's impact history.
- The seed marks its own rows with `raw_input = "seed_demo: impact history"` so a rerun can delete and rewrite exactly those.
- After a fresh seed: 20 rows.

### `verdicts`

One row per legacy screening: `id` (8 hex characters), `device_id`, `device_label`, `facility_slug`, `facility_name`, `outcome` (the outcome's text, such as `won't work`), `verdict_json`, `intel_json`, `created_at`.

- Written by `POST /check` and by the seed's history (twenty screenings of library devices against the three clinics).
- Read by `/verdict/<id>` and by `/impact`. The impact page's counters, bars and model paragraph are built from this table and nothing else. Screenings on the matches page are not logged here, so they do not move the impact figures.
- After a fresh seed: 20 rows, 11 `won't work` and 9 `needs info`. There is no `works if you also send` row in the seeded history, so that counter reads 0 (the B6 report lists this).

## Tables in `app/data/marketplace_schema.py`

These back the two-sided marketplace: offers, requests, matches. The first five predate this round.

### `facilities_v2`

One row per clinic: `slug` (key), `name`, `country`, `latitude`, `longitude`, `profile_json`, `verification`, `verification_note`, `created_at`.

- Written by `POST /clinic` (the clinic's own form) and by the seed, both through `save_facility`, which replaces the row with the same slug.
- `latitude` and `longitude` are copied out of the profile so distance needs no JSON parsing. On the form they come from the country's centroid, not the clinic's address.
- `verification` and `verification_note` are written only by a reviewer on `POST /review/<slug>`. Values: `entered on the form` (the default), `matched in a public registry`, `registration document on file`, `approved by a reviewer`, `contradicted by a check`. A clinic writes its profile, and a reviewer writes these two columns, so `save_facility` never touches them.
- After a fresh seed: 3 rows. `uganda-district` is Kayunga District Hospital, `haiti-community` is Hopital Communautaire de Pignon, and `nepal-phc` is Bardiya Primary Health Centre.

### `offers`

One row per donor offer: `id`, `donor_name`, `donor_email`, `device_label`, `device_type` (the request vocabulary, for example `oxygen concentrator`), `support_kind` (which add-on kind it can serve, or blank), `device_json`, `country`, `city`, `latitude`, `longitude`, `quantity_offered`, `quantity_reserved`, `quantity_shipped`, `available_until`, `status`, `created_at`.

- Written by `POST /offer` (the donor form, a library pick, or a confirmed extraction), by `POST /offer/csv/<token>/post` (one row per screened CSV row), and by the seed.
- `support_kind` is set by a model call on the offer form (`categorise_support`, keywords as fallback) and by keywords only on the CSV path.
- `quantity_reserved` goes up when a clinic presses "Ask this donor" (`POST /match`) and back down when the donor says no on the confirm page. Nothing in the app writes `quantity_shipped`.
- `status` is `open` for every offer the app writes. The app never stores `exhausted` or `expired`. Whether an offer can be matched is worked out on each read from the quantities and `available_until`. The seed writes `withdrawn` on a duplicate of an offer it is replacing, instead of deleting it, so an old match still loads.
- After a fresh seed: 11 rows. Offers 1 to 3 are the hero trio on a fresh database (A 525DS from Nashville, B 525KS from Leeds, C the unnamed concentrator from Houston). On the VM, whose database was seeded before this round, they are 8, 9 and 10.

### `requests`

One row per clinic request: `id`, `facility_slug`, `note`, `status`, `created_at`.

- Written by `POST /request` and by the seed, which creates the Kayunga request for one oxygen concentrator once and finds it again on a rerun.
- `status` is always `open`. No route changes it.

### `request_lines`

One row per kind of equipment on a request: `id`, `request_id`, `device_type`, `quantity`, `position` (order on the form). Written with its request. A request with no lines is refused.

### `matches`

One row per proposal to a donor: `id` (12 characters), `request_id`, `offer_id`, `quantity`, `distance_km`, `state`, `token`, `verdict_json`, `intel_json`, `created_at`, `responded_at`.

- Written by `POST /match`. The route screens the offer again first and refuses a `won't work` or a `needs info` verdict, so no row is ever written for an offer with open questions.
- The row is stored in state `awaiting donor`. `POST /confirm/<token>` moves it to `confirmed` or `withdrawn` and sets `responded_at`. `shipped` exists in the state machine (`app/matching/verification.py`) but no route reaches it.
- `token` is the only thing in the donor's confirm link. It is unique and indexed.
- `verdict_json` and `intel_json` freeze the verdict and supply facts the proposal was made on. The packet page reads the newest match's verdict for its send-with list. When no match exists it screens again with empty supply facts.
- After a fresh seed: 0 rows.

### `supply_answers` (new this round)

One row per recorded answer about supporting a device in a country: `id`, `device_type`, `model` (empty string means any model of the type), `country`, `consumables_available`, `spare_parts_available`, `sold_locally`, `manufacturer_supported`, `recorded_by`, `note`, `recorded_at`.

- The four answers are nullable booleans. NULL is a recorded "don't know", and it leaves the fact unknown. It never becomes a no.
- Written by `POST /supply/<country>` (the nonprofit's form) and by the seed. `save_supply_answer` replaces the row with the same (device type, model, country), backed by the unique key `uq_supply_answer`.
- Read by `supply_lookup` in `app/matching/supply_order.py`: the exact model in the country first, then the device type with `model = ''`. The model lookup ignores device type, so the legacy `/check` route, which has no type, still finds a model answer.
- After a fresh seed: 1 row, `("oxygen concentrator", "525KS", "Uganda")`, consumables no, spare parts yes, sold locally and manufacturer support NULL, recorded by "Fit-Check field team". No row for the type alone, so Offer C's supply questions stay open.

### `registry_lookups` (new this round)

A cache of FDA GUDID answers: `model` (key, stripped and lower case), `fetched_at`, `status`, `record_url`, `raw_json`.

- Written by `app/reference/gudid.py` after each live call to `api.fda.gov/device/udi.json`. `raw_json` is openFDA's whole response, so a second manufacturer asking about the same model is judged again rather than handed the first one's answer.
- A good answer, including openFDA's 404 "no matches", is reused for 7 days. A timeout or error is stored as `lookup failed` and blocks a retry for 10 minutes only.
- The seed never calls GUDID, so after a fresh seed this table is empty. On the VM it fills the first time someone opens the matches page with `GUDID_LOOKUP=on`.

### `packets` (new this round)

One built handover packet per offer and clinic: `offer_id` and `facility_slug` (together the key), `language`, `card_json`, `translated_json`, `built_at`.

- Written by `GET /packet/<offer_id>?facility=<slug>` the first time it builds a card, so the next click is instant. `?rebuild=1` deletes the row and builds again.
- A failed translation is not stored, so the next visit retries it. `translated_json` is NULL when no translation was made or kept.
- After a fresh seed: 0 rows.

### `extractions` (new this round)

Facts read from a donor's paste or photo, held for the confirm step: `id` (32 hex characters), `facts_json`, `document_json`, `created_at`.

- Written by `POST /offer/extract` each time the inspector panel reads something.
- Read by `POST /offer` when the donor posts with that id. The route reloads the facts from here and clears the fields the donor marked wrong. The browser never posts a fact value.
- A row older than one hour is ignored (`EXTRACTION_TTL`). Nothing deletes old rows. They stay in the table, unread.
- After a fresh seed: 0 rows.

## What the JSON columns carry

All of them are plain JSON in a `Text` column, written by `app/data/serialize.py` or the repo functions.

**A fact** is `{"value": ..., "source": "...", "note": "..."}`. `source` is a provenance label: `nameplate`, `spec sheet`, `stated by the donor`, `clinic profile`, `model knowledge`, `IEC 60601-1 default`, `FDA GUDID`, `recorded by the nonprofit` or `unknown`. `note` is the citation: a document title and page (`"<title>, p.11"`), a URL, or who recorded an answer and when. An unknown fact is stored with `value` null. A date is stored as `{"date": "2027-03-31"}` so it comes back as a date.

**`offers.device_json`, `devices.facts_json`, `extractions.facts_json`.** One fact per `DeviceFacts` field, plus `raw_input` as a plain string. The fields are manufacturer, model, year of manufacture, voltage min and max, frequencies, power, needs stable power, temperature min and max, humidity max, needs oxygen, needs distilled water, needs compressed air, consumables, expiry date, dated items, manual languages, documents, and operating complexity. Two of them were added this round inside the JSON, with no column change:
- `dated_items` is the donor's answer to "does anything in this consignment expire?". A blank expiry field on the form stores `false`.
- `documents` is a list of the manufacturer documents for the model, each `{"slug", "file", "title", "url", "languages", "kind"}`. It points at a text file under `data/library/<slug>/`. The packet reads the manual through it.

**`facilities.profile_json`, `facilities_v2.profile_json`.** Every `FacilityProfile` field as a plain value, not a fact: mains voltage and frequency, plug types, maximum temperature and humidity, air conditioning, outages per week, stabiliser, generator, oxygen, distilled water, compressed air, technician, trained operators, staff languages, notes, coordinates. These are the clinic's own statements, so they carry no provenance per field.

**`verdicts.verdict_json`, `matches.verdict_json`.** `{"outcome", "results", "requirements", "questions"}`. Each result is `{"key", "label", "status", "detail", "fix", "source", "question"}`. `requirements` is the list of fix texts, and `questions` the de-duplicated questions.

**`verdicts.intel_json`, `matches.intel_json`.** `{"country", "consumables_available", "spare_parts_available", "manufacturer_supported", "sold_locally", "reasoning"}`, the four as facts. `reasoning` is a leftover from the retired model-research step. The live routes leave it empty. The seed's history writes the recorded answer's note into it (`recorded_intel` in `scripts/seed_demo.py`).

**`extractions.document_json`.** `{"document", "library_slug", "device_type", "naming", "citations"}`. `naming` is what the model named: manufacturer, model, device type and `plate_text`, its transcription of the photo. `document` is a fetched document's URL, title, fetch time and page count, or null. On a library hit it is null, because the library text is on disk already. `citations` maps each field to `{"title", "url", "page"}`.

**`packets.card_json`.** `{"label", "dropped", "quick_start", "care_schedule", "consumables", "warnings"}`. Each line carries its text and the manual page it came from, and `dropped` counts the lines the gate removed. **`packets.translated_json`** is `{"language", "engine", "flag", "provenance", "card"}` with the same card shape inside.

**`registry_lookups.raw_json`.** openFDA's response as it came, or `{}`.

## What is never stored

- **The nameplate photo.** The route reads the upload, sends it to the vision model as a data URL, and drops it. Only the model's transcription (`plate_text`) is kept, in `extractions.document_json`.
- **Document text.** Library manuals are files in `data/library/`, committed with the code. No table holds them.
- **Matches page screenings.** The matches page and the candidate verdict page run all fifteen checks again on every load and store nothing. A verdict is stored only when a match is proposed (`matches.verdict_json`) or on the legacy `/check` path (`verdicts`).
- **The structured add-on.** The stored verdict keeps each result's `fix` text but not its `Requirement` (kind and detail). Code that reloads a verdict cannot search the inventory from it. The packet uses the fix text for that reason.
- **CSV screenings.** `POST /offer/csv` holds the result in the app process's memory for an hour, at most 100 at a time, under a random token (`app/web/csv_routes.py`). A restart loses them. Rows reach `offers` only when someone presses "post these offers".
- **The impact paragraph.** It is cached in process memory by a hash of the figures (`app/ai/impact.py`). A model failure is retried after 5 minutes.
- **The role.** Donor, clinic or nonprofit is a browser cookie, `role`. It is not tied to any record and there are no logins.
- **Credentials.** No OCI key, OCID or database password is in any table. The VM's `.env` holds the connection string and the compartment.
- **Prompts and raw model replies.** Only the gated results are stored.

One thing that is stored and worth knowing about: `offers.donor_email` and `offers.donor_name` are plain text, as typed by the donor.
