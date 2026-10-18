"""app/ai/extract.py: name step, library hit, no-hit read and the gate.

No model is called: a fake client hands back queued JSON replies and records
each prompt, so the tests also see how many calls a path makes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.ai import client as client_module
from app.ai import extract as ex
from app.ai import fetch
from app.config import SETTINGS
from app.domain.device import DeviceFacts
from app.domain.provenance import UNKNOWN, Fact, Provenance
from app.reference import device_library as lib

LIBRARY = Path(__file__).parent / "fixtures" / "library"
DONOR = Provenance.DONOR_STATED
PLATE = Provenance.NAMEPLATE
SHEET = Provenance.SPEC_SHEET


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self.replies.pop(0)


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setattr(lib, "LIBRARY_PATH", LIBRARY / "b4-index.json")
    monkeypatch.setattr(lib, "LIBRARY_DIR", LIBRARY)
    monkeypatch.delenv(fetch.SEARCH_PROVIDER_ENV, raising=False)


@pytest.fixture
def no_library(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, "LIBRARY_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(lib, "LIBRARY_DIR", tmp_path / "absent")
    monkeypatch.delenv(fetch.SEARCH_PROVIDER_ENV, raising=False)


def naming(manufacturer=None, model=None, device_type=None, plate_text=None):
    return {"manufacturer": manufacturer, "model": model, "device_type": device_type,
            "plate_text": plate_text}


# ---- step 1: name it ----------------------------------------------------------

def test_name_device_reads_text_with_the_reasoning_model():
    client = FakeClient(naming("Drive DeVilbiss", "525DS", "oxygen concentrator", "ignored"))

    named = ex.name_device("DeVilbiss 525DS concentrator", "", client)

    assert named == ex.Naming("Drive DeVilbiss", "525DS", "oxygen concentrator", None)
    call = client.calls[0]
    assert "model" not in call and "image_data_url" not in call and "max_tokens" not in call
    assert "DeVilbiss 525DS concentrator" in call["prompt"]
    assert "there is no image" in call["prompt"]


def test_name_device_reads_a_photo_with_the_vision_model():
    client = FakeClient(naming("Philips", "VM6", None, "SureSigns VM6\n100-240 V~ 50/60 Hz"))

    named = ex.name_device("", "data:image/png;base64,AAAA", client)

    assert named.plate_text == "SureSigns VM6\n100-240 V~ 50/60 Hz"
    assert named.device_type is None
    call = client.calls[0]
    assert call["model"] == SETTINGS.vision_model
    assert call["image_data_url"] == "data:image/png;base64,AAAA"
    # The 4096 ceiling is the client's, per model (client.MODEL_MAX_TOKENS).
    assert "max_tokens" not in call
    assert client_module.max_tokens_for(call["model"], client_module.DEFAULT_MAX_TOKENS) == 4096
    assert "transcribe every printed line" in call["prompt"]


def test_name_device_treats_null_words_as_none():
    client = FakeClient(naming("null", " ", "unknown", None))
    assert ex.name_device("an old monitor", "", client) == ex.Naming()


# ---- step 2a: library hit -----------------------------------------------------

def test_a_525ds_paste_hits_the_525ds_entry_and_reads_nothing_more(library):
    client = FakeClient(naming("Drive DeVilbiss", "525DS", "oxygen concentrator"))

    result = ex.extract(typed_text="Specifications 525DS\n310 Watt Average", client=client)

    assert len(client.calls) == 1
    assert result.library_hit and result.library_slug == "b4-525ds"
    assert result.device_type == "oxygen concentrator"
    facts = result.facts
    assert facts.voltage_min_v == Fact(97, SHEET, "DeVilbiss 525 owner's manual, p.11")
    assert facts.voltage_max_v.value == 127
    assert facts.frequencies_hz.value == [60]
    assert facts.temp_min_c.value == 5 and facts.temp_max_c.value == 35
    assert facts.power_watts == Fact(310, SHEET, "525DS sell sheet, p.2")
    assert facts.raw_input == "Specifications 525DS\n310 Watt Average"
    assert facts.documents.known
    assert result.citations["voltage_min_v"].link == "https://example.org/525ds-manual.pdf#page=11"
    assert result.citations["power_watts"].to_dict() == {
        "title": "525DS sell sheet", "url": "https://example.org/525ds-sell-sheet.pdf#page=2", "page": 2,
    }


def test_spacing_and_hyphens_in_the_named_model_still_hit(library):
    client = FakeClient(naming("DeVilbiss", "525-KS"))
    assert ex.extract(typed_text="525-KS", client=client).library_slug == "b4-525ks"


def test_a_url_with_an_anchor_keeps_it(library):
    client = FakeClient(naming("DeVilbiss", "525KS"))
    result = ex.extract(typed_text="525KS", client=client)
    assert result.citations["frequencies_hz"].link == "https://example.org/525ks-manual.pdf#page=1"


@pytest.mark.parametrize("model", ["525", "DeVilbiss 525", None])
def test_devilbiss_525_is_no_hit_and_takes_the_read_path(library, model):
    client = FakeClient(naming("DeVilbiss", model, "oxygen concentrator"), {})

    result = ex.extract(typed_text="DeVilbiss 525 oxygen concentrator", client=client)

    assert not result.library_hit
    assert len(client.calls) == 2
    assert result.facts.voltage_min_v is UNKNOWN
    assert result.device_type == "oxygen concentrator"


def test_extract_device_keeps_its_signature_and_returns_facts(library):
    client = FakeClient(naming("Drive DeVilbiss", "525DS"))
    facts = ex.extract_device(typed_text="525DS", image_data_url="", client=client)
    assert isinstance(facts, DeviceFacts) and facts.frequencies_hz.value == [60]


def test_extract_refuses_an_empty_request():
    with pytest.raises(ValueError):
        ex.extract(typed_text="  ", client=FakeClient())


# ---- step 2b: no hit -----------------------------------------------------------

VX200 = (
    'Portable ventilator, model "VX-200", manufactured 2011. Power: 120 V, 60 Hz only.\n'
    "Operating range 10–30°C. Uses single-use flow-sensor cartridges, sold only by the US distributor.\n"
    "Needs a 50 psi oxygen supply. Manual in English only."
)


def read_reply(**fields):
    sources = {k: v[1] for k, v in fields.items()}
    quotes = {k: v[2] for k, v in fields.items() if len(v) > 2}
    reply = {k: v[0] for k, v in fields.items()}
    reply.update(sources=sources, quotes=quotes)
    return reply


def test_no_hit_reads_the_paste_and_the_gate_drops_what_is_not_printed(no_library):
    client = FakeClient(
        naming(None, "VX-200", "ventilator"),
        read_reply(
            model=("VX-200", "donor"),
            year_of_manufacture=(2011, "donor", "manufactured 2011"),
            voltage_min_v=(120, "donor", "120 V"),
            voltage_max_v=(120, "donor", "120 V"),
            frequencies_hz=([60], "donor", "60 Hz only"),
            power_watts=(150, "donor", "150 W"),
            temp_min_c=(10, "donor", "10–30°C"),
            temp_max_c=(30, "donor", "10–30°C"),
            humidity_max_pct=(95, "model knowledge"),
            needs_oxygen=(True, "donor", "Needs a 50 psi oxygen supply"),
            needs_distilled_water=(False, "donor", "no water needed"),
            consumables=(["single-use flow-sensor cartridges"], "donor"),
            manual_languages=(["English"], "donor", "Manual in English only"),
        ),
    )

    result = ex.extract(typed_text=VX200, client=client)
    facts = result.facts

    assert facts.model == Fact("VX-200", DONOR, "")
    assert facts.year_of_manufacture.value == 2011
    assert facts.voltage_min_v == Fact(120, DONOR, "120 V")
    assert facts.frequencies_hz.value == [60]
    assert facts.temp_min_c.value == 10 and facts.temp_max_c.value == 30
    assert facts.needs_oxygen.value is True
    assert facts.consumables.value == ["single-use flow-sensor cartridges"]
    assert facts.manual_languages.value == ["English"]
    # Not printed anywhere: 150 W, the humidity from memory, a made-up quote.
    assert facts.power_watts == Fact(note=ex.NOT_FOUND)
    assert facts.humidity_max_pct == Fact(note=ex.NOT_FOUND)
    assert facts.needs_distilled_water == Fact(note=ex.NOT_FOUND)
    assert not facts.power_watts.known
    assert result.document is None and result.citations == {}
    read_prompt = client.calls[1]["prompt"]
    assert VX200 in read_prompt and '"model knowledge"' not in read_prompt
    assert client.calls[1].get("model", "") == ""


def test_a_vague_paste_gives_an_honest_empty_result(no_library):
    client = FakeClient(naming(None, None, "ultrasound scanner"), {"sources": {}, "quotes": {}})
    result = ex.extract(typed_text="Older ultrasound machine, works fine.", client=client)
    assert all(not getattr(result.facts, name).known for name in ex.FIELDS)


def test_the_plate_text_is_a_source_and_marks_nameplate(no_library):
    plate = "MODEL N-560\n100-240 V~ 50/60 Hz 45 VA\nMFD 2009-04"
    client = FakeClient(
        naming("Nellcor", "N-560", "pulse oximeter", plate),
        read_reply(
            voltage_min_v=(100, "nameplate"), voltage_max_v=(240, "nameplate"),
            frequencies_hz=([50, 60], "nameplate"),
            year_of_manufacture=(2009, "donor"),
        ),
    )

    facts = ex.extract(typed_text="", image_data_url="data:image/jpeg;base64,AA", client=client).facts

    assert facts.voltage_min_v.source is PLATE and facts.voltage_max_v.value == 240
    assert facts.frequencies_hz == Fact([50, 60], PLATE, "")
    # Claimed from the paste, which is empty; found on the plate instead.
    assert facts.year_of_manufacture.source is PLATE
    assert "RATING PLATE TRANSCRIPTION" in client.calls[1]["prompt"]


def test_a_source_the_model_was_not_given_is_refused(no_library):
    client = FakeClient(naming(None, "X1"), read_reply(voltage_min_v=(230, "document")))
    facts = ex.extract(typed_text="X1 230 V", client=client).facts
    assert facts.voltage_min_v == Fact(note=ex.NOT_FOUND)


def test_a_live_document_is_read_and_cited_when_a_provider_is_configured(no_library, monkeypatch):
    doc = fetch.Document(text="Specs\fRated 230 V 50 Hz\fOperating 5 to 40 C", url="https://x/m.pdf",
                         title="X1 manual", fetched_at=datetime(2026, 10, 18),
                         pages=("Specs", "Rated 230 V 50 Hz", "Operating 5 to 40 C"))
    monkeypatch.setattr(fetch, "live_search_configured", lambda: True)
    monkeypatch.setattr(fetch, "search", lambda q: [fetch.Hit("X1 manual", doc.url, "brave")])
    monkeypatch.setattr(fetch, "fetch", lambda hit: doc)
    client = FakeClient(
        naming("Acme", "X1"),
        read_reply(voltage_min_v=(230, "document", "Rated 230 V 50 Hz"),
                   temp_max_c=(40, "document", "Operating 5 to 40 C")),
    )

    result = ex.extract(typed_text="Acme X1", client=client)

    assert result.document is doc
    assert result.facts.voltage_min_v == Fact(230, SHEET, "X1 manual, p.2")
    assert result.facts.temp_max_c.note == "X1 manual, p.3"
    assert result.citations["voltage_min_v"].link == "https://x/m.pdf#page=2"
    assert result.facts.documents.value[0]["url"] == "https://x/m.pdf"
    assert "MANUFACTURER DOCUMENT (X1 manual)" in client.calls[1]["prompt"]
    assert result.stored_document()["document"]["page_count"] == 3


# ---- the gate ------------------------------------------------------------------

def gate(paste="", plate="", **facts):
    return ex.gate_against_text(DeviceFacts(**facts), {DONOR: paste, PLATE: plate})


@pytest.mark.parametrize("text", ["115V", "115 V", "115-120 V", "rated 115.0 volts", "AC115"])
def test_numbers_match_their_printed_forms(text):
    assert gate(text, voltage_min_v=Fact(115, DONOR)).voltage_min_v.value == 115


def test_35_matches_35_point_0_and_a_range_end():
    assert gate("max 35.0 C", temp_max_c=Fact(35, DONOR)).temp_max_c.known
    assert gate("10-35 C", temp_max_c=Fact(35.0, DONOR)).temp_max_c.known


def test_a_dash_after_a_digit_is_a_range_and_elsewhere_a_minus():
    assert not gate("10-40 C", temp_min_c=Fact(-40, DONOR)).temp_min_c.known
    assert gate("storage (-25 C)", temp_min_c=Fact(-25, DONOR)).temp_min_c.known
    assert gate("storage −25 C", temp_min_c=Fact(-25, DONOR)).temp_min_c.known


def test_a_number_inside_another_number_does_not_match():
    assert not gate("1150 W", power_watts=Fact(115, DONOR)).power_watts.known
    assert not gate("2.115 kg", power_watts=Fact(115, DONOR)).power_watts.known


def test_decimal_commas_and_thousands_both_read():
    assert gate("1,55 Amp", power_watts=Fact(1.55, DONOR)).power_watts.known
    assert gate("1,200 W", power_watts=Fact(1200, DONOR)).power_watts.known


def test_frequencies_keep_only_the_printed_ones():
    gated = gate("60 Hz only", frequencies_hz=Fact([50, 60], DONOR)).frequencies_hz
    assert gated == Fact([60], DONOR, "")
    assert gate("50/60 Hz", frequencies_hz=Fact([50, 60], DONOR)).frequencies_hz.value == [50, 60]


def test_an_empty_frequency_list_is_unknown():
    assert gate("no mains", frequencies_hz=Fact([], DONOR, "no mains")).frequencies_hz == Fact(note=ex.NOT_FOUND)


def test_one_unprinted_consumable_drops_the_whole_list():
    paste = "Uses MC44D-605 intake filter and 303DZ-605 cabinet filter."
    whole = gate(paste, consumables=Fact(["MC44D-605", "303DZ 605"], DONOR)).consumables
    assert whole.value == ["MC44D-605", "303DZ 605"]
    partial = gate(paste, consumables=Fact(["MC44D-605", "sieve beds"], DONOR)).consumables
    assert partial == Fact(note=ex.NOT_FOUND)


def test_no_consumables_stands_only_on_a_quote_saying_so():
    assert gate("It uses no consumables.", consumables=Fact([], DONOR, "uses no consumables")).consumables.value == []
    assert not gate("Works fine.", consumables=Fact([], DONOR, "")).consumables.known


@pytest.mark.parametrize("paste, claimed, kept", [
    ("Manual: en, es, fr", ["English", "Spanish", "French"], ["English", "Spanish", "French"]),
    ("Manuals in ESPAÑOL and FRANÇAIS", ["es", "French"], ["Spanish", "French"]),
    ("Manual in English only", ["English", "German"], ["English"]),
    ("Manual in zh-Hans", ["Chinese (Simplified)"], ["Chinese (Simplified)"]),
])
def test_languages_are_normalised_and_unprinted_ones_dropped(paste, claimed, kept):
    assert gate(paste, manual_languages=Fact(claimed, DONOR)).manual_languages.value == kept


@pytest.mark.parametrize("paste", ["Is it working? no idea, de facto old", "manual per request"])
def test_ordinary_words_are_not_read_as_language_codes(paste):
    claimed = ["Italian", "Norwegian", "German", "Persian"]
    assert not gate(paste, manual_languages=Fact(claimed, DONOR)).manual_languages.known


def test_names_must_be_printed_ignoring_case_spaces_and_hyphens():
    assert gate("Nellcor N 560", model=Fact("N-560", DONOR)).model.known
    assert not gate("Nellcor oximeter", model=Fact("N-560", DONOR)).model.known


def test_booleans_and_complexity_stand_on_their_quote():
    paste = "Needs a 50 psi oxygen supply. Requires trained biomedical engineer for service."
    gated = gate(
        paste,
        needs_oxygen=Fact(True, DONOR, "Needs a 50 psi oxygen supply"),
        operating_complexity=Fact("high", DONOR, "Requires trained biomedical engineer"),
        needs_compressed_air=Fact(False, DONOR, "no compressed air"),
        needs_stable_power=Fact(True, DONOR, "no"),
    )
    assert gated.needs_oxygen.value is True
    assert gated.operating_complexity.value == "high"
    assert gated.needs_compressed_air == Fact(note=ex.NOT_FOUND)
    assert gated.needs_stable_power == Fact(note=ex.NOT_FOUND)


def test_a_value_found_only_in_the_other_source_takes_that_provenance():
    gated = gate("", "230V 50Hz", voltage_min_v=Fact(230, DONOR))
    assert gated.voltage_min_v.source is PLATE


def test_the_claimed_source_wins_when_both_hold_the_value():
    gated = gate("230 V", "230V 50Hz", voltage_min_v=Fact(230, PLATE))
    assert gated.voltage_min_v.source is PLATE


def test_unknown_and_ungated_fields_pass_through():
    gated = gate("x", expiry_date=Fact("2027-01-01", DONOR), dated_items=Fact(True, DONOR))
    assert gated.expiry_date.value == "2027-01-01" and gated.dated_items.value is True
    assert gated.voltage_min_v is UNKNOWN


def test_the_read_step_coerces_and_refuses_unusable_values():
    payload = {
        "voltage_min_v": "115 V", "voltage_max_v": "100-240", "frequencies_hz": "50/60",
        "operating_complexity": "expert", "needs_oxygen": "yes", "consumables": "filter",
        "sources": {k: "donor" for k in ex.FIELDS}, "quotes": {"needs_oxygen": "uses oxygen"},
    }
    facts = ex._facts_from_payload(payload, allowed=frozenset({"donor"}))
    assert facts.voltage_min_v.value == 115
    assert facts.voltage_max_v == Fact(note=ex.NOT_USABLE)
    assert facts.frequencies_hz.value == [50, 60]
    assert facts.operating_complexity == Fact(note=ex.NOT_USABLE)
    assert facts.needs_oxygen == Fact(True, DONOR, "uses oxygen")
    assert facts.consumables.value == ["filter"]
