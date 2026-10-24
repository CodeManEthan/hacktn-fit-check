# The fifteen checks and the verdict

This page answers one question: how does Fit-Check decide whether a donated device will work at a clinic, and why does each check say what it says?

It is written from `app/checks/` (the checks), `app/domain/requirement.py` (the add-on rule), `app/matching/pipeline.py` and `app/matching/bundle.py` (ranking and the send-with list), `app/matching/supply_order.py` and `app/reference/gudid.py` (supply facts), `data/import_rules.json`, and design §2. The hero outcomes at the end were produced by running the checks on a freshly seeded database at commit 385bc8f.

No check calls a model. Given the same facts, the checks return the same verdict every time. The inputs can be wrong. The judgement cannot be invented.

## Four outcomes per check

Every check returns one of four statuses (`CheckStatus` in `app/domain/result.py`):

- `pass`: it works on this point.
- `mitigable`: it works if something is sent with it, or a condition is met. The `fix` text says what.
- `needs info`: a fact it needs is unknown. The `question` says what to ask.
- `fail`: it cannot work here. The `fix` text says what the clinic would have to change. A fail never carries an add-on, and nothing searches the inventory for it.

## The rollup

`build_verdict` in `app/checks/runner.py` turns the fifteen results into one verdict, in this order:

1. any `fail` → **won't work** (stamp "Do not ship");
2. else any `needs info` → **needs info** (stamp "Needs info", headline "N questions before a verdict");
3. else any `mitigable` → **works if you also send** (stamp "Ship with");
4. else → **works** (stamp "Clear to ship").

The rule underneath: an unknown fact never reads as a pass. A `needs info` verdict still lists its add-ons, so the send-with list is visible before the questions are answered. A question that two checks share is asked once. That is why Offer C has 12 unknown results and 11 questions.

The verdict page lists results by status, fails first, then mitigable, then needs info, then pass. Within a status they keep the order below.

## The fifteen, in code order

The registry is `ALL_CHECKS` (fourteen checks) plus `ROUTE_CHECKS` (one) in `app/checks/runner.py`. The top bar's count is `len(ALL_CHECKS) + len(ROUTE_CHECKS)`, read from the code (`rule_count()` in `app/web/presenters.py`), and it is 15.

Before any check runs, `with_defaults()` fills an unstated operating temperature range and maximum humidity from IEC 60601-1, which only requires medical equipment to work from 5 to 40 °C and up to 93 % humidity. Those rows show the source "IEC 60601-1 default". So the temperature and humidity checks never ask a question. They judge on the standard's range.

### 1. Supply frequency (`check_frequency`, `app/checks/power.py`)

Reads the device's rated frequencies and the clinic's mains frequency.
- Unknown rating → needs info.
- The mains frequency is in the rating → pass.
- Otherwise → fail. A motor built for 60 Hz runs slow or burns out on 50 Hz. The fix names a rotary frequency converter, which normally costs more than a surplus device is worth.

There is no mitigable outcome. This is the check that rejects the 525DS at Kayunga.

### 2. Supply voltage (`check_voltage`, `app/checks/power.py`)

Reads the device's rated minimum and maximum voltage and the clinic's mains voltage.
- Either end unknown → needs info.
- Mains inside the range → pass.
- Outside → mitigable, add-on **transformer**, sized at 1.25 × the device's power draw when that is known.

Voltage never fails. A transformer ships in the crate.

### 3. Operating temperature (`check_temperature`, `app/checks/environment.py`)

Reads the device's maximum operating temperature and the room's maximum. With air conditioning the room counts as the lower of the outdoor maximum and 24 °C.
- Room at or below the rating → pass.
- Hotter, with air conditioning → fail.
- Hotter, no air conditioning → fail, "needs air conditioning the ward does not have". Cooling a room is building works, not an add-on.

There is no mitigable outcome.

### 4. Humidity (`check_humidity`, `app/checks/environment.py`)

