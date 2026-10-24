"""List-upload headers and quantity cap, the model's visitor message, and
supply answers keyed the same way on save and load."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest
from sqlalchemy import func, insert, select

from app.ai import client as client_module
from app.ai.client import UNAVAILABLE_MESSAGE, GenAiClient, GenAiError
from app.config import SETTINGS
from app.data import db
from app.data import marketplace_repo as repo
from app.data.marketplace_schema import supply_answers
from app.domain.supply_answer import SupplyAnswer
from app.intake.tables import IntakeError, parse_table
from app.web.forms import MAX_QUANTITY


# --- List-upload headers --------------------------------------------------


@pytest.mark.parametrize("header", ["device_type", "Device Type", "type"])
def test_the_donor_csv_device_header_is_read_as_the_device_column(header):
    lines = parse_table(f"{header},quantity\noxygen concentrator,2\n".encode(), "list.csv")

    assert [(line.device_type, line.quantity) for line in lines] == [
        ("oxygen concentrator", 2)
    ]


def test_a_heading_row_naming_no_device_column_is_refused_not_stored():
    with pytest.raises(IntakeError, match="column headings"):
        parse_table(b"kit,quantity\noxygen concentrator,1\n", "list.csv")


def test_a_headerless_list_still_reads_its_first_row_as_equipment():
    lines = parse_table(b"oxygen concentrator,2\nsuction pump,1\n", "list.csv")

    assert [line.device_type for line in lines] == ["oxygen concentrator", "suction pump"]


# --- List-upload quantity cap ---------------------------------------------


def test_a_list_line_above_the_form_cap_is_refused_with_the_cap_named():
    raw = b"Device,Quantity\noxygen concentrator,1e9\nsuction pump,5\n"

    with pytest.raises(IntakeError, match=f"at most {MAX_QUANTITY}") as caught:
        parse_table(raw, "list.csv")
    assert "oxygen concentrator" in str(caught.value)


def test_a_list_line_at_the_cap_is_kept():
    lines = parse_table(f"Device,Quantity\nsuction pump,{MAX_QUANTITY}\n".encode(), "list.csv")

    assert lines[0].quantity == MAX_QUANTITY


# --- The model's visitor message ------------------------------------------

SECRET = "Could not find config file at /var/tmp/secret-oci/config"


def test_a_client_build_failure_shows_a_plain_message_and_logs_the_detail(
    monkeypatch, caplog
):
    def refuse(settings):
        raise FileNotFoundError(SECRET)

    monkeypatch.setattr(client_module, "build_signer_and_config", refuse)

    with caplog.at_level(logging.WARNING, logger="app.ai.client"):
        with pytest.raises(GenAiError) as caught:
            GenAiClient(SETTINGS)._ensure_client()

    assert str(caught.value) == UNAVAILABLE_MESSAGE
    assert "/var/tmp" not in str(caught.value) and ".oci" not in str(caught.value)
    assert SECRET in caplog.text


def test_a_failed_chat_call_shows_a_plain_message_and_logs_the_detail(caplog):
    class Refusing:
        def chat(self, details):
            raise RuntimeError(f"ServiceError: {SECRET}")

    chat = GenAiClient(replace(SETTINGS, compartment_id="ocid1.compartment.oc1..fake"))
    chat._client = Refusing()

    with caplog.at_level(logging.WARNING, logger="app.ai.client"):
        with pytest.raises(GenAiError) as caught:
            chat.complete("hello")

    assert str(caught.value) == UNAVAILABLE_MESSAGE
    assert SECRET in caplog.text


# --- Supply answers: one key on save and load ------------------------------


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'supply_key.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    db.init_db()
    yield
    if db._engine is not None:
        db._engine.dispose()


def an_answer(**kw) -> SupplyAnswer:
    base = dict(device_type="oxygen concentrator", model="525KS", country="Uganda",
                consumables_available=False, spare_parts_available=True,
                recorded_by="Fit-Check field team", note="Filters not stocked")
    return SupplyAnswer(**{**base, **kw})


def stored_rows() -> int:
    with db.get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(supply_answers)).scalar_one()


def test_resaving_in_another_case_updates_the_one_row(database):
    first = repo.save_supply_answer(an_answer())
    again = repo.save_supply_answer(
        an_answer(device_type="Oxygen  Concentrator", model="525ks", country="uganda",
                  recorded_by="Second")
    )

    assert again == first
    assert stored_rows() == 1
    assert repo.load_supply_answer("oxygen concentrator", "525KS", "Uganda").recorded_by == "Second"


def test_a_type_wide_answer_resaved_in_another_case_updates_the_one_row(database):
    repo.save_supply_answer(an_answer(model=""))
    repo.save_supply_answer(an_answer(model="", device_type="OXYGEN CONCENTRATOR",
                                      recorded_by="Second"))

    assert stored_rows() == 1


def test_a_row_stored_before_the_fix_is_found_and_updated(database):
    # Written straight to the table in the seed's spelling, as older saves did.
    with db.get_engine().begin() as conn:
        conn.execute(insert(supply_answers).values(
            device_type="oxygen concentrator", model="525KS", country="Uganda",
            consumables_available=False, spare_parts_available=True,
            recorded_by="Fit-Check field team", note="",
        ))

    assert repo.load_supply_answer("Oxygen Concentrator", " 525ks ", "UGANDA") is not None
    repo.save_supply_answer(an_answer(model="525ks", country="uganda", recorded_by="Second"))
    assert stored_rows() == 1
