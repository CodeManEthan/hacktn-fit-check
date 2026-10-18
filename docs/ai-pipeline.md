# The AI pipeline

What this answers: where Fit-Check calls a model, what each call may and may not decide, and how code checks what comes back.

The short version for a judge: the fifteen checks and the verdict are code. A model never supplies a device fact. The model does four jobs: it names the device, it copies facts out of text the donor gave (only when the library has no entry), it writes the handover card from the manual, and it writes the impact paragraph. Code gates each of those against a source. The candidate page says this under its audit table: "Every row is decided by code, not by a model."

Everything below is read from the code at main 385bc8f. File paths are relative to the repository root.

## 1. Models, calls and token caps

Model ids come from `app/config.py` (`DEFAULT_REASONING_MODEL`, `DEFAULT_VISION_MODEL`), overridable by `REASONING_MODEL` and `VISION_MODEL` in `.env` (see `.env.example`):

- reasoning model: `openai.gpt-oss-120b`
- vision model: `meta.llama-4-maverick-17b-128e-instruct-fp8`

Every chat call goes through `GenAiClient.complete` in `app/ai/client.py`. It sends temperature 0.0 (`DEFAULT_TEMPERATURE`) and `max_tokens = max_tokens_for(model_id, requested)`. The default request is `DEFAULT_MAX_TOKENS = 6000`, because gpt-oss spends tokens reasoning before it answers. `MODEL_MAX_TOKENS = {DEFAULT_VISION_MODEL: 4096}` lowers the cap for the vision model, which refuses more than 4096 ("Invalid 'maxTokens': Value is greater than maximum: 4096", found live by B4). The clamp is keyed by the default vision model id. A `VISION_MODEL` override gets no clamp.

| Step | Function | Model | max_tokens sent |
|---|---|---|---|
| Name the device from a paste | `extract.name_device` | reasoning | 6000 |
| Name the device from a plate photo | `extract.name_device` | vision | 6000 requested, clamped to 4096 |
| Read facts (no library hit only) | `extract.read_facts` | reasoning | 6000 |
| Handover card | `packet.build_card` | reasoning | 6000 (`CARD_MAX_TOKENS`) |
| Translation fallback (OCI Language not enabled) | `translate._chat_translate` | reasoning | 6000 |
| Impact paragraph | `impact.write_summary` | reasoning | 6000 |
| Support-item category, at offer post | `categorise.categorise_support` | reasoning | 200 |

The primary translation path is not a chat model. It is OCI Language, `AIServiceLanguageClient.batch_language_translation`, in batches of 25 lines (`translate.BATCH_DOCUMENTS`).

Both the chat client and the Language client get credentials from one function, `build_signer_and_config` in `app/ai/client.py`: `OCI_AUTH=api_key` reads `~/.oci/config`; `OCI_AUTH=instance_principal` signs as the VM and needs `OCI_COMPARTMENT_ID`. Deploy details are in `docs/deploy.md`.

`categorise_support` is the one model call outside the four jobs. When an offer is posted, it picks which support need the item meets (stabiliser, UPS and so on) from a closed list, or null. It normalises the donor's words; it decides no device fact and no verdict. If the model fails it falls back to a keyword table (`keyword_category`). CSV rows use the keyword table only.

## 2. The prompt files

| Prompt | File | Constants |
|---|---|---|
| Naming | [app/ai/prompts.py](../app/ai/prompts.py) | `NAMING_SYSTEM`, `NAMING_USER`, `PLATE_RULE_IMAGE`, `PLATE_RULE_TEXT` |
| Reading (no hit) | [app/ai/prompts.py](../app/ai/prompts.py) | `READ_SYSTEM`, `READ_SCHEMA`, `READ_USER`, `READ_SOURCE_NAMES` |
| Card | [app/ai/packet_prompts.py](../app/ai/packet_prompts.py) | `CARD_SYSTEM`, `CARD_USER` |
| Translation fallback | [app/ai/packet_prompts.py](../app/ai/packet_prompts.py) | `TRANSLATE_SYSTEM`, `TRANSLATE_USER` |
| Impact paragraph | [app/ai/impact.py](../app/ai/impact.py) | `SYSTEM`, `PROMPT` |
| Support category | [app/ai/categorise.py](../app/ai/categorise.py) | `CATEGORISE_SYSTEM`, `CATEGORISE_USER` |
| Supply research (no caller) | [app/ai/prompts.py](../app/ai/prompts.py) | `RESEARCH_SYSTEM`, `RESEARCH_USER` |

## 3. Extraction: name, library hit, or read and gate