Reads the device's maximum humidity and the clinic's.
- Clinic at or below the rating → pass.
- Above → mitigable, add-on **dehumidifier**.

### 5. Grid reliability (`check_grid_reliability`, `app/checks/environment.py`)

Reads the clinic profile only, not the device. The grid counts as unstable when the clinic has 3 or more outages a week and does not have both a generator and a stabilizer.
- Stable → pass.
- Unstable → mitigable. The add-on is **backup power** when the clinic has no generator, else a **voltage stabilizer**.

It never asks and never fails. Every device sent to Kayunga (6 outages a week, a generator, no stabilizer) gets a stabilizer on its list.

### 6, 7, 8. Medical oxygen, distilled water, compressed air (`check_oxygen`, `check_distilled_water`, `check_compressed_air`, `app/checks/utilities.py`)

Each reads whether the device needs the utility, and whether the clinic has it.
- Need unknown → needs info.
- Not needed → pass.
- Needed and the clinic has it → pass.
- Needed and missing → fail. The fix names what the clinic would have to put in, such as a medical oxygen plant or a still.

These are site works. They never become add-ons.

### 9. Consumables (`check_consumables`, `app/checks/supply.py`)

Reads the device's consumables list, then whether those consumables are sold in the country.
- List unknown → needs info. The donor answers "what does it consume?".
- Empty list → pass.
- Sold in the country unknown → needs info. The nonprofit answers on `/supply`.
- Sold → pass.
- Not sold → mitigable, add-on **consumables**: "2 years of <the items>, shipped with the device, and a named supplier for after that".

### 10. Spare parts (`check_spare_parts`, `app/checks/supply.py`)

Reads whether parts can be bought in the country.
- Unknown → needs info.
- Yes → pass.
- No → mitigable, add-on **spare parts** kit.

### 11. Age and manufacturer support (`check_support_window`, `app/checks/supply.py`)

Reads whether the manufacturer still supports the model, and the build year. Age is this calendar year minus the build year.
- Support known to be withdrawn → fail. WHO asks for at least 3 years of support from transfer.
- Year unknown → needs info.
- 15 years or older (`END_OF_LIFE_YEARS`) → fail, "send a younger device".
- 10 to 14 years (`AGEING_YEARS` is 10) → mitigable: written confirmation the maker or its distributor will support it 3 more years. This result carries no add-on.
- Younger → pass.

Only a known "no" on support can fail this check. Nothing in tonight's data writes a "no": GUDID never denies (see below), and the seeded recorded answer leaves support as "don't know".

### 12. Manual language (`check_manual_language`, `app/checks/documentation.py`)

Reads the manual's languages, the device's manufacturer documents, and the staff's languages. Both language lists pass through one normaliser (`app/domain/language.py`), so "en" and "English" agree.
- A shared language → pass.
- No shared language, and a manufacturer document is on file → mitigable, add-on **handover packet**. Fit-Check builds the packet in the staff language from the manufacturer's manual.
- Manual languages unknown, but a document is on file → mitigable, the same packet.
- No document at all → needs info.

Manuals never fail (ruling [F9]). The packet is generated, not shipped, so the send-with list marks it "generated by Fit-Check".

### 13. Training and repair (`check_operation_and_repair`, `app/checks/documentation.py`)

Reads how much skill the device needs (`operating_complexity`) and whether the clinic has trained operators and a biomedical technician.
- Complexity unknown → needs info.
- The clinic has both → pass.
- Anything below high complexity → pass, "simple enough to run" with the gap named.
- High complexity and a gap → fail. Training and a service contract are not shipped in a crate.

### 14. Import and registration (`check_import_rules`, `app/checks/import_rules.py`)

Reads the clinic's country row in `data/import_rules.json`, the build year, the manual languages and the documents. Three rules, in this order:

1. **Country not on file** → needs info, "What are <country>'s import and registration rules for donated medical equipment?". Only Uganda is on file. Haiti and Nepal ask this question.
2. **Remaining life.** If 15 minus the device's age is less than the country's minimum, the check fails and quotes the rule. If the year is unknown, this rule is skipped, because the age check already asks for the year.
3. **Label language.** No label-language fact exists, so the manual's languages stand in for the label's. A maker prints its labels in the languages of its manual. No shared language with the country's label languages → fail. Manual languages unknown but a document on file → skipped (the packet reads the language off the document). Both unknown → needs info, the same question the manual check asks, so it is asked once.

A life fail wins over anything the label rule would say. When neither rule fails, the check passes and its detail lists the paperwork the country asks for. The paperwork never fails and is never an add-on. The packet prints it as a checklist.

In 2026, against Uganda's two years, rule 2 fails a unit built in 2012 or earlier (age 14 or more). The age check alone would only fail it from 2011.

### 15. Shelf life on arrival (`check_shelf_life`, `app/checks/timing.py`)

This is the one route check. It reads the expiry date, the donor's "nothing dated" answer, and the estimated shipment.
- The donor said nothing is dated (a blank expiry field) → pass, "Nothing in this consignment carries an expiry date (donor)".
- No expiry date and no such answer → needs info.
- Already expired → fail.
- No shipment estimate → needs info.
- Expires before it lands → fail.
- Lands with less than 365 days left → mitigable: written confirmation the clinic can use it in time. This result carries no add-on.
- Otherwise → pass.

The 365 days is borrowed from WHO's medicine donation guideline, because the device guidance sets no number. The shipment is an estimate from distance (`app/domain/shipment.py`): 14 days of preparation, road at 500 km a day or sea at 800 km a day plus 18 days of port handling past 1,500 km, and 21 days of customs across a border. The matches page always has an estimate when both ends have coordinates. The legacy `/check` path passes none.

## The add-on rule

A requirement is an add-on only when it ships in the container and plugs in (design §2.4, `app/domain/requirement.py`):

- **Add-ons:** transformer, voltage stabilizer, backup power, dehumidifier, consumables, spare parts, and the handover packet.
- **Site works:** cooling, oxygen supply, water supply, medical air compressor. A check that would need one fails and says what the clinic lacks.
- **Training and service** is neither. Training fails the check that needs it.

The send-with list (`build_bundle`) searches the open offers once per add-on, by the offer's support kind or by search terms such as "stabiliser", "avr" or "voltage regulator". Two mitigable results carry no add-on and so never reach the search: the age check's 10-to-14-year band, and the short-shelf-life band. Their fix text still shows on the verdict.

### The add-on self-check

An add-on counts as a fix only when its own plate says it runs on this clinic's mains. For the four power add-ons (transformer, stabilizer, backup power, dehumidifier), `build_bundle` runs the frequency and voltage checks on each candidate add-on's own device against the clinic:

- any fail → "won't work here itself", listed and not counting;
- both pass → covers;
- anything else, such as an unknown plate or a stabilizer that would itself need a transformer → "unverified", listed and not counting.

Consumables and parts kits have no plate. They count as found.

This is the "120 V UPS fails in Uganda" clause. At Kayunga the seeded Sollatek AVS30 (160 to 260 V, 50 Hz) covers Offer B's stabilizer, and the "Houston surplus AVR" (110 to 120 V, 60 Hz) shows as "won't work here itself". The Sollatek's plate figures are a stand-in: the seed uses the stated input range of UNICEF's S0002632 1 kVA medical stabilizer until a Sollatek document is fetched, and its note says so (design §4.3).

## Ranking and the matches page

`screen_offers` runs the checks on every open offer of the requested type, then splits them. A `won't work` offer goes to the rejected list, in the order the offers came in. Everything else is ranked by the key `(info_rank, condition_count, located_rank, distance)` in `_sort_key`:

