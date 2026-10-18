"""Check the seeded demo on the live site: the Kayunga matches page shows the three verdicts.

    python3 deploy/check_live.py http://147.224.201.81 < seed-output.txt

Standard library only, so it runs with any python3. Reads the output of
scripts/seed_demo.py (or deploy/demo_ids.py) on stdin: the first three offer lines
are the hero trio A (525DS), B (525KS) and C (the older concentrator), and the
request line is Kayunga's. Then it fetches /request/<id>/matches and each
candidate page and checks what the demo walk test checks on SQLite:
B first and shippable, A under "Won't work", C needing info. Prints every demo
URL with its status; exits 1 on any failure.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request

KAYUNGA = "uganda-district"


def fetch(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def parse(seed_output: str) -> tuple[int | None, list[tuple[int, str]]]:
    offers = [(int(m.group(1)), m.group(2).strip())
              for m in re.finditer(r"^\s*offer (\d+) \w+: (.*)$", seed_output, re.M)]
    request = re.search(r"^\s*request (\d+) ", seed_output, re.M)
    return (int(request.group(1)) if request else None), offers[:3]


def main(argv: list[str]) -> int:
    base = argv[1].rstrip("/") if len(argv) > 1 else "http://147.224.201.81"
    request_id, hero = parse(sys.stdin.read())
    problems: list[str] = []
    if request_id is None or len(hero) < 3:
        print("!! could not read the request and three hero offers from the seed output")
        return 1
    (a_id, a_label), (b_id, b_label), (c_id, _c_label) = hero
    if "525DS" not in a_label or "525KS" not in b_label:
        problems.append(f"hero order unexpected: A={a_label!r} B={b_label!r}")

    matches_url = f"{base}/request/{request_id}/matches"
    status, body = fetch(matches_url)
    print(f"GET {matches_url} -> {status}")
    if status != 200:
        problems.append("matches page did not answer 200")
    else:
        link = lambda offer_id: f'href="/request/{request_id}/offer/{offer_id}"'  # noqa: E731
        try:
            at_b, at_a, at_c = body.index(link(b_id)), body.index(link(a_id)), body.index(link(c_id))
            won_t = body.index("Won't work at Kayunga District Hospital")
        except ValueError as exc:
            problems.append(f"matches page is missing a hero offer or the won't-work heading ({exc})")
        else:
            b_part, a_part, c_part = body[at_b:won_t], body[at_a:at_c], body[at_c:]
            verdicts = {
                "B ships (Ship with / Clear to ship)": at_b < won_t
                and ("Ship with" in b_part or "Clear to ship" in b_part),
                "A won't work": won_t < at_a and "Buy locally instead?" in a_part,
                "C needs info": at_a < at_c and "Needs info" in c_part,
            }
            for name, ok in verdicts.items():
                print(f"  {'ok' if ok else 'FAIL'}: {name}")
                if not ok:
                    problems.append(name)

    for path in (
        f"/request/{request_id}/offer/{a_id}",
        f"/request/{request_id}/offer/{b_id}",
        f"/request/{request_id}/offer/{c_id}",
        f"/packet/{b_id}?facility={KAYUNGA}",
    ):
        status, _ = fetch(base + path)
        print(f"GET {base}{path} -> {status}")
        if status != 200:
            problems.append(f"{path} answered {status}")

    for problem in problems:
        print(f"!! {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
