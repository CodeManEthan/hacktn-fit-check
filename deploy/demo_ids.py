"""Print the seeded demo's live ids in the seed's own output format, without seeding.

Read-only. Run on the VM from the live checkout, so `.env` and `scripts` resolve:

    ssh opc@<vm> 'cd /opt/fitcheck && ./.venv/bin/python -' < deploy/demo_ids.py

deploy/check_live.py reads these lines (or the seed's) to find the Kayunga request
and the hero offers A, B and C.
"""

from scripts import seed_demo as seed

stored = seed._stored_offers_by_key()
for offer in seed.all_offers()[:3]:
    found = stored.get(seed._offer_key(offer.donor_name, offer.device), [])
    if found:
        print(f"  offer {found[0].id} kept: {offer.device.label}")
for request in seed.repo.list_open_requests():
    if request.facility_slug == seed.KAYUNGA and any(
        line.device_type.strip().casefold() == seed.HERO_TYPE for line in request.lines
    ):
        print(f"  request {request.id} kept: Kayunga, 1 {seed.HERO_TYPE}")
        break