1. `info_rank`: 0 for a fully known verdict, 1 for `needs info`. A known offer with three add-ons ranks above an unknown one with none.
2. `condition_count`: the number of mitigable results. Fewer is better.
3. `located_rank`: an offer with no coordinates goes behind every located one, rather than counting as 0 km away.
4. `distance` in km.

Each part is compared in turn, not summed, so no number of kilometres buys off a condition or a question. Distance is never used to rescue a rejected offer, because rejected offers are not in the ranking.

The matches page (`app/web/templates/matches.html`) shows each request line in this order:

1. the known offers that work, `works` and `works if you also send`, in rank order, each with "Ask this donor" and the packet link;
2. the section "Won't work at <clinic>", each offer with the label and detail of every failing check, and the buy-locally advisory where it applies;
3. the `needs info` offers, each with "Answer N questions first" linking to its candidate page instead of "Ask this donor".

`POST /match` refuses both a `won't work` and a `needs info` offer. Nothing is proposed to a donor before the facts are known. The coverage line ("1 of 1 available") counts only offers that can be proposed today, so a `needs info` offer adds nothing to it.

### The buy-locally advisory

Under a rejected offer whose failures include the frequency check, the page shows what buying locally would cost (`advisory_for` in `app/web/presenters.py`, prices in `data/local_prices.json`, as of 2026-10-17). Only oxygen concentrators are priced:

- a new 5 LPM concentrator built for local power, about $290 (UNICEF indicative price, $288);
- a frequency converter to run the unit on 50 Hz, $1,319 to $2,273 (ATO, 500 VA to 2 kVA);
- a 1 kVA 230 V medical stabilizer, $205 (UNICEF S0002632).

It also says whether this kind of device is sold in the country, from the recorded answer, or "don't know, ask the nonprofit" with a link to `/supply`. The advisory reads the verdict. It never changes it.

## Where the supply facts come from, and why GUDID only affirms

The consumables, spare-parts and support checks read supply facts that a manual cannot give. `supply_lookup` (`app/matching/supply_order.py`) fills them in this order. Each source fills only fields still unknown.

1. **FDA GUDID**, live (`app/reference/gudid.py`). One query to openFDA's UDI endpoint for the exact model. If a record from a company whose name contains the manufacturer's first word says "In Commercial Distribution", the manufacturer counts as supporting the model, with provenance "FDA GUDID" and the record's URL as its citation. Any other answer leaves the field unknown.
2. **The nonprofit's recorded answer** (`supply_answers`): the exact model in the country, then any model of the type. A recorded "don't know" leaves the field unknown.
3. Otherwise unknown, and the check asks.

GUDID affirms and never denies, because it covers one market. A model withdrawn from US sale can still be sold and serviced elsewhere, and the 525KS is exactly that: its GUDID record reads "Not in Commercial Distribution", while the recorded answer the demo seeds (design §4.3) notes that Drive's East Africa distributor services the 525 series. Treating that record as a "no" would fail the device the clinic should get.

A GUDID "yes" changes no verdict tonight. The support check reads only a "no". The "yes" shows as a source link on the support row: the 525DS has an in-distribution record (`accessgudid.nlm.nih.gov/devices/00885304000846`). If GUDID is unreachable, the row simply has no registry link. The verdicts stay the same.

The model's memory is never a source of facts. The old `research_supply` step is still in the tree (`app/ai/research.py`) but nothing calls it.

## The import rules table

`data/import_rules.json` has one country, keyed by the country name as the clinic stores it.

