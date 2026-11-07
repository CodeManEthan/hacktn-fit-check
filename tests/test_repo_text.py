"""The repository text carries no pointers a reader can't follow.

Comments, docstrings, docs and tests explain their reasoning in place. They do
not cite a planning document by section number, a work item by a bracketed tag,
or a person or stage of the work by an internal name. Every tracked file is
read as UTF-8 text; binary files are skipped, and so is data/library/, which is
vendored manufacturer text. A section sign that follows an IEC or ISO standard
number on the same line points at a public standard and is allowed.

Test file names and fixture paths are checked too: they name their subject,
not the stage of the work that added them.

The patterns are assembled from pieces so this file does not match itself.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SECTION = chr(0xA7)
VENDORED = ("data/library/",)
NO_BARE_SECTION_UNDER = ("data/",)

TAG_LETTERS = "BFRAC"
TEXT_PATTERNS = [
    re.compile("design" + r"\s*" + SECTION, re.I),
    re.compile("design" + r" \d+\.\d+", re.I),
    re.compile(r"\[" + "[" + TAG_LETTERS + r"]\d+" + r"\]", re.I),
    re.compile(r"\b" + "the" + " lead" + r"\b", re.I),
    re.compile(r"\b" + "this" + " round" + r"\b", re.I),
    re.compile("report" + r" F\d+", re.I),
    # A bare build name: a capital B and digits, but not a CSS colour such as #B45309.
    re.compile(r"(?<![#\w])" + "B" + r"\d+\b"),
]
BARE_SECTION = re.compile(SECTION + r"\s*\d")
STANDARD_BEFORE = re.compile(r"\b(?:IEC|ISO)[\s/-]*\d", re.I)
PATH_PATTERNS = [
    re.compile("test" + r"_b\d+"),
    re.compile(r"/b\d+-"),
    re.compile(r"/b\d+_"),
]


def _tracked_paths() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.decode("utf-8").split("\0") if p]


def _text(path: Path) -> str | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _hits_in_line(rel: str, line: str) -> bool:
    if any(p.search(line) for p in TEXT_PATTERNS):
        return True
    if rel.startswith(NO_BARE_SECTION_UNDER):
        return False
    for match in BARE_SECTION.finditer(line):
        if not STANDARD_BEFORE.search(line[: match.start()]):
            return True
    return False


def test_no_tracked_file_cites_an_internal_document_or_name():
    hits = []
    for rel in _tracked_paths():
        if rel.startswith(VENDORED):
            continue
        text = _text(ROOT / rel)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _hits_in_line(rel, line):
                hits.append(f"{rel}:{number}")
    assert not hits, "internal citations:\n" + "\n".join(hits)


def test_no_tracked_path_is_named_for_a_stage_of_the_work():
    hits = [rel for rel in _tracked_paths() if any(p.search(rel) for p in PATH_PATTERNS)]
    assert not hits, "paths named for a stage of the work:\n" + "\n".join(hits)


@pytest.mark.parametrize("line, hit", [
    ("see design " + SECTION + "2.4", True),
    ("Design " + "9.1 says so", True),
    ("# [" + "B" + "6] the toggle", True),
    ("after " + "the" + " lead's cutover", True),
    ("new " + "this" + " round", True),
    ("per " + "report" + " F3", True),
    ("the " + "B" + "7 report", True),
    ("bare " + SECTION + "7.1 here", True),
    ("IEC 60601-1 " + SECTION + "7.9.3.1 sets 40 C", False),
    ("ISO 80601-2-69 " + SECTION + "201.12", False),
    ("color: #B45309;", False),
    ("Offer B ships first", False),
    ("they lead with grid reliability", False),
])
def test_the_patterns_catch_citations_and_spare_standards(line, hit):
    assert _hits_in_line("app/x.py", line) is hit


def test_a_bare_section_sign_under_data_is_not_counted():
    assert not _hits_in_line("data/import_rules.json", "Act " + SECTION + "12")
