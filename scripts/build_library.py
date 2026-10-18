"""Build data/device_library.json and data/library/ from the verified device notes.

Run once with:  ./.venv/bin/python -m scripts.build_library [NOTES_DIR]

Sources (read-only, in the notes tree):
- the hero JSON block and fact tables in reports/verify-hero-device.md, for the
  DeVilbiss 525DS and 525KS, typed here with each quote and checked against
  the document text below;
- device-docs/library-a/library-a.json and device-docs/library-b/library-b.json,
  five devices each, converted field by field.

Every fact keeps the field name DeviceFacts uses, a source label, the
document it was read from, the PDF page and the verbatim quote. Pages are
found by searching the document text (pdftotext puts a form feed between
pages); a quote that cannot be found keeps the page its report gave. Only
the .txt extractions are copied; the PDFs stay in the notes tree and are
linked by URL.

Mapping rules (design §4.1):
- a value that is null, or whose quote says "not stated", is left out (unknown);
- a frequencies_hz of [] is left out: "no mains", not "no frequency";
- year_introduced, support status, battery and UI-language facts go to notes;
- languages are normalised through app.domain.language;
- device_type uses the request vocabulary.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.domain.language import normalise_languages

NOTES = Path("/home/jpluto/notes/jpluto/projects/hospital-surplus")
OUT_JSON = PROJECT_ROOT / "data" / "device_library.json"
OUT_DIR = PROJECT_ROOT / "data" / "library"

SPEC = "spec sheet"
PRODUCT_PAGE = "product page"

# Library device types, mapped to the words a clinic types on the request form.
DEVICE_TYPES = {
    "blood_pressure_monitor": "blood pressure monitor",
    "suction_machine": "suction pump",
    "pulse_oximeter": "pulse oximeter",
    "multiparameter_patient_monitor": "patient monitor",
    "ultrasound": "ultrasound scanner",
    "fetal_doppler": "fetal doppler",
    "autoclave_tabletop": "autoclave",
    "glucose_meter": "glucose meter",
    "nebulizer_compressor": "nebuliser",
    "infant_incubator": "infant incubator",
}

# DeviceFacts fields a library JSON can fill, and the quote key each falls
# back to when the JSON quotes a pair once (min/max read off one line).
FACT_FIELDS = {
    "manufacturer": "manufacturer",
    "model": "model",
    "voltage_min_v": "voltage_min_v",
    "voltage_max_v": "voltage_min_v",
    "frequencies_hz": "frequencies_hz",
    "power_watts": "power_watts",
    "temp_min_c": "temp_min_c",
    "temp_max_c": "temp_min_c",
    "humidity_max_pct": "humidity_max_pct",
    "needs_oxygen": "needs_oxygen",
    "needs_distilled_water": "needs_distilled_water",
    "needs_compressed_air": "needs_compressed_air",
    "consumables": "consumables",
    "manual_languages": "manual_languages",
    "operating_complexity": "operating_complexity",
}
SUPPLY_FIELDS = ("needs_oxygen", "needs_distilled_water", "needs_compressed_air")
NOT_STATED = "not stated"

S1_URL = ("https://shop.drivemedical.com/medias/3061-1?context=bWFzdGVyfGltYWdlc3w3NzU1NDE1fGFwcGxp"
          "Y2F0aW9uL3BkZnxhRGxoTDJobFl5OHhNalE1TXpVMU9UUTVOamN6TkM4ek1EWXhMVEV8ZTRlMTg5ZDU5M2ExYmY5"
          "NzYyNDYyYmNhMjczMDJlNzU0OTJiZmIyYmVhN2ZmNGVjOGVlMzY5MDE4MzVjYjZhNw")
S2_URL = ("https://shop.drivemedical.com/medias/3061-1?context=bWFzdGVyfGltYWdlc3w2MTQ4MTh8YXBwbGlj"
          "YXRpb24vcGRmfGFEZzRMMmhsT1M4eE1qUTVNelUxT1RVMk1qSTNNQzh6TURZeExURXw5YTQ2MzVmNmJmZGY0N2Yz"
          "NTllMzIxYWUxMTY0MWI0MmJlNmNiYjFjOTQwOTI4MzM1NmQ1MTFjNTJmYTk2ZmQz")
S3_URL = ("https://shop.drivemedical.com/medias/3061-1?context=bWFzdGVyfGltYWdlc3w2NTk4NDF8YXBwbGlj"
          "YXRpb24vcGRmfGFHWXpMMmhsT1M4eE1qUTVNelUxT1RVeU9UVXdNaTh6TURZeExURXwyNGZiYjZlYzcwNzc0NGRi"
          "MTZhNWU1Y2VkMTM1MzY2MTNmZDNkYzNkZDBlN2E0ZjJjNjQ0ZjM0MGE2Nzk3MTE3")
S4_URL = ("https://shop.drivemedical.com/ca/en/products/respiratory/oxygen-therapy/oxygen-concentrators/"
          "stationary-concentrators/devilbiss-5-liter-oxygen-concentrator/p/525DS/downloadPdf")
DE_MANUALS = "https://www.drivemedical.de/ALL_PRODUCT_Media/RESPIRATORY/Oxygen/525KS/pdf"
S6_URL = f"{DE_MANUALS}/User%20Manual/SE-525KS-1%20Rev_D.pdf"
S7_URL = f"{DE_MANUALS}/User%20Manual/SE-525KS-2-Rev-A.pdf"
S8_URL = f"{DE_MANUALS}/User%20Manual/SE-525KS-3%20Rev%20B_FINAL.pdf"
S10_URL = f"{DE_MANUALS}/DoC/525KS_PS%20Oxygen%20Concentrators-accessi%20DoC%2012-873_rev%2012.pdf"

ROOM_AIR = ("The oxygen concentrator is electrically operated. The unit separates oxygen from "
            "room air which allows high-purity supplemental oxygen to be delivered to you "
            "through the oxygen outlet.")
ROOM_AIR_NOTE = ("the concentrator makes its oxygen from room air with its own compressor; the "
                 "manual does not say in so many words that no piped {what} is needed")
HUMIDIFIER = ("If your physician has prescribed an oxygen humidifier as part of your therapy, "
              "follow these steps")
WATER_NOTE = ("no water requirement in the owner's manual; the bubble humidifier is an "
              "optional accessory")
INDICATIONS = ("The DeVilbiss Oxygen Concentrator is intended for use as an oxygen concentrator "
               "to provide supplemental low flow oxygen therapy in the home, nursing homes, "
               "patient care facilities, etc.")
COMPLEXITY_NOTE = ("lead's reading of the intended use: supplemental low flow oxygen therapy in "
                   "the home, nursing homes, patient care facilities; the manual does not grade "
                   "operator skill")
FILTERS = ["MC44D-605 intake bacteria filter", "303DZ-605 cabinet air filter",
           "PV5LD-651 final bacteria filter", "525DD-626 compressor filter"]
FILTER_NOTE = ("the four filters of the 525 series; sieve bed 525D-619 and compressor rebuild "
               "kit 525D-643 are spare parts, not consumables (see notes)")


def _supply_facts(doc: str) -> dict[str, tuple]:
    return {
        "needs_oxygen": (False, doc, ROOM_AIR, ROOM_AIR_NOTE.format(what="oxygen")),
        "needs_compressed_air": (False, doc, ROOM_AIR, ROOM_AIR_NOTE.format(what="compressed air")),
        "needs_distilled_water": (False, doc, HUMIDIFIER, WATER_NOTE),
        "operating_complexity": ("low", doc, INDICATIONS, COMPLEXITY_NOTE),
    }


# The two hero entries, typed from verify-hero-device.md (its Facts JSON and
# the fact tables with page and quote). Each fact: (value, document key,
# quote, note). Quote fragments joined by " ... " are each checked.
HERO: list[dict[str, Any]] = [
    {
        "slug": "drive-devilbiss-525ds",
        "manufacturer": "DeVilbiss Healthcare",
        "model": "525DS",
        "aliases": ["525", "DeVilbiss 525", "DeVilbiss 525DS", "Drive DeVilbiss 525DS", "525DS-Q",
                    "DeVilbiss 5 Liter Compact Concentrator"],
        "device_type": "oxygen concentrator",
        "documents": [
            {"key": "S1", "src": "hero/525ds_owners_manual.txt", "kind": "manual",
             "title": "DeVilbiss 525DS Instruction Guide A-525DS Rev A (2022)", "url": S1_URL,
             "languages": ["en", "es", "fr"]},
            {"key": "S2", "src": "hero/525ds_sell_sheet.txt", "kind": SPEC,
             "title": "DeVilbiss 525DS sell sheet LT-2007 Rev O (2020)", "url": S2_URL,
             "languages": ["en"]},
            {"key": "S3", "src": "hero/525ds_parts_diagram.txt", "kind": "service manual",
             "title": "DeVilbiss 525DS parts diagram (03/2026)", "url": S3_URL, "languages": ["en"]},
            {"key": "S4", "src": "hero/525ds_catalog_page.txt", "kind": PRODUCT_PAGE,
             "title": "Drive DeVilbiss 525DS catalog page", "url": S4_URL, "languages": ["en"]},
            {"key": "S5", "src": None, "kind": PRODUCT_PAGE,
             "title": "Drive Medical US product page, 525DS",
             "url": "https://shop.drivemedical.com/us/en/p/525DS", "languages": ["en"]},
            {"key": "S12", "src": None, "kind": "registry", "title": "FDA GUDID records, model 525DS",
             "url": "https://api.fda.gov/device/udi.json?search=version_or_model_number:525DS&limit=10",
             "languages": []},
        ],
        "facts": {
            "manufacturer": ("DeVilbiss Healthcare", "S1",
                             "DeVilbiss Healthcare LLC • 100 DeVilbiss Drive • Somerset, PA 15501 • USA", ""),
            "model": ("525DS", "S1", "Catalog Number 525DS, 525DS-Q", ""),
            "voltage_min_v": (97, "S1", "Operating Voltage Range 97-127 V~, 60 Hz", ""),
            "voltage_max_v": (127, "S1", "Operating Voltage Range 97-127 V~, 60 Hz", ""),
            "frequencies_hz": ([60], "S1", "Electrical Rating 115 V, 60 Hz, 3.3 Amp",
                               "no document gives a frequency tolerance"),
            "power_watts": (310, "S1", "310 Watts Average ... 275 Watts @ 1.2 LPM & below", ""),
            "temp_min_c": (5, "S1", "Operating Temperature Range 41˚F (5˚C) to 95˚F (35˚C)", ""),
            "temp_max_c": (35, "S1", "Operating Temperature Range 41˚F (5˚C) to 95˚F (35˚C)", ""),
            "humidity_max_pct": (93, "S1", "Operating Relative Humidity Range 15% to 93%, non-condensing", ""),
            **_supply_facts("S1"),
            "consumables": (FILTERS, "S3",
                            "23 Cabinet Air Filter- 303DZ-605 ... 24 Intake Bacteria Filter- MC44D-605 ... "
                            "26 Compressor Filter (Bronze)- 525DD-626 ... 27 Final Bacteria Filter- PV5LD-651",
                            FILTER_NOTE),
            "manual_languages": (["en", "es", "fr"], "S1", "ENGLISH ... ESPAÑOL ... FRANÇAIS",
                                 "one combined guide: en-2, es-15, fr-29"),
        },
        "notes": [
            "Year introduced: 2007, inferred from FDA 510(k) K071397 (DeVilbiss 5 Liter Compact "
            "Oxygen Concentrator, Model 525 Series, decision 2007-10-01); no document states an "
            "introduction year. It is the model's market date, not this unit's build year.",
            "Service life: \"The expected service life of the 525 series oxygen concentrator, which "
            "includes the performance of any required service or maintenance, is 5 years.\" (A-525DS p.6)",
            "Spare parts (service centre only): sieve bed 525D-619, compressor rebuild kit 525D-643; "
            "replaced \"when indicated by device performance\" (A-525DS p.14; parts diagram).",
            "Oxygen purity check every 3 years or between patients (A-525DS p.14).",
            "GUDID: In Commercial Distribution, DI 00885304000846 (record version 2024-07-18).",
            "Current product on Drive's US and Canada sites, UPC 885304000846.",
        ],
    },
    {
        "slug": "drive-devilbiss-525ks",
        "manufacturer": "DeVilbiss Healthcare",
        "model": "525KS",
        "aliases": ["525", "DeVilbiss 525", "DeVilbiss 525KS", "Compact 525", "Oxygen Concentrator 525",
                    "525KS-LT", "525KS-UK"],
        "device_type": "oxygen concentrator",
        "documents": [
            {"key": "S6", "src": "hero/525ks_ifu.txt", "kind": "manual",
             "title": "DeVilbiss 525KS Instruction Guide SE-525KS-1 Rev D (2025)", "url": S6_URL,
             "languages": ["en", "es", "fr", "de", "it", "nl", "tr", "pt", "pl", "ar"]},
            {"key": "S7", "src": "hero/525ks_SE-525KS-2_RevA.txt", "kind": "manual",
             "title": "DeVilbiss 525KS Instruction Guide SE-525KS-2 Rev A (2022)", "url": S7_URL,
             "languages": ["en", "el", "da", "sv", "fi", "no", "cs", "sk", "zh-Hans"]},
            {"key": "S8", "src": "hero/525ks_SE-525KS-3_RevB.txt", "kind": "manual",
             "title": "DeVilbiss 525KS Instruction Guide SE-525KS-3 Rev B (2025)", "url": S8_URL,
             "languages": ["en", "sl", "ro", "ms"]},
            {"key": "S10", "src": "hero/525ks_DoC.txt", "kind": SPEC,
             "title": "EC Declaration of Conformity 525KS/525KS-LT/525KS-UK/525PS (2024)", "url": S10_URL,
             "languages": ["en"]},
            {"key": "S11", "src": None, "kind": PRODUCT_PAGE,
             "title": "Drive Medical DE product page, Oxygen Concentrator 525 (525KS)",
             "url": "https://www.drivemedical.de/drivemedical/en/respiratory/oxygen-concentrator/"
                    "oxygen-concentrator-compact-525.html", "languages": ["en"]},
            {"key": "S13", "src": None, "kind": "registry", "title": "FDA GUDID record, model 525KS",
             "url": "https://api.fda.gov/device/udi.json?search=version_or_model_number:525KS&limit=10",
             "languages": []},
        ],
        "facts": {
            "manufacturer": ("DeVilbiss Healthcare", "S10", "Manufacturer: DeVilbiss Healthcare LLC", ""),
            "model": ("525KS", "S6", "Catalog Number 525KS, 525KS-LT", ""),
            "voltage_min_v": (187, "S6", "Operating Voltage Range 187-253 V~, 50 Hz", ""),
            "voltage_max_v": (253, "S6", "Operating Voltage Range 187-253 V~, 50 Hz", ""),
            "frequencies_hz": ([50], "S6", "Electrical Rating 220-230 V~, 50 Hz, 1.55 Amp",
                               "SE-525KS-2 Rev A (2022) also lists a 230 V / 60 Hz 525KS build; the 2025 "
                               "editions list 50 Hz only. Check the unit's rating label"),
            "power_watts": (312, "S6", "230V / 50 Hz - 312 Watts Average", ""),
            "temp_min_c": (5, "S6", "Operating Temperature Range 41˚F (5˚C) to 95˚F (35˚C)", ""),
            "temp_max_c": (35, "S6", "Operating Temperature Range 41˚F (5˚C) to 95˚F (35˚C)", ""),
            "humidity_max_pct": (93, "S6", "Operating Relative Humidity Range 15% to 93%, non-condensing", ""),
            **_supply_facts("S7"),
            "consumables": (FILTERS, "S10",
                            "EXT Life Intake Bacteria Filter-DFT MC44D-605 ... Compressor Filter (Sintered "
                            "Bronze) 525DD-626 ... Cabinet Air Filter 303DZ-605 ... Final Bacteria Filter PV5LD-651",
                            FILTER_NOTE),
            "manual_languages": (["en", "es", "fr", "de", "it", "nl", "tr", "pt", "pl", "ar", "el", "da",
                                  "sv", "fi", "no", "cs", "sk", "zh-Hans", "sl", "ro", "ms"], "S6",
                                 "ENGLISH ... DEUTSCH",
                                 "21 languages across three editions: SE-525KS-1 (en es fr de it nl tr pt "
                                 "pl ar), SE-525KS-2 (en el da sv fi no cs sk zh), SE-525KS-3 (en sl ro ms)"),
        },
        "notes": [
            "Year introduced: 2007, inferred from the EC Declaration of Conformity, \"Start of EC "
            "Marking: 2007-10-01\". It is the model's market date, not this unit's build year.",
            "Service life: 5 years for the 525 series (SE-525KS-1 p.5).",
            "Spare parts listed on the DoC: sieve bed 525D-619, compressor 525K-625, outlet connector CN100.",
            "GUDID: Not in Commercial Distribution (US), end date 2024-07-09, DI 00885304000976 "
            "\"(international model)\". The 525KS is sold outside the US; a GUDID silence is not a "
            "denial of support.",
            "Current product: Drive Medical DE lists item 525KS with manuals.",
            "Current draw: the IFU says 1.55 A; the 2019 spec sheet says 3.2 A. The IFU value is used.",
        ],
    },
]

# Library entries: clean names and aliases for search and best_match, the
# documents with their local text, and the facts the reports say to read as
# unknown even though the JSON carries a value.
LIBRARY: dict[str, dict[str, Any]] = {
    "omron-hem-907xl": {
        "source": ("library-a", 0, "omron-hem-907xl"),
        "manufacturer": "Omron Healthcare", "model": "HEM-907XL",
        "aliases": ["HEM-907-Z2", "IntelliSense HEM-907XL", "Omron HEM-907XL", "HEM-907"],
        "documents": [
            ("hem-907xl-im-en_1628519-0k.txt", "manual",
             "Omron HEM-907XL IntelliSense instruction manual 1628519-0K",
             "https://omronhealthcare.com/storage/pdfs/intellisense-blood-pressure-monitor-hem-907xl-im-en_1628519-0k.pdf",
             ["English"]),
            (None, PRODUCT_PAGE, "Omron Healthcare product page, HEM-907XL",
             "https://omronhealthcare.com/products/intellisense-professional-digital-blood-pressure-monitor-hem-907xl",
             ["English"]),
        ],
    },
    "laerdal-lsu": {
        "source": ("library-a", 1, "laerdal-lsu"),
        "manufacturer": "Laerdal Medical", "model": "LSU",
        "aliases": ["Laerdal Suction Unit", "LSU Serres", "78 00 30", "78 00 00"],
        "documents": [
            ("user_guide_lsu_w_serres_international.txt", "manual",
             "Laerdal Suction Unit with Serres user guide 20-16505 Rev C (2023)",
             "https://cdn.laerdal.com/downloads/f6374/user_guide_lsu_w_serres_suction_bag_canister__international_",
             ["English", "Spanish", "Italian", "French", "Dutch", "German"]),
            ("laerdal_suction_unit_ie.txt", "manual", "Laerdal Suction Unit user guide 8179 Rev Q (2017)",
             "https://cdn.laerdal.com/downloads/f2538/laerdal_suction_unit___ie_", ["English"]),
            (None, PRODUCT_PAGE, "Laerdal product listing, item 78003003",
             "https://www.laerdal.com/distributors/item/78003003/", ["English"]),
        ],
    },
    "nellcor-n-560": {
        "source": ("library-a", 2, "nellcor-n-560"),
        "manufacturer": "Nellcor", "model": "N-560",
        "aliases": ["OxiMax N-560", "Nellcor OxiMax N-560", "Covidien N-560"],
        "documents": [
            ("rhn_N560_HomeUseGuide_EN_10075132A00.txt", "manual",
             "Nellcor OxiMax N-560 Home Use Guide 10075132A00 (2011)",
             "https://intranet.rhn.org.uk/wp-content/uploads/2024/08/N560_HomeUseGuide_EN_10075132A00.pdf",
             ["English"]),
        ],
    },
    "philips-suresigns-vm6": {
        "source": ("library-a", 3, "philips-suresigns-vm6"),
        "manufacturer": "Philips Medical Systems", "model": "SureSigns VM6",
        "aliases": ["VM6", "Philips VM6", "863064", "863065"],
        "documents": [
            (None, PRODUCT_PAGE, "Philips product page, SureSigns VM6 (HC863065)",
             "https://www.philips.com.au/healthcare/product/HC863065", ["English"]),
            ("usme_Philips_SureSigns_VM_Series_Service_Manual.txt", "service manual",
             "Philips SureSigns VM Series Service Guide, Release A (2006)",
             "https://usme.com/wp-content/uploads/2022/06/Philips_SureSigns_VM_Series_Service_Manual.pdf",
             ["English"]),
        ],
    },
    "sonosite-m-turbo": {
        "source": ("library-a", 4, "sonosite-m-turbo"),
        "manufacturer": "FUJIFILM SonoSite", "model": "M-Turbo",
        "aliases": ["SonoSite M-Turbo", "Sonosite MTurbo"],
        "documents": [
            ("M-Turbo_UG_ENG_P07662-09C_e.txt", "manual", "SonoSite M-Turbo User Guide P07662-09 (2021)",
             "https://www.sonosite.com/support/userdocs/M-Turbo_UG_ENG_P07662-09C_e.pdf", ["English"]),
            ("MKT03562_Rev_E_End_of_Sale_Notice_Sonosite_M-Turbo_Customer_Communication.txt", "notice",
             "SonoSite M-Turbo end-of-sale notice MKT03562 Rev E (2021)",
             "https://www.sonosite.com/sites/default/files/MKT03562_Rev_E_End_of_Sale_Notice_Sonosite_M-Turbo_Customer_Communication.pdf",
             ["English"]),
            ("M03625_Rev_B_Sonosite_M-Turbo_End_of_Life_Customer_Communication.txt", "notice",
             "SonoSite M-Turbo end-of-life notice M03625 Rev B",
             "https://www.sonosite.com/sites/default/files/M03625_Rev_B_Sonosite_M-Turbo_End_of_Life_Customer_Communication.pdf",
             ["English"]),
        ],
    },
    "edan-sonotrax-basic-a": {
        "source": ("library-b", 0, "fetal-doppler"),
        "manufacturer": "Edan Instruments", "model": "SONOTRAX Basic A",
        "aliases": ["Sonotrax", "Edan Sonotrax Basic A", "STBA.02"],
        "documents": [
            ("edan-sonotrax-series-manual-tigermedical.txt", "manual",
             "Edan SONOTRAX Series User Manual 01.54.455405-11 (2012)",
             "https://tigermedical.com/cdn/shop/files/EDASonoTraxVascular--20160729102756394.pdf", ["en"]),
            ("doctorpoint-docp-pdf3968.txt", "manual", "Edan Doppler Ultrasonico Portatile manual v1.2 (2008)",
             "https://www.doctorpoint.it/images/deplian/docp-pdf3968.pdf", ["it"]),
            ("fda-K080087.txt", "registry", "FDA 510(k) summary K080087",
             "https://www.accessdata.fda.gov/cdrh_docs/pdf8/K080087.pdf", []),
            ("edanusa-sonotrax-basic-a-page.txt", PRODUCT_PAGE, "Edan USA product page, SONOTRAX Basic A",
             "https://edanusa.com/product/sonotrax-basic-a-fetal-doppler-baby-heart-monitor/", ["en"]),
        ],
    },
    "tuttnauer-2540m": {
        "source": ("library-b", 1, "autoclave"),
        "manufacturer": "Tuttnauer", "model": "2540M",
        "aliases": ["Tuttnauer 2540M", "2540 M autoclave"],
        "documents": [
            ("tuttnauer-M-MK-operators-manual-rev-Q.txt", "manual",
             "Tuttnauer M & MK tabletop autoclaves operation and maintenance manual MAN205-0007000EN Rev Q",
             "https://statimusa.com/wp-content/uploads/2019/09/M___MK_Operator_s_Manual__Rev_Q.pdf", ["en"]),
            ("tuttnauer-us-2540m-page.txt", PRODUCT_PAGE, "Tuttnauer US product page, 2540M",
             "https://tuttnauer.com/us/medical-clinics/tabletop-sterilizers/manual/2540m", ["en"]),
        ],
    },
    "roche-accu-chek-performa": {
        "source": ("library-b", 2, "glucose-meter"),
        "manufacturer": "Roche Diabetes Care", "model": "Accu-Chek Performa",
        "aliases": ["Accu-Chek", "Performa", "Roche Accu-Chek Performa"],
        "documents": [
            ("accu-chek-performa-user-manual-uk.txt", "manual",
             "Accu-Chek Performa user's manual (UK edition, EN/FR/PT/AR)",
             "https://www.accu-chek.co.uk/document/accu-chek-performa-blood-glucose-meter-user-manual/download?inline",
             ["en", "fr", "pt", "ar"]),
            ("accu-chek-performa-test-strip-insert-uk.txt", "manual",
             "Accu-Chek Performa test strip package insert (2022-01)",
             "https://www.accu-chek.co.uk/document/accu-chek-performa-test-strip-insert-0/download?inline", []),
            ("accu-chek-uk-performa-support-page.txt", PRODUCT_PAGE, "Accu-Chek UK support page, Performa",
             "https://www.accu-chek.co.uk/support/performa", ["en"]),
        ],
    },
    "devilbiss-pulmo-aide-5650d": {
        "source": ("library-b", 3, "nebulizer"),
        "manufacturer": "DeVilbiss Healthcare", "model": "5650D",
        "aliases": ["Pulmo-Aide 5650D", "Pulmo-Aide", "DeVilbiss Pulmo-Aide", "DeVilbiss 5650D"],
        "documents": [
            ("drive-devilbiss-5650D-instruction-guide-3087-1.txt", "manual",
             "DeVilbiss Pulmo-Aide compressor/nebulizer instruction guide A-5650 Rev E (2016)",
             "https://shop.drivemedical.com/medias/3087-1?context=bWFzdGVyfGltYWdlc3wxNjY3ODAwfGFwcGxpY2F0aW9uL3BkZnxhR1E1TDJnME5TOHhNalE1TXpNM05ERXlPREUxT0M4ek1EZzNMVEV8NjUwMWM5MzA2NTkwN2VmYzVmNmZjNTk4YjRjY2RkODY3YzUzN2FjYjM0YzBlMmY4NmE2ZGRlZmNmYzBiMzQ1Nw",
             ["en", "es", "fr"]),
            ("devilbiss-pulmo-aide-5650-instructions-apria.txt", "manual",
             "DeVilbiss Pulmo-Aide instructions (Sunrise Medical, 2001)",
             "https://www.apria.com/hubfs/User%20Manual/DeVilbiss_Pulmo-Aide_Pulmo-Aide-LT_PulmoMate_Instructions_05-01.pdf",
             []),
            ("devilbiss-5650-service-manual.txt", "service manual", "DeVilbiss 5650 service manual (1996)",
             "https://cdn.shopify.com/s/files/1/0615/2069/files/5650-service-manual.pdf", []),
            ("drivemedical-5650D-page.txt", PRODUCT_PAGE, "Drive Medical US product page, 5650D",
             "https://shop.drivemedical.com/us/en/Products/Respiratory/c/p/5650D", ["en"]),
        ],
    },
    "draeger-isolette-c2000": {
        "source": ("library-b", 4, "infant-warmer"),
        "manufacturer": "Draeger Medical Systems", "model": "Isolette C2000",
        "aliases": ["C2000", "Isolette", "Air-Shields Isolette C2000", "Drager Isolette C2000"],
        "documents": [
            ("drager-isolette-c2000-technical-service-manual-usme.txt", "service manual",
             "Draeger Isolette C2000 Technical Service Manual, Revision J (2006)",
             "https://usme.com/wp-content/uploads/2022/09/Drager-C200-Infant-Incubator.pdf", ["en"]),
        ],
        # verify-library-b.md: the 5-99 % "operating range" sits in the hood
        # humidity table and may not describe the room. Read as unknown.
        "unknown": {"humidity_max_pct": "ambiguous: the 5-99 % RH range sits in the humidity-system "
                                        "table and may describe the hood, not the room"},
    },
}

NOTE_KEYS = ("year_introduced", "support_status", "has_battery", "battery_runtime", "ui_languages")


# ---- text, pages and quotes ------------------------------------------------

def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class Document:
    """One document's text, searchable with whitespace ignored, page-aware."""

    def __init__(self, path: Path | None):
        self.text = path.read_text(encoding="utf-8", errors="replace") if path else ""

    def page_of(self, fragment: str) -> int | None:
        words = [w for w in fragment.split() if w]
        if not self.text or not words:
            return None
        pattern = re.compile(r"\s*".join(r"\s+".join(map(re.escape, w.split())) for w in words), re.I)
        found = pattern.search(self.text)
        return None if found is None else self.text.count("\f", 0, found.start()) + 1