Design §6. Code: `extract.extract` in [app/ai/extract.py](../app/ai/extract.py). Routes: `POST /offer/extract` (the "Read these facts now" button) and `POST /offer` when no extraction id is posted.

**Step 1, name it.** `name_device` sends the paste (reasoning model) or the photo (vision model) and gets back `manufacturer`, `model`, `device_type` and, with a photo, `plate_text`, a line-by-line transcription of the plate. "null", "unknown" and blanks become None. `NAMING_SYSTEM` says: "Copy names exactly as printed. Never complete, correct or guess a model number." The B4 check tried three vague pastes live ("DeVilbiss 525 ... 5 litre", "Drive DeVilbiss 525 Series", "DeVilbiss, 5L model"). The model returned "525", "525 Series" and "5L model" and never completed the number.

**Step 2, library hit.** `device_library.best_match(manufacturer, model)` in [app/reference/device_library.py](../app/reference/device_library.py) ranks the twelve entries of `data/device_library.json`. An exact model match ranks 3, an alias 2, a manufacturer-only match 1. Model strings are compared with case, spaces and hyphens removed, so "525 DS" is "525DS". There is a hit only when exactly one entry holds the top rank and that rank is a model or alias match. "525" is an alias of both 525 entries, so a paste that prints only "525" ties and is no hit; the form then offers the library dropdown.

On a hit the facts are `to_device_facts(entry)` and the model reads nothing more. Each fact carries the library's citation: document title, URL with `#page=N`, page, quote. `library_citations` turns them into the inspector's links. One model call in all. The pre-staged 525DS paste took 1.1 s on the live host.

Why a hit stops the model: the library values were checked against the manual text. A second model reading of the same manual could only add a value that is printed but wrong for the field, such as a storage temperature read as operating, or a nominal voltage read as a range.

**Step 3, no hit: read, then gate.** `read_facts` asks the reasoning model to fill `READ_SCHEMA` from the paste and the plate text. A live search provider would add a fetched document; none is configured, so this branch does not run tonight (`SEARCH_PROVIDER` defaults to `library`; `google_cse` and `brave` raise `NotConfigured`). The schema asks for a source per field and a quote. The allowed sources are `donor`, `nameplate` (only when there is a plate transcription), `document` (only with a fetched document) and `unknown`. `READ_SYSTEM` says: "Do not use anything you know about this model from elsewhere. Do not infer a value from the kind of device. Do not convert units." Any other source the model names, "model knowledge" included, becomes unknown.

Then `gate_against_text(facts, texts)` keeps a value only where it is printed in the text:

- Numbers must occur as printed numbers. "115" matches "115V", "115 V" and "115-120"; "35" matches "35.0". A number inside another number ("1150") does not match.
- Manufacturer, model and list strings must occur with case, spaces and punctuation ignored.
- Booleans and `operating_complexity` cannot be found as a value, so the model's quote (at least 6 letters or digits) must occur in the source.
- Languages are normalised (`app/domain/language.py`) and match a name, an endonym or a code in a code list. A lone "it", "no" or "de" never counts.
- Unprinted frequencies and languages are dropped one by one, because claiming fewer only makes a check stricter. Consumables stand or fall whole: dropping one would hide a supply the clinic must find.
- A value found nowhere becomes unknown with the note "model answer not found in the source".
- A kept value takes the provenance of the text it was found in: `NAMEPLATE` for the plate transcription, `DONOR_STATED` for the paste, `SPEC_SHEET` with "title, p.N" for a fetched document.

What the donor can do next (design §6.3): the result is cached server-side for an hour in table `extractions` under a random id. The page shows 14 rows (`app/web/inspector.py`, `ROWS`), each with its source and link. The donor can mark a row "wrong", which clears it to unknown. The donor cannot type a replacement value. The client posts only the id and the cleared field names, never a fact. A library pick shows no "wrong" control; to change a cited value the donor clears the pick and pastes.

The library pick (`POST /offer/prefill`) makes no model call. It renders the same 14 rows from `to_device_facts(entry)`.

## 4. The handover card and its gate

Design §5.2. Code: [app/ai/packet.py](../app/ai/packet.py). Route: `GET /packet/<offer_id>?facility=<slug>`.

The route picks the manual: the first document of kind `manual`, else the first whose title contains manual, instruction, guide or IFU, else the first document. Its text comes from `data/library/<slug>/<file>.txt`, split into pages on form feeds or "=== page N ===" markers (`split_pages`).

