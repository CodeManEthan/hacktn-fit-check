"""app/ai/fetch.py: library search and fetch, page splitting, local PDF and HTML.

The live fetch path is exercised on local bytes and a faked opener; nothing
here touches the network.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.ai import fetch
from app.reference import device_library as lib

LIBRARY = Path(__file__).parent / "fixtures" / "library"
NOW = datetime(2026, 10, 18, 3, 0, 0)


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setattr(lib, "LIBRARY_PATH", LIBRARY / "b4-index.json")
    monkeypatch.setattr(lib, "LIBRARY_DIR", LIBRARY)
    monkeypatch.delenv(fetch.SEARCH_PROVIDER_ENV, raising=False)


def minimal_pdf(pages: list[str]) -> bytes:
    """A valid PDF with one line of Helvetica text per page, xref offsets exact."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {4 + 2 * i} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


# ---- search -----------------------------------------------------------------

def test_library_search_returns_each_document_of_each_matching_entry(library):
    hits = fetch.search("DeVilbiss 525DS")

    assert hits[0] == fetch.Hit(
        title="DeVilbiss 525 owner's manual", url="https://example.org/525ds-manual.pdf",
        source="library", slug="b4-525ds", file="b4-525ds/owners_manual.txt",
    )
    assert [(h.slug, h.file) for h in hits] == [
        ("b4-525ds", "b4-525ds/owners_manual.txt"),
        ("b4-525ds", "b4-525ds/sell_sheet.txt"),
        ("b4-525ks", "b4-525ks/owners_manual.txt"),
    ]


def test_library_is_the_default_provider(library):
    assert fetch.provider() == "library"
    assert not fetch.live_search_configured()
    assert fetch.search("nothing like it") == []


@pytest.mark.parametrize("name, needed", [
    ("google_cse", ("GOOGLE_CSE_KEY", "GOOGLE_CSE_ID")),
    ("brave", ("BRAVE_SEARCH_KEY",)),
])
def test_live_providers_raise_not_configured_naming_their_variables(library, monkeypatch, name, needed):
    monkeypatch.setenv(fetch.SEARCH_PROVIDER_ENV, name)

    with pytest.raises(fetch.NotConfigured) as caught:
        fetch.search("525DS")

    for variable in needed:
        assert variable in str(caught.value)
    assert not fetch.live_search_configured()
    for variable in needed:
        monkeypatch.setenv(variable, "x")
    assert fetch.live_search_configured()


def test_an_unknown_provider_is_refused(library, monkeypatch):
    monkeypatch.setenv(fetch.SEARCH_PROVIDER_ENV, "bing")
    with pytest.raises(fetch.NotConfigured, match="library, google_cse, brave"):
        fetch.search("525DS")


# ---- library fetch ----------------------------------------------------------

def test_library_fetch_reads_the_stored_text_split_on_form_feeds(library):
    hit = fetch.search("525DS")[0]

    doc = fetch.fetch(hit, now=NOW)

    assert doc.url == hit.url and doc.title == hit.title and doc.fetched_at == NOW
    assert len(doc.pages) == 11
    assert "97-127 V~, 60 Hz" in doc.pages[10]
    assert doc.page_of("97-127 v~") == 11
    assert doc.page_of("+5 to +35") == 3
    assert doc.page_of("not in it") is None


def test_library_fetch_of_a_missing_file_is_a_fetch_error(library):
    with pytest.raises(fetch.FetchError):
        fetch.fetch(fetch.Hit(title="x", url="", slug="b4-525ds", file="b4-525ds/absent.txt"))


# ---- page splitting ---------------------------------------------------------

def test_split_pages_on_form_feeds_drops_trailing_blank_pages():
    assert fetch.split_pages("one\ftwo\f\n") == ("one", "two")


def test_split_pages_on_markers_keeps_the_marker_numbers():
    text = "cover\n=== page 2 ===\nsecond\n=== Page 4 ===\nfourth\n"

    pages = fetch.split_pages(text)

    assert pages[0].strip() == "cover"
    assert pages[1].strip() == "second"
    assert pages[2] == ""
    assert pages[3].strip() == "fourth"


def test_split_pages_without_breaks_is_one_page():
    assert fetch.split_pages("all on one page") is None
    doc = fetch.Document(text="x", url="", title="", fetched_at=NOW)
    assert doc.page_of("x") is None


# ---- live fetch on local bytes ----------------------------------------------

def test_a_pdf_becomes_one_text_page_per_pdf_page(tmp_path):
    path = tmp_path / "spec.pdf"
    path.write_bytes(minimal_pdf(["Rated 115 V 60 Hz", "Operating 10 to 40 C"]))

    doc = fetch.document_from_bytes(path.read_bytes(), "application/pdf",
                                    url="https://example.org/spec.pdf", title="Spec")

    assert len(doc.pages) == 2
    assert "115 V 60 Hz" in doc.pages[0]
    assert doc.page_of("10 to 40") == 2
    assert doc.text.count("\f") == 1


def test_a_pdf_is_recognised_by_its_bytes_without_a_content_type():
    doc = fetch.document_from_bytes(minimal_pdf(["Only page"]), "", url="https://x/y")
    assert doc.pages == ("Only page",)


def test_a_broken_pdf_is_a_fetch_error():
    with pytest.raises(fetch.FetchError):
        fetch.document_from_bytes(b"%PDF-1.4 nonsense", "application/pdf", url="https://x/a.pdf")


def test_html_loses_its_tags_scripts_and_styles(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        "<html><head><title>525DS product page</title><style>p{color:red}</style>"
        "<script>var x = 115;</script></head><body><h1>DeVilbiss 525DS</h1>"
        "<p>Electrical: 115&nbsp;V, 60 Hz</p><ul><li>310 W</li></ul></body></html>",
        encoding="utf-8",
    )

    doc = fetch.document_from_bytes(path.read_bytes(), "text/html; charset=utf-8",
                                    url="https://example.org/525ds")

    assert doc.title == "525DS product page"
    assert doc.text.splitlines() == ["DeVilbiss 525DS", "Electrical: 115\xa0V, 60 Hz", "310 W"]
    assert "var x" not in doc.text and "color" not in doc.text
    assert doc.pages is None


def test_live_fetch_uses_a_ten_second_get(monkeypatch):
    seen = {}

    class Response:
        headers = {"Content-Type": "text/plain"}

        def read(self, limit):
            return b"230 V 50 Hz"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout):
        seen["url"], seen["timeout"] = request.full_url, timeout
        return Response()

    monkeypatch.setattr(fetch.urllib.request, "urlopen", opener)

    doc = fetch.fetch(fetch.Hit(title="Plate", url="https://example.org/a.txt", source="brave"))

    assert seen == {"url": "https://example.org/a.txt", "timeout": 10}
    assert doc.text == "230 V 50 Hz"


def test_live_fetch_refuses_non_http_urls():
    with pytest.raises(fetch.FetchError, match="http"):
        fetch.fetch(fetch.Hit(title="x", url="file:///etc/passwd", source="brave"))
