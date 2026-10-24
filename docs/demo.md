# The demo

What this answers: the four screens and the closing page on the live host, what to type or click on each, what to say, and what to do if one breaks.

Live host: https://fitcheck.care/

Caddy on the VM serves that name with a Let's Encrypt certificate and redirects plain HTTP to it. The header bar links About us, Contact and Get help; the role toggle is on its right; Impact is in the bar for every role, and the nonprofit role adds the desk links (Supply answers, Recipients, Inventory CSV). The bare IP, 147.224.201.81, still answers on plain HTTP as the compatibility door for anyone holding the old link; it is not a link to give out or show on a slide.

The ids below are the live MySQL's, read from the pages on 2026-10-18 after the 385bc8f redeploy. The B6 report's table uses a fresh local database, where the same offers are 1, 2 and 3; on the live host they are 8, 9 and 10. The seed upserts, so a reseed keeps these ids.

| Offer | Device | Donor, city | Verdict at Kayunga |
|---|---|---|---|
| 8 (A) | DeVilbiss Healthcare 525DS | Nashville General Hospital biomedical department, Nashville | Do not ship |
| 9 (B) | DeVilbiss Healthcare 525KS | Leeds Teaching Hospitals NHS Trust, surplus, Leeds | Ship with |
| 10 (C) | Unnamed oxygen concentrator | Harris County clinic network, Houston | Needs info |

The Kayunga request is request 1.

## Before the pitch

Check each page answers 200:

```
curl -s -o /dev/null -w '%{http_code}\n' https://fitcheck.care/offer
curl -s -o /dev/null -w '%{http_code}\n' https://fitcheck.care/request/1/matches
curl -s -o /dev/null -w '%{http_code}\n' https://fitcheck.care/request/1/offer/9
curl -s -o /dev/null -w '%{http_code}\n' "https://fitcheck.care/packet/9?facility=uganda-district"
curl -s -o /dev/null -w '%{http_code}\n' https://fitcheck.care/impact
curl -s -o /dev/null -w '%{http_code}\n' https://fitcheck.care/offer/csv
```

Open https://fitcheck.care/packet/9?facility=uganda-district once in the browser. The first build of a packet makes one gpt-oss call (about 13 s in B5's run); after that it comes from the cache. On 2026-10-18 it answered in 0.04 s, so it was already built. A redeploy keeps the cache (it lives in MySQL), so open it once anyway; it costs nothing when cached.

Copy the paste for screen 1 to the clipboard (on the laptop, from the repository checkout):

```
cd /home/jpluto/projects/hospital-surplus
sed -n '1,40p;77,91p' data/library/drive-devilbiss-525ds/525ds_sell_sheet.txt | wl-copy
```

Three things not to do on the live host, in rehearsal or on stage:

