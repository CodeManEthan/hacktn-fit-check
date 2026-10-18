"""Prompt text, kept apart from the code that sends it."""

# ---- extraction, step 1: name the device ---------------------------------

NAMING_SYSTEM = """\
You identify a medical device from what a donor supplied: pasted text, a
photograph of its rating plate, or both.

Rules you must follow:
- Return ONLY a JSON object. No prose, no code fence.
- Copy names exactly as printed. Never complete, correct or guess a model
  number: if only a series or family name is printed, return that.
- Use null for anything the source does not show."""

NAMING_USER = """\
Name the device in the source below. Return exactly this JSON shape:

{{
  "manufacturer": string|null,
  "model": string|null,
  "device_type": string|null,
  "plate_text": string|null
}}

- "manufacturer": the maker as printed.
- "model": the model or catalogue number exactly as printed, e.g. "525DS".
- "device_type": plain words for the kind of device, e.g. "oxygen
  concentrator", "patient monitor", "ventilator".
- "plate_text": {plate_rule}

SOURCE:
{source}"""

PLATE_RULE_IMAGE = """\
transcribe every printed line of the rating plate in the image, one
  line per plate line, exactly as printed (numbers, units, symbols' text)."""

PLATE_RULE_TEXT = "null (there is no image)."

# ---- extraction, step 2b: read the facts when the library has no entry --

READ_SYSTEM = """\
You copy facts about a medical device out of the sources a donor supplied.

Rules you must follow:
- Return ONLY a JSON object. No prose, no code fence.
- Every value must be printed in one of the sources below. If it is not
  printed there, the value is null and its source is "unknown".
- Do not use anything you know about this model from elsewhere. Do not
  infer a value from the kind of device. Do not convert units: if a value
  is printed only in other units (for example only in degrees F), use null.
- A wrong fact here produces a confident wrong verdict, which is worse than
  an admitted gap. An empty answer is a good answer when the source is thin."""

READ_SCHEMA = """\
{
  "manufacturer": string|null,
  "model": string|null,
  "year_of_manufacture": integer|null,
  "voltage_min_v": number|null,
  "voltage_max_v": number|null,
  "frequencies_hz": [number]|null,
  "power_watts": number|null,
  "needs_stable_power": boolean|null,
  "temp_min_c": number|null,
  "temp_max_c": number|null,
  "humidity_max_pct": number|null,
  "needs_oxygen": boolean|null,
  "needs_distilled_water": boolean|null,
  "needs_compressed_air": boolean|null,
  "consumables": [string]|null,
  "manual_languages": [string]|null,
  "operating_complexity": "low"|"medium"|"high"|null,
  "sources": { "<field name>": {source_names} },
  "quotes": { "<field name>": "the exact words in that source that state it" }
}"""

READ_USER = """\
The device: {device}

Fill this exact JSON shape from the sources below and from nothing else:

{schema}

Notes:
- For every non-null field, "sources" names where it is printed:
{source_lines}
  and "quotes" copies the words that state it, exactly as printed, at most
  20 words. Do not paraphrase a quote.
- A single rated voltage ("115 V") is both voltage_min_v and voltage_max_v.
  A printed range ("100-240 V") gives both ends.
- "frequencies_hz" lists every frequency printed, e.g. "50/60 Hz" is [50, 60].
- "temp_min_c" and "temp_max_c" are the OPERATING range, not storage.
- "year_of_manufacture" is the year this unit was made (MFD, manufactured,
  built), never a copyright, revision or model-introduction year.
- "consumables" lists the items the source names that the device uses up
  (filters, cartridges, sensors, circuits, reagents), each copied as
  printed. null when the source names none.
- "needs_oxygen", "needs_distilled_water", "needs_compressed_air",
  "needs_stable_power": true or false only when the source says so.
- "operating_complexity" only when the source says what training running
  or servicing it needs.
- "manual_languages": the languages the source says the manual is in.

{sources}"""

READ_SOURCE_NAMES = {
    "donor": "the donor's text",
    "nameplate": "the rating plate transcription",
    "document": "the manufacturer document",
}

RESEARCH_SYSTEM = """\
You advise on whether a medical device can be supported in a specific country.

Rules:
- Return ONLY a JSON object. No prose, no code fence.
- Use null when you genuinely do not know. An admitted gap is useful; a
  confident guess about a country's supply chain is dangerous.
- Judge the country's market, not the individual hospital."""

RESEARCH_USER = """\
Device: {device}
Consumables it uses: {consumables}
Country: {country}

Answer in this exact JSON shape:

{{
  "consumables_available": true|false|null,
  "spare_parts_available": true|false|null,
  "manufacturer_supported": true|false|null,
  "reasoning": "two or three sentences, naming what you based this on"
}}

- "consumables_available": are those consumables routinely sold or distributed
  in that country?
- "spare_parts_available": can spare parts be sourced there at a price a
  district hospital could meet?
- "manufacturer_supported": is this model still supported by its maker or an
  authorised distributor? Use false only if you are confident it is retired."""
