"""Small builders so each test states only the facts it cares about."""

from __future__ import annotations

from dataclasses import replace

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.provenance import Provenance, fact

NAMEPLATE = Provenance.NAMEPLATE


def device(**overrides) -> DeviceFacts:
    """A fully specified, unremarkable device. Override only what the test needs."""
    base = DeviceFacts(
        manufacturer=fact("Acme", NAMEPLATE),
        model=fact("V100", NAMEPLATE),
        year_of_manufacture=fact(2022, NAMEPLATE),
        voltage_min_v=fact(100, NAMEPLATE),
        voltage_max_v=fact(240, NAMEPLATE),
        frequencies_hz=fact([50, 60], NAMEPLATE),
        power_watts=fact(400, NAMEPLATE),
        needs_stable_power=fact(True, Provenance.SPEC_SHEET),
        temp_min_c=fact(5.0, Provenance.SPEC_SHEET),
        temp_max_c=fact(40.0, Provenance.SPEC_SHEET),
        humidity_max_pct=fact(90.0, Provenance.SPEC_SHEET),
        needs_oxygen=fact(False, Provenance.SPEC_SHEET),
        needs_distilled_water=fact(False, Provenance.SPEC_SHEET),
        needs_compressed_air=fact(False, Provenance.SPEC_SHEET),
        consumables=fact([], Provenance.SPEC_SHEET),
        manual_languages=fact(["English"], Provenance.SPEC_SHEET),
        operating_complexity=fact("low", Provenance.MODEL_KNOWLEDGE),
    )
    return replace(base, **overrides)


def facility(**overrides) -> FacilityProfile:
    """A facility with nothing wrong with it."""
    base = FacilityProfile(
        slug="test-clinic",
        name="Test Clinic",
        country="Testland",
        mains_voltage_v=230,
        mains_frequency_hz=50,
        plug_types=("G",),
        ambient_temp_max_c=28.0,
        humidity_max_pct=60.0,
        has_air_conditioning=True,
        outages_per_week=0,
        has_voltage_stabilizer=True,
        has_generator=True,
        has_oxygen_supply=True,
        has_distilled_water=True,
        has_compressed_air=True,
        has_biomed_technician=True,
        trained_operators=True,
        staff_languages=("English",),
    )
    return replace(base, **overrides)


def intel(**overrides) -> SupplyIntel:
    base = SupplyIntel(
        country="Testland",
        consumables_available=fact(True, Provenance.MODEL_KNOWLEDGE),
        spare_parts_available=fact(True, Provenance.MODEL_KNOWLEDGE),
        manufacturer_supported=fact(True, Provenance.MODEL_KNOWLEDGE),
    )
    return replace(base, **overrides)
