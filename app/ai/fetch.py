"""Find a manufacturer document for a named device and read its text.

`search(query)` returns hits; `fetch(hit)` returns the document's text split
into pages. Tonight the only working provider is the device library: a hit is
one of an entry's stored documents and fetching it reads the stored `.txt`.
The live providers (`google_cse`, `brave`) keep the same interface and raise
NotConfigured, naming the environment variables they need, so a key drops in
without touching a caller.

The live fetch path (HTTP GET, PDF through pypdf, HTML through a tag
stripper) is unit-tested on local bytes and is not exercised against the web.
"""

from __future__ import annotations

import io
import os
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

from app.reference import device_library

SEARCH_PROVIDER_ENV = "SEARCH_PROVIDER"
LIBRARY = "library"
GOOGLE_CSE = "google_cse"
BRAVE = "brave"
PROVIDERS = (LIBRARY, GOOGLE_CSE, BRAVE)
# What each live provider needs before it can run.
PROVIDER_ENV = {
    GOOGLE_CSE: ("GOOGLE_CSE_KEY", "GOOGLE_CSE_ID"),
    BRAVE: ("BRAVE_SEARCH_KEY",),
}

FETCH_TIMEOUT_S = 10
MAX_FETCH_BYTES = 20 * 1024 * 1024
USER_AGENT = "Fit-Check/1.0 (donated equipment screening)"

FORM_FEED = "\f"
_PAGE_MARKER = re.compile(r"^[ \t]*=+[ \t]*page[ \t]+(\d+)[ \t]*=+[ \t]*$", re.IGNORECASE | re.MULTILINE)


class NotConfigured(RuntimeError):
    """The chosen search provider cannot run: it needs a key that is not set."""


class FetchError(RuntimeError):
    """The document could not be read."""


@dataclass(frozen=True)
class Hit:
    """One document a search found. Library hits carry the slug and file."""

    title: str
    url: str
    source: str = LIBRARY
    slug: str = ""
    file: str = ""


@dataclass(frozen=True)
class Document:
    """A fetched document's text. `pages[i]` is page i + 1; None means one page."""

    text: str
    url: str
    title: str
    fetched_at: datetime
    pages: tuple[str, ...] | None = None

    def page_of(self, needle: str) -> int | None:
        """The first page whose text holds `needle` (case-insensitive), or None."""
        if not self.pages or not needle:
            return None
        wanted = needle.casefold()
        for number, text in enumerate(self.pages, start=1):
            if wanted in text.casefold():
                return number
        return None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "fetched_at": self.fetched_at.isoformat(timespec="seconds"),
            "page_count": len(self.pages) if self.pages else 1,
        }


def provider() -> str:
    """The configured search provider, `library` when the env is unset."""
    return (os.environ.get(SEARCH_PROVIDER_ENV) or LIBRARY).strip().lower() or LIBRARY


def live_search_configured() -> bool:
    """True when a live provider is selected and every variable it needs is set."""
    chosen = provider()
    needed = PROVIDER_ENV.get(chosen)
    return bool(needed) and all(os.environ.get(name) for name in needed)


def search(query: str) -> list[Hit]:
    """Documents for the query, from the configured provider."""
    chosen = provider()
    if chosen == LIBRARY:
        return _library_search(query)
    if chosen in PROVIDER_ENV:
        names = " and ".join(PROVIDER_ENV[chosen])
        raise NotConfigured(
            f"SEARCH_PROVIDER={chosen} is not wired yet; it will need {names}. "
            f"Set SEARCH_PROVIDER={LIBRARY} to search the device library."
        )
    raise NotConfigured(
        f"Unknown SEARCH_PROVIDER {chosen!r}; use one of {', '.join(PROVIDERS)}."
    )


def _library_search(query: str) -> list[Hit]:
    hits = []
    for entry in device_library.search(query):
        for doc in entry.documents:
            file = str(doc.get("file") or "")
            if not file:
                continue
            hits.append(Hit(
                title=str(doc.get("title") or file),
                url=str(doc.get("url") or ""),
                source=LIBRARY,
                slug=entry.slug,
                file=file,
            ))
    return hits