def _fragments(quote: str) -> list[str]:
    """The checkable pieces of a quote: its double-quoted parts, else its ' ... ' parts."""
    quoted = re.findall(r'"([^"]{8,})"', quote)
    parts = quoted or [quote]
    pieces = []
    for part in parts:
        pieces.extend(p.strip(' .;') for p in re.split(r"\s*\.\.\.\s*|\s*\|\s*", part))
    return [p for p in pieces if len(p) >= 5]


def _reported_page(quote: str, url: str) -> int | None:
    anchor = re.search(r"#page=(\d+)", url or "")
    if anchor:
        return int(anchor.group(1))
    printed = re.search(r"\bp\.(\d+)", quote or "")
    return int(printed.group(1)) if printed else None


# ---- building ---------------------------------------------------------------

def _fact(value: Any, source: str, document: str, url: str, page: int | None,
          quote: str, note: str = "") -> dict[str, Any]:
    payload = {"value": value, "source": source, "document": document, "url": url,
               "page": page, "quote": quote}
    if note:
        payload["note"] = note
    return payload


def _document_payload(slug: str, src: str | None, kind: str, title: str, url: str,
                      languages: list[str]) -> dict[str, Any]:
    return {"file": f"{slug}/{Path(src).name}" if src else "", "title": title, "url": url,
            "kind": kind, "languages": normalise_languages(languages)}


