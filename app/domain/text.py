"""Small text helpers shared by the domain and the pages."""

from __future__ import annotations


def plural(n: int, noun: str) -> str:
    """A count and its noun, agreeing: '1 condition', '3 conditions', '0 items'.

    Regular nouns only: the plural is the noun plus "s".
    """
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
