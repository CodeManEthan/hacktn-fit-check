"""§5.2: the handover card, its section selection and the gate.

The fixture is pages 8, 9, 10, 13 and 14 of the DeVilbiss 525 series
instruction guide SE-525KS-2 Rev A (English part), copied verbatim from
device-docs/hero/525ks_SE-525KS-2_RevA.txt with "=== page N ===" markers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai import packet
from app.ai.packet import Card, CardLine, card_from_dict, gate, select_sections, split_pages

EXCERPT = (Path(__file__).parent / "fixtures" / "525ks_excerpt.txt").read_text(encoding="utf-8")
PAGES = split_pages(EXCERPT)


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.prompts: list[str] = []

    def complete_json(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.kwargs = kwargs
        return self.payload


def test_pages_come_from_markers_form_feeds_or_nowhere():
    assert sorted(PAGES) == [8, 9, 10, 13, 14]
    assert split_pages("one\fthree") == {1: "one", 2: "three"}
    assert split_pages("no markers at all") == {}


def test_sections_pick_setup_operation_and_care_under_the_cap():
    excerpt = select_sections(EXCERPT, PAGES)

    assert len(excerpt) <= packet.EXCERPT_CAP
    assert "SETTING UP YOUR OXYGEN CONCENTRATOR" in excerpt
    assert "OPERATING YOUR DEVILBISS OXYGEN CONCENTRATOR" in excerpt
    assert "CARING FOR YOUR DEVILBISS OXYGEN CONCENTRATOR" in excerpt
    assert "SERVICE AND MAINTENANCE GUIDANCE" in excerpt
    assert "=== page 9 ===" in excerpt
    # Not a heading the card needs.
    assert "RETURN AND DISPOSAL" not in excerpt


def test_sections_keep_document_order_and_mark_each_page_before_its_text():
    excerpt = select_sections(EXCERPT, PAGES)
    lines = excerpt.splitlines()

    assert lines[0] == "=== page 8 ==="
    assert excerpt.index("SETTING UP") < excerpt.index("CARING FOR")
    markers = [i for i, line in enumerate(lines) if line.startswith("=== page")]
    assert all(not lines[i + 1].startswith("=== page") for i in markers)


def test_text_with_no_matching_heading_falls_back_to_its_start():
    text = "PRODUCT OVERVIEW\nThe unit weighs 16 kg.\n" + "x " * 20_000
    excerpt = select_sections(text, {})

    assert excerpt.startswith("PRODUCT OVERVIEW")
    assert len(excerpt) <= packet.EXCERPT_CAP


def test_callout_words_do_not_end_a_section():
    assert not packet._is_heading("DANGER")
    assert not packet._is_heading(" CAUTION NO")
    assert not packet._is_heading("EN - 8 SE-525KS-2")
    assert not packet._is_heading("ENGLISH..................................... en-2")
    assert packet._is_heading("TROUBLESHOOTING")
    assert packet._is_heading("IMPORTANT SAFEGUARDS")


TRUE_LINE = CardLine("Wipe the outer cabinet with a damp cloth.", page=9, interval="7 days")
INVENTED_CODE = CardLine("Replace the intake filter.", page=14, part_number="MC99Z-123")


def test_the_gate_keeps_a_true_line_and_drops_an_invented_part_code():
    card = Card(label="525KS", care_schedule=(TRUE_LINE,), consumables=(INVENTED_CODE,))

    gated = gate(card, EXCERPT, PAGES)

    assert gated.care_schedule == (TRUE_LINE,)
    assert gated.consumables == ()
    assert gated.dropped == 1


@pytest.mark.parametrize("line", [
    CardLine("Clean the cabinet every 45 days.", page=9),          # number not in the manual
    CardLine("Clean the cabinet up to 260 times.", page=14),       # true number, wrong page
    CardLine("Turn the unit OFF before cleaning.", page=99),       # page does not exist
    CardLine("Turn the unit OFF before cleaning.", page=None),     # no page cited
])
def test_the_gate_drops_lines_the_cited_page_does_not_bear_out(line):
    gated = gate(Card(warnings=(line,)), EXCERPT, PAGES)

    assert gated.warnings == ()
    assert gated.dropped == 1


def test_the_gate_keeps_a_wordy_line_on_a_real_page_and_a_real_part_code():
    lines = (
        CardLine("Turn the unit OFF before cleaning.", page=9),
        CardLine("Call DeVilbiss customer service on 800-338-1988.", page=13),
        CardLine("Allow up to 15 minutes for start up.", page=8),
        CardLine("Do not use the 5-liter unit near flames.", page=8),
    )
    gated = gate(Card(quick_start=lines), EXCERPT, PAGES)

    assert gated.quick_start == lines
    assert gated.dropped == 0


def test_without_pages_only_the_document_text_is_checked():
    flat = EXCERPT.replace("=== page", "--- leaf")
    card = Card(care_schedule=(CardLine("Clean every 7 days.", page=None),
                               CardLine("Clean every 45 days.", page=3)))

    gated = gate(card, flat, {})

    assert [l.text for l in gated.care_schedule] == ["Clean every 7 days."]
    assert gated.dropped == 1


def test_numbers_with_units_count_as_numbers_and_codes_need_a_letter_or_hyphen():
    assert packet._classify("230V") == ("number", ("230",))
    assert packet._classify("5-Liter") == ("number", ("5",))
    assert packet._classify("50/60") == ("number", ("50", "60"))
    assert packet._classify("MC44D-605") == ("code", ("MC44D-605",))
    assert packet._classify("525KS") == ("code", ("525KS",))


def test_every_line_dropped_is_an_empty_card():
    gated = gate(Card(warnings=(INVENTED_CODE,)), EXCERPT, PAGES)

    assert gated.empty and gated.dropped == 1


def test_build_card_sends_only_the_selected_sections_and_reads_the_json():
    client = FakeClient({
        "quick_start": [{"text": "Allow up to 15 minutes for start up.", "page": 8}],
        "care_schedule": [{"text": "Wipe the outer cabinet.", "interval": "7 days", "page": "9"}],
        "consumables": [{"item": "Intake filter", "part_number": "not stated",
                         "interval": "Every 3-years", "page": 14}, "junk"],
        "warnings": [{"text": "", "page": 8}],
    })

    card = packet.build_card(EXCERPT, PAGES, "Drive DeVilbiss 525KS", client)

    prompt = client.prompts[0]
    assert "Drive DeVilbiss 525KS" in prompt and "=== page 8 ===" in prompt
    assert "RETURN AND DISPOSAL" not in prompt
    assert client.kwargs["max_tokens"] == packet.CARD_MAX_TOKENS
    assert card.care_schedule[0].page == 9
    assert card.consumables == (CardLine("Intake filter", 14, "Every 3-years", ""),)
    assert card.warnings == ()
    assert gate(card, EXCERPT, PAGES).dropped == 0


def test_a_card_round_trips_through_its_stored_json():
    card = Card(label="525KS", quick_start=(TRUE_LINE,), consumables=(INVENTED_CODE,),
                dropped=2)

    stored = card.to_dict()

    assert set(stored) >= {"quick_start", "care_schedule", "consumables", "warnings"}
    assert stored["consumables"][0] == {"item": "Replace the intake filter.",
                                        "part_number": "MC99Z-123", "interval": "",
                                        "page": 14}
    assert card_from_dict(stored).quick_start[0].text == TRUE_LINE.text
    assert card_from_dict(stored) == Card(
        label="525KS", quick_start=(CardLine(TRUE_LINE.text, 9),),
        consumables=(INVENTED_CODE,), dropped=2)