class Checks:
    """Counts of quotes found in their document, for the build's own report."""

    def __init__(self):
        self.found = 0
        self.missing: list[str] = []

    def locate(self, doc: Document, quote: str, label: str) -> int | None:
        pages = [doc.page_of(f) for f in _fragments(quote)]
        if pages and all(p is not None for p in pages):
            self.found += 1
            return pages[0]
        self.missing.append(label)
        return None


def build_hero(spec: dict[str, Any], notes: Path, checks: Checks) -> tuple[dict, list[Path]]:
    slug = spec["slug"]
    docs = {d["key"]: d for d in spec["documents"]}
    texts = {k: Document(notes / "device-docs" / d["src"] if d["src"] else None) for k, d in docs.items()}
    facts = {}
    for name, (value, key, quote, note) in spec["facts"].items():
        doc = docs[key]
        page = checks.locate(texts[key], quote, f"{slug}.{name}")
        if page is None:
            raise SystemExit(f"hero quote not found: {slug}.{name} in {doc['src']}: {quote!r}")
        if name == "manual_languages":
            value = normalise_languages(value)
        source = PRODUCT_PAGE if doc["kind"] == PRODUCT_PAGE else SPEC
        facts[name] = _fact(value, source, f"{slug}/{Path(doc['src']).name}", f"{doc['url']}#page={page}",
                            page, quote, note)
    entry = {
        "slug": slug, "manufacturer": spec["manufacturer"], "model": spec["model"],
        "aliases": spec["aliases"], "device_type": spec["device_type"], "facts": facts,
        "documents": [_document_payload(slug, d["src"], d["kind"], d["title"], d["url"], d["languages"])
                      for d in spec["documents"]],
        "notes": "\n".join(spec["notes"]),
    }
    files = [notes / "device-docs" / d["src"] for d in spec["documents"] if d["src"]]
    return entry, files


