# The made-up document set

Real company documents may not be used until a written permission exists, so the checking aid is built and tested on documents made up for the purpose (design specification, section 8, item 2). The set has 20 supplier order confirmations with the purchase order (PO) PDFs they answer, and 2 captured webshop cart pages with the PO exports they are checked against. It also holds a reference answer for every case. Every later task tests against this set, and the model bake-off reads it.

Nothing here comes from a real company. The suppliers are named S01 to S22, the item codes are KM numbers, and the descriptions are generic hydraulic and fastening parts. No document holds a real company, person, address, e-mail address or price list.

## Regenerate

```bash
python3 -m aid.madeup.generate --spec aid/madeup/spec --out aid/madeup/out
```

The command draws every case into `out/` and writes `references.csv` next to the spec folder. If a spec's values contradict the differences it declares, it stops before writing anything. The output is the same byte for byte on every run: reportlab runs in its invariant mode, and the scans and the screenshot are rendered the same way each time. The tests generate the set into a temporary folder (the session fixture `madeup_out` in `aid/tests/conftest.py`). `out/` is committed so that the bake-off and readers have the files.

## The try-out files

`tryout/` holds made-up files in forms the set does not have, for trying out what is set up on the company side. They belong to no case and have no reference answer.

```bash
python3 -m aid.madeup.generate --tryout aid/madeup/tryout
```

The command writes the try-out files and nothing else: `out/` and `references.csv` are left as they are. `tryout/` is committed, and a test checks that the generator writes the same text.

- `EXPORT_nl.csv` is an export of open purchase order lines as a Dutch ERP overview saved as CSV, for the export reader (`aid/readers/po_export.py`). It is cut at semicolons, writes its numbers as 1.234,50 and its days as 24-11-2026, and has a title row and a blank row above its column titles (Inkooporder, Leverancier, Artikel, Omschrijving, Aantal, Eenheid, Prijs, Per, Leverdatum, Bedrag). The PO number and the supplier stand on the first row of each order only, the lines have no line number, Bedrag is a column the aid does not read, and the last row is a total. It holds PO 250301 of supplier S21 (KM-3101 to KM-3103) and PO 250302 of supplier S22 (KM-3201 and KM-3202); these numbers and item codes belong to no case of the set. It is read with the column map `TRYOUT_EXPORT_COLUMNS` of `generate.py`, the decimal comma and the fill-down of the PO number (the settings `export_columns`, `export_decimal_comma` and `export_fill_down`).

## The cases

| Case | Layout | Language | Difference | PO | Item codes |
|---|---|---|---|---|---|
| MU01 | table_nl | nl | None. Three lines at 12,50 per piece, delivery 16-11-2026 | 250101 | KM-1001 to KM-1003 |
| MU02 | text_en | en | None. The price 1234.50 is printed in the European form "1.234,50" | 250102 | KM-0201 to KM-0202 |
| MU03 | table_nl | nl | None. PO versions 1 and 2 differ in line 2's quantity (4 and 6); the confirmation quotes version 1 and matches it | 250103 | KM-0301 to KM-0303 |
| MU04 | table_de | de | None. 32 lines over two pages, in the PO and in the confirmation | 250104 | KM-0401 to KM-0432 |
| MU05 | scan | nl | None. A scan without a text layer | 250105 | KM-0501 to KM-0503 |
| MU06 | total_only | nl | None. Only line totals are printed, so the prices cannot be settled | 250106 | KM-0601 to KM-0603 |
| MU07 | table_de | de | Price above the threshold: line 1 12,50 to 14,00 (12%). Line 2 (100 x 55,00) takes the PO total above the test limit | 250107 | KM-0701 to KM-0702 |
| MU08 | table_nl | nl | Price within 2% and 25 euro: line 1 12,50 to 12,70 (1.6%, 3 pieces, 0,60 per line) | 250108 | KM-0801 to KM-0802 |
| MU09 | table_nl | nl | Price basis: line 1 "12,50 / 100 st" against 12,50 per piece | 250109 | KM-0901 to KM-0902 |
| MU10 | table_de | de | Line discount: line 1 20,00 less 5% (19,00 net) against 20,00 | 250110 | KM-0101 to KM-0102 |
| MU11 | text_en | en | Quantity, pack size: line 1 12 pcs "packed per 12" against 10 pcs | 250111 | KM-1101 to KM-1102 |
| MU12 | text_en | en | Unit: line 1 50 pcs against 50 m | 250112 | KM-1201 to KM-1202 |
| MU13 | table_de | de | Unit missing on line 1 | 250113 | KM-1301 to KM-1302 |
| MU14 | table_de | de | Delivery: line 1 KW 48 against 16-11-2026; line 2's KW 47 holds the PO date | 250114 | KM-1401 to KM-1402 |
| MU15 | table_de | de | Delivery: line 1 30.11.2026 - 04.12.2026 against 16-11-2026; line 2's range holds the PO date | 250115 | KM-1501 to KM-1502 |
| MU16 | scan | nl | Delivery date missing on line 2 | 250116 | KM-1601 to KM-1602 |
| MU17 | text_en | en | PO line 3 missing on the confirmation | 250117 | KM-1701 to KM-1703 |
| MU18 | text_en | en | Extra confirmation line 3 (KM-1803), which answers no PO line | 250118 | KM-1801 to KM-1803 |
| MU19 | merged | nl | None. KM-1901 and KM-1902 on one confirmation line, every value printed once | 250119 | KM-1901 to KM-1903 |
| MU20 | text_en | en | Incoterm missing on the confirmation | 250120 | KM-2201 to KM-2202 |
| CART01 | html | nl | None. The cart matches its PO | 250201 | KM-2101 to KM-2103 |
| CART02 | png (page and screenshot) | nl | Price: KM-2002 at 41,00 against 40,00. KM-2003 is not in the cart | 250202 | KM-2001 to KM-2003 |

