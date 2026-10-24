"""The staged clinic profiles. Real grid and climate figures, named facilities."""

from __future__ import annotations

from app.domain.facility import FacilityProfile

FACILITIES: tuple[FacilityProfile, ...] = (
    FacilityProfile(
        slug="uganda-district",
        name="Kayunga District Hospital",
        latitude=0.7028,
        longitude=32.8856,
        country="Uganda",
        mains_voltage_v=240,
        mains_frequency_hz=50,
        plug_types=("G",),
        ambient_temp_max_c=34.0,
        humidity_max_pct=85.0,
        has_air_conditioning=False,
        outages_per_week=6,
        has_voltage_stabilizer=False,
        has_generator=True,
        has_oxygen_supply=True,
        has_distilled_water=False,
        has_compressed_air=False,
        has_biomed_technician=False,
        trained_operators=False,
        staff_languages=("English",),
        notes="District hospital, 100 beds. One generator shared across theatre "
        "and maternity. Nearest biomedical technician is in Kampala.",
    ),
    FacilityProfile(
        slug="haiti-community",
        name="Hopital Communautaire de Pignon",
        latitude=19.3333,
        longitude=-72.1167,
        country="Haiti",
        mains_voltage_v=110,
        mains_frequency_hz=60,
        plug_types=("A", "B"),
        ambient_temp_max_c=36.0,
        humidity_max_pct=90.0,
        has_air_conditioning=False,
        outages_per_week=12,
        has_voltage_stabilizer=False,
        has_generator=True,
        has_oxygen_supply=False,
        has_distilled_water=False,
        has_compressed_air=False,
        has_biomed_technician=False,
        trained_operators=True,
        staff_languages=("French", "Haitian Creole"),
        notes="Community hospital. US-style 110 V 60 Hz grid, so American "
        "equipment plugs straight in, but nothing is stocked locally.",
    ),
    FacilityProfile(
        slug="nepal-phc",
        name="Bardiya Primary Health Centre",
        latitude=28.3000,
        longitude=81.4000,
        country="Nepal",
        mains_voltage_v=230,
        mains_frequency_hz=50,
        plug_types=("C", "D", "M"),
        ambient_temp_max_c=42.0,
        humidity_max_pct=80.0,
        has_air_conditioning=False,
        outages_per_week=4,
        has_voltage_stabilizer=True,
        has_generator=False,
        has_oxygen_supply=True,
        has_distilled_water=True,
        has_compressed_air=False,
        has_biomed_technician=False,
        trained_operators=True,
        staff_languages=("Nepali", "English"),
        notes="Primary health centre in the Terai. Summer ward temperatures pass "
        "42 C. Stabilizer on the main feed, no generator.",
    ),
)

FACILITIES_BY_SLUG = {f.slug: f for f in FACILITIES}


def get_facility(slug: str) -> FacilityProfile:
    """Look a facility up, failing loudly rather than returning a default."""
    try:
        return FACILITIES_BY_SLUG[slug]
    except KeyError as exc:
        raise KeyError(f"No clinic profile with slug {slug!r}") from exc