`select_sections` cuts the excerpt the model sees. It finds headings (capitals at the left margin, no dot leaders, one-word call-outs such as WARNING excluded) and spends a 14,000-character budget (`EXCERPT_CAP`) in this order: quick start, setup, operation, cleaning, maintenance, filter, accessories, troubleshooting, warnings. The sections go to the model in document order with their page markers. The model gets nothing else: `CARD_USER` carries the device label and the excerpt.

`build_card` asks for JSON with four lists, `quick_start`, `care_schedule`, `consumables`, `warnings`, each line citing a page. `CARD_SYSTEM` says: "Never add advice, numbers, intervals or part numbers that are not printed in the excerpt."

`gate(card, text, pages)` then drops a line when:

- any number in it (with or without a unit: 230V, 5-Liter, 50/60) is not printed in the manual;
- any part code in it (4 or more characters with a digit and a letter or hyphen: MC44D-605, 525KS) is not printed in the manual;
- pages are known and the cited page does not exist;
- pages are known, the line states a number or code, and none of them is on the cited page.

Dropped lines are counted; the page shows "N lines dropped: not found in the manual". A card with every line dropped reads "The card could not be built from this manual", and the packet still lists the documents and the paperwork.

B5's live run built the 525KS card in 12.6 s: 24 lines, 24 kept, none dropped. The gate was not needed that night; the unit tests show it dropping an invented part code, an invented "999 hours" and a wrong page.

The card is cached in table `packets` per (offer, clinic). The first open of a packet makes one gpt-oss call; later opens read the cache. `?rebuild=1` (the "Rebuild this packet" link) clears it and calls the model again. A model failure shows the problem on the page and caches nothing.

## 5. Translation and its fallback

Design §5.4. Code: [app/ai/translate.py](../app/ai/translate.py).

`pick_target(staff_languages)` reads the clinic's staff languages, normalised:

- first language English: no translation. The header reads "English, as requested". This is Kayunga.
- first language supported by OCI Language: that language, beside English.
- first language not supported: the next supported staff language that is not English, or else the English card with the flag "<language> is not supported by OCI Language; English card supplied". Nepali and Haitian Creole are not in `SUPPORTED` (30 base codes).

The seeded clinics: Kayunga (`uganda-district`) reads English; Pignon (`haiti-community`) lists French then Haitian Creole, so it gets French; Bardiya (`nepal-phc`) lists Nepali then English, so it gets the English card with the Nepali flag.

The translation path:

1. OCI Language `batch_language_translation`, source `en`. A translated card shows beside the English one, labelled "machine-translated, verify".
2. Only a 4xx whose code or message says "not enabled" or "not authorized" (`is_not_enabled`) falls back to the chat model, with `TRANSLATE_SYSTEM`. The header then adds "(translated by the chat model: model knowledge, not OCI Language)", and the translation carries `Provenance.MODEL_KNOWLEDGE`.
3. Any other failure (5xx, 429, timeout) gives the English card and "Translation into <language> failed; English card supplied". It is not cached, so the next visit retries.

The re-check runs on both engines. `_same_figures` compares the numbers and part codes of each translated line with its English line, reading a decimal comma as a point ("6,5 pieds" matches "6.5 feet"). A line whose figures changed shows the English line instead, with the note "English shown: the translation changed a number or part code".

B5 checked OCI Language live, en to fr, through the API-key path. It also found the limit the "verify" label is for: "Clean oxygen outlet connector..." came back as "Connecteur de sortie d'oxygène propre...", which reads "clean connector" (an adjective), not "clean the connector". The re-check catches changed numbers, not changed meaning.

## 6. The impact paragraph and its gate

Design §7.3. Code: [app/ai/impact.py](../app/ai/impact.py). Route: `GET /impact` (`app/web/routes.py`).