## Conventions

- **PO numbers and suppliers.** MUNN has PO number 2501NN and supplier SNN. CARTNN has PO number 2502NN; CART01 has supplier S21 and CART02 has S22. Each case has its own supplier.
- **Item codes.** A code is KM-<block><line>, where the block is the case number and the line number has two digits (MU04 runs from KM-0401 to KM-0432). Two cases keep codes that the plan's later tests name: MU01 uses block 10 and CART02 uses block 20. MU10 and MU20 therefore take blocks 01 and 22, the blocks MU01 and CART02 would have had, and CART01 takes block 21. Every code belongs to exactly one case. `ITEM_BLOCKS` in `generate.py` holds the exceptions, and the spec model checks every code against its block.
- **Dates.** Most PO versions are dated 19 or 20 October 2026, with confirmations two or three days later. MU03's versions are dated 12 and 26 October and its confirmation 14 October, so the confirmation date matches neither version and only the version quote, or the lines, can settle the version. The carts' POs are dated 3 and 4 November, the days of capture. Most delivery dates are 16 November 2026, the Monday of ISO week 47.
- **Number and date formats.** Dutch and German documents write 1.234,50, 16-11-2026 (Dutch) and 16.11.2026 (German). English documents write 1,234.50 and ISO dates (2026-11-16), a form the document schema's normaliser reads. Week numbers are "week 48" in Dutch and English and "KW 48" in German.
- **Authorization.** `buyer_authorization_exceeded` is true only for MU07, whose PO total is above the made-up test limit in `aid/tests/conftest.py`. The buyer's real limit is never written here.

## The layouts

**The PO PDF (po_madeup)** is in the style of an Exact PO. Its six header rows are "Inkooporder <number>", "Versie <n>", "Datum <dd-mm-yyyy>", "Leverancier: <name>", "Levering: <Incoterm>" and "Valuta: EUR". Then comes a table with the columns Regel, Artikel, Omschrijving, Aantal, Eenheid, Prijs, Per and Leverdatum. A page holds 25 lines, and the column titles are repeated on every page. Per is the number of units the price is for (1 or 100).

**Every confirmation** starts with the supplier's name and the title (Orderbevestiging, Auftragsbestätigung or Order confirmation), each on its own row. Then come the PO number ("Uw inkooporder <number>", "Ihre Bestellung <number>" or "Your PO <number>"; MU03 adds ", versie 1"), the date, the currency and, unless the case removes it, the Incoterm. Each header field has a row of its own.

| Layout | What it looks like |
|---|---|
| table_nl | Columns Pos, Artikel, Omschrijving, Aantal, Eenheid, Prijs, Per, Leverdatum |
| table_de | Columns Pos, Artikel-Nr., Bezeichnung, Menge, ME, Preis, PE, Rabatt, Liefertermin |
| text_en | One sentence per line, no table: "Line 1: 2 pcs of KM-0201 Gear pump 8 cc at EUR 1.234,50 each, delivery 2026-11-23." |
| merged | table_nl, with one line holding two item codes |
| total_only | table_nl with a Totaal column (the line total) in place of Prijs and Per |
| scan | table_nl rendered at 150 dpi in grey, turned by 0.8 degrees and stored as a JPEG image in a PDF, so there is no text layer |

**A cart page** is a saved page of the supplier's webshop: a Winkelwagen with "Uw referentie: <PO number>" and the capture date. CART02 is also a 1280 x 720 screenshot (`CART02.png`) with the same content. No PO PDF exists yet at the time of a pre-check, so the PO side comes from the ERP export, `<case>_export.csv`. Its columns are po_number, line_id, item_code, description, quantity, unit, unit_price, price_basis and delivery_date, written with a decimal point, ISO dates and the unit as the PO prints it.

## The specs

Each `spec/<case>.json` holds the case id, the PO and either a confirmation or a cart.