| Field | Uganda |
|---|---|
| Source | MoH Uganda, National Medical Equipment Guidelines, November 2024, section 3.6 Donated Equipment and Supplies, pp. 29-30 ([PDF](https://nms.go.ug/wp-content/uploads/2025/12/National-Medical-Equipment-Guidelines-November-20241.pdf)) |
| Label languages | English |
| Minimum remaining life | 2 years |
| Paperwork | Prior no-objection from the Ministry of Health before shipment (item 11); a detailed equipment list with serial number, year of manufacture, last service certificate and quality documents (item 6); a detailed packing list (item 7) |

The quotes the rules come from, verbatim from the guideline:

- Labels: "Each donated equipment to Uganda shall have labels in English and should be easy to use by health professionals in Uganda."
- Life: "On arrival in Uganda, the donated medical equipment shall have a useful life of at least two years."
- Life, item 2: "Donated equipment shall be obtained from reliable sources and shall comply with quality standards of both the donor country and Uganda with at least two years guaranteed service life"
- No-objection: "Donation of medical equipment or other related supplies shall be sent with prior no-objection from the Ministry of Health, after confirming that the donor has met all the donation requirements."

Haiti and Nepal are not in the table. Their import check reads `needs info` until someone fills a row from a document.

## A judge holding the 525 manual: "it says five years"

The 525 owner's manual says, on page 6 (`data/library/drive-devilbiss-525ds/525ds_owners_manual.txt`, the "Service Life" section):

> "The expected service life of the 525 series oxygen concentrator, which includes the performance of any required service or maintenance, is 5 years."

Fit-Check does not use that number. The remaining-life rule counts every device against one constant, `END_OF_LIFE_YEARS = 15` in `app/checks/supply.py`. The ruling is design §2.5:

> "The manufacturer's stated service life is not used tonight: the 525 series states five years, which would fail both hero units against Uganda's two-year rule while the ruled outcome for the 525KS is works-if. The constant is the one basis every device gets."

The honest answer for the judge:

- They are right about the manual. By the maker's own figure, a 525KS built in 2019 is past its expected service life, and under Uganda's two-year rule it would be rejected.
- Fit-Check applies one working-life figure, 15 years, to every device, because tonight no device carries a service-life fact. Service life is not a field in the library, so a 525 and a ventilator are judged on the same basis.
- The manual's figure is an expectation that assumes all the manufacturer's maintenance. The same passage says actual life varies with use, environment and maintenance. Uganda's rule asks for useful life on arrival, and nobody has ruled on which reading the Ministry applies.
- Reading service life from each manual is the obvious next step. The check already has the rule and the quote. It needs a `service_life_years` fact per device, which the library could cite like any other.

## The hero outcomes at Kayunga

Run on a freshly seeded database with the recorded 525KS answer and GUDID off (the results with GUDID on are the same):

| Offer | Outcome | What decides it |
|---|---|---|
| B, 525KS from Leeds, built 2019 | works if you also send | 2 mitigable (grid → stabilizer, consumables → two years of the four filters), 13 pass. The Sollatek covers the stabilizer. No donor has the filters yet. |
| A, 525DS from Nashville, built 2016 | won't work | Frequency is the only fail (rated 60 Hz, grid 50 Hz). Voltage, grid and age are mitigable, consumables and spare parts need info. Shown with the advisory. |
| C, unnamed concentrator from Houston | needs info | 12 needs info, "11 questions before a verdict". Grid is mitigable. Temperature and humidity pass on the IEC defaults. |

The ranked list reads B, then C. A is in the rejected list. The page shows B, then A under "Won't work", then C.

## Rough edges to know about

- Temperature and humidity never ask. An offer with no facts at all, like C, passes both on the IEC 60601-1 defaults. At Bardiya (42 °C, no air conditioning) a device with no stated range fails temperature on the default 40 °C.
- The grid check reads only the clinic. Every device at an unstable-grid clinic gets a stabilizer or backup power on its list, including a battery device.
- The label rule uses manual languages as a proxy. A unit whose manual lacks English fails Uganda's import check even if its labels are in English.
- The module docstring at the top of `app/checks/supply.py` still says these "are the only checks where the model's world knowledge decides the outcome". That stopped being true this round. The docstring is stale. The code is not.
- The stored verdict JSON keeps each result's fix text but drops the structured add-on, so a reloaded verdict cannot search the inventory again (see `docs/schema.md`).