`figures()` builds the figures in code from the `verdicts` table: `screened`, `blocked` (won't work), `add_ons` (works if), `needs_info`, `blockers` (per check label, FAIL results only), `clinics`, `countries`. `write_summary` sends them as JSON and asks for two or three sentences using only those figures. `SYSTEM` forbids percentages, ratios, differences and any other number, and allows adding two figures only "if you say that you did".

`gate(paragraph, figures)` reads every number in the reply, in digits and in words ("twenty", "twenty-one", "dozen"). It keeps the reply only if each number is:

- one of the figures, a blocker count, or the number of countries; or
- the sum of two figures, and both addends are also stated in the paragraph (B6 fix 848ba89). "8 on temperature and 3 on training, 11 together" passes; a bare "twelve" does not, even though 3 + 9 = 12.

A decimal fails. Any failing number replaces the whole reply with `code_sentence(figures)`, a sentence code writes from the same figures. The page labels the paragraph "written by the model from the figures above" or "written by code from the figures above".

The result is cached per process by the SHA-256 of the figures, so the model is called once per change in the log, not per page load. A model failure gives the code sentence and is retried after 300 s (`FAILURE_RETRY_SECONDS`). A restart clears the cache, so the paragraph's wording can change after a redeploy; its numbers cannot.

## 7. What "model knowledge" was, and why it is gone

Before this round (main 9e00968), the extraction prompt offered the model four sources per field: "nameplate", "spec sheet", "model knowledge" ("not in the source, but you know it for this exact model") and "unknown". And `research_supply` (`app/ai/research.py`) asked the model whether a device's consumables, spare parts and manufacturer support were available in a country, from its world knowledge. The legacy `/check` route called it. Both put the model's memory into facts the checks then trusted.

Ruling [F12] says: "The model's memory is never a source of facts." The round removed both paths:

- Extraction is retrieval-first: the library's cited facts on a hit; on no hit, only values printed in the paste, the plate transcription or a fetched document, each gated (section 3). "model knowledge" is not an allowed source; a value claimed from it becomes unknown.
- Supply facts come in this order (`app/matching/supply_order.py`, design §2.6): the FDA GUDID registry, live, for manufacturer support (it can only affirm, never deny); then the nonprofit's recorded answer (table `supply_answers`, form at `/supply`); otherwise unknown, and the check asks the question. `/check` calls `supply_lookup` too.
- `research_supply` and its prompts stay in the tree with no caller, for a later round to delete.

`Provenance.MODEL_KNOWLEDGE` ("model knowledge") still exists in `app/domain/provenance.py` and is not in `TRUSTED_SOURCES`. The one place the running app sets it is the chat-model translation fallback (section 5), on the translation and never on the card. `grep -rln MODEL_KNOWLEDGE app` lists three files: `provenance.py` (the definition), `research.py` (no caller) and `translate.py`.

## 8. What a judge should hear about hallucination

What holds:

- The verdict is fifteen code checks over facts that each carry a source tag. No model sits between the facts and the verdict.
- On the demo devices, every fact comes from the device library. Each links to the page of the manufacturer's document it was read from.
- When the model reads a paste, a value that is not printed in the paste or plate becomes unknown, and unknown becomes "needs info", never a pass.
- Card lines whose numbers or part codes are not in the manual, or not on the cited page, are dropped and counted on the page.
- A translated line whose numbers changed shows the English line.
- An impact paragraph with any number that is not a figure from the log is replaced by code's sentence, and the label says which one you are reading.

What the gates do not do, said plainly:

- **The extraction gate is loose** (B4 check, L4). A number is kept if it appears anywhere in the claimed text, whatever its unit or field: "40 pages" would keep a `temp_max_c` of 40. Names and list strings are substring matches with a 2-character minimum, so a manufacturer "GE" would match "charge". A boolean needs its quote in the text, but nothing checks the model read the quote the right way round. This is within design §6.2 and is a backstop. It matters for long no-hit pastes, not for library devices.
- **The plate gate is a consistency check.** A photo's values are checked against the vision model's own transcription of the plate (design §6.2), not against an independent reading.
- **The card gate checks figures, not prose.** A card line with no number or part code is checked only for its cited page existing. In B5's live card that was 15 of 24 lines. A decimal comma in an English line ("6,5") splits into two small integers, which pass easily (B5 check, L4). `select_sections` returns an empty excerpt if no heading matches and the first line is over 14,000 characters, and the card then reads "could not be built" (B5 check, L3; rare with pypdf text).
- **The impact gate checks numbers, not claims.** The live paragraph on 2026-10-18 ended "This ensures that only equipment that truly matches each clinic's conditions is sent, protecting resources and patient safety." No number, so the gate let it through.
- **Translation meaning is not checked.** Only figures are. Hence "machine-translated, verify".
- **The library itself was compiled before the demo** by verification workers reading the documents. Their reports (`verify-hero-device.md`, `verify-library-a.md`, `verify-library-b.md`) are the project's research notes, kept outside this repository; the `reports/` names in `scripts/build_library.py` point there, not to a folder in the fork. `scripts/build_library.py` checks each quote against the stored text. All 30 quotes for the two 525 entries were found. For the other ten entries, 78 were found and 35 were not: table reconstructions, product-page quotes with no stored text, and quotes that join two documents. Those keep the page their report gave (B3 report). The 525s' `operating_complexity = "low"` is the lead's reading of the intended-use sentence, and the fact's note says so.

In live runs the gates dropped nothing: gpt-oss-120b claimed only printed values and declined to convert units (B4), and the card kept 24 of 24 lines (B5). The gates are there for the model, or the paste, that does not behave.
