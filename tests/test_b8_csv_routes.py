"""[B8] §8 routes: upload form, result table, download, and the post button.

Each test gets its own SQLite file and a TestClient without the lifespan; the
test saves the one clinic it screens against. GUDID is off and no model is
called (categorisation on post is the keyword table).
"""

from __future__ import annotations

import csv
import html
import io
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.data import db, repository
from app.data import marketplace_repo as repo
from app.main import app
from app.web import csv_routes
from tests.factories import facility
from tests.test_b8_csv_screen import FIVE_ROWS

CLINIC = facility(slug="test-clinic", name="Test Clinic", country="Testland",
                  latitude=0.0, longitude=0.0)
TYPES = ("oxygen concentrator", "suction pump", "patient monitor")


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'csv.db'}"
    monkeypatch.setattr(db, "SETTINGS", replace(SETTINGS, database_url=url))
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setenv("GUDID_LOOKUP", "off")
    monkeypatch.setattr(csv_routes, "_HELD", {})
    repository.setup()
    repo.save_facility(CLINIC)
    yield TestClient(app)
    if db._engine is not None:
        db._engine.dispose()


def offers_saved() -> int:
    return sum(len(repo.open_offers_for_type(t)) for t in TYPES)


def upload(client, text: str = FIVE_ROWS, **form):
    data = {"facility": CLINIC.slug, "donor_name": "Mercy Regional",
            "donor_email": "biomed@example.org", **form}
    return client.post("/offer/csv", data=data,
                       files={"inventory": ("stock.csv", text.encode(), "text/csv")})


def token_of(body: str) -> str:
    return re.search(r'/offer/csv/([0-9a-f]{32})\.csv', body).group(1)


def test_the_upload_form_lists_the_clinics_and_the_columns(client):
    response = client.get("/offer/csv")
    assert response.status_code == 200
    body = response.text
    assert 'enctype="multipart/form-data"' in body and 'name="inventory"' in body
    assert f'<option value="{CLINIC.slug}" selected>' in body
    assert "frequencies_hz" in body and "manual_languages" in body


def test_the_upload_renders_the_table_and_saves_nothing(client):
    response = upload(client)
    assert response.status_code == 200
    body = response.text
    assert "5 rows screened against Test Clinic" in body
    verdicts = [html.unescape(v) for v in re.findall(r'data-verdict="([^"]+)"', body)]
    # No supply answer is recorded for Testland, so the 525KS asks about its filters.
    assert verdicts == ["needs info", "won't work", "blank row", "error", "needs info"]
    assert "quantity must be a number" in body
    assert "Supply frequency" in body
    assert re.search(r'href="/offer/csv/[0-9a-f]{32}\.csv"', body)
    assert "Post these 3 offers" in body
    assert offers_saved() == 0


def test_the_download_is_the_input_columns_plus_four(client):
    token = token_of(upload(client).text)
    response = client.get(f"/offer/csv/{token}.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "stock-screened-test-clinic.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 5
    assert list(rows[0])[-4:] == ["verdict", "blockers", "send_with", "questions"]
    assert rows[0]["model"] == "525KS" and rows[0]["verdict"] == "needs info"
    assert rows[1]["verdict"] == "won't work"
    assert offers_saved() == 0


def test_the_result_page_can_be_opened_again(client):
    token = token_of(upload(client).text)
    again = client.get(f"/offer/csv/{token}")
    assert again.status_code == 200 and "5 rows screened" in again.text
    assert offers_saved() == 0


def test_posting_saves_the_screened_rows_once(client):
    token = token_of(upload(client).text)
    assert offers_saved() == 0
    response = client.post(f"/offer/csv/{token}/post",
                           data={"donor_name": "Mercy Regional", "donor_email": "biomed@example.org"})
    assert response.status_code == 200
    assert "3 offers saved for Mercy Regional" in response.text
    ids = [int(i) for i in re.findall(r'href="/offer/(\d+)"', response.text)]
    assert len(ids) == 3
    assert offers_saved() == 3
    saved = repo.load_offer(ids[0])
    assert saved.device.model.value == "525KS" and saved.quantity_offered == 2
    assert saved.device.dated_items.value is False
    assert saved.donor_email == "biomed@example.org"
    # A second press shows what was saved and saves nothing more.
    again = client.post(f"/offer/csv/{token}/post",
                        data={"donor_name": "Mercy Regional", "donor_email": "biomed@example.org"})
    assert again.status_code == 200 and "3 offers saved" in again.text
    assert offers_saved() == 3
    # A saved offer page opens.
    assert client.get(f"/offer/{ids[0]}").status_code == 200


def test_posting_needs_the_donor(client):
    token = token_of(upload(client, donor_name="", donor_email="").text)
    response = client.post(f"/offer/csv/{token}/post", data={"donor_name": "", "donor_email": ""})
    assert response.status_code == 400
    assert "Your name is needed" in response.text
    assert f'href="/offer/csv/{token}"' in response.text
    assert offers_saved() == 0


@pytest.mark.parametrize("token", ["0" * 32, "not-a-token", "ABCDEF" * 6])
def test_an_unknown_token_is_a_clean_error(client, token):
    for response in (client.get(f"/offer/csv/{token}.csv"),
                     client.get(f"/offer/csv/{token}"),
                     client.post(f"/offer/csv/{token}/post",
                                 data={"donor_name": "M", "donor_email": "m@x"})):
        assert response.status_code == 404
        assert "not held any more" in response.text
        assert "Traceback" not in response.text
    assert offers_saved() == 0


def test_an_expired_token_is_a_clean_error(client, monkeypatch):
    start = csv_routes._now()
    token = token_of(upload(client).text)
    assert client.get(f"/offer/csv/{token}.csv").status_code == 200
    monkeypatch.setattr(csv_routes, "_now", lambda: start + csv_routes.CACHE_SECONDS + 5)
    response = client.get(f"/offer/csv/{token}.csv")
    assert response.status_code == 404 and "kept for an hour" in response.text
    post = client.post(f"/offer/csv/{token}/post",
                       data={"donor_name": "M", "donor_email": "m@x"})
    assert post.status_code == 404
    assert offers_saved() == 0


def test_an_unreadable_file_or_no_clinic_is_an_error_page(client):
    bad = upload(client, text="name,colour\nx,y\n")
    assert bad.status_code == 400 and "stock.csv: None of the header" in bad.text
    nobody = upload(client, facility="nowhere")
    assert nobody.status_code == 400 and "No clinic is registered as nowhere" in nobody.text
    nofile = client.post("/offer/csv", data={"facility": CLINIC.slug})
    assert nofile.status_code == 400 and "Choose a CSV file" in nofile.text


def test_the_offer_page_and_the_nonprofit_bar_link_here(client):
    assert 'href="/offer/csv"' in client.get("/offer").text
    client.cookies.set("role", "nonprofit")
    bar = client.get("/supply").text
    assert re.search(r'<nav class="desk-links"[^>]*>.*href="/offer/csv".*</nav>', bar, re.S)


def test_offer_ids_still_route_past_the_csv_paths(client):
    assert client.get("/offer/999").status_code == 400  # the existing "No offer" page