def _source_url(raw: dict[str, Any], name: str, quote_key: str) -> str:
    sources = raw.get("sources") or {}
    url = sources.get(name) or sources.get(quote_key) or ""
    return url.split(" ; ")[0].strip()


def _strip_anchor(url: str) -> str:
    return url.split("#", 1)[0]


def build_library_entry(slug: str, spec: dict[str, Any], notes: Path, checks: Checks) -> tuple[dict, list[Path]]:
    library, index, folder = spec["source"]
    raw = json.loads((notes / "device-docs" / library / f"{library}.json").read_text())[index]
    base = notes / "device-docs" / library / folder
    documents = [
        {"src": src, "kind": kind, "title": title, "url": url, "languages": langs,
         "text": Document(base / src if src else None)}
        for src, kind, title, url, langs in spec["documents"]
    ]
    by_url = {_strip_anchor(d["url"]): d for d in documents}
    quotes = raw.get("quotes") or {}
    field_notes = raw.get("notes") if isinstance(raw.get("notes"), dict) else {}
    unknown = spec.get("unknown", {})
    entry_notes = [f"Library model string: {raw['manufacturer']}, {raw['model']}."]

    facts = {}
    for name, quote_key in FACT_FIELDS.items():
        value = raw.get(name)
        if name == "manufacturer":
            value = spec["manufacturer"]
        if name == "model":
            value = spec["model"]
        quote = quotes.get(name) or quotes.get(quote_key) or ""
        if name in SUPPLY_FIELDS and not quote:
            quote = quotes.get("needs_compressed_air") or ""
        if name in unknown:
            entry_notes.append(f"{name} left unknown: {unknown[name]}.")
            continue
        if value is None or value == [] or NOT_STATED in quote.lower():
            if NOT_STATED in quote.lower():
                entry_notes.append(f"{name}: {quote}")
            continue
        if name == "manual_languages":
            value = normalise_languages(value)
        url = _source_url(raw, name, quote_key)
        doc = by_url.get(_strip_anchor(url)) or documents[0]
        page = checks.locate(doc["text"], quote, f"{slug}.{name}") if quote else None
        # A document with no stored text (a product page read in a browser)
        # has no pages; the quote and the URL are the citation.
        page = (page or _reported_page(quote, url)) if doc["src"] else None
        note = field_notes.get(name, "")
        if name in SUPPLY_FIELDS and not note:
            note = ("inferred from a complete supply specification that names electrical power "
                    "only; not an explicit statement (verify report)")
        if name == "operating_complexity" and raw.get("operating_complexity_reason"):
            note = f"verifier's reading: {raw['operating_complexity_reason']}"
        if name == "power_watts" and "DERIVED" in quote:
            note = "derived from the adapter rating, not stated as watts"
        source = PRODUCT_PAGE if doc["kind"] == PRODUCT_PAGE else SPEC
        file = f"{slug}/{doc['src']}" if doc["src"] else ""
        link = f"{_strip_anchor(doc['url'])}#page={page}" if page and doc["src"] else doc["url"]
        facts[name] = _fact(value, source, file, link, page, quote, note)

    for key in NOTE_KEYS:
        if raw.get(key) not in (None, "", []):
            value = raw[key]
            text = ", ".join(value) if isinstance(value, list) else str(value)
            label = key.replace("_", " ").capitalize()
            extra = f" ({quotes[key]})" if key in quotes else ""
            entry_notes.append(f"{label}: {text}{extra}.")
    for key, text in field_notes.items():
        if key not in facts and key not in unknown:
            entry_notes.append(f"{key}: {text}")
    if raw.get("year_introduced") is not None:
        entry_notes.append("The year introduced is the model's market date, not this unit's build year.")

    entry = {
        "slug": slug, "manufacturer": spec["manufacturer"], "model": spec["model"],
        "aliases": spec["aliases"], "device_type": DEVICE_TYPES[raw["device_type"]], "facts": facts,
        "documents": [_document_payload(slug, d["src"], d["kind"], d["title"], d["url"], d["languages"])
                      for d in documents],
        "notes": "\n".join(entry_notes),
    }
    files = [base / d["src"] for d in documents if d["src"]]
    return entry, files


def build(notes: Path = NOTES, out_json: Path = OUT_JSON, out_dir: Path = OUT_DIR) -> Checks:
    checks = Checks()
    built: list[tuple[dict, list[Path]]] = [build_hero(spec, notes, checks) for spec in HERO]
    built += [build_library_entry(slug, spec, notes, checks) for slug, spec in LIBRARY.items()]

    if out_dir.exists():
        shutil.rmtree(out_dir)
    for entry, files in built:
        target = out_dir / entry["slug"]
        target.mkdir(parents=True, exist_ok=True)
        for path in files:
            shutil.copyfile(path, target / path.name)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps([e for e, _ in built], indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return checks


def main(argv: list[str]) -> None:
    notes = Path(argv[1]) if len(argv) > 1 else NOTES
    checks = build(notes)
    entries = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    size = sum(p.stat().st_size for p in OUT_DIR.rglob("*.txt"))
    print(f"entries: {len(entries)}  texts: {size / 1e6:.1f} MB")
    print(f"quotes found in their document: {checks.found}; not found: {len(checks.missing)}")
    for label in checks.missing:
        print(f"  not found (page from the report): {label}")


if __name__ == "__main__":
    main(sys.argv)