- The PO has its number, supplier, currency, Incoterm, `buyer_authorization_exceeded` and its versions with their lines.
- The confirmation has its layout, language, document date, `version_quoted`, Incoterm, lines and differences. The cart has its layout, capture date, lines and differences.

Values are stored in normalised form: a decimal point, the units pc and m, ISO dates, ISO weeks such as 2026-W48, and ranges such as 2026-11-30/2026-12-04. A null means the document does not print the value.

`text_override` prints a text in place of a value's usual form, for example MU02's "1.234,50" and MU09's "12,50 / 100 st". An empty text leaves the cell empty: MU09 prints its basis inside the price, so its Per cell is empty.

The spec adds three fields to the plan's interface:
- `discount_pct` on document lines, used by MU10;
- `line_total` on document lines, used by the total_only layout;
- `differences` on the cart.

`differences` lists what the documents differ in. Its `kind` uses the words of the comparison step's flags: different, missing on the document, missing line, extra line, not in cart, later and earlier. Its `line_id` is one of:
- the PO line;
- the confirmation line's own number, for an extra line;
- blank, for the case-level Incoterm.

## The references

`references.csv` has the header of `evaluation/templates/references.csv`. Each case has:
- one version item;
- one line-presence item per PO line and per extra confirmation line;
- one field item per field of each line pair (price, quantity, unit and delivery_date; for the carts only price, quantity and unit, because the pre-check compares no dates);
- one required-information item.

Every row has `reader` = spec and `adjudicated` = no. The generator works out each label from the values by the scoring manual's rules (sections 2 and 3). `generate()` checks every spec before it writes a file: if a spec's values do not give exactly the differences it declares, it stops and writes nothing. The tests in `aid/tests/test_madeup.py` check the references in three ways:

- `test_references_match_the_specs`: the committed `references.csv` has the template's header and equals, row for row and in order, the rows the specs give, carts included.
- `test_references_consistent`: every difference a confirmation declares has a row that is not same.
- `test_contradicting_spec_writes_nothing`: a spec whose values contradict its declared differences makes `generate()` raise before it writes any file.

- **version_answered** is on every row: it is the version the items are judged against. The version item's label is empty.
- **Line presence** is same for a matched or merged line, missing on the confirmation for a missing line (in CART02, the item not in the cart) and different for an extra line. `line_label` is matched, merged, missing or extra.
- **Field labels** are same, different, missing on the confirmation or cannot be settled. The rules behind them:
  - Price: the confirmation's net price, after any line discount, converted to the PO's price basis and compared to the cent.
  - Quantity: compared in the same unit.
  - Delivery date: a week number covers Monday to Friday of its ISO week and a range covers its days. A PO date inside them is same.
- **Required information** is same, or missing on the confirmation (MU20). For the carts it is not applicable.
- **Sources** come in three forms:
  - `<file>, page p, line l`: the lines of each page are numbered from 1, top to bottom. Every value of a table row sits on that row and every header field on its own row, which is how the PDF text reader of Task 5 numbers them.
  - `po`: an item that only the PO shows (a missing line, the missing Incoterm, a cart's version).
  - `<file>, row r`: the r-th article row of a cart page.

How the hard items are labelled, and why:

- **MU05 and MU16 are scans.** A person can read a scan, so their items carry ordinary labels. Whether the aid can read them is a question about the aid, not about the reference.
- **MU06 prints only line totals.** The scoring manual compares totals only when no unit price is given, and it leaves prices given only as totals to be fixed on the development cases (section 3). Until that rule is fixed, the unit prices are cannot be settled. The note shows the arithmetic, which agrees with the PO.
- **MU12 and MU13 have unit problems.** MU12 states pieces against metres on the PO, and MU13 states no unit at all. Quantity and price are compared in the same unit, and a conversion counts only if a document states it (section 3). So quantity and price are cannot be settled, and the unit item carries the difference.
- **MU19's merged line prints each value once for PO lines 1 and 2.** It is a kit: 4 at 31,50, which is 24,00 plus 7,50. The scoring manual leaves the values of merged lines to the development cases, and a value printed once cannot be split between two PO lines. All eight field items of the two PO lines are therefore cannot be settled. A value stated per code (for example "2 x KM-1901, 3 x KM-1902") would have been compared as usual, but no value on this line is stated that way. MU19 declares no difference; its PO line 3 is answered by confirmation line 2.
- **MU09** prices at 12,50 per 100, which is 0,125 per piece, against 12,50 per piece on the PO: different.
- **MU10** prints the same price as the PO, but 20,00 less 5% is 19,00 net: different.

## Files in out/

`<case>_po_v<n>.pdf` (one per PO version: 21 files, as MU03 has two), `<case>_conf.pdf` (20), `CART01.html`, `CART02.html`, `CART02.png`, `CART01_export.csv` and `CART02_export.csv`.
