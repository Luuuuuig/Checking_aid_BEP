# Using and maintaining the checking aid

The design, the implementation plan and the evaluation protocol are in the thesis repository (Luuuuuig/Bachelor-End-Project, folder `docs/`).
This manual is written for the state of the code on 5 November 2026 (the thesis repository's work branch at 75207cc); where the code changes, the code is right and this manual must follow. The aid README (`aid/README.md`) has the install and the "Try it out" steps on the made-up set.

## 1. What the aid does

The checking aid is a Python program that checks orders for the buyer, Arno. It does two checks.

- **The post-check.** A supplier sends an order confirmation. The aid compares it with the purchase order (PO): every line, the price, the quantity, the unit, the delivery date, missing or extra lines and the required information (the Incoterm).
- **The pre-check.** Before ordering in a webshop, the buyer saves the cart page. The aid compares the cart with the PO: price, quantity, unit and whether each line is in the cart.

**The flow of a confirmation.**

1. A mailbox rule saves the e-mails for the aid into a folder, the inbox.
2. The runner looks at the inbox every 60 seconds. It files PO PDFs as versions of their PO. For each order confirmation it opens a case. Other documents (packing slips, invoices, quotations) are skipped and listed in the digest.
3. The aid reads the confirmation, picks the PO version it answers, pairs its lines with the PO lines and compares the values by fixed rules.
4. The routing decides per finding: the aid makes the change itself (AUTO), asks the supplier a standard question (ASK), or hands the item to the buyer (REVIEW).
5. The aid makes the routine changes, sends the questions and records everything in the case folder.
6. Once a day the buyer gets a digest: what the aid did, with an undo link next to each change it made in the system, and a link to the review page for each case that waits for him.

**The rules as the buyer experiences them.** The rule codes appear only in the records, never on the page or in the digest. The settings each rule reads are named in brackets.

| Rule | What happens |
|---|---|
| R11 | A new supplier (fewer closed cases than `first_cases_review`) or a supplier the buyer marked (`review_suppliers`): the whole case comes to the buyer, whatever the findings. |
| R09 | The aid could not pick a PO version, or could not compare a value (another currency, a total only, an uncertain match, an unreadable value and similar): the item comes to the buyer. |
| R02 | The delivery date is earlier than the PO date, or later but within the supplier's lead time plus the tolerance (`supplier_lead_times`, `lead_time_tolerance_days`): the aid writes the new date on the PO line. |
| R03 | The delivery date is later than that, missing on the document, the lead time is unknown, or the line is split: the item comes to the buyer. |
| R04 | The price differs by at most `price_pct` percent of the PO price and at most `price_abs` euro on the line: the aid writes the new price on the PO line and on the article. |
| R05 | The price differs by more, is missing, the quantity is unknown, or the line is split: the item comes to the buyer. |
| R06 | The quantity or the unit differs: the aid asks the supplier (`messages_off_suppliers`). |
| R07 | A line is missing or extra: the aid asks the supplier. A line that is not in a cart comes to the buyer. |
| R08 | Required information is missing or different: the aid asks the supplier. |
| R10 | The PO is above the authorization limit (`authorization_limit`): the aid makes its date or price change and asks the approver to approve the PO again, with the buyer in copy. When a limit is set and the PO total is unknown, the change is not made and the item comes to the buyer. |
| R01 | When nothing waits for the buyer or the supplier, the aid attaches the confirmation to the PO and sets its status from Besteld to Bevestigd. When a question or a review item is open, it attaches the confirmation only, if it made other changes. |
| R00 | A finding that no rule covers comes to the buyer. |

On a cart, every question of R06, R07 and R08 comes to the buyer instead ("cart: for the buyer"), and nothing is attached and no status is set. For a supplier in `messages_off_suppliers` the questions come to the buyer instead ("messages off for this supplier").

**What the aid never does.**

- It never creates, releases or sends a PO, and never pays anything.
- It changes nothing but the PO line's price, delivery date and quantity, the article's purchase price, the attached confirmation and the confirmation status.
- It sends a supplier only the three standard questions, always with the buyer in copy, and the approver only the request to approve a PO again.
- It never says "no difference" when it could not compare a value.
- It never makes a change without a line in the write log that holds the old value, so that the change can be undone.
- In the Bachelor End Project it never writes to the live system: only to the copy of the test account, or as prepared changes.
- It never sends a company document, or anything taken from one, to an AI service outside the company's Microsoft tenant.

## 2. For the buyer

### 2.1 The daily digest

The digest is an e-mail from the aid's mailbox to the buyer. Its subject is "[Inkoopassistent] Dagoverzicht DD-MM-YYYY" (in English "[Inkoopassistent] Daily digest DD-MM-YYYY"). It comes once a day, at the first pass of the runner at or after `digest_time` (16:30 by default), or, when `digest` is `per_case`, after every pass that has a case or a skipped file to report. If the company allows a Power Automate flow, the same text also arrives in Teams (section 3.9).

The digest has up to five blocks. An empty block is left out.

| Block (Dutch) | Block (English) | What it lists |
|---|---|---|
| Behandelde zaken | Handled cases | Every case handled since the last digest, one line each: PO, supplier, status and the number of changes. Under it, a line per change: the field, the line or article, old value → new value. A change made in the system has a link "ongedaan maken" (undo). A prepared change says "(voorbereid)" and a failed one "(mislukt)": they changed nothing, so there is nothing to undo. |
| Wacht op u | Waiting for you | The cases in review, each with a link "beoordelen" (review) and a line per item with its reason, field and line. |
| Vraag gesteld aan de leverancier | Question sent to the supplier | The cases that wait for a supplier's answer, each with the PO, the supplier and the kind of each question (for example "aantal of eenheid"). |
| Mislukt | Failed | The cases the aid could not finish, with the first line of the error. Tell the researcher. |
| Overgeslagen | Skipped | Files in the inbox that opened no case, with the reason (for example "invoice", "duplicate", "more than one PO number"). |

A day without anything says "Er zijn geen zaken behandeld en geen bestanden overgeslagen." A pre-check case begins with the word "voorcontrole". Prices use the decimal comma in Dutch.

**The links.** "beoordelen" opens the review page with that case selected. "ongedaan maken" opens the page with that case and that change selected; nothing is undone until you press the undo button on the page. The links work only in a browser on the computer where the review page runs (section 3.8).

**Today.** The write route to Exact does not exist yet, so every change of the aid is a prepared change, marked "(voorbereid)". The digest then has no undo links, and the researcher applies the prepared changes by hand.

### 2.2 The review page

**Opening it.** Click a link in the digest, or open http://127.0.0.1:7860 in a browser on the computer where the page runs. If that port is taken, the command takes the next free one and prints the address, and `review_url` must then name that port. The page is in Dutch. The switch "Taal" at the top changes it to English ("Nederlands" / "English"). The switch changes the page only; the digest and the questions follow the language of the settings.

**The case list.** The dropdown "Zaak" (Case) shows the cases in review, and any case with a change made in the system that is not undone yet. Each case reads like "PO 250106 (S06), 2026-11-05 14:18, 3 punten": the PO, the supplier, the time the case was opened and the number of items to decide. A case that is not in review also shows its status. The button "Lijst vernieuwen" (Refresh the list) reloads the list. Cases that wait for a supplier, and closed or failed cases without an undoable change, are not in the list.

**The two documents.** Side by side: "Document van de leverancier" (the supplier's document, page by page) and "Inkooporder" (the PO version the aid chose). A cart saved as a web page, and a PO taken from an ERP export, have no pages to show; their values are in the lists.

**The three lists.**

- "Afwijkingen" (Differences): the differences, and the items the buyer must decide.
- "Overeenkomsten" (Matches): the values that agree.
- "Niet te vergelijken" (Could not compare): the values the aid could not compare, with the reason.

Each list has the columns Punt (item), Veld (field), Regel (line), Inkooporder (the PO value), Leverancier (the supplier's value), Reden (reason), Bron inkooporder and Bron leverancier (where the value stands: page, line and the text of that line). Items to decide are numbered RI1, RI2 and so on.

**The four decisions.** Under "Besluiten" (Decisions) each item has a row with the choice "Besluit". A choice is saved at once ("Besluit over RI1 opgeslagen."); nothing is prepared or written until you close the case. Today a close only prepares its changes in cases/prepared/prepared_changes.csv and files its e-mails in cases/outbox/; nothing goes to Exact and no mail is sent from the page (section 3.7). The last choice on an item counts.

| Decision | What the close does |
|---|---|
| Bevestigen: waarde van de leverancier overnemen (Confirm: take the supplier's value) | On a price, delivery date or quantity of one PO line: writes the supplier's value on the PO line (today only as a prepared change). On any other item it writes nothing: handle it outside the aid. On an item about the whole case (for example "new supplier"; it names no finding) it means "seen, fine". |
| Waarde van de inkooporder houden (Keep the purchase order's value) | Writes nothing and sends nothing. On an item about the whole case it also means "seen, fine". |
| Leverancier vragen (Ask the supplier) | Prepares the standard question for that field, buyer in copy, one e-mail per kind of question. Today the page does not send it: it writes the e-mail as an .eml file in cases/outbox/ (section 3.7) and the researcher sends it by hand. The case then waits for the answer. Not offered on an item about the whole case (it has no field, for example "new supplier"), nor on a cart. |
| Onopgelost (Unresolved) | Nothing happens; the case stays in review. |

**The article tick.** On a price item the tick "Ook de artikelprijs bijwerken" (Also update the article price) appears. With the tick and "Bevestigen", the close also writes the new price on the article. The tick is set already when the aid had tried that article change itself and it waits for you.

**The close.** The button "Zaak afsluiten" (Close the case) carries out the decisions. The page then says the new status, for example "De zaak is nu: afgesloten." The case closes when every item is "Bevestigen" or "Houden", nothing went wrong and no question to the supplier is open. Then the aid attaches the confirmation and sets the PO to Bevestigd. The case waits for the supplier when an item is on "Leverancier vragen". It stays in review when an item is undecided or "Onopgelost", or a message could not be sent.

What a close writes, for the record: each change in the write log of the case and in the prepared changes (the page always uses the prepared writer, section 3.7); the closes and undos in `steps/09_close.json` and in the audit record; a row per decision in the case's `dispositions.csv`. Your choices themselves are in `decisions.jsonl`. A second close never repeats a change or a question that was made already.

**Messages after a close.**

- "Een schrijfactie is niet gelukt (...): ... De zaak blijft ter beoordeling." (A write did not go through.) The close stopped at that change; the reason is "De schrijfactie is geweigerd" (The write was refused) or the label of the item it became, such as "Voorbereide wijziging: met de hand doorvoeren".
- "Een bericht is niet verzonden; de zaak blijft ter beoordeling." (A message was not sent.) Usually the supplier has no e-mail address in the settings. Tell the researcher.
- "Vraag aan de leverancier staat open." (A question to the supplier is open.) The PO is not confirmed while the supplier may still answer.
- "Deze zaak kan niet worden afgesloten: zij is niet meer ter beoordeling." The case was closed or changed in the meantime. Refresh the list.
- "Dit besluit kan hier niet worden genomen." (This decision cannot be taken here.) For example "Leverancier vragen" on an item about the whole case or on a cart (the page does not offer it there).

**The undo.** Below the close button: choose the change under "Schrijfactie" (Write) and press "Ongedaan maken" (Undo). Only changes made in the system are offered. The page says "Ongedaan gemaakt: ..." (Undone), or "Niets ongedaan gemaakt: ..." when the change was undone already, or "Ongedaan maken is mislukt." when the undo failed. "Kies eerst een schrijfactie." means no change was chosen. Note for the operator: the review page, as its command builds it today, uses the prepared writer, so an undo from the page is a prepared change too (section 3.10).

**The re-approval banner.** "Deze wijziging stuurt de inkooporder opnieuw door de fiattering" (This change sends the purchase order through approval again) shows on a PO above the authorization limit. A close that changes a PO line then also asks the approver to approve the PO again, with you in copy.

**Other messages.** "Kies eerst een zaak." means no case is chosen. "De besluiten van deze zaak kunnen niet worden gelezen (decisions.jsonl)." means the file with your choices is damaged; tell the researcher.

### 2.3 The supplier questions and the wait

The aid asks only three standard questions: about a quantity or unit ("aantal of eenheid"), about a missing or extra line ("ontbrekende of extra regel"), and about missing information ("ontbrekende gegevens"). Each is an e-mail from the aid's mailbox to the supplier, the buyer in copy, with the subject "Vraag over orderbevestiging PO <number>" and a table of the lines (line, item code, description, our value, your value). It asks for an answer by reply within the wait, `ask_wait_working_days`, three working days by default.

- **The supplier replies** to the e-mail: the aid files the reply into the case, and the case comes back to you with the item "supplier answered: read the reply". The page shows this reason as "Andere reden" (Another reason), and the digest shows the English text. The page does not show the reply itself: read it in your mailbox (you were in copy, so a reply to all reaches you). If the answer needs a change, make it by hand, then choose "Bevestigen" or "Houden" on the item and close the case.
- **The e-mail bounces**: the case comes back at once with "Bericht kwam terug" (Message bounced).
- **No answer in time**: after the wait (Saturday and Sunday are skipped; public holidays are not) the case comes back with "Geen antwoord binnen de wachttijd" (No answer within the wait).

An out-of-office reply changes nothing. After a question the aid asked itself, the differences stay in the list "Afwijkingen" without a decision row. Handle them outside the aid if needed, then decide on the new item (without a finding) and close. After a question you asked from the page, the item you decided on keeps its row, still on "Leverancier vragen": change it to "Bevestigen" or "Houden" before you close, or the case stays in review. A case that comes back is listed again under "Wacht op u" in the next digest.

### 2.4 What the review reasons mean

The page shows each reason by its label. The digest uses the same labels.

| Reason in the records | Label (Dutch) | Label (English) | In plain words |
|---|---|---|---|
| new supplier | Nieuwe leverancier | New supplier | One of the first cases of this supplier: please look at all of it. |
| supplier marked for review | Leverancier gemarkeerd voor beoordeling | Supplier marked for review | You asked to see every case of this supplier. |
| no version selected | Geen versie van de inkooporder gekozen | No version of the purchase order chosen | The aid could not tell which PO version the document answers, or does not know the PO. |
| split line: parts differ | Gesplitste regel: de delen verschillen | Split line: the parts differ | The supplier split one PO line into parts with different values. |
| no delivery date on the document | Geen leverdatum op het document | No delivery date on the document | The supplier gave no date for the line. |
| no lead time known for the supplier | Geen levertijd bekend voor de leverancier | No lead time known for the supplier | The later date cannot be judged: the settings hold no lead time for this supplier. |
| delivery date beyond the lead time plus the tolerance | Leverdatum later dan de levertijd plus de marge | Delivery date later than the lead time plus the margin | The date is later than the aid may accept by itself. |
| no price on the document | Geen prijs op het document | No price on the document | The supplier gave no price for the line. |
| quantity unknown for the price rule | Aantal onbekend voor de prijscontrole | Quantity unknown for the price check | Without a quantity the euro limit cannot be checked. |
| price above threshold | Prijsverschil boven de drempel | Price difference above the threshold | The price differs more than the aid may accept by itself. |
| PO total unknown | Totaalbedrag van de inkooporder onbekend | Purchase order total unknown | The aid cannot tell whether the PO is above the authorization limit, so it changed nothing. |
| cart: for the buyer | Winkelwagen: voor de inkoper | Cart: for the buyer | A difference on a cart: correct the cart or the PO before ordering. |
| messages off for this supplier | Berichten aan deze leverancier staan uit | Messages to this supplier are off | The aid would have asked the supplier, but you switched messages off for this supplier. |
| could not be placed | Kon niet in de winkelwagen worden geplaatst | Could not be placed in the cart | A PO line is not in the cart. |
| not covered by a rule | Voor dit verschil is geen vaste aanpak | No set way of handling this difference | No rule covers this difference. |
| prepared change: apply by hand | Voorbereide wijziging: met de hand doorvoeren | Prepared change: apply it by hand | The write to Exact failed; the change is prepared for the researcher. |
| write failed: ... | Schrijfactie mislukt | Write failed | The change could not be made or prepared. |
| not applied: an earlier write failed ... | Niet uitgevoerd: een eerdere schrijfactie is mislukt | Not applied: an earlier write failed | The aid stopped writing on this PO after a failed change. |
| no address for the supplier | Geen e-mailadres van de leverancier | No e-mail address of the supplier | The question was not sent: the settings hold no address for this supplier. |
| message not sent | Bericht niet verzonden | Message not sent | The mail server did not take the message. |
| message bounced | Bericht kwam terug | Message bounced | The supplier's address did not accept the question. |
| no answer in N working days | Geen antwoord binnen de wachttijd | No answer within the wait | The supplier did not answer in time. |
| supplier answered: read the reply | Andere reden | Another reason | The supplier replied (section 2.3). |
| run cut off after writes: check the write log | Andere reden | Another reason | The run stopped half way after a change; the researcher checks the write log first. |

The kind of difference, and why a value could not be compared:

| Reason in the records | Label (Dutch) | Label (English) | In plain words |
|---|---|---|---|
| different | Verschil | Different | The values differ. |
| missing on the document | Ontbreekt op het document | Missing on the document | The supplier left the value out. |
| missing line | Regel ontbreekt | Line missing | A PO line is not on the confirmation. |
| extra line | Extra regel | Extra line | The confirmation has a line the PO does not have. |
| not in cart | Niet in de winkelwagen | Not in the cart | A PO line is not in the cart. |
| later | Later dan besteld | Later than ordered | A later delivery date. |
| earlier | Eerder dan besteld | Earlier than ordered | An earlier delivery date. |
| different currency | Andere valuta | Another currency | The document uses another currency. |
| currency missing | Valuta ontbreekt op het document | Currency missing on the document | The PO states a currency and the document none. |
| unknown price basis | Prijseenheid onbekend | Price basis unknown | One side prices per 1, the other per 100 or similar, and the aid cannot tell. |
| unknown unit conversion | Omrekening van de eenheid onbekend | Unit conversion unknown | The units differ and the aid cannot convert them. |
| total only | Alleen een totaalbedrag | A total amount only | The document gives no price per line. |
| match uncertain | Koppeling van de regels onzeker | Line match uncertain | The aid is not sure which document line answers which PO line. |
| unreadable or unverified source | Bron onleesbaar of niet gecontroleerd | Source unreadable or not verified | A value could not be read with certainty. |
| no PO date | Geen leverdatum op de inkooporder | No delivery date on the purchase order | The PO has no date to compare with. |
| merged line | Samengevoegde regel | Merged line | The supplier put several PO lines on one line. |
| no PO price | Geen prijs op de inkooporder | No price on the purchase order | The PO has no price to compare with. |
| no PO quantity | Geen aantal op de inkooporder | No quantity on the purchase order | The PO has no quantity to compare with. |
| no PO unit | Geen eenheid op de inkooporder | No unit on the purchase order | The PO has no unit to compare with. |
| any other | Andere reden | Another reason | A reason without a label. |

### 2.5 The pre-check today

Today the pre-check works on a cart page saved from the browser as a web page (HTML), together with the PO side from an ERP export, run through the batch command (section 6). The watched folder does not take cart pages. A cart saved as a screenshot (PNG) needs the AI reader, which is not available for company documents yet. On a cart, every difference comes to the buyer; "Leverancier vragen" is not offered, because the buyer corrects the cart or the PO before ordering. A capture straight from the browser, with the PO number, is not built. aid/sources/capture.py can open a cart case from a saved page and a PO number, but no command or page calls it yet, and no browser tool exists. Until then a cart goes through the batch command.

## 3. Running it

### 3.1 The host and the folder layout

The host (a company PC or the company's Microsoft environment), how the runner is started and kept running, the folder `<root>` and where the two credentials are kept follow Remy's answer, which has not come yet. The first run on documents of the test-account copy also needs the company supervisor's written OK. What follows is what the code needs, wherever it runs.

The host needs Python 3.13 and a copy of the repository. From the folder that holds `aid/`:

```bash
python3 -m pip install -r aid/requirements.txt -r aid/requirements-dev.txt
python3 -m pytest aid/tests -q
```

The suite runs offline and calls no AI service.

The folder layout, with the folder settings of the example (`cases_root` "cases", `po_store_root` "po_store", `watched_folder` "inbox"); relative paths in the settings resolve against the folder that holds the settings file:

```
<root>/
  settings.json          the settings with the real values (kept only here)
  STOP                   create it to stop the runner
  inbox/                 the watched folder, filled by the mailbox rule
    processed/           the files the runner has handled
  po_store/<PO>/v<n>/    po.pdf and po.json, one folder per PO version
  cases/
    <case>/              one folder per case, named <PO number>_<YYYYMMDDTHHMMSS> or unknown_<...>
    prepared/            prepared_changes.csv, the changes to apply by hand
    outbox/              messages written as .eml files instead of sent
    digests/             <day>.html of each digest
    pricelists/<day>/    the write log of a price-list import (when --settings gives the folder)
    runner.log           the runner's errors
    digest_state.json    what the next digest reports
    review_items.jsonl   the files the scan skipped that the buyer must look at
```

A case folder holds:

```
case.json          the case id, kind, PO number, time of opening, the input files with their sha256
input/             the copy of the supplier's document
steps/             01_po_versions.json, 02_read_document.json, 03_version.json, 04_match.json,
                   05_compare.json, 06_route.json, 07_execute.json, 08_result.json,
                   and 09_close.json after a close or an undo on the review page
messages/          sent.jsonl, replies/, bounces/, auto/, escalated.jsonl
write_log.jsonl    one line per change made, prepared or failed, and per undo
dispositions.csv   the decisions in the evaluation's form
decisions.jsonl    the buyer's choices on the review page
audit.json         the audit record
```

### 3.2 The settings file

Copy `aid/settings.example.json` to `<root>/settings.json` and fill it in. A key the aid does not know stops it, so a typing error is never silently ignored. An error names the field, never its value. The settings file with the real values never goes into a repository. The Exact token and the mailbox password are not settings: they are the environment variables `AID_EXACT_TOKEN` and `AID_MAIL_PASSWORD`, and never go into any file.

| Setting | Default | Meaning | Company-specific |
|---|---|---|---|
| `language` | `"nl"` | The language of the digest and the questions: `"nl"` or `"en"`. | |
| `price_pct` | `"2.0"` | R04: largest price difference the aid accepts, in percent of the PO price. | the company sets it |
| `price_abs` | `"25.00"` | R04: largest price difference the aid accepts on a line, in euro (difference per piece times quantity). | the company sets it |
| `lead_time_tolerance_days` | `7` | R02: calendar days a later date may lie beyond the supplier's lead time. | the company sets it |
| `ask_wait_working_days` | `3` | Working days the aid waits for a supplier's answer. | the company sets it |
| `first_cases_review` | `5` | R11: the number of closed cases of a supplier before the aid acts on its own for that supplier. | the company sets it |
| `precheck_suppliers` | `[]` | Reserved; no part of the aid reads it yet. | |
| `review_suppliers` | `[]` | R11: suppliers whose every case goes to the buyer. | yes |
| `messages_off_suppliers` | `[]` | R06 to R08: suppliers the aid never asks; their questions go to the buyer. | yes |
| `digest` | `"daily"` | `"daily"` or `"per_case"`. | |
| `digest_time` | `"16:30"` | `"HH:MM"`, the host's local time from which the daily digest is made. | |
| `authorization_limit` | `null` | R10: the authorization limit; a PO total above it needs the approver again. `null` switches R10 off. | yes, never in a repository |
| `buyer_email` | (required) | The buyer's address: the digest, and the copy of every question. | yes |
| `buyer_name` | `""` | The name printed under the signature of the questions and of the re-approval request ("Inkoop" in Dutch, "Purchasing" in English); empty: the signature alone. | yes |
| `aid_email` | (required) | The aid's own mailbox, the sender of every message. | yes |
| `approver_email` | (required) | The approver who gets the request to approve a PO again. | yes |
| `supplier_emails` | `{}` | Supplier (as the PO names it) → the address for the aid's questions. | yes |
| `po_number_pattern` | `(?i:PO\|Inkooporder\|Bestelling\|Bestellung\|Order)[\s:#-]*(\d{6,8})` | The regular expression that finds a PO number in an e-mail or on a document; its first group is the number. | adjust if the PO numbers differ |
| `cases_root` | (required) | The folder of the cases, for example `"cases"`. | |
| `po_store_root` | (required) | The folder of the PO versions, for example `"po_store"`. | |
| `watched_folder` | (required) | The inbox, for example `"inbox"`. | |
| `supplier_lead_times` | `{}` | Supplier → lead time in days, from the ERP. A supplier without one gets every later date as a review item. | yes |
| `reader` | `"llm"` | `"llm"` or `"parser"`. The runner accepts only `"parser"` until Task 7b (section 3.12). Set it to `"parser"`. | |
| `model` | the id of the development model | The model of the AI reader. The runner does not use it; the freeze record shows it. | |
| `effort` | `"medium"` | `"low"`, `"medium"` or `"high"`, for the development AI client on the made-up set only. | |
| `exact_base_url` | `null` | The address of the Exact API (https). `null`: the aid only prepares its changes. | yes |
| `exact_endpoints` | `{}` | The path of each endpoint: `po_line`, `article`, `attachment`, `status` (section 3.10). | yes |
| `exact_fields` | `{}` | The Exact field name per kind of change, where it differs from the writer's default. | yes |
| `smtp_host` | `null` | The mail server of the aid's mailbox. `null`: messages are written as files. | yes |
| `smtp_port` | `587` | Its port (STARTTLS). | yes |
| `smtp_user` | `null` | The login of the aid's mailbox. | yes |
| `export_columns` | `{}` | For the pre-check: the aid's field name → the column name of the ERP export (section 6). Empty: the columns are named after the fields. | yes |
| `review_url` | `"http://localhost:7860"` | The address of the review page, used only in the digest's links. | |

`buyer_email`, `aid_email`, `approver_email`, `cases_root`, `po_store_root` and `watched_folder` have no default and must be given. Numbers that are amounts are written as text: `"2.0"`, `"25.00"`.

### 3.3 The mailbox rule

The mailbox rule is set up by Remy; its details follow his answer. It must:

- save the e-mails that carry Exact's PO PDFs, so that the aid knows every PO version;
- save the suppliers' e-mails with their attachments (confirmations, and any other document);
- save the replies to the aid's questions;
- save each as a whole e-mail, an `.eml` file, straight into `inbox/` (not into a subfolder).

Whole e-mails are best: the aid then reads the PO number from the subject and body too, and it can file a reply only by its headers. A `.pdf` or `.png` saved on its own is also handled; any other file is skipped as "unsupported file type". In the Bachelor End Project the documents of the test-account cases are to be re-sent to the researcher's company mailbox. The rule that files them into `inbox/` is not set up yet; it waits on Remy's answer.

### 3.4 Starting the runner and keeping it running

```bash
python3 -m aid.runner --settings <root>/settings.json [--poll-seconds N] [--max-passes N]
```

- `--settings` (required): the settings file. The STOP file is looked for next to it.
- `--poll-seconds`: seconds between passes, default 60.
- `--max-passes`: stop after this many passes; leave it out in production.

Each pass: handle the inbox, run every new case, end the waits that are over, and make the digest when it is due. The command runs until the STOP file exists, then prints the number of passes and ends with exit code 0. It ends at once with exit code 2 when the settings file cannot be read or is not valid, when `reader` is `"llm"`, or when the writer refuses the Exact settings (for example an address that is not https).

How it is kept running (a scheduled task at start-up, or a service) is the operator's choice on the host. Whatever starts it must:

- run it from the folder that holds `aid/`, with Python 3.13 and the packages installed;
- give the full path of the settings file;
- put `AID_EXACT_TOKEN` (when `exact_base_url` is set) and `AID_MAIL_PASSWORD` (when `smtp_host` and `smtp_user` are set) in the environment of the process, from the company's secret store, before the start;
- start it again if it ends with a code other than 0;
- run one runner per `<root>`, never two.

The runner uses the host's clock, so `digest_time` is the host's local time.

### 3.5 The STOP file

Create an empty file `STOP` next to the settings file. The runner ends before its next pass; a pass that is running finishes first, and the runner may take up to the poll interval (60 seconds by default) to notice the file. While `STOP` exists, a new start makes no pass at all. Delete `STOP` before starting the runner again. Documents that arrive meanwhile wait in the inbox and are handled once the runner runs again. `STOP` does not stop the review page.

### 3.6 The logs and the audit record

`cases/runner.log` has one line per problem: the time, the case id or the stage (`scan`, `scan <file>`, `escalation`, `digest`, `cases root`, `digest_state.json`), and the error. A case that keeps failing is logged once per run of the loop, and once more when it later succeeds. The pass goes on after an error.

`cases/digest_state.json` holds `last_sent` (the time of the last digest, or null), `reported` (the cases the next digest reports) and `skipped` (the skipped files it lists). When it cannot be read, a fresh one is started and the log says so.

`cases/<case>/audit.json` is the audit record, for the researcher, IT and the thesis; the buyer never sees it. It holds the inputs by sha256, the model (none in parser mode), the settings with the authorization limit only as "set" or "unset", every step's output with its seconds, every rule decision with the settings it read, every write and message, the errors, and the timings. The review page adds a list `close`, the mailbox a list `answers`, and the escalation a list `escalations`.

### 3.7 The review page on the host

```bash
python3 -m aid.review --settings <root>/settings.json [--share]
```

It runs on its own, beside the runner, and serves the page on 127.0.0.1 of the host only: open it in a browser on that machine. It uses port 7860 when that port is free. If it is taken (for example by a review page that is still running), the page starts on the next free port, up to 7959, and prints its address. Stop the old page first, or set `review_url` to the printed address, so that the digest's links work. `--share` opens a public link and is for the made-up set only: with company settings (an Exact address, endpoints or fields, an authorization limit, an SMTP server or login, or an address outside example.com and example.org) it is refused with exit code 2. An unreadable settings file also gives exit code 2.

As the command builds the page today, its writer is the prepared writer and its mailer writes files: a close prepares its changes in `cases/prepared/prepared_changes.csv` and writes its questions as `.eml` files in `cases/outbox/`, even when the runner itself writes to Exact or sends by SMTP. Wiring the Exact writer and the SMTP mailer into the page is not built yet; until then the researcher sends those questions and applies those changes by hand.

### 3.8 Where the digest goes

The runner always writes the HTML version to `cases/digests/<YYYY-MM-DD>.html` (a second digest on the same day replaces it). The text goes to `buyer_email` through the aid's mailbox when SMTP is set up, and otherwise as an `.eml` file into `cases/outbox/`.

### 3.9 Teams via Power Automate

A Power Automate flow outside the code can relay the digest to the buyer in Teams, so that no AI service is connected to Teams. The runner sets up no flow.

- Trigger: "When a new email arrives" (Office 365 Outlook connector) in the mailbox that receives the digest, with the sender `aid_email` and a subject that starts with `[Inkoopassistent]`. The prefix is the same in both languages.
- Action: "Post message in a chat or channel" (Microsoft Teams connector, by the Flow bot in a chat with the buyer) with the e-mail's text, or "Post card in a chat or channel" with a card built from the HTML version in `cases/digests/<day>.html`. The e-mail itself carries only the text.

If the flow is not allowed, the e-mail is the notification.

### 3.10 The Exact writer

**Today.** No route to Exact exists yet: `exact_base_url` is `null`, and every change is a row in `cases/prepared/prepared_changes.csv` (columns `entry_id`, `case_id`, `po_number`, `version`, `line_id`, `article_code`, `kind`, `old_value`, `new_value`, `document`, `rule_id`, `timestamp`). The researcher applies these by hand in the test-account copy.

**When the route exists.** The writer is built and tested against a fake server; what is missing is Remy's route. Then set `exact_base_url` (https only), the endpoints with their placeholders, and, where needed, the field names:

```json
"exact_base_url": "https://exact.example.com/api",
"exact_endpoints": {"po_line": "/po/{po}/v/{version}/lines/{line}", "article": "/articles/{article}",
                    "attachment": "/po/{po}/attachments", "status": "/po/{po}/status"},
"exact_fields": {}
```

These paths are an illustration only; the real ones follow Remy's answer. `po_line` must hold `{po}` and `{line}`, `article` `{article}`, `attachment` and `status` `{po}`. The default fields are `price` (PO line price and article price), `delivery_date`, `quantity` and `status`. The token is read from `AID_EXACT_TOKEN` at every call and sent only as "Authorization: Bearer".

For each change of a price, date, quantity, article price or status, the writer reads the field, writes the new value and reads it back to check it. Attaching posts the file; removing deletes it. A failed call is tried twice in all; then the change is prepared instead and becomes a review item "prepared change: apply by hand", and the aid makes no further change in that case. Every change, made or prepared, is a line in the case's `write_log.jsonl` with the old value, the new value, the rule and the evidence.

**The undo.** The undo applies the reverse change (the old value back; an attached file removed) and logs it with `undo_of` naming the change. Each change is undone once. From the review page the undo is prepared today (section 3.7). To undo every change of a case through the runner's writer, newest first:

```python
import os
from datetime import datetime
from pathlib import Path
from aid.settings import load_settings
from aid.runner import build_deps
from aid.execution.writer import undo_case

settings = load_settings(Path("<root>/settings.json"))
deps = build_deps(settings, os.environ)
print(undo_case(settings.cases_root / "<case id>", deps.writer, datetime.now()))
```

### 3.11 The SMTP mailer

Set `smtp_host`, `smtp_port` (587) and `smtp_user`, and put the password in `AID_MAIL_PASSWORD` before the runner starts. The runner checks for the variable once, at the start: without it, it writes every message as a file into `cases/outbox/` and sends nothing, without a warning. The mailer uses STARTTLS and checks the server's certificate. It sends from `aid_email`. An error names its kind, never the password, the login or a recipient. Each message carries a Message-ID, by which a reply is filed. In the Bachelor End Project the questions go to the researcher's address only, never to a real supplier: `supplier_emails` holds that address.

### 3.12 The AI service

The runner reads with the template parser only (`"reader": "parser"`). It refuses `"llm"` because the Microsoft client of Task 7b does not exist yet, and company documents never go to another AI service. In parser mode:

- a document without a text layer (a scan, a photo) cannot be classified: it is skipped as "no text layer; class unknown" and listed in the digest;
- a document with text but no known keyword is skipped as "other";
- a confirmation is read only if a parser template fits its layout (section 4.1); otherwise the case fails.

Task 7b needs from the company: the endpoint of the Azure OpenAI service (or another Copilot-family route) in the company's Microsoft tenant, and a way to authenticate (an API key in the secret store, or an Entra ID sign-in). The client then reads its credential from the environment or the secret store, never from the settings file. The settings will gain a field for the endpoint.

Also not built yet: the template of Exact's real PO PDF and the templates of the real suppliers' layouts (Task 5b). Until then the PO reader knows only the made-up layout, so a real PO PDF stays in the inbox as an error.

## 4. Maintaining it

### 4.1 Adding a supplier

1. Add the lead time in days to `supplier_lead_times`, under the supplier's name as the PO prints it.
2. Add the address for the questions to `supplier_emails`.
3. If the supplier's confirmations have a layout no template reads, add a parser template.

A template is a JSON file `aid/readers/templates/<name>.json`; the file name is the template's `name`. Files whose name starts with `po_` are PO templates and are not used for confirmations. Templates are tried in the order of their file names; the first whose `detect` expressions all match the document's text is used. Look at `table_nl.json` (a table) and `text_en.json` (one sentence per line) as examples. The fields:

- `name`, `language` (`"nl"`, `"de"` or `"en"`), `decimal_comma` (true for "1.234,50");
- `detect`: regular expressions that must all match somewhere in the text;
- `header`: per field (`po_reference`, `version_hint`, `supplier`, `document_date`, `currency`, `incoterm`) an expression with exactly one group, the value;
- `table` with `"strategy": "lines"`: `header_row_contains` (text on the title row), `columns` (each a `title` word and the `field` it fills: `line_id`, `item_code`, `description`, `quantity`, `unit`, `unit_price`, `price_basis`, `discount_pct`, `net_price`, `delivery_date`; `line_id` is required), and optionally `line_id_pattern`, `basis_mark` and `item_code_regex`;
- or `table` with `"strategy": "text"`: `line_regex` with named groups after those fields (`line_id` required), and optionally `line_start` and `item_code_regex`.

A misspelled key or a bad expression is refused when the template loads. Test a template on one document:

```bash
python3 -B -c "
from pathlib import Path
from aid.readers.supplier_parser import load_templates, detect_template, read_with_template
pdf = Path('aid/madeup/out/MU01_conf.pdf')
t = detect_template(pdf, load_templates())
print(t.name if t else 'no template fits')
doc = read_with_template(pdf, t)
for line in doc.lines: print(line.line_id, line.item_code.value, line.quantity.value, line.unit_price.value, line.delivery_date.value)
"
```

Or run the batch (section 6) with a one-line `cases.csv` and look at `steps/02_read_document.json` of the case. Add a test beside those of `aid/tests/test_supplier_parser.py` and run the suite. A template for a real supplier is company material: it stays on the company side and never goes into a repository; only the made-up templates are in the repository.

### 4.2 Changing thresholds

Edit `settings.json` and restart the runner and the review page (create `STOP`, wait for the runner to end, delete `STOP`, start both again). The settings are read once at start. The thresholds are `price_pct`, `price_abs`, `lead_time_tolerance_days`, `ask_wait_working_days` and `first_cases_review`; the supplier lists are `review_suppliers` and `messages_off_suppliers`. Each case's audit record keeps the settings it ran with. During the evaluation the settings must not change (section 4.8).

### 4.3 The price-list import and its undo

```bash
python3 -m aid.pricelist --file <list.csv or list.xlsx> --supplier <supplier> --current <current_prices.csv> --prepared <folder> (--write-log <folder> | --settings <root>/settings.json)
```

- `--file`: the price list, CSV (comma or semicolon) or the first sheet of an `.xlsx`, with the columns `item_code` and `price`, and optionally `basis`, `unit`, `min_quantity`, `valid_from`, `valid_to`.
- `--supplier`: the supplier, recorded with every change.
- `--current`: the current prices, a CSV file with the columns `item_code` and `price`.
- `--prepared`: the folder for `prepared_changes.csv`.
- `--write-log`: the folder whose `write_log.jsonl` gets every change; without it, `--settings` gives `<cases_root>/pricelists/<YYYY-MM-DD>`. One of the two is required.

The import always prepares its changes; it writes nothing to Exact. It prints a line per row (updated, skipped with the reason, or error) and a summary, and warns when rows are past `valid_to`. Exit code 0 when it ran, 1 when a file cannot be read or a write failed, 2 for a wrong command line. To undo an import, reverse its write-log folder with `undo_case` (as in section 3.10, with that folder in place of the case folder and a `PreparedWriter` or the runner's writer).

### 4.4 Re-running a case

A case is run once. The runner never runs a case again that holds `steps/08_result.json`, because a second run would make its changes again and send its questions again. A case whose run was cut off before any change or message is simply run at the next pass. A case cut off after a change or a message is not run again: it goes to review with the item "run cut off after writes: check the write log". Then the researcher compares its `write_log.jsonl` (and `messages/sent.jsonl`) with Exact and the mailbox, finishes by hand what is missing, and the buyer decides on the page.

To run a case again on purpose (after a fix, and only when its changes were undone or never made), through Python:

```python
import os
from pathlib import Path
from aid.settings import load_settings
from aid.runner import build_deps
from aid.case import load_case
from aid.pipeline import run_case, record_dispositions

settings = load_settings(Path("<root>/settings.json"))
deps = build_deps(settings, os.environ)
case = load_case(settings.cases_root, "<case id>")
result = run_case(case, deps, rerun=True)
record_dispositions(case, result)
```

This makes the changes and sends the questions again, and adds rows to `dispositions.csv`. A safer way to try a document again is the batch (section 6): it opens a new case folder, prepares its changes and sends nothing. Sending the same file to the inbox again does not work: a document seen before is skipped as "duplicate".

### 4.5 Cleaning old cases

During the evaluation, delete nothing under `cases/` and `po_store/`. The evaluation needs each case folder whole (`case.json`, `input/`, `steps/`, `audit.json`, `write_log.jsonl`, `dispositions.csv`, `decisions.jsonl`, `messages/`), and `cases/prepared/prepared_changes.csv`. Deleting a case folder also loses its undo, makes a re-sent copy of its document open a new case, and lowers the count of closed cases that rule R11 uses for its supplier.

What may go after it has been read: the files in `inbox/processed/` (each case keeps its own copy in `input/`, and each PO its copy in `po_store/`), old files in `cases/digests/`, the `.eml` files in `cases/outbox/` once handled, and an old `runner.log` while the runner is stopped. After the evaluation, archive the case folders to the company's restricted storage rather than deleting them.

### 4.6 Backups

Back up the whole `<root>` folder, at a moment when the runner is stopped or between passes. It holds company data (documents, addresses, the authorization limit in `settings.json`), so the backup stays on the company side, in storage the company names. The credentials are not in the folder; they stay in the secret store.

### 4.7 Updating the code

1. Create `STOP` and wait for the runner to end; stop the review page.
2. In the copy of the thesis repository: `git pull`.
3. Install the packages again and run the suite:

```bash
python3 -m pip install -r aid/requirements.txt -r aid/requirements-dev.txt
python3 -m pytest aid/tests -q
```

4. Only when every test passes: delete `STOP` and start the runner and the review page again.

Development happens in the thesis repository. The aid's own repository, Luuuuuig/Checking_aid_BEP, is a copy of `aid/` and of the two templates its tests read (`evaluation/templates/dispositions.csv` and `references.csv`). A sync copies these again by hand: copy them, run the suite in the copy, commit and push there. Nothing does this automatically.

### 4.8 The freeze

```bash
python3 -m aid.freeze --settings <root>/settings.json [--out <file>]
```

It writes the freeze record (default `freeze_<date>.json` in the current folder): the git commit and whether the working tree is clean, the sha256 of the settings file and the settings themselves (the authorization limit only as "set" or "unset"), the prompt files with their versions and hashes, the model, the rules R01 to R11 with the settings they read and their values, the aid version, the Python version and the installed packages. The record holds the settings' addresses and supplier names, so it stays on the company side.

During the evaluation nothing of this may change: no `git pull`, no change to `settings.json` (its hash is recorded), no new template or prompt, no package update. Freeze from a clean working tree. If something must change, record a new freeze and note the deviation as the evaluation protocol says.

## 5. Troubleshooting

**A case failed.** It is in the digest under "Mislukt" with the first line of its error, and not on the review page. Look in the case folder: `steps/08_result.json` (status `failed`, field `error`, which names the change it failed at, if any), `audit.json` (`errors`, with the step and, for an unexpected error, the traceback), and the last step file in `steps/`, which shows how far it got. A confirmation whose layout no template fits fails at reading. Fix the cause, then use the batch to try the document again (section 4.4).

**A document was not recognised.** See the "Overgeslagen" block of the digest and `cases/review_items.jsonl`. The class comes from keywords on the first page: "orderbevestiging", "order confirmation", "auftragsbestätigung" and similar make it a confirmation; a title line beginning with "Inkooporder", "Purchase order" or "Bestellung" a PO; "pakbon", "factuur", "offerte" and similar the other classes. Without a keyword the document is "other"; without a text layer it is "no text layer; class unknown" (only the AI reader of Task 7b can classify those). The PO number comes from `po_number_pattern` in the e-mail and on the first page. Two different numbers: "more than one PO number", skipped and listed. None: the aid tries to find the PO by its item codes, and else opens the case without a PO, which goes to review as "no version selected". If the company's PO numbers do not fit the pattern (6 to 8 digits after a keyword), change the pattern. A file that raised an error stays in the inbox, is tried again at every pass and is logged once in `runner.log` as `scan <file>`.

**A write was refused.** The Exact writer raised a WriterError (no token, a status that is not 2xx, a value that reads back different, no connection). After two attempts the change is prepared instead: it is in `prepared_changes.csv`, the write-log entry has the `error`, and the case is in review with "prepared change: apply by hand", while every later change of that case is "not applied: an earlier write failed". Check the token, the route and the endpoints, then apply the prepared change by hand and let the buyer close the case.

**A question was not sent.** The case is in review with "no address for the supplier" (add the supplier to `supplier_emails`) or "message not sent" (the mail server refused it; see the case's `audit.json`, `messages`). If questions appear as files in `cases/outbox/` instead of being sent, SMTP is not set, or `AID_MAIL_PASSWORD` was missing when the runner started. Questions asked from the review page are always written to `cases/outbox/` today (section 3.7).

**A reply was not filed.** A reply is filed only when its `In-Reply-To` or `References` header names the Message-ID of a question the aid sent (recorded in the case's `messages/sent.jsonl`). A reply written as a new e-mail, or a forward that drops these headers, is not filed: it is handled like any other e-mail. The mailbox rule must save the reply as a whole `.eml` file. A reply to a re-approval request is never filed to a case.

**The page shows no cases.** The page lists only cases in review and cases with a change made in the system that is not undone (whatever their status). Check that the page was started with the same settings file as the runner, press "Lijst vernieuwen", and look at the digest: cases that wait for a supplier, closed cases and failed cases are not listed unless they hold such a change.

**The digest did not come.** The daily digest is made at the first pass at or after `digest_time` (host time), once a day. If `last_sent` in `cases/digest_state.json` is today, it was made already. Look for `cases/digests/<day>.html` and, without SMTP, an `.eml` in `cases/outbox/`. A failed digest is logged in `runner.log` as `digest` and made again at the next pass. In `per_case` mode a pass without cases or skipped files makes no digest. The runner must be running at that time.

**The runner stopped.** If it printed "stopped by .../STOP", the STOP file exists: delete it and start again. Exit code 2 with "aid.runner: ..." means the settings file cannot be read or is not valid (the message names the fields), or `reader` is not `"parser"`. Otherwise see `cases/runner.log` and the log of the scheduled task or service on the host.

## 6. The evaluation commands

**The batch** runs a list of cases headless:

```bash
python3 -m aid.batch --cases <cases.csv> --po-dir <folder> --settings <file> --reader parser|recorded [--recorded-dir <folder>] --out <summary.csv>
```

`cases.csv` has the columns `case_id` (the evaluation's id), `document` (the supplier's document, relative to the folder of `cases.csv` or absolute), `po_number` (blank when unknown) and `kind` (`confirmation` or `cart`). First every `*_po_v*.pdf` and then every `*_export.csv` (with `export_columns`) of `--po-dir` is filed into the PO store. `--reader recorded` replays recorded AI answers from `--recorded-dir` (made-up documents only). A cart saved as HTML is read by the cart reader under either reader. The batch prepares every change in `<cases_root>/prepared/`, sends nothing, and ends the waits that are over. `--out` gets one row per case: `case_id`, `status`, `version`, `flags`, `uncompared`, `actions`, `writes`, `messages`, `review_items`, `error`. Exit code 1 when a case failed. To try it on the made-up set: `python3 -m aid.demo --out <folder>` writes the settings, the `cases.csv` of the 22 made-up cases and runs them (options: `--out`, default `demo`; `--reader parser|recorded|anthropic`, default `parser`, where `anthropic` calls the development AI service with the key in the environment variable `ANTHROPIC_API_KEY` and is for the made-up documents only, never for company documents; `--force`; `--prepare-only`, which writes the folder and files the purchase orders but runs no case, the folder to watch the runner loop work by itself, as the aid README's section "Try it out on the made-up set" shows). A folder that exists and is not empty is refused with exit code 2 unless `--force` is given.

**The disposition log** is each case's `dispositions.csv` with the columns `case_id`, `version_selected`, `line_id`, `field`, `decision`, `source`, `note` (the header of `evaluation/templates/dispositions.csv`). The aid's own decisions read "Exact update executed (R04)", "prepared (R01)", "write failed (...)" and "ask the supplier (R06)"; the buyer's decisions on the page carry "B01": "accept the PO value (B01)", "accept the supplier's value (B01)", "accept (B01)", "ask the supplier (B01)", "unresolved (B01)". The batch writes the `case_id` of `cases.csv`.

**The necessity check** decides whether the AI reader is needed:

```bash
python3 -m aid.evalscripts.necessity --cases <folder> --spec <folder> --recorded <folder> [--out <file>]
```

It has the parser and the AI reader read every `*_conf.pdf`, scores both against the specs and prints the decision (`keep_llm` or `parser_only`). The default output is `aid/evalscripts/results/necessity_<date>.csv`. As the command stands it replays recorded answers; a run on the made-up set is development evidence, not the check. The check on the development cases runs on the company side and needs the Microsoft client of Task 7b.

**The freeze record**: `python3 -m aid.freeze --settings <file> [--out <file>]` (section 4.8).

The evaluation protocol, with the disposition log (section 6.4) and the handling of the held-out run, is `docs/process/Evaluation_Protocol_v2_Draft_2026-10-21.md` in the thesis repository.

## 7. Korte handleiding voor de inkoper

**Het dagoverzicht.** Elke dag na de ingestelde tijd (standaard 16:30) krijgt u een e-mail "[Inkoopassistent] Dagoverzicht" (en via Teams, als dat is ingesteld). Daarin staat:
- *Behandelde zaken*: wat de assistent heeft gedaan, per inkooporder. Achter een wijziging in Exact staat "ongedaan maken". "(voorbereid)" betekent: nog niet in Exact, de onderzoeker voert het met de hand door.
- *Wacht op u*: zaken waar u naar moet kijken, met de link "beoordelen".
- *Vraag gesteld aan de leverancier*: zaken die op een antwoord wachten.
- *Mislukt* en *Overgeslagen*: geef deze door aan de onderzoeker.

**De beoordelingspagina.** Klik op "beoordelen", of open http://127.0.0.1:7860 op de computer waar de beoordelingspagina draait (de onderzoeker meldt het adres als de poort anders is). Kies een zaak bij "Zaak". Links ziet u het document van de leverancier, rechts de inkooporder. Daaronder de lijsten "Afwijkingen", "Overeenkomsten" en "Niet te vergelijken". Bij "Besluiten" kiest u per punt (RI1, RI2, ...) een besluit. Met "Taal" schakelt u naar het Engels.

**De vier besluiten.**
- *Bevestigen: waarde van de leverancier overnemen*: de waarde van de leverancier gaat in de orderregel (prijs, leverdatum of aantal). Vink "Ook de artikelprijs bijwerken" aan om ook de artikelprijs aan te passen.
- *Waarde van de inkooporder houden*: er verandert niets.
- *Leverancier vragen*: de assistent stuurt de leverancier een standaardvraag, met u in de kopie.
- *Onopgelost*: de zaak blijft open.

**Let op, nu nog:** de pagina voert niets zelf uit. Een besluit bij het afsluiten wordt een voorbereide wijziging (cases/prepared) en een vraag aan de leverancier of de fiatteur wordt als e-mailbestand klaargezet (cases/outbox). De onderzoeker voert de wijzigingen met de hand door en verstuurt de e-mails.

Druk daarna op "Zaak afsluiten". Als alles is bevestigd of gehouden, voegt de assistent de bevestiging toe en zet de order op Bevestigd. Ziet u de melding "Deze wijziging stuurt de inkooporder opnieuw door de fiattering", dan vraagt de assistent de fiatteur de order opnieuw goed te keuren.

**Ongedaan maken.** Klik in het dagoverzicht op "ongedaan maken", of kies op de pagina een schrijfactie onder "Schrijfactie". Druk dan op "Ongedaan maken". Pas na die knop wordt de wijziging teruggedraaid. Nu nog is dat een voorbereide wijziging: de onderzoeker draait die met de hand terug in Exact. Zolang alle wijzigingen "(voorbereid)" zijn, staat er geen "ongedaan maken" in het dagoverzicht.

**De vraag aan de leverancier en de wachttijd.** De leverancier krijgt standaard drie werkdagen om te antwoorden (de instelling kan anders zijn) (zaterdag en zondag tellen niet mee, feestdagen wel). Antwoordt de leverancier, komt de bounce terug, of komt er geen antwoord, dan staat de zaak weer bij "Wacht op u". Het antwoord zelf leest u in uw mailbox, want u stond in de kopie. Pas zo nodig de order met de hand aan, kies dan "Bevestigen" of "Houden" en sluit de zaak af.

**Wie belt u?** Bij een vraag, een fout, een zaak bij "Mislukt" of een pagina die niet opent: bel of mail de onderzoeker, Yijie.