- **Do not press "Ask this donor" on the 525KS** (matches page or offer 9's page). It reserves the offer's one unit, and the 525KS then leaves the matches page. A reseed does not bring it back: `scripts/seed_demo.py` keeps each stored offer's reserved count when it upserts (`upsert_offers`). To release the unit, open the `/confirm/<token>` link the next page shows and press "No, it has gone"; that calls `offer.release`.
- **Do not press "Post these 6 offers" on the CSV results.** The posted 525KS is a second Leeds 525KS from a different donor name, so it shows on Kayunga's matches page and no reseed removes it. Post only against a scratch database, or last in a rehearsal you will not repeat.
- **Do not press "Rebuild this packet".** It clears the cache and calls the model again on stage.

## Screen 1: the donor names the device

URL: https://fitcheck.care/offer (role toggle: Donor)

Fast path: in "Pick from the library", choose **DeVilbiss Healthcare 525DS (oxygen concentrator)** (slug `drive-devilbiss-525ds`). The inspector fills with 14 rows. Twelve are known, each tagged "spec sheet" with a p.N link into Drive's manual (`#page=11` for voltage, frequency, power, temperature and humidity). Year made and Needs stable power stay unknown. The pick makes no model call. Since B6 fix 9ca4edd a pick fills the inspector in the browser.

Paste path (the [F4] story, and the fallback): leave the pick on "Not listed", paste the text below into the spec box, press "Read these facts now". The model names it "525DS", code finds the library entry, and the same 14 rows appear with the same links. This is the 525DS spec block B4 used: lines 1-40 and 77-91 of `data/library/drive-devilbiss-525ds/525ds_sell_sheet.txt`, copied verbatim. On the live host on 2026-10-18 it hit `drive-devilbiss-525ds` in 1.1 s with one model call.

```
                                                                                               CHERISH. EVERY. BREATH.




Drive DeVilbiss 5-Liter Oxygen
Concentrator (525)
Built upon a legacy of reliable, field-proven concentrator
models, the DeVilbiss 5 Liter Oxygen Concentrators are
designed with enhanced quality, durability, performance and
simplicity in mind.

Assembled in the USA, the DeVilbiss 5 liter concentrators are
robust, state-of-the-art oxygen systems that offer the right
balance of cool and quiet performance.

With improved reliability and reduced operational costs, the
5 liter concentrator is perfect for your patients and the right
investment for your business.



                C E L E B R AT I N G

                                                                       5
                                                                           4                              10
                                                                               3                               8
                                                                                   2                               6

                                                                                       1                               4

                                                                                                                           2
                                                                                           0
                                                                                                        RESET




                                S
                           AR
 Specifications                      525DS
 Outlet Pressure                     8.5 ± 0.5 psig (58.6 ± 3.5 kPa)
 Oxygen Percentage                   1–5 LPM=87-96%
 Power Consumption                   310 Watt Average; 275 Watts @ 1.2 LPM & below
 Weight                              36 lbs. (16.3 Kilograms)
 Dimensions                          24.5” H x 13.5” W x 12” D (62.2 x 34.2 x 30.4 cm)
 Warranty                            3 years limited
 Alarms                              Audible and visual high pressure, low/high flow, low oxygen, high gas temperature, power failure, Oxygen
                                     Sensing Device (OSD)


 Ordering Information
 525DS                               5-liter oxygen concentrator
 525DS-Q                             Ultra-quiet 5-liter oxygen concentrator
```

A paste must print "525DS". The sell sheet's first 40 lines alone print only "Concentrator (525)". "525" names both the 525DS and the 525KS, so that is no hit, and the model reads the paste instead.

What to say: "The model only names the device. Code finds the manufacturer's documents, and every value links to the page it was read on. Nothing here is from the model's memory."

If it breaks: if the pick does not fill the table, use the paste. If "Read these facts now" shows an error (the model did not answer), use the pick, which needs no model.

## Screen 2: the clinic sees three verdicts

URL: https://fitcheck.care/request/1/matches (role toggle: Clinic, pressed first; it opens the clinic registration form at `/clinic`, so then open this URL)

The page reads "Of 3 offers of this kind, 1 is known to work at Kayunga District Hospital, 1 won't, and 1 needs answers first." In page order:

- **525KS, Leeds: Ship with.** It must go with a voltage stabilizer and 2 years of the four 525 filters. The Sollatek AVS30 from Kampala Power Trust covers the stabilizer. The Houston surplus AVR is listed as "Won't work here itself": its own plate fails 240 V 50 Hz. The filters have no donor yet. It has "Ask this donor" (do not press it) and "Generate handover packet".
- **Under "Won't work at Kayunga District Hospital": 525DS, Nashville: Do not ship.** Supply frequency: "Rated 60 Hz, but the grid is 50 Hz." Under it, "Buy locally instead?": a new 5 LPM concentrator for local power, about $290 (UNICEF); a frequency converter, $1,319 to $2,273 (ATO); a 1 kVA 230 V stabilizer, $205 (UNICEF S0002632). "Sold in Uganda: don't know, ask the nonprofit." No packet link and no "Ask this donor".
- **Unnamed oxygen concentrator, Houston: Needs info.** "Answer 11 questions first." Its spare-parts question links to `/supply`.

The seeded request is there so this is one click. The landing page (rebuilt 2026-10-18 from the teammate's design: a hero, a donor card to `/offer` and a clinic card to `/clinic`, with the nonprofit's operator strip under them) no longer has the seeded-clinic chips, so open this URL directly. Each unit on the matches page shows its distance in km and miles.

Why each verdict:

- 525KS: its plate (187-253 V, 50 Hz, 5-35 °C, 93 %) fits Kayunga's 240 V 50 Hz and 34 °C wards. Six outages a week make the stabilizer a condition. The nonprofit's recorded answer says the filters are not stocked in Kampala, which makes 2 years of filters the second condition, and that spare parts are available. Made 2019, so its age passes.
- 525DS: same family, the US build. Its plate is 97-127 V, 60 Hz. The voltage alone would be a condition (a step transformer), but the frequency cannot be fixed by anything that ships in the box, so it fails.
- Houston unit: "older oxygen concentrator, works fine, 2 units", no plate. Every fact is unknown, and an unknown never reads as a pass.

What to say: "Same request, three donors. One ships with what it needs, one is stopped with the reason and a local alternative, one is held until someone answers the questions."

If it breaks: the page calls the FDA GUDID registry live for manufacturer support (5 s timeout per model, cached 7 days in `registry_lookups`, a failure remembered for 10 minutes). If the FDA is slow, the page waits up to 5 s per uncached lookup and then shows the same verdicts without the GUDID record link. A GUDID answer changes no verdict tonight; it only adds the "FDA GUDID" tag on the 525DS's support row. `GUDID_LOOKUP=off` in the VM's `.env` skips the FDA altogether (see `docs/deploy.md`). If the page itself errors, `docs/deploy.md` has the redeploy.

## Screen 3: the 525KS verdict

URL: https://fitcheck.care/request/1/offer/9

It shows "Ship with", "2 things it cannot arrive without", 2 conditional and 13 clear: all 15 checks. Each row has its source tag and a link to the cited page (SE-525KS-1 Rev D p.11 for the electrical facts, SE-525KS-2 Rev A p.6 for the room-air and intended-use facts, the MoH Uganda guideline p.29-30 for the import row). Under "Must go with it": the Sollatek AVS30 "covers", the Houston surplus AVR "won't work here itself" and "does not count", and the filters with "no donor has one yet". The consumables and spare-parts rows say "recorded by the nonprofit" (Fit-Check field team).

The 525KS has no open questions: its year is stated and the nonprofit's recorded answer covers consumables and spare parts. [F4]'s "open question" is on the other two pages:

- https://fitcheck.care/request/1/offer/8 (525DS): "Do not ship", the advisory, and 2 open questions (a supplier for the four filters in Uganda; spare parts), each linking to `/supply`. Its support row carries the FDA GUDID record link.
- https://fitcheck.care/request/1/offer/10 (Houston): "Needs info", "11 questions before a verdict". Only spare parts links to `/supply`; the other ten say "(for the donor)".

What to say: "Fifteen checks, each decided by code, each pointing at its source. The add-ons are checked too: a stabilizer counts only if its own plate runs on this clinic's mains."

If it breaks: the page is screened again on every load. If it shows an error saying the offer can no longer be proposed, someone pressed "Ask this donor"; release the unit through the `/confirm` link (see "Before the pitch") or show offer 8 and 10 instead.

## Screen 4: the handover packet

Click "Generate handover packet" on offer 9's page, or open https://fitcheck.care/packet/9?facility=uganda-district

It shows, top to bottom:

- "For Kayunga District Hospital, Uganda. Staff read English." and the header "English, as requested". No translation, because Kayunga reads English.
- The manufacturer's own editions: the three 525KS instruction guides (SE-525KS-1 Rev D, SE-525KS-2 Rev A, SE-525KS-3 Rev B), with their languages.
- The English card from SE-525KS-1 Rev D: quick start, care schedule, consumables, warnings, each line with its page (p.8, p.9). Every number and part code on it was checked against the manual.
- "Paperwork for Uganda": the Ministry of Health no-objection, the detailed equipment list and the packing list, from the MoH guideline.
- "Send with it".

The rejected 525DS has no packet link (B6 Decision 1). `/packet/8` still answers if typed; do not show it as the 525KS.

What to say: "The card is the manual's own words, cut to a page. A line whose numbers aren't in the manual is dropped before anyone sees it. For a clinic that reads French, OCI Language puts the translation beside the English, marked 'machine-translated, verify'."

"Send with it" lists the voltage stabilizer and the 2 years of filters, the same two conditions as the candidate page. With no stored match for this offer and clinic, `_packet_verdict` (`app/web/marketplace_routes.py`) screens the offer with the same supply intel the candidate page uses, so the recorded answer about filters carries over. A stored match still wins.

This went live with the lead's redeploy after these docs were written (build b10-packet-intel). If the packet still lists only the stabilizer, a rebuild should not be needed, since the send-with list is computed on each request, not cached with the card; if it is, add `&rebuild=1` to the packet URL.

Other clinics, if asked: `/packet/9?facility=haiti-community` (French beside English) and `/packet/9?facility=nepal-phc` (Nepali flag, English card). Neither is pre-built: the first open makes one card call, and Haiti also one OCI Language call.

If it breaks: the card is cached after the first build, so a model outage on stage does not touch it. If the cache was cleared and the model is down, the page says "The card could not be built" and still shows the editions and the paperwork.

## Close: the impact page

URL: https://fitcheck.care/impact

The counters read: 20 devices screened, 11 stopped before shipping, 0 need extras sent with them, 0 cleared as they are, 9 need answers first. The seeded history has no works or works-if verdict, so those two counters read 0.

Under them is the paragraph, labelled "written by the model from the figures above". On 2026-10-18 it read:

> The tool screened 20 devices for three clinics in Haiti, Nepal and Uganda, and stopped 11 shipments because they would not work in the target setting. The most frequent blockers were operating temperature (8 cases), supply frequency (2 cases) and training and repair (3 cases), while 9 screenings remain pending further information and none required additional add‑ons. This ensures that only equipment that truly matches each clinic's conditions is sent, protecting resources and patient safety.

It is cached per process, so a restart can reword it. Its numbers are gated: each must be a figure from the log.

What to say: "Every number on this page is a query. The model wrote the sentences, and code checked each number in them against the log before showing it."

Disclose if asked (B6 check M2, recorded for the operator): the paragraph's blocker counts (temperature 8, frequency 2, training 3) count hard failures only. The bars under it, "Why donations failed", count failures and conditions together, so they lead with grid reliability 20 and supply voltage 7, which the paragraph does not name. The blocker counts add to 13 against 11 blocked devices, because one device can fail more than one check.

If it breaks: if the model fails or the gate refuses the reply, the page shows a sentence code writes from the same figures, labelled "written by code from the figures above". A failed call is retried after 5 minutes.

## Optional: the inventory CSV

URL: https://fitcheck.care/offer/csv

Upload `data/sample_inventory.csv` (also at https://fitcheck.care/offer/csv/sample). Kayunga is preselected. Press "Screen every row". The page reads "6 rows screened against Kayunga District Hospital": 0 works, 1 works if you also send, 3 needs info, 2 won't work. Checked live on 2026-10-18.

| Line | Device | Verdict |
|---|---|---|
| 2 | DeVilbiss 525KS | Ship with: stabilizer and 2 years of filters |
| 3 | Laerdal LSU | Needs info |
| 4 | Philips SureSigns VM6 | Needs info |
| 5 | Nellcor N-560 | Needs info |
| 6 | SonoSite M-Turbo | Do not ship: training and repair |
| 7 | DeVilbiss Pulmo-Aide 5650D | Do not ship: supply frequency (60 Hz) |

Screening writes nothing. "Download CSV" gives the input columns plus verdict, blockers, send_with, questions and notes. Do not press "Post these 6 offers" (see "Before the pitch"). Results are held in the server's memory for an hour; a restart forgets them, and the page then says the screening is not held any more.

If it breaks: skip it. It is not one of the four screens.
