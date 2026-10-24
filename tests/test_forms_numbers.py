"""Number fields: non-finite and out-of-range input is a FormError, never a 500.

"inf", "nan" and "1e400" all parse as floats. A whole-number field used to
raise OverflowError on them, which no route catches; a decimal field used to
store them. The clinic form bounds temperature and humidity.
"""
from __future__ import annotations

import csv
import io
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.main import app
from app.web import csv_routes, forms, marketplace_routes
from app.web.forms import FormError
from tests.factories import facility
from tests.test_b8_csv_screen import FIVE_ROWS

NOT_FINITE = ["inf", "-inf", "Infinity", "nan", "1e400", "-1e400"]


@pytest.mark.parametrize("raw", NOT_FINITE)
def test_whole_number_refuses_values_with_no_integer(raw):
    with pytest.raises(FormError, match="must be a number"):
        forms.whole_number(raw, "How many")


@pytest.mark.parametrize("raw", NOT_FINITE)
def test_decimal_refuses_non_finite_values(raw):
    with pytest.raises(FormError, match="must be a number"):
        forms.decimal(raw, "Temperature")


def test_decimal_bounds_are_inclusive_and_optional():
    assert forms.decimal("-50", "T", minimum=-50, maximum=70) == -50.0
    assert forms.decimal("70", "T", minimum=-50, maximum=70) == 70.0
    with pytest.raises(FormError, match="cannot be below -50 C"):
        forms.decimal("-50.5", "T", minimum=-50, maximum=70, unit=" C")
    with pytest.raises(FormError, match="cannot be above 100%"):
        forms.decimal("101", "Humidity", minimum=0, maximum=100, unit="%")
    assert forms.decimal("-1e6", "T") == -1e6
    assert forms.decimal("", "T", default=30.0, minimum=40) == 30.0


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'numbers.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setenv("GUDID_LOOKUP", "off")
    monkeypatch.setattr(csv_routes, "_HELD", {})
    monkeypatch.setattr(marketplace_routes, "categorise_support", lambda *a: None)
    repository.setup()
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def register(client, **fields):
    data = {"name": "Number Clinic", "country": "Uganda", **fields}
    return client.post("/clinic", data=data, follow_redirects=False)


@pytest.mark.parametrize("field", ["mains_voltage_v", "mains_frequency_hz", "outages_per_week"])
@pytest.mark.parametrize("raw", ["inf", "1e400"])
def test_the_clinic_form_answers_an_infinite_whole_number_with_the_error_page(client, field, raw):
    response = register(client, **{field: raw})
    assert response.status_code == 400
    assert "must be a number" in response.text
    assert repo.list_facilities() == []


@pytest.mark.parametrize("fields, message", [
    ({"ambient_temp_max_c": "nan"}, "Temperature must be a number"),
    ({"ambient_temp_max_c": "inf"}, "Temperature must be a number"),
    ({"ambient_temp_max_c": "71"}, "Temperature cannot be above 70 C"),
    ({"ambient_temp_max_c": "-51"}, "Temperature cannot be below -50 C"),
    ({"humidity_max_pct": "-50"}, "Humidity cannot be below 0%"),
    ({"humidity_max_pct": "101"}, "Humidity cannot be above 100%"),
    ({"humidity_max_pct": "nan"}, "Humidity must be a number"),
])
def test_the_clinic_form_refuses_impossible_climate_numbers(client, fields, message):
    response = register(client, **fields)
    assert response.status_code == 400
    assert message in response.text
    assert repo.list_facilities() == []


def test_the_clinic_form_still_takes_sensible_climate_numbers(client):
    response = register(client, ambient_temp_max_c="34.5", humidity_max_pct="85")
    assert response.status_code == 303
    saved = repo.load_facility("number-clinic")
    assert (saved.ambient_temp_max_c, saved.humidity_max_pct) == (34.5, 85.0)


def test_an_infinite_csv_quantity_is_a_row_error_not_a_500(client):
    repo.save_facility(facility(slug="test-clinic", latitude=0.0, longitude=0.0))
    rows = list(csv.reader(io.StringIO(FIVE_ROWS)))
    header, first = rows[0], rows[1]
    first[header.index("Quantity")] = "inf"
    out = io.StringIO()
    csv.writer(out).writerows([header, first])
    response = client.post(
        "/offer/csv",
        data={"facility": "test-clinic", "donor_name": "Mercy Regional",
              "donor_email": "biomed@example.org"},
        files={"inventory": ("stock.csv", out.getvalue().encode(), "text/csv")},
    )
    assert response.status_code == 200
    assert "quantity must be a number" in response.text
