"""Prompt text for the handover card (design §5.2).

Kept apart from the extraction prompts in prompts.py: [B4] owns those and
[B5] owns these.
"""

from __future__ import annotations

CARD_SYSTEM = """\
You write a one-page handover card for clinic staff who will run a donated
medical device. You work only from the manufacturer's manual excerpt you are
given.

Rules:
- Reply with ONLY a JSON object, no prose and no code fence.
- Use only what the excerpt says. Never add advice, numbers, intervals or part
  numbers that are not printed in the excerpt. If a section has nothing in the
  excerpt, give an empty list.
- Copy every number, unit and part number exactly as the excerpt prints it.
- Every line cites "page": the number N of the "=== page N ===" marker above
  the text it comes from. If the excerpt has no page markers, use null.
- Short, plain, imperative lines, one action or fact each. At most 8 lines in
  a section.
- quick_start: setting the device up and switching it on, in order.
- care_schedule: cleaning and maintenance the staff do, each with the interval
  the manual gives ("" if none is given).
- consumables: filters and other parts that wear out or are replaced, with the
  part number if the excerpt prints one ("" otherwise) and the replacement or
  inspection interval.
- warnings: the dangers and warnings that matter most for daily use."""

CARD_USER = """\
Device: {label}

Manual excerpt:
{excerpt}

Answer with this JSON shape:
{{"quick_start": [{{"text": "...", "page": N}}],
 "care_schedule": [{{"text": "...", "interval": "...", "page": N}}],
 "consumables": [{{"item": "...", "part_number": "...", "interval": "...", "page": N}}],
 "warnings": [{{"text": "...", "page": N}}]}}"""

TRANSLATE_SYSTEM = """\
You translate short instruction lines from a medical device manual.

Rules:
- Reply with ONLY a JSON object, no prose and no code fence.
- Translate each line into {target}. Keep every number, unit and part number
  exactly as written. Add nothing and leave nothing out.
- Return exactly as many lines as you were given, in the same order."""

TRANSLATE_USER = """\
Lines (JSON list):
{lines}

Answer as: {{"lines": ["<line 1 in {target}>", ...]}}"""
