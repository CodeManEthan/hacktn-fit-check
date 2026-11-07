"""Print the seeded demo's live ids in the seed's own output format, without seeding.

Read-only. Run on the VM from the live checkout, so `.env` and `scripts` resolve:

    ssh opc@<vm> 'cd /opt/fitcheck && ./.venv/bin/python -' < deploy/demo_ids.py

deploy/check_live.py reads these lines (or the seed's) to find the Kayunga request
and the hero offers A, B and C.

The text comes from this checkout but runs against whichever head is live, and
the live database may hold the hero donors under their old, real names or their
new, fictional ones. So the names are written out here, not read from the live
scripts.seed_demo, and each hero offer is found under either. From the live code
this uses only the stored-offer reader and the open-request list.
"""

from scripts import seed_demo as seed

# Each hero offer's donor as (old name, new name), in the order A, B, C, with the
# model that tells it from another offer by the same donor. app/data/seeded.py
# holds the same names (DONOR_RENAMES); the two must agree, and
# tests/test_seeded.py checks that they do.
HERO_DONORS = (
    ("Nashville General Hospital biomedical department", "Riverside General biomedical department"),
    ("Leeds Teaching Hospitals NHS Trust, surplus", "Midland Teaching Hospital, surplus"),
    ("Harris County clinic network", "Bayou community clinic network"),
)
HERO_MODELS = ("525DS", "525KS", "")
HERO_TYPE = "oxygen concentrator"
KAYUNGA = "uganda-district"


def _plain(value) -> str:
    return str(value or "").strip().casefold()


stored = [offer for group in seed._stored_offers_by_key().values() for offer in group]
for names, model in zip(HERO_DONORS, HERO_MODELS):
    found = sorted(
        (offer for offer in stored
         if _plain(offer.donor_name) in {_plain(name) for name in names}
         and _plain(offer.device.model.value) == _plain(model)
         and _plain(offer.device_type) == HERO_TYPE),
        key=lambda offer: offer.id,
    )
    if found:
        # The seed keeps the lowest id of a donor's duplicates, so this is the one it shows.
        print(f"  offer {found[0].id} kept: {found[0].device.label}")
for request in seed.repo.list_open_requests():
    if request.facility_slug == KAYUNGA and any(
        _plain(line.device_type) == HERO_TYPE for line in request.lines
    ):
        print(f"  request {request.id} kept: Kayunga, 1 {HERO_TYPE}")
        break