def fetch(hit: Hit, *, now: datetime | None = None) -> Document:
    """The hit's document. Library hits read the stored text; others go to the web."""
    fetched_at = now or datetime.now()
    if hit.source == LIBRARY:
        text = device_library.document_text(hit.slug, hit.file)
        if not text:
            raise FetchError(f"The library holds no text for {hit.slug}/{hit.file}.")
        return Document(text=text, url=hit.url, title=hit.title,
                        fetched_at=fetched_at, pages=split_pages(text))
    raw, content_type = _http_get(hit.url)
    return document_from_bytes(raw, content_type, url=hit.url, title=hit.title,
                               fetched_at=fetched_at)


def _http_get(url: str) -> tuple[bytes, str]:
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError(f"Only http and https documents are fetched, not {url!r}.")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            raw = response.read(MAX_FETCH_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except OSError as exc:
        raise FetchError(f"Could not fetch {url}: {exc}") from exc
    if len(raw) > MAX_FETCH_BYTES:
        raise FetchError(f"{url} is larger than {MAX_FETCH_BYTES // 1024 // 1024} MB.")
    return raw, content_type


def document_from_bytes(
    raw: bytes, content_type: str, *, url: str, title: str = "",
    fetched_at: datetime | None = None,
) -> Document:
    """Turn fetched bytes into a Document: PDF through pypdf, else HTML or plain text."""
    fetched_at = fetched_at or datetime.now()
    kind = (content_type or "").lower()
    if "pdf" in kind or raw[:5] == b"%PDF-" or url.lower().split("#")[0].endswith(".pdf"):
        pages = pdf_pages(raw)
        return Document(text=FORM_FEED.join(pages), url=url, title=title,
                        fetched_at=fetched_at, pages=pages)
    text = raw.decode(_charset(kind), errors="replace")
    if "html" in kind or "<html" in text[:2000].lower():
        page_title, text = html_to_text(text)
        title = title or page_title
    return Document(text=text, url=url, title=title, fetched_at=fetched_at,
                    pages=split_pages(text))


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type)
    return match.group(1) if match else "utf-8"


def pdf_pages(raw: bytes) -> tuple[str, ...]:
    """Each page's text from a PDF, in order."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        return tuple((page.extract_text() or "") for page in reader.pages)
    except (PdfReadError, ValueError, KeyError) as exc:
        raise FetchError(f"Could not read the PDF: {exc}") from exc


def split_pages(text: str) -> tuple[str, ...] | None:
    """Pages split on form feeds or "=== page N ===" markers; None when there are none.

    A document with no page breaks is one page: the quote is the citation.
    """
    if FORM_FEED in text:
        pages = text.split(FORM_FEED)
    elif _PAGE_MARKER.search(text):
        pages = _marked_pages(text)
    else:
        return None
    while pages and not pages[-1].strip():
        pages.pop()
    return tuple(pages) or None


def _marked_pages(text: str) -> list[str]:
    """Pages at the numbers their markers give; text before the first marker is page 1."""
    by_number: dict[int, str] = {}
    markers = list(_PAGE_MARKER.finditer(text))
    lead = text[: markers[0].start()]
    if lead.strip():
        by_number[1] = lead
    for marker, following in zip(markers, [*markers[1:], None]):
        end = following.start() if following else len(text)
        number = max(1, int(marker.group(1)))
        by_number[number] = by_number.get(number, "") + text[marker.end():end]
    return [by_number.get(n, "") for n in range(1, max(by_number) + 1)]


class _TextExtractor(HTMLParser):
    SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})
    BLOCK = frozenset({
        "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5",
        "h6", "section", "article", "table", "ul", "ol", "dd", "dt", "header",
        "footer", "main", "nav", "title",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, visible text) of an HTML page, scripts and styles removed."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip()
             for line in "".join(parser.parts).splitlines())
    text = "\n".join(line for line in lines if line)
    return parser.title.strip(), text
