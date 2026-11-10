# Using and maintaining the checking aid

The design, the implementation plan and the evaluation protocol are in the thesis repository (Luuuuuig/Bachelor-End-Project, folder `docs/`).
This manual was written for the state of the code at commit 75207cc of the thesis repository's work branch and updated for Task 25 (the pre-check in the browser) on 8 November 2026; where the code changes, the code is right and this manual must follow. The aid README (`aid/README.md`) has the install and the "Try it out" steps on the made-up set.

## 1. What the aid does

The checking aid is a Python program that checks orders for the buyer, Arno. It does two checks.

- **The post-check.** A supplier sends an order confirmation. The aid compares it with the purchase order (PO): every line, the price, the quantity, the unit, the delivery date, missing or extra lines and the required information (the Incoterm).
- **The pre-check.** Before ordering in a webshop, a browser extension sends the cart page the buyer has open to the aid's capture server on the same computer. The aid compares the cart with the PO at once (price, quantity, unit and whether each line is in the cart) and the extension shows the result in a banner above the page, before the buyer presses order. A cart page saved from the browser can still go through the batch command instead (section 2.5).

**The flow of a confirmation.**

1. The e-mails for the aid arrive in a folder, the inbox. After a sign-in with the buyer's own Microsoft account on the settings tab of the review page, the runner reads one folder of the work mailbox itself and saves each new e-mail there (section 3.14); without a sign-in, a mailbox rule saves them there, as before (section 3.3).
2. The runner looks at the inbox every 60 seconds. It files PO PDFs as versions of their PO. For each order confirmation it opens a case. Other documents (packing slips, invoices, quotations) are skipped and listed in the digest.
3. The aid reads the confirmation, picks the PO version it answers, pairs its lines with the PO lines and compares the values by fixed rules. It reads with the AI service, Microsoft 365 Copilot, used with the buyer's own work sign-in (section 3.15), or without AI with the template parser (section 3.12). Copilot reads the text layer of a PDF only: a document without one (a scan) is not read, and its case goes to the buyer, listed as failed in the digest.
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

On a cart, every question of R06, R07 and R08 comes to the buyer instead ("cart: for the buyer"), and nothing is attached and no status is set. R04 works on a cart as on a confirmation: a small price difference is written on the PO line's price and on the article price (or prepared, while no route to Exact exists), before the order is placed, and the banner says so. For a supplier in `messages_off_suppliers` the questions come to the buyer instead ("messages off for this supplier").

**What the aid never does.**

- It never creates, releases or sends a PO, and never pays anything.
- It changes nothing but the PO line's price, delivery date and quantity, the article's purchase price, the attached confirmation and the confirmation status.
- It sends a supplier only the three standard questions, always with the buyer in copy, and the approver only the request to approve a PO again.
- It only reads the folder of the work mailbox it is connected to: it never moves, flags or deletes a message there. When it sends through that mailbox, each message is first a draft there, which then goes to Sent Items (section 3.14).
- It never says "no difference" when it could not compare a value.
- It never makes a change without a line in the write log that holds the old value, so that the change can be undone.
- In the Bachelor End Project it never writes to the live system: only to the copy of the test account, or as prepared changes.
- It never sends a company document, or anything taken from one, to an AI service outside the company's Microsoft tenant.

## 2. For the buyer

### 2.1 The daily digest

The digest is an e-mail to the buyer, from the aid's mailbox, or from the work mailbox when the aid sends through it (section 3.14). Its subject is "[Inkoopassistent] Dagoverzicht DD-MM-YYYY" (in English "[Inkoopassistent] Daily digest DD-MM-YYYY"). It comes once a day, at the first pass of the runner at or after `digest_time` (16:30 by default), or, when `digest` is `per_case`, after every pass that has a case or a skipped file to report. If the company allows a Power Automate flow, the same text also arrives in Teams (section 3.9).

The digest has up to five blocks. An empty block is left out.

| Block (Dutch) | Block (English) | What it lists |
|---|---|---|
| Behandelde zaken | Handled cases | Every case handled since the last digest, one line each: PO, supplier, status and the number of changes. Under it, a line per change: the field, the line or article, old value → new value. A change made in the system has a link "ongedaan maken" (undo). A prepared change says "(voorbereid)" and a failed one "(mislukt)": they changed nothing, so there is nothing to undo. |
| Wacht op u | Waiting for you | The cases in review, each with a link "beoordelen" (review) and a line per item with its reason, field and line. |
| Vraag gesteld aan de leverancier | Question sent to the supplier | The cases that wait for a supplier's answer, each with the PO, the supplier and the kind of each question (for example "aantal of eenheid"). |
| Mislukt | Failed | The cases the aid could not finish, with the first line of the error. Tell the researcher. |
| Overgeslagen | Skipped | Files in the inbox that opened no case, with the reason (for example "invoice", "duplicate", "more than one PO number"). |

A day without anything says "Er zijn geen zaken behandeld en geen bestanden overgeslagen." Under the title, before the blocks, a line says "Postvak niet verbonden" while the mailbox's sign-in has lapsed (section 5), and a line "Copilot werkt niet (<the error>): <n> zaken wachten erop" while Copilot fails, with the number of cases waiting for it (section 3.15). A pre-check case begins with the word "voorcontrole". Prices use the decimal comma in Dutch.

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

The aid asks only three standard questions: about a quantity or unit ("aantal of eenheid"), about a missing or extra line ("ontbrekende of extra regel"), and about missing information ("ontbrekende gegevens"). Each is an e-mail to the supplier, from the aid's mailbox or from the work mailbox (section 3.14), the buyer in copy, with the subject "Vraag over orderbevestiging PO <number>" and a table of the lines (line, item code, description, our value, your value). It asks for an answer by reply within the wait, three working days by default; you set it on the settings tab ("Wachttijd op een antwoord", section 2.6).

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
| message not sent | Bericht niet verzonden | Message not sent | The mail server did not take the message, or the Microsoft sign-in of the work mailbox has lapsed (section 3.14): send it by hand. |
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

### 2.5 The pre-check in the browser

**The extension.** A small extension for Edge or Chrome (`aid/extension`, loaded as its README describes) watches the pages of the webshops listed in its options. When a page holds a cart table (a table with an item column, a quantity column and a price column), it sends a copy of the page to the aid's capture server on the same computer, once per change of the cart: when the page opens, and again about a second after a quantity or a line changes. The server opens a cart case, checks it like any other case and answers at once. The case lands in the case folders like any other: on the review page when it waits for the buyer, and in the next digest under the pre-check ("voorcontrole"), which the runner makes when it runs on the same folder (section 3.13).

**The banner.** The answer appears in one banner at the top of the page. Its title is fixed, in Dutch with the English in brackets; the lines under it come from the aid in the language of the settings.

- **Green**, "Winkelwagen komt overeen met de inkooporder": the cart agrees with its PO, or the only differences were small price differences that the aid settled itself (R04). Those lines are listed under the title. While no route to Exact exists every change is prepared, and the line reads "regel 2 (KM-2102): prijs op de inkooporder: wijziging voorbereid van 4.10 naar 4.15, zie de zaak (prijs binnen de drempel)"; once changes are written to Exact it reads "regel 2 (KM-2102): prijs op de inkooporder bijgewerkt van 4.10 naar 4.15 (prijs binnen de drempel)". A case the buyer closed on the review page is green only when every difference is settled as it stands now: a line the buyer decided counts by the buyer's latest decision (taking the cart's value, with its change prepared or made and not undone, whoever made it; a change left to apply by hand does not count; keeping the PO's value leaves the difference), and a line the buyer did not decide by the change the aid's rules wrote or prepared; a line whose change was undone on the review page counts as a difference again ("wijziging ongedaan gemaakt").
- **Amber**, "Let op: de winkelwagen wijkt af van de inkooporder": one line per difference, in the order of the lines, for example "regel 2 (KM-2002): prijs 41.00 in de winkelwagen, 40.00 op de inkooporder (prijsverschil boven de drempel)", "regel 3 (KM-2003): kon niet in de winkelwagen worden geplaatst" or "regel 4 (KM-2004): extra regel, niet op de inkooporder". A reason about the whole case is a line of its own, for example "eerste zaken van deze leverancier gaan naar de inkoper". A small price difference that the aid wrote or prepared (R04) shows as such, with the text of the green banner, and is not counted as a difference: do not correct it again. A reason the aid has no wording for reads "zie de zaak voor de reden". When the buyer decided on a line on the review page, the line says so ("besluit van de inkoper: waarde van de inkooporder gehouden"). Correct the cart, or the PO, before ordering.
- **Grey**, "Geen inkooporder gevonden": the cart names no PO the aid knows, and its item codes match none.
- **Red**, "Voorcontrole mislukt" when the check failed (the case's run failed, or the server met an error, with "the pre-check failed; see capture.log"), "Inkoopassistent niet bereikbaar" when the capture server does not run, "Inkoopassistent weigert de pagina" with the server's reason when it refused the page (a wrong token, or "no cart table in ..." for a page the cart reader cannot read). When the extension was updated while the page was open, the title is "Voorcontrole mislukt" with the line "Herlaad de pagina (reload the page)" under it: no check ran and no case exists; reloading the page is all it needs.

Under the title a sentence sums the case up ("Inkooporder 250202: 1 regel komt overeen, 1 verschil en 1 regel niet in de winkelwagen"); the lines follow, and under them the link "Bekijk de zaak". The link selects the case on the review page only while the case waits for the buyer (in review) or holds a change made in Exact that can be undone; any other case, a green or a failed one, or an amber one the buyer already closed, is read in its case folder.

**A reload opens no new case.** The same page sent again, byte for byte, is answered from its case on any day; the same cart in a page whose invisible parts changed (a time stamp, an advertisement) is answered from the case it opened that day. No second case is opened. A changed cart (another quantity, a line more or less) is a new case. A case that failed is never reused.

**The fallback.** A cart page saved from the browser as a web page (HTML) can still go through the batch command with its PO number (section 6), as before the extension existed. The watched folder does not take cart pages. A cart saved as a screenshot (PNG) cannot be read: Copilot, the AI service, reads the text layer of a document only (section 3.15), so save the cart page as HTML. On a cart, every difference comes to the buyer; "Leverancier vragen" is not offered, because the buyer corrects the cart or the PO before ordering.

### 2.6 Your settings on the page

Your own rules are on the tab "Instellingen" (Settings) of the review page. You never need to open a settings file for them: everything else was set up at the installation. The tab shows what the settings file holds when the page loads and each time you select the tab, also a change someone made in the file itself; what you typed and did not save is then replaced by the file's values. The page is in Dutch, with the switch "Taal" for English. A number takes a decimal comma or a point, and a percentage or an amount at most two decimals: write a thousand as 1000, because "1.000" is refused with "hoogstens twee decimalen (schrijf duizend als 1000, zonder punt of komma)".

From top to bottom:

- **"Wat de assistent zelf mag afhandelen"** (What the aid may settle by itself).
  - "Prijsverschil: hoogstens dit percentage" and "Prijsverschil: hoogstens dit bedrag per regel (euro)" are the threshold for the price ("de drempel"). When a price difference is at most that percentage of the PO's price, and at most that amount on the line (the difference per piece times the quantity), the aid takes the supplier's price over on the PO line and changes the article's price too (a line without an item code has no article to change). A difference above the threshold comes to you.
  - "Leverdatum: dagen bovenop de levertijd" is the margin for the delivery date ("de marge"). A later delivery date is taken over up to and including the supplier's lead time plus these days, counted from the delivery date of the purchase order's line, not from the day the order was placed. An example: the line asks for delivery on 16 November, the supplier's lead time is 10 days and the margin is 7 days. A confirmed date up to and including 3 December (16 November plus 17 days) is taken over, and 4 December comes to you. A date given as a week counts as its Friday. Every later date of a supplier without a known lead time comes to you. An earlier delivery date is taken over, also without a known lead time.
  - "Eerste zaken van een leverancier": until this many cases of a supplier are closed, every case of that supplier comes to you, whatever it holds, with the reason "Nieuwe leverancier" on the review tab; 0 turns this off.
- **"Vragen aan leveranciers"** (Questions to suppliers). "Wachttijd op een antwoord (werkdagen)": the working days the aid waits for a supplier's answer before the case comes to you. "Uw naam onder de berichten": your name under "Inkoop" in the questions and in the request to the approver, or under "Purchasing" when the aid's language is English; empty leaves "Inkoop" or "Purchasing" alone.
- **"Dagoverzicht en taal"** (Digest and language). "Dagoverzicht": "Elke dag op een vaste tijd", with the time under "Tijd van het dagoverzicht (UU:MM)" (for example 16:30), or "Na elke zaak". "Uw e-mailadres": where the digest goes; you are also in copy of every question and of the request to the approver. "Taal van de assistent", "Nederlands" or "English": the language of the digest, of the messages to suppliers and to the approver, and of the banner in the webshop (the switch "Taal" at the top changes the page only).
- **"Fiattering"** (Approval). "E-mailadres van de fiatteur": the person who is asked to approve a PO again after the aid changed it above the authorization limit. The limit itself is set at the installation and is not shown.

Press "Opslaan" under these groups. The save writes only the fields you changed since the form was filled. A field you did not touch is never written, so a value that was changed in the file meanwhile stays as the file has it, and every other line of the file stays as it was. "Opgeslagen. De assistent gebruikt dit vanaf de volgende ronde, binnen een minuut." means it is saved, and the fields then show the values as the file holds them (a time typed as 9:05 shows as 09:05). With nothing changed the page says "Er is niets gewijzigd; er is niets opgeslagen.", leaves the file alone and shows the file's values as they are at that moment. When a value is not right, nothing is saved and the page names each field with what it takes, for example "Niet opgeslagen: Prijsverschil: hoogstens dit percentage: een getal van 0 tot 100." If the page was started again while your browser tab stayed open, the first save is not made: the page fills the form again with the settings as they are and asks you to enter your change once more.

**What a save changes, and what it does not.** The runner uses the new values from its next pass, the pre-check in the browser from its next check, and the review page itself at once. What was checked before a save keeps its outcome: a case that waits for you keeps its reason, and a cart that was checked before is answered from its case, as section 2.5 describes (the same page on any day, the same cart on the same day). So on the same day a cart is checked anew, with the new values, only when the cart itself changes (another quantity, a line more or less).

**"Leveranciers"** (Suppliers). The table lists every supplier you set something for: the e-mail address for the aid's questions, the lead time in days, "Altijd naar u" (every case of the supplier comes to you) and "Geen vragen uit zichzelf" (the aid sends this supplier no questions by itself). The questions the aid would have asked such a supplier come to you. A question you choose yourself on the review tab, "Leverancier vragen", is still sent to the supplier (section 2.2). Without an address the aid asks the supplier nothing either, and the case comes to you. To change a supplier, choose it under "Leverancier": the form below fills with its values and with the two ticks, "Elke zaak van deze leverancier komt naar u" and "De assistent stuurt deze leverancier uit zichzelf geen vragen". To add one, choose "Leverancier toevoegen" and type its name exactly as the purchase order gives it. Then press "Leverancier opslaan"; of a listed supplier only what you changed is written. "Leverancier verwijderen" takes the chosen supplier out of every list. A listed supplier left without an address, a lead time and both ticks is no longer listed. Two saves are refused. A name typed under "Leverancier toevoegen" that is listed already is not saved, so that a listed supplier never loses its values: "Niet opgeslagen: er staat al een leverancier met deze naam in de lijst. Kies die leverancier bij "Leverancier" om hem te wijzigen." A new supplier with a name and nothing else is not saved either: "Niet opgeslagen: een leverancier heeft een e-mailadres, een levertijd of een vinkje nodig." The note of a supplier save stays on the page until you choose another supplier yourself.

**When the settings file cannot be read.** If the file is missing, held by another program, damaged, or holds a value the aid refuses, one line at the top of the tab says so: "Het instellingenbestand kan niet worden gelezen. Hieronder staan de instellingen die de assistent nu gebruikt. Opslaan kan pas weer als het bestand is hersteld." When a value is refused, the line names its field, by its label on the tab, or by the setting's name for a setting the installation sets, never a value. The form then shows the settings the aid works with at that moment, and every save on the tab is refused with the same line until the file is put right. Tell the researcher.

**"Koppelingen: het Microsoft-postvak en de AI-dienst"**, folded away at the bottom of the tab, holds the connection of the mailbox (section 3.14) and of Copilot (section 3.15); a click on its heading opens it. It is set up at the installation. You need it when the digest says "Postvak niet verbonden" or "Copilot werkt niet": the buttons to sign in again are in this part (section 5).

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
    captures/            the cart pages the extension sent; nocart_<...> a page without a cart table
    prepared/            prepared_changes.csv, the changes to apply by hand
    outbox/              messages written as .eml files instead of sent
    digests/             <day>.html of each digest
    pricelists/<day>/    the write log of a price-list import (when --settings gives the folder)
    runner.log           the runner's errors
    capture.log          the capture server's errors and refused requests
    digest_state.json    what the next digest reports
    mailbox_state.json   the connection to the work mailbox, when one is made (section 3.14)
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
capture.json       a cart case of the capture server: the page's sha256, the digest of its cart, the shop's host, the saved page
```

### 3.2 The settings file

Copy `aid/settings.example.json` to `<root>/settings.json` and fill it in. A key the aid does not know stops it, so a typing error is never silently ignored. An error names the field, never its value. The settings file with the real values never goes into a repository. The Exact token and the mailbox password are not settings: they are the environment variables `AID_EXACT_TOKEN` and `AID_MAIL_PASSWORD`, and never go into any file. The tokens of the Microsoft sign-in are no settings either: they are kept in a protected store on the PC (section 3.14). The file holds company data (the authorization limit, the suppliers' addresses): keep `<root>` in a folder that only the runner's user can read. On Linux and macOS a save on the settings tab keeps the file's permissions; on Windows the saved file takes the access its folder gives, whatever access the file had before.

| Setting | Default | Meaning | Company-specific |
|---|---|---|---|
| `language` | `"nl"` | The language of the digest and the questions: `"nl"` or `"en"`. | |
| `price_pct` | `"2.0"` | R04: largest price difference the aid accepts, in percent of the PO price. | the company sets it |
| `price_abs` | `"25.00"` | R04: largest price difference the aid accepts on a line, in euro (difference per piece times quantity). | the company sets it |
| `lead_time_tolerance_days` | `7` | R02: calendar days a later date may lie beyond the supplier's lead time, both counted from the delivery date of the PO line. | the company sets it |
| `ask_wait_working_days` | `3` | Working days the aid waits for a supplier's answer. | the company sets it |
| `first_cases_review` | `5` | R11: the number of closed cases of a supplier before the aid acts on its own for that supplier. | the company sets it |
| `precheck_suppliers` | `[]` | Reserved; no part of the aid reads it yet. | |
| `review_suppliers` | `[]` | R11: suppliers whose every case goes to the buyer. | yes |
| `messages_off_suppliers` | `[]` | R06 to R08: suppliers the aid asks nothing by itself; the questions of these rules go to the buyer. A question the buyer chooses on the review page is still sent to the supplier. | yes |
| `digest` | `"daily"` | `"daily"` or `"per_case"`. | |
| `digest_time` | `"16:30"` | `"HH:MM"`, the host's local time from which the daily digest is made. | |
| `authorization_limit` | `null` | R10: the authorization limit; a PO total above it needs the approver again. `null` switches R10 off. | yes, never in a repository |
| `buyer_email` | (required) | The buyer's address: the digest, and the copy of every question. | yes |
| `buyer_name` | `""` | The name printed under the signature of the questions and of the re-approval request ("Inkoop" in Dutch, "Purchasing" in English); empty: the signature alone. | yes |
| `aid_email` | (required) | The aid's own mailbox: the sender of every message sent through SMTP or written as a file. A message sent through the work mailbox (section 3.14) comes from that mailbox instead. | yes |
| `approver_email` | (required) | The approver who gets the request to approve a PO again. | yes |
| `supplier_emails` | `{}` | Supplier (as the PO names it) → the address for the aid's questions. | yes |
| `po_number_pattern` | `(?i:PO\|Inkooporder\|Bestelling\|Bestellung\|Order)[\s:#-]*(\d{6,8})` | The regular expression that finds a PO number in an e-mail or on a document; its first group is the number. | adjust if the PO numbers differ |
| `cases_root` | (required) | The folder of the cases, for example `"cases"`. | |
| `po_store_root` | (required) | The folder of the PO versions, for example `"po_store"`. | |
| `watched_folder` | (required) | The inbox, for example `"inbox"`. | |
| `supplier_lead_times` | `{}` | Supplier → lead time in days, from the ERP. A supplier without one gets every later date as a review item. | yes |
| `reader` | `"llm"` | How the documents are read: `"llm"`, by Microsoft 365 Copilot with the work sign-in (section 3.15), which needs `ms_tenant_id` and `ms_client_id` (the runner refuses `"llm"` without them); or `"parser"`, by the template parser, without AI (section 3.12). The settings tab sets it ("Documenten lezen met"). | |
| `model` | the id of the development model | The model of the development AI client on the made-up set. The runner does not use it, as Copilot names no model; the freeze record shows it. | |
| `effort` | `"medium"` | `"low"`, `"medium"` or `"high"`, for the development AI client on the made-up set only. | |
| `exact_base_url` | `null` | The address of the Exact API (https). `null`: the aid only prepares its changes. | yes |
| `exact_endpoints` | `{}` | The path of each endpoint: `po_line`, `article`, `attachment`, `status` (section 3.10). | yes |
| `exact_fields` | `{}` | The Exact field name per kind of change, where it differs from the writer's default. | yes |
| `smtp_host` | `null` | The mail server of the aid's mailbox. `null`: messages are written as files. | yes |
| `smtp_port` | `587` | Its port (STARTTLS). | yes |
| `smtp_user` | `null` | The login of the aid's mailbox. | yes |
| `ms_tenant_id` | `null` | The Directory (tenant) ID of the app registration in Microsoft Entra ID (a GUID, or the tenant's domain name), for the Microsoft sign-in (section 3.14). An identifier, no secret. | yes |
| `ms_client_id` | `null` | The Application (client) ID of the app registration (a GUID). An identifier, no secret. | yes |
| `mailbox` | `null` | The address of the mailbox the aid reads and sends from. `null`: the signed-in account's own mailbox. Another mailbox needs the shared permissions and access in Exchange (section 3.14). | yes |
| `mail_folder` | `null` | The id of the folder of that mailbox that the runner reads at every pass, as Microsoft gives it; the settings tab fills it in. `null`: no mailbox is read, and the inbox is filled by a mailbox rule. | yes |
| `mail_folder_name` | `null` | The folder's display name, shown on the settings tab only. | yes |
| `send_via` | `"auto"` | How the runner's messages go out: `"microsoft"` (through the mailbox of the sign-in), `"smtp"` (through the SMTP settings above), `"file"` (written as files, sent by no one) or `"auto"`: the mailbox when `mail_folder` is set and an account is signed in at the time of the message, else SMTP when it is set up, else files. While the sign-in has lapsed, `"auto"` and `"microsoft"` send no question at all: it becomes "message not sent" on the review page, and the digest is written into `cases/outbox/` (section 3.14). | |
| `export_columns` | `{}` | For the pre-check: the aid's field name → the column title of the export of PO lines (section 6, "The export of PO lines"). It must name `po_number` and `item_code`, and may name `line_id`, `description`, `quantity`, `unit`, `unit_price`, `price_basis`, `discount_pct`, `net_price`, `delivery_date` and `supplier`. Map `supplier` only to a column that holds the supplier exactly as the supplier lists of these settings name it (the name on the purchase order), and otherwise leave it out: `review_suppliers`, `messages_off_suppliers`, `supplier_emails` and `supplier_lead_times` are keyed by that name. Empty: each column is named after its field. | yes |
| `export_decimal_comma` | `false` | How the export writes a number as text: `true` for 1.234,50 (decimal comma), `false` for 1,234.50 (decimal point). A number cell of an Excel file is read as the number it holds, whatever this says. | the company sets it |
| `export_fill_down` | `false` | `true` when the export prints the PO number once for each PO, on its first line or on a row of its own above its lines: a line without a PO number then belongs to the PO whose number stands nearest above it. With `false` such a line refuses the whole file. | the company sets it |
| `review_url` | `"http://localhost:7860"` | The address of the review page, used in the digest's links and in the banner's link. | |
| `capture_token` | `null` | The token the browser extension sends to the capture server (section 3.13); the extension's options hold the same value. Empty or `null`: the server does not start. | yes, never in a repository |

`buyer_email`, `aid_email`, `approver_email`, `cases_root`, `po_store_root` and `watched_folder` have no default and must be given. Numbers that are amounts are written as text: `"2.0"`, `"25.00"`. The six settings of the work mailbox are filled in and saved on the settings tab of the review page (section 3.14); with them empty or `null` the aid works as before.

**What the settings tab writes, and what only the installation sets.** The buyer changes these on the settings tab of the review page (section 2.6), never in the file: the eleven settings `price_pct`, `price_abs`, `lead_time_tolerance_days`, `first_cases_review`, `ask_wait_working_days`, `buyer_name`, `digest`, `digest_time`, `buyer_email`, `language` and `approver_email`, and the four supplier settings `supplier_emails`, `supplier_lead_times`, `review_suppliers` and `messages_off_suppliers`. The tab's connections also write the six settings of the work mailbox and `reader`. A save of the buyer's part writes only what the buyer changed: the fields whose text differs from the text the form was filled with, and of a supplier the values that were changed. In the file a save replaces the text of the values that changed, where they stand, and adds a key the file lacks at its end; every other key keeps its bytes, and so do the order of the keys, the indentation and the line ends. A save that changes nothing leaves the file and its modification time alone, so that "Opslaan" on an untouched form never changes the hash of the file, which the freeze record keeps (section 4.8). Each save is written through a temporary file with a name of its own and a rename, and one save is made at a time, each on the file as the one before left it, so that two saves pressed at the same moment both land. The buyer's part of the tab is filled from the file when the page loads and each time the tab is selected. When the file cannot be read (it is missing, held by another program, no JSON, or holds a value the aid refuses), the tab shows one line that says so, naming the fields (a field of the tab by its label, any other setting by its name) and never a value, shows the settings in use, and refuses every save until the file is put right. Only the installation sets the rest: `authorization_limit`, the folders (`cases_root`, `po_store_root`, `watched_folder`), the Exact route (`exact_base_url`, `exact_endpoints`, `exact_fields`), the SMTP server (`smtp_host`, `smtp_port`, `smtp_user`), `aid_email`, the token (`capture_token`), `po_number_pattern`, `export_columns`, `review_url`, `model`, `effort` and the reserved `precheck_suppliers`. Set these in the file before the buyer starts; the page never shows them. The runner reads the settings file again at the start of a pass when the file has changed; a file that is not valid then is logged in `runner.log` (stage `settings`, the fields only) and the settings in use are kept. The capture server does the same before each pre-check (its log is `capture.log`), and the review page when its review tab opens or shows a case and before a close or an undo, so that all three work with the same thresholds.

### 3.3 The mailbox rule

The mailbox rule is set up by Remy; its details follow his answer. It must:

- save the e-mails that carry Exact's PO PDFs, so that the aid knows every PO version;
- save the suppliers' e-mails with their attachments (confirmations, and any other document);
- save the replies to the aid's questions;
- save each as a whole e-mail, an `.eml` file, straight into `inbox/` (not into a subfolder).

Whole e-mails are best: the aid then reads the PO number from the subject and body too, and it can file a reply only by its headers. A `.pdf` or `.png` saved on its own is also handled; any other file is skipped as "unsupported file type". In the Bachelor End Project the documents of the test-account cases are to be re-sent to the researcher's company mailbox. The rule that files them into `inbox/` is not set up yet; it waits on Remy's answer.

Instead of this rule, the runner can read a folder of the work mailbox itself after a Microsoft sign-in (section 3.14); it saves the e-mails into `inbox/` in the same form.

### 3.4 Starting the runner and keeping it running

```bash
python3 -m aid.runner --settings <root>/settings.json [--poll-seconds N] [--max-passes N]
```

- `--settings` (required): the settings file. The STOP file is looked for next to it.
- `--poll-seconds`: seconds between passes, default 60.
- `--max-passes`: stop after this many passes; leave it out in production.

Each pass: handle the inbox, run every new case, end the waits that are over, and make the digest when it is due. The command runs until the STOP file exists, then prints the number of passes and ends with exit code 0. It ends at once with exit code 2 when the settings file cannot be read or is not valid, when `reader` is `"llm"` without `ms_tenant_id` and `ms_client_id`, or when the writer refuses the Exact settings (for example an address that is not https).

How it is kept running (a scheduled task at start-up, or a service) is the operator's choice on the host. Whatever starts it must:

- run it from the folder that holds `aid/`, with Python 3.13 and the packages installed;
- give the full path of the settings file;
- put `AID_EXACT_TOKEN` (when `exact_base_url` is set) and `AID_MAIL_PASSWORD` (when `smtp_host` and `smtp_user` are set) in the environment of the process, from the company's secret store, before the start;
- start it again if it ends with a code other than 0;
- with the work mailbox connected (section 3.14) or with `"reader": "llm"` (Copilot, section 3.15), run it as the same Windows user who signs in on the review page, not as a service account or SYSTEM: the sign-in's tokens are encrypted for that user, and the mailbox and Copilot use the same tokens;
- run one runner per `<root>`, never two.

The runner uses the host's clock, so `digest_time` is the host's local time.

#### 3.4.1 One launcher for the three parts

On the buyer's own PC, the runner, the review page (section 3.7) and the capture server (section 3.13) run side by side. One command starts the three and keeps them running:

```bash
python -m aid.start --settings <root>/settings.json [--testshop] [--poll-seconds N] [--background]
python -m aid.start --settings <root>/settings.json --status
python -m aid.start --settings <root>/settings.json --stop
python -m aid.start --settings <root>/settings.json --resume [--testshop] [--poll-seconds N] [--background]
python -m aid.start --settings <root>/settings.json [--testshop] [--poll-seconds N] --install-autostart
python -m aid.start --settings <root>/settings.json --remove-autostart
```

- **What it starts.** `python -m aid.runner` (with `--poll-seconds` when given), `python -m aid.review` and `python -m aid.capture_server` (with `--testshop` when given), each with the settings file by its full path, from the folder that holds `aid/`, and with the same Python that runs the launcher. On Windows the word `python` may be a placeholder that does nothing; then call the launcher with the full path of the Python (for example `C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe`), and the parts and the start at logon use that path too. The parts open no window. The settings file is read first: when it cannot be read, the launcher names the file and the fields at fault, never their values, starts nothing and ends with exit code 2.
- **In a window or in the background.** A launcher started with the first command lives in the window it was typed in, and its first line says so: closing that window stops the three parts. With `--background` the command first checks, in the window, whether the start is possible (the settings, `STOP`, a launcher that already runs, parts that already answer) and says why when it is not. Otherwise it starts the launcher apart from the window (on Windows with `pythonw.exe`, so without a window of its own), prints where the logs are and returns; the window can then be closed. `--resume --background` does the same after deleting `STOP`. The launcher that Windows starts at logon is such a launcher without a window.
- **The parts end with the launcher.** On Windows the launcher ties the three parts to itself with a job object of Windows: when the launcher ends in any way, also when its window is closed, when it is ended in Task Manager or at a logoff, Windows ends the parts with it. What a part starts itself, such as the browser window that the review page opens for the Microsoft sign-in, is not tied and stays open. If Windows refuses the tie, `start.log` says once "the parts could not be tied to the launcher" and the launcher goes on; only then can parts stay behind when the launcher is ended from outside. On Linux and macOS there is no such tie: the launcher stops its parts on Ctrl-C, on Ctrl-\, on SIGTERM and when its terminal is closed, but not when it is killed outright.
- **One launcher on a PC.** The review page and the capture server always take http://127.0.0.1:7860/ and http://127.0.0.1:7870/health, so only one launcher runs on a PC, whatever its settings folder. Before it starts anything, a launcher asks both addresses. When one of them answers, it starts nothing, ends with exit code 3 and writes "something already answers at <address>: only one launcher runs on a PC; a launcher on other settings is stopped with --stop on its own settings, and parts started by hand are closed". A launcher that runs on another settings folder (the try-out folder `live`, for example) is stopped with `--stop` on that folder's settings file, not by closing its parts, which it would start again; a review page or a capture server started by hand is closed in its own window. When the state file of the last launcher on these settings still names parts as running that were not tied to it (on Linux and macOS, or after a failed tie), a second line gives their names and process ids: close those by the process id (Task Manager, Details). `--stop` on settings where no launcher runs names each address that still answers in the same words, because `STOP` ends a runner only.
- **The logs.** Everything goes to the folder `logs/` next to the settings file: `runner.out`, `review.out` and `capture.out` hold what each part prints (appended, never emptied), and `start.log` holds the launcher's own lines with the time: each start, each end with its exit code, each start again, a part given up, the stop. A fault of the launcher itself is written to `start.log` with its traceback, because a launcher without a window has no other place to show it. The runner's own `cases/runner.log` and the capture server's `cases/capture.log` stay where section 3.6 says.
- **The restart rule.** The launcher looks at the parts every second. A part that ends is started again after 5 seconds, at most 5 times in any 10 minutes; after that it is given up and the other two keep running. A part that ends with exit code 2 had its settings refused (for example the capture server with an empty `capture_token`): `start.log` says so and names its `.out` file, which holds the reason. Such a part is started again as soon as the settings file changes, for example after "Opslaan" on the settings tab, and that start is not one of the five. A part that cannot be started at all is logged with the error itself. When all three have ended or been given up, the launcher ends with exit code 1.
- **The STOP file is respected.** While `STOP` exists next to the settings (section 3.5), the launcher starts nothing, writes "stopped by the STOP file: delete it, or use --resume" and ends with exit code 3; this holds at logon too. When `STOP` appears while it runs, it ends the review page and the capture server at once, lets the runner end by itself at its next pass (and ends it after its poll time plus 30 seconds if it has not), and ends with exit code 0. Ctrl-C in its window, SIGTERM and, on Linux and macOS, Ctrl-\ and a closed terminal stop it the same way, within a second: the launcher then creates `STOP` itself for the time of the stop, with the text "launcher" and the time, so that the runner ends by itself, and deletes the file again when the parts have ended. A `STOP` file that was already there is never changed or deleted by such a stop. A second Ctrl-C, a second or more after the first, ends the parts at once. If such a stop is cut off, for example because the PC is switched off, the launcher's own `STOP` file can stay behind; the next start sees by its text that no person made it, removes it and says so. `--stop` creates `STOP`, says that it waits, and waits up to 90 seconds for the three to end, then says whether they did. `--resume` deletes `STOP` and starts as above. When a launcher is still stopping, `--resume` first waits for it to end, at most 150 seconds, and only then deletes `STOP` and starts; when that launcher has not ended by then, it starts nothing and leaves `STOP` as it is, and when another launcher was started meanwhile (by a second `--resume`), it starts nothing either. A Ctrl-C while `--stop` waits ends that command and leaves `STOP` in place, so the launcher goes on stopping; a Ctrl-C during the watch of a start (below) or the wait of `--resume` ends that command, and nothing is started. Both say so in one line and end with exit code 130.
- **The state file, and a launcher that ended without a stop.** The launcher keeps `start_state.json` next to the settings: its start, a heartbeat written every 5 seconds, the runner's poll time, and per part its start, the number of starts, whether it was given up, whether it was tied to the launcher and, while it runs, its process id. A second launcher on the same settings is refused. A start that finds a heartbeat older than 20 seconds takes the file for the leftover of a launcher that ended without a stop (a crash, a shutdown, a closed window). A younger heartbeat is watched for 7 seconds, and the window says so first. When it moves, a launcher runs: the start says "already running" and ends with exit code 3. When it has not moved, the file is a leftover as well. So after a quick log off and on, when the heartbeat of the session before is still fresh, the launcher at logon starts 7 seconds later instead of not at all. When the leftover names a runner that was not tied to its launcher (Linux and macOS, or a failed tie on Windows), that runner may still be running, and only `STOP` ends it: the start makes the launcher's own `STOP` file, says that it gives that runner its poll time plus 5 seconds to end (65 seconds when the launcher was started without `--poll-seconds`), waits that long, removes the file and goes on. `--background` and `--install-autostart` make this watch and this wait in the window and remove the leftover there ("found the state file (start_state.json) of a launcher that no longer runs: it is removed"), so that the launcher they start does neither again; a launcher in a window or at logon replaces the file itself. A start after a leftover thus takes up to 7 seconds longer, and where the parts were not tied also the runner's poll time plus 5 seconds. `--status` prints one line for the launcher and one per part, and whether the review page (http://127.0.0.1:7860/) and the capture server (http://127.0.0.1:7870/health) answer; it ends with 0 while the launcher runs and with 1 otherwise. For up to 20 seconds after a launcher ended without a stop, it still shows that launcher as running. After that, on Windows, it shows "launcher: not running; it was ended from outside (last heartbeat ...), and its parts ended with it" and each part as "ended with the launcher": Windows ended them, and there is nothing to close. Where the parts were not tied, it shows "it ended without stopping its parts", the runner as "may still run (process N); it obeys the STOP file", and a review page or a capture server as "leftover, process N" while its address answers and as "ended" otherwise. The next start replaces the file. The status lines and the addresses assume the default ports: a review page that moved to another port (section 3.7) shows as not answering.
- **The start at logon on Windows.** `--install-autostart` puts a shortcut `Inkoopassistent.lnk` in the user's own Startup folder (normally `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`; a Start Menu that the organisation moved elsewhere is followed), which starts the launcher with `pythonw.exe` beside the Python in use, so that no window opens, with the same settings, `--testshop` and `--poll-seconds` as given. It needs no administrator rights. The shortcut is made through PowerShell and read back. When PowerShell cannot be used, or the saved shortcut does not hold the paths it was given (a path with a character that the shortcut cannot keep), a file `Inkoopassistent.cmd` in the same folder does the same, and the command says which of the two it made. When neither can be made, the command says that the start at logon could not be set, prints what the shortcut would run (so that one can be made by hand in the Startup folder), starts nothing and ends with exit code 1. Otherwise it starts the launcher in the background at once, after the same checks as any start, and prints where the logs are. Its last line asks you to log off and on once and to check with `--status` that the launcher started, after `--resume --background` when `STOP` kept the launcher from starting now: that is the only real test of the start at logon. From then on the three parts start by themselves each time this user logs on. The shortcut holds the full paths of the Python, of the folder that holds `aid/` and of the settings file. When the project folder or the settings folder is moved, or the Python is installed elsewhere, run `--install-autostart` again: otherwise nothing starts at logon and nothing is logged. To take it away, run `--remove-autostart`, which deletes the shortcut and the `.cmd` file; a launcher that is running goes on until `--stop`. On another system both options say that they are for Windows and end with exit code 2.
- **Environment variables.** The parts get the environment of the launcher. A variable set in one PowerShell window (`$env:AID_EXACT_TOKEN = "..."`) reaches a launcher started from that window, also with `--background` or `--install-autostart`, but not the launcher that Windows starts at the next logon. A variable the aid needs (`AID_EXACT_TOKEN`, `AID_MAIL_PASSWORD`, section 3.4) must therefore be a user variable of Windows ("Edit environment variables for your account" in the Start menu), so that the launcher at logon has it too. Without the token every write to Exact is refused and prepared instead (section 5, "A write was refused"), and without the mailbox password every message is written as a file, without a warning (section 3.11).

The exit codes of the launcher: 0 a clean stop or a done action, also `--install-autostart` once the start at logon is set, whatever became of the start now, and `--stop` whatever became of the stop; 1 every part ended, a fault of the launcher, a start at logon that could not be set, `--status` when no launcher runs, a start in the background that cannot be made, or `--remove-autostart` that cannot delete a file; 2 the settings or an option refused, also a settings file that does not exist; 3 not started (`STOP` exists, a launcher already runs, a part already answers, another launcher was started on the same settings in the same moment, or `--resume` found that the launcher it waited for has not ended or that another one was started meanwhile); 130 a Ctrl-C outside the supervision (the watch of a start, the waits of `--stop` and `--resume`).

### 3.5 The STOP file

Create an empty file `STOP` next to the settings file. The runner ends before its next pass; a pass that is running finishes first, and the runner may take up to the poll interval (60 seconds by default) to notice the file. While `STOP` exists, a new start makes no pass at all. Delete `STOP` before starting the runner again. Documents that arrive meanwhile wait in the inbox and are handled once the runner runs again. `STOP` does not stop a review page started on its own. The launcher of section 3.4.1 obeys `STOP` as well: it starts nothing while the file exists, and when the file appears it stops all three parts, the review page and the capture server included. A `STOP` file whose text begins with the word "launcher" is the launcher's own, made for the time of a stop that it started itself (Ctrl-C, for example), or by a start for the time it gives a runner left behind by an earlier launcher to end, and deleted afterwards; leave such a file alone.

### 3.6 The logs and the audit record

`cases/runner.log` has one line per problem: the time, the case id or the stage (`scan`, `scan <file>`, `escalation`, `digest`, `cases root`, `digest_state.json`, `mailbox`, `settings`), and the error. A case that keeps failing is logged once per run of the loop, and once more when it later succeeds. The pass goes on after an error.

`cases/capture.log` is the capture server's: one line per refused request (the status and the reason; a page without a cart table names its kept copy `captures/nocart_<...>`), per error outside a case's run (with its traceback; the banner shows "Voorcontrole mislukt" with "the pre-check failed; see capture.log") and per connection that ended badly (a client that stalled past the 60-second timeout, a request the server could not parse). A case whose run failed is not in it: its error is in the case folder, as for any failed case (section 5). The token is never in it. The server also prints one line per capture on its console: the time, the shop's host, the case id and the verdict.

`cases/digest_state.json` holds `last_sent` (the time of the last digest, or null), `reported` (the cases the next digest reports), `skipped` (the skipped files it lists), `mailbox_lapsed` while the Microsoft sign-in has lapsed (section 5, "Postvak niet verbonden"), `copilot` while Copilot fails as a whole (the error and the number of cases waiting for it), and `copilot_cases` while Copilot has failed a case (the cases, with their refusals and failures, in the order they are tried last; section 3.15). When it cannot be read, a fresh one is started and the log says so.

`cases/<case>/audit.json` is the audit record, for the researcher, IT and the thesis; the buyer never sees it. It holds the inputs by sha256, the model (none in parser mode), the settings with the authorization limit only as "set" or "unset", every step's output with its seconds, every rule decision with the settings it read, every write and message, the errors, and the timings. The review page adds a list `close`, the mailbox a list `answers`, and the escalation a list `escalations`.

### 3.7 The review page on the host

```bash
python3 -m aid.review --settings <root>/settings.json [--share]
```

It runs on its own, beside the runner, and serves the page on 127.0.0.1 of the host only: open it in a browser on that machine. The page answers its own host only: a request whose Host header is not 127.0.0.1 or localhost, alone or with the page's port, gets "Invalid host header" (status 400) and reaches nothing of the page, so that a web page whose domain name points at this computer cannot press the page's buttons. Open the page by one of those two names. It uses port 7860 when that port is free. If it is taken (for example by a review page that is still running), the page starts on the next free port, up to 7959, and prints its address. Stop the old page first, or set `review_url` to the printed address, so that the digest's links work. `--share` opens a public link and is for the made-up set only: with company settings (an Exact address, endpoints or fields, an authorization limit, an SMTP server or login, or an address outside example.com and example.org) it is refused with exit code 2. An unreadable settings file also gives exit code 2. The page has two tabs: "Beoordelen", the review of the cases, and "Instellingen", the buyer's settings and suppliers (section 2.6) and, folded away at its bottom under "Koppelingen: het Microsoft-postvak en de AI-dienst", the connection of the work mailbox (section 3.14) and of the AI service (section 3.15), which writes into the settings file the page was started with. With `--share` the host of the public link passes the check of the Host header as well. On a page opened with `--share` the settings tab says only "Niet beschikbaar op een openbare link" and offers nothing, and `--share` is refused altogether while the settings name a tenant (`ms_tenant_id`) or a folder of the work mailbox (`mail_folder`), since the cases may then come from company e-mails.

As the command builds the page today, its writer is the prepared writer and its mailer writes files: a close prepares its changes in `cases/prepared/prepared_changes.csv` and writes its questions as `.eml` files in `cases/outbox/`, even when the runner itself writes to Exact or sends by SMTP or through the work mailbox. Wiring the Exact writer and the SMTP mailer into the page is not built yet; until then the researcher sends those questions and applies those changes by hand.

### 3.8 Where the digest goes

The runner always writes the HTML version to `cases/digests/<YYYY-MM-DD>.html` (a second digest on the same day replaces it). The text goes to `buyer_email` the way `send_via` says (section 3.2): through the work mailbox while its sign-in works (section 3.14), through the aid's mailbox when SMTP is set up, and otherwise as an `.eml` file into `cases/outbox/`. A digest that the work mailbox cannot send because the sign-in has lapsed is written into `cases/outbox/` too, and counts as made.

### 3.9 Teams via Power Automate

A Power Automate flow outside the code can relay the digest to the buyer in Teams, so that no AI service is connected to Teams. The runner sets up no flow.

- Trigger: "When a new email arrives" (Office 365 Outlook connector) in the mailbox that receives the digest, with a subject that starts with `[Inkoopassistent]` and the sender the digest really has: `aid_email` when the aid sends through SMTP, the address of the work mailbox when it sends through that mailbox (section 3.14; with `mailbox` empty, the signed-in account's own address). The prefix is the same in both languages. A digest written into `cases/outbox/` is not sent, and reaches no flow.
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

### 3.12 Reading without AI: the parser

The runner reads with Microsoft 365 Copilot (`"reader": "llm"`, section 3.15) or with the template parser (`"reader": "parser"`). Company documents never go to another AI service. In parser mode:

- a document without a text layer (a scan, a photo) cannot be classified: it is skipped as "no text layer; class unknown" and listed in the digest;
- a document with text but no known keyword is skipped as "other";
- a confirmation is read only if a parser template fits its layout (section 4.1); otherwise the case fails.

Not built yet: the template of Exact's real PO PDF and the templates of the real suppliers' layouts (Task 5b). Until then the PO reader knows only the made-up layout, so a real PO PDF stays in the inbox as an error.

### 3.13 The capture server

The capture server answers the browser extension (section 2.5). It runs beside the runner and the review page, on the computer where the buyer's browser runs. It reads every cart with the cart reader and asks no model, also with `"reader": "llm"`: the pre-check stays fast, needs no Copilot sign-in, and what the rules cannot settle goes to the buyer.

```bash
python3 -m aid.capture_server --settings <root>/settings.json [--host 127.0.0.1] [--port 7870] [--testshop]
```

- `--settings` (required): the settings file, the same one the runner uses, so that the cases land under the same `cases_root`.
- `--host`: the address it listens on, default 127.0.0.1 (this computer only). Leave it so.
- `--port`: default 7870.
- `--testshop`: also serve the made-up cart pages of `aid/madeup/out` under `/testshop/` (for example `http://127.0.0.1:7870/testshop/CART02.html`), for trying it out with the demo's settings. Leave it out on the company side.

It prints the address it listens on and runs until Ctrl-C. How it is kept running is the operator's choice, as for the runner (section 3.4).

**The PO side.** The server looks for a cart's PO only in the PO store, and it files nothing itself. For a cart the PO side is the ERP export, because no PO PDF exists before ordering: file the export first with the batch, its `--po-dir` holding the `*_export.csv` (a cases file with only its header row is enough; section 6). Until the cart's PO is in the store, the banner is grey ("Geen inkooporder gevonden") and the case opens as `unknown_<...>`.

**What the export may look like.** An overview of the order lines saved from the ERP as an Excel workbook (.xlsx) or as CSV, with one row per order line. Its columns need not have the aid's names or order: `export_columns` says which column title holds the PO number, the item code and the other values, `export_decimal_comma` how the file writes its numbers, and `export_fill_down` whether the PO number stands only once for each PO (section 3.2). A CSV file may be cut at semicolons, commas or tabs: the aid takes the mark under which it finds the row of column titles. Rows that are no order line are passed over: rows above the column titles, blank rows, the column titles repeated lower down, rows without an item code (totals, group rows, text lines) and, when the map names the quantity column, rows with an item code and no quantity, such as a total that prints its word in the item code column. With `export_fill_down` a line without a PO number belongs to the PO whose number stands nearest above it, also when that number stands on a group row or a text line. Section 6, "The export of PO lines", gives the rules and what makes the aid refuse a file. The batch picks up only files named `*_export.csv`, so an overview saved as a workbook is saved as CSV for the batch.

**The digest.** The server runs its cases itself and never writes the runner's digest state. The runner, at each pass on the same `cases_root`, reports every case of the capture server that has its result once, so that it appears in the next digest under "voorcontrole". Without a runner on that folder, capture cases are in no digest.

**A case in progress.** While the server runs a case, a new one or one it resumes because an earlier run of it was cut off, the case folder holds a file `.running` (the process id and the start). The runner leaves such a case alone, checking with the time of the check itself, and a start up to ten minutes ahead of its own clock counts as running too (the two processes' clocks may differ); a `.running` file older than ten minutes is a run that crashed, and the runner resumes the case as it resumes any case without a result.

**The token.** Set `capture_token` in `settings.json` to a long random value and put the same value in the extension's options. The extension sends it with every page; a request without it, or with another value, is refused. The value is company-specific and never goes into a repository. The extension keeps it in the browser profile's local extension storage on that computer.

**The extension's options.** The address of the server (default `http://127.0.0.1:7870`; change it only with `--host` or `--port`), the token, and the webshops, one address per line. Saving asks the browser for access to those shops; a shop taken off the list loses that access. An address on `http://127.0.0.1` or `http://localhost` needs no entry: the test shop is always on, and the options page says so. Open tabs of a shop just added need a reload.

**What the process needs.** The server builds its writer and mailer as the runner does: when the settings set the Exact writer (`exact_base_url`), `AID_EXACT_TOKEN` must be in its environment, and when they set SMTP, `AID_MAIL_PASSWORD`, exactly as for the runner. The banner's link is `review_url` with the case, and the review page listens on 127.0.0.1 of its own computer only: start the review page (section 3.7) on the computer where the browser and the capture server run and, when it printed another port, set `review_url` to that address.

**What it logs and keeps.** Each page sent is saved under `cases/captures/` (a page answered by an earlier case is not kept twice, and a page whose pre-check failed before a case was opened is removed). The extension empties in the copy every password field and every field whose name, id or autocomplete holds, in any case and also with its punctuation removed, one of: pass, wachtwoord, card, kaart, cc-, ccnum, cardnumber, creditcard, cvc, cvv, csc, securitycode, iban, rekening. A select so matched has no option chosen in the copy. A field named otherwise keeps its typed value in the copy. Errors go to `cases/capture.log`, one line per capture to the console (section 3.6). A page larger than 5 MB is refused.

**Exit code 2.** The server does not start, and ends with exit code 2 and a line "aid.capture_server: ...", when the settings file cannot be read or is not valid, when `capture_token` is empty, when the writer refuses the Exact settings, when the port is outside 0 to 65535, or when the address cannot be used (the port is taken by another program, for example a capture server that still runs).

### 3.14 Connecting the work mailbox

Instead of a mailbox rule (section 3.3), the aid can read the work mailbox itself. After a sign-in with the buyer's own Microsoft work account on the settings tab of the review page, the runner reads one folder of that mailbox at every pass, through Microsoft Graph, and saves each new e-mail as an `.eml` file into `inbox/`, before it handles the inbox: a confirmation that arrives opens its case in the same pass. The messages that are in the folder when it is connected are left alone; every message that comes into the folder after that is read, also one that an Outlook rule or a drag moves there, whenever it was received. One limit: when the reading was broken for a while (the runner stopped for days, so that Microsoft no longer knows where the aid left off), the aid reads again what was received since its last read, and an older message moved into the folder while the reading was broken is not read; move it out of the folder and in again. The runner only reads the folder: it never moves, flags or deletes a message. The aid's own e-mails that come back into the folder (the digest, the buyer's copy of a question) are passed over. Without a sign-in everything works as before.

**What must reach the folder.** The folder takes the place of the rule of section 3.3, so it must receive the same mail: the e-mails with Exact's PO PDFs, the suppliers' e-mails with their confirmations, and the replies to the aid's questions. A reply arrives in the inbox of the mailbox the question was sent from, so give that mailbox an Outlook rule that moves it into the folder, for example on the subject words "Vraag over orderbevestiging PO" (in English "Question about order confirmation PO"), which a reply keeps after its "RE:". With "Verzenden via" on the SMTP server, the questions go out from the aid's own mailbox (`aid_email`) and the replies arrive there; an Outlook rule cannot move mail into another mailbox, so let that mailbox forward the replies to the work mailbox, or keep the mailbox rule of section 3.3 for it. For the rest, use rules on the sender of Exact's PO e-mails and on the suppliers' sender addresses or domains, or choose "Postvak IN" itself as the folder. Subject words such as "orderbevestiging" or "order confirmation" are examples only: suppliers also write "Opdrachtbevestiging", "Auftragsbestätigung" or "Order acknowledgement", or answer the PO e-mail with the confirmation attached. An e-mail that never reaches the folder is never checked, and nothing reports it; a reply that never reaches it is not filed, and its case waits until its wait ends.

**The app registration.** The sign-in needs an app registration in Microsoft Entra ID. A user can make one in a few minutes when the company allows users to register applications:

1. Open entra.microsoft.com and sign in with the work account.
2. Open App registrations and choose New registration.
3. Name it "Inkoopassistent", choose "Accounts in this organizational directory only" and register it.
4. Under Authentication, add the platform "Mobile and desktop applications" with the redirect URI `http://localhost`, and set "Allow public client flows" to Yes. Save.
5. Under API permissions, add Microsoft Graph, Delegated permissions: `Mail.Read`, `Mail.ReadWrite`, `Mail.Send` and `User.Read`, and, to read and send from another mailbox than your own (below), also `Mail.Read.Shared`, `Mail.ReadWrite.Shared` and `Mail.Send.Shared`. `Mail.ReadWrite` lets the aid create the draft that each of its messages is sent from (Microsoft does not say that a message sent at once keeps the aid's Message-ID, by which a reply is filed); it changes no other message.
6. On the Overview, copy the Directory (tenant) ID and the Application (client) ID into the settings tab. A portal in Dutch calls them "Map-id (tenant)" and "Toepassings-id (client)", as the tab does.

No client secret and no certificate is made: the app is a public client, and the person signs in with their own account. The two IDs are identifiers, not secrets.

**When the portal refuses.** If New registration is greyed out or refused, the account may not register applications: the company decides that, with a setting of Entra ID. Ask IT to make the registration with the settings above, or keep to the mailbox rule of section 3.3. If the sign-in asks for an administrator's approval of the permissions, the company does not let users approve apps themselves: an administrator approves them once.

**Connecting.** Start the review page on the PC where the runner runs (section 3.7), as the same Windows user as the runner (below), and open the tab "Instellingen". The fields and buttons of the connection are at the bottom of that tab, folded away under "Koppelingen: het Microsoft-postvak en de AI-dienst": a click on that heading opens the part. Everything this section names on the tab is inside it.

1. Fill in the Map-id (tenant) and the Toepassings-id (client).
2. Press "Aanmelden bij Microsoft". A browser window with Microsoft's sign-in page opens on the PC where the page runs: sign in with the work account and accept the permissions. The tab waits for it for at most five minutes. If no window opens, press "Aanmelden met een code": the tab shows an address and a code; open the address on any device, enter the code and sign in, and the tab waits until that is done.
3. The tab shows "Aangemeld als" with the account, and the folders of the mailbox. Only the folders at the top of the mailbox are listed, beside "Postvak IN", not the folders inside them.
4. Leave "Postvak" empty for your own mailbox.
5. Choose the folder and press "Verbinding testen": the tab shows how many messages the folder holds and when the newest arrived, never a subject or a sender.
6. Leave "Verzenden via" on "Automatisch", which sends each message through the mailbox while you are signed in and a folder is chosen, and through the SMTP server or as files when no folder is chosen or no one is signed in (after "Afmelden"); or choose another way. While the sign-in has lapsed, nothing is sent (see "Sending").
7. Press "Opslaan". The six settings are written into the settings file, every other setting kept as it was, and the runner takes them over at its next pass: "De controle neemt dit over bij de volgende ronde". Under "Stand van de controle" the tab shows "Nog niet verbonden" until the runner's next pass has connected the folder, then since when the folder is connected, when it was last read and how many messages were saved, or "nog niet gelezen" when that pass could not read the folder (a sign-in to repeat, or a folder the account may not read yet). The line under the sign-in buttons shows who is signed in, read from the PC without asking Microsoft; whether the sign-in still works shows when you press "Verbinding testen" or "Mappen ophalen": "Aanmelding van ... verlopen" if it has lapsed.

**Sending.** With "Automatisch" (once signed in with a folder chosen) or "Het postvak van de aanmelding", the runner's supplier questions, re-approval requests and digest go out from that mailbox, From its address. Each message is first made as a draft in the mailbox and then sent, so that it ends in Sent Items; a draft whose sending failed may stay in the Drafts folder. Who is signed in is looked at for each message, so a sign-in on the tab counts from the next message on, without a restart. A question asked on the review page itself is still written as a file into `cases/outbox/` (section 3.7). While the sign-in has lapsed (the account is still on the PC, but Microsoft no longer accepts it), the supplier questions and re-approval requests are not sent: each case comes to you on the review page with "Bericht niet verzonden", for you to send the message by hand, and no other way is tried, since a reply to another sender would never reach the folder and so never its case. The digest is then written into `cases/outbox/` (section 3.8). Only after "Afmelden" do the messages under "Automatisch" go through SMTP or as files.

**A test with your own mailbox.** Make a folder for the test at the top of your own mailbox (beside "Postvak IN", not inside it), choose it on the tab and save. After the next pass, drag a made-up confirmation e-mail into the folder, or let an Outlook rule move it there. At the next pass (within a minute) it is saved into `inbox/` and its case opens. Use another made-up confirmation for each test: a document the aid has handled once is skipped as "duplicate". To test a reply, answer a question the aid sent to your own address and let the reply reach the folder (see "What must reach the folder").

**Another mailbox.** To read another mailbox, such as the purchasing department's, fill in its address under "Postvak", press "Mappen ophalen", choose its folder and save; the tab refuses a folder listed for another mailbox than the one in the field. Another mailbox or folder starts a new connection from that moment, and the aid sends from that mailbox too. This needs two things: the app registration's shared permissions of step 5, which the aid asks for when a mailbox is filled in (sign in again after filling it in), and the signed-in account's access in Exchange to read that mailbox and to send from it, which IT grants.

**Where the tokens are kept.** The sign-in's tokens are kept on the PC only: on Windows in `%LOCALAPPDATA%\Inkoopassistent`, encrypted with Windows' data protection (DPAPI), so that only this Windows user on this PC can read them; elsewhere in `~/.config/inkoopassistent`, encrypted where the system offers a keychain, else in files that only their owner can read. The review page and the runner share them, so both must run as the same Windows user: a runner started as a service account or as SYSTEM looks in another folder, cannot read the page's tokens, and keeps reporting "Postvak niet verbonden" however often the buyer signs in. Copilot (section 3.15) uses the same tokens, one store per Windows user: such a runner cannot ask Copilot either, every case waits with `SignInNeeded`, and the digest says "Copilot werkt niet". The folder remembers the kind of store it was made with, and a process that cannot use that kind is refused: the runner then logs `mailbox: StoreRefused: ...` once in a run and goes on with `inbox/` (section 5). The tokens are never written to the settings file, the repository, a case folder, the audit or a log, and the aid refuses to keep them in a folder inside a git repository.

**Signing out.** "Afmelden" removes the account and its tokens from the PC, and empties the folder list on the tab. Pressed alone, it leaves the folder in the settings file: the runner keeps trying to read the mailbox, handles `inbox/` as before, logs "mailbox: not connected" at most once an hour, and every digest says "Postvak niet verbonden" (section 5); with "Automatisch" its messages go through SMTP or as files. Press "Opslaan" after "Afmelden" to disconnect the mailbox completely: the folder is saved empty, the runner stops reading the mailbox and forgets the connection, and the line goes from the digest. To connect again later, sign in, choose the folder again and save: the connection starts from that moment, as a new one does, and the mail that came into the folder meanwhile is left alone. Saving another folder forgets the connection of the one before in the same way. "Afmelden" ends the sign-in for Copilot too (section 3.15): with "Documenten lezen met" on "Copilot", no case is read and the digest says "Copilot werkt niet" until "Aanmelden voor Copilot" is pressed again or the reader is set to "zonder AI (parser)".

### 3.15 The AI service: Microsoft 365 Copilot

The AI service of the aid is Microsoft 365 Copilot, used with the buyer's own work sign-in of section 3.14 through the Copilot Chat API of Microsoft Graph. With `"reader": "llm"` Copilot reads the confirmations, and it answers the questions the rules leave open: which PO version a document answers, which PO line a line answers, and what kind of document an attachment without a keyword is. The licence covers its use, so the audit record shows no tokens and no cost; it names the conversation of each call by its id. The pre-check in the browser (section 3.13) never asks Copilot: the capture server reads the cart page with the cart reader, whatever `reader` says.

**What Copilot needs.**

- A Microsoft 365 Copilot licence on the account that signs in.
- Seven delegated permissions of Microsoft Graph on the app registration of section 3.14, together: `Sites.Read.All`, `Mail.Read`, `People.Read.All`, `OnlineMeetingTranscript.Read.All`, `Chat.Read`, `ChannelMessage.Read.All` and `ExternalItem.Read.All`. The Chat API asks for all seven, even though the aid sends it only the document's text.
- An administrator's consent for four of them: `People.Read.All`, `OnlineMeetingTranscript.Read.All`, `ChannelMessage.Read.All` and `ExternalItem.Read.All`.

**The app registration.** On entra.microsoft.com, open the app registration of section 3.14:

1. Under API permissions, choose Add a permission, Microsoft Graph, Delegated permissions, and add the seven permissions above (`Mail.Read` is there already).
2. Press "Grant admin consent for" the organisation. Only an administrator can press it: ask IT to press it once, for the four permissions that need it. Without that consent the sign-in for Copilot, or Copilot itself, refuses.

**Connecting.** On the tab "Instellingen" of the review page, open "Koppelingen: het Microsoft-postvak en de AI-dienst" at the bottom of the tab with a click on its heading. There, under "De AI-dienst: Microsoft 365 Copilot", with the "Map-id (tenant)" and the "Toepassings-id (client)" filled in:

1. Press "Aanmelden voor Copilot". A browser window with Microsoft's sign-in page opens on the PC where the page runs and asks for the permissions of the mailbox and of Copilot together, and for the shared permissions as well when "Postvak" holds another mailbox (section 3.14). Sign in with the work account. The tab says that it waits, for at most five minutes.
2. Press "Copilot testen". The aid sends Copilot one message, "Answer with the word OK only.", and the tab shows "Copilot antwoordt: OK" (the first 20 characters of the answer), or the error, for example "Copilot refused: 403 Forbidden" (section 5).
3. Under "Documenten lezen met", choose "Copilot". The tab writes `"reader": "llm"` into the settings file at once, and the runner takes it over at its next pass. The tab always lists Copilot, but refuses it until the two IDs are saved ("Opslaan"), because the runner refuses `"llm"` without them: it then says so and keeps the reader that is in the file.

**What Copilot is sent.** For a confirmation with a text layer, the aid sends the text of each page, every line starting with its page and line number ("p1 r12: ..."), the instructions of the reading prompt and the schema of the answer. The file itself is not sent and is not put in OneDrive. Copilot reads the text layer only: a scan or a screenshot cannot be read, and its case fails at reading with "Copilot reads the text layer only: this document has no text layer" and is listed under "Mislukt" in the digest, for the buyer to check by hand. A PDF with a scanned page (an image without text) among pages with text is not read in part either: its case fails with "Copilot reads the text layer only: page <n> has no text layer", so that the lines of that page never come out as missing lines. A blank page, with neither text nor an image, is left out. A cart page saved as HTML is read by the cart reader, which needs no AI.

**Grounding and the check.** Web grounding is switched off for every message, so Copilot does not search the web. Enterprise grounding cannot be switched off: Copilot may draw on the user's Microsoft 365 data as its semantic index allows, the mail, chats, meetings, sites and files the account can reach in the organisation. The aid therefore checks every value against the text of the page: a value whose text does not stand on the line it cites (or on the line just before or after it) is marked uncertain and comes to the buyer (R09), so a value Copilot takes from elsewhere is never used as certain.

**A preview of Microsoft.** The Chat API is a preview, in Graph's beta endpoint, which Microsoft does not support for use in production applications. It may change or stop working without notice; then switch back to the parser as below. Microsoft states that using the Chat API is agreeing to its Microsoft 365 Copilot APIs Terms of Use (preview): pressing "Copilot testen" or running the runner with `"reader": "llm"` accepts them, so the company should agree to them first.

**When Copilot fails.** A refusal of Copilot, an outage, a network failure while the sign-in's token is renewed (`Copilot could not be reached: ConnectionError`), or a sign-in that has lapsed, while a case runs, is no failure of the case: the case stays without a result, `runner.log` names the error once under the case id (for example `CopilotError: Copilot refused: 503 no code`, never a token), and a later pass runs it again, after the cases Copilot has not failed, so that a case that keeps failing never holds back the others. A request that Copilot answers with 429, 503 or 504 is sent once more after the wait it asks for, at most 30 seconds.

- **Copilot fails as a whole**: the sign-in has lapsed, the token cannot be had, or the conversation cannot be created, always; or the chat fails while no other case was read since the case's first failure. The other cases of that pass are not sent to Copilot and wait for the next pass too, so that a Copilot that is down or asks the aid to wait is not asked once per case. The digest says "Copilot werkt niet (<the error>): <n> zaken wachten erop" until a pass reads a case through Copilot again. Failures in this state never finish a case.
- **Copilot fails one document**: a conversation that Copilot ends itself ("Copilot ended the conversation: disengagedForRai", its own refusal of that content), a 400 or 413 of the chat that carries the document, or any other failure of that chat after Copilot read another case. The pass goes on with the other cases, and the digest names these documents in a line of their own: "Copilot kon 1 document niet lezen; nieuwe poging bij de volgende ronde". After three refusals of the document, or three failures of its chat in all once Copilot has read another case after the first one, the case is finished as failed with "Copilot could not read this document: <the error>" and listed under "Mislukt", for the buyer to check that document by hand.

An unreadable document (no text layer) is no failure of Copilot: its case is listed as failed, as above.

**Back to the parser.** Choose "zonder AI (parser)" under "Documenten lezen met" (in the same part "Koppelingen" of the settings tab), or set `"reader": "parser"` in the settings file. The runner takes it over at its next pass and reads with the template parser again (section 3.12).

## 4. Maintaining it

### 4.1 Adding a supplier

1. On the settings tab of the review page, under "Leveranciers", choose "Leverancier toevoegen", type the supplier's name as the PO prints it, its address for the questions and its lead time in days, and press "Leverancier opslaan" (section 2.6). This writes `supplier_emails` and `supplier_lead_times`; editing those two in `settings.json` is the other way. A supplier that is listed already is not added a second time: choose it under "Leverancier" and change its values there. A new supplier needs an address, a lead time or a tick besides its name.
2. If the supplier's confirmations have a layout no template reads, add a parser template.

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

Change them on the settings tab of the review page and press "Opslaan" (section 2.6); editing `settings.json` is the other way, and the tab shows such a change the next time it is selected. The runner reads the file again at the start of its next pass when the file has changed (a file that is not valid is logged in `runner.log` and the settings in use are kept); the capture server reads it before its next pre-check and the review page before its next close or undo, so neither needs a restart. A case that was handled before the change keeps its outcome, and on the same day a cart that was checked before is checked anew only when the cart itself changes (section 2.6). The thresholds are `price_pct`, `price_abs`, `lead_time_tolerance_days`, `ask_wait_working_days` and `first_cases_review`; the supplier lists are `review_suppliers` and `messages_off_suppliers`. Each case's audit record keeps the settings it ran with. During the evaluation the settings must not change (section 4.8).

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

### 4.9 Known limitations of the pre-check in the browser

- **A cart changed after a small price difference repeats the change.** When the aid accepted a small price difference on a cart (R04) and the buyer then changes the cart, the new case makes the same write, or prepares the same change, again. The PO the aid knows in the Bachelor End Project is an ERP export, which does not learn the aid's own writes; reading the PO through the Exact API would.
- **Closed cart cases count for the first cases of a supplier.** A cart case that closes counts toward `first_cases_review` of its supplier, like a closed confirmation, so a few pre-checks can end the first-cases review of that supplier.
- **The capture server and the runner share the cases.** Both may use the same `cases_root` on one computer. The runner never runs a case again that has a result, and it leaves a case alone while the server's `.running` file in it is younger than ten minutes. A run of the server that takes longer than that (a slow Exact route, for example) can therefore be run a second time by the runner, and a case whose run crashed waits up to ten minutes before the runner finishes it.

## 5. Troubleshooting

**A case failed.** It is in the digest under "Mislukt" with the first line of its error, and not on the review page. Look in the case folder: `steps/08_result.json` (status `failed`, field `error`, which names the change it failed at, if any), `audit.json` (`errors`, with the step and, for an unexpected error, the traceback), and the last step file in `steps/`, which shows how far it got. A confirmation whose layout no template fits fails at reading. Fix the cause, then use the batch to try the document again (section 4.4).

**A document was not recognised.** See the "Overgeslagen" block of the digest and `cases/review_items.jsonl`. The class comes from keywords on the first page: "orderbevestiging", "order confirmation", "auftragsbestätigung" and similar make it a confirmation; a title line beginning with "Inkooporder", "Purchase order" or "Bestellung" a PO; "pakbon", "factuur", "offerte" and similar the other classes. Without a keyword the document is "other"; without a text layer it is "no text layer; class unknown" (with `"reader": "llm"` Copilot is asked to class those from the file name and the e-mail's text). The PO number comes from `po_number_pattern` in the e-mail and on the first page. Two different numbers: "more than one PO number", skipped and listed. None: the aid tries to find the PO by its item codes, and else opens the case without a PO, which goes to review as "no version selected". If the company's PO numbers do not fit the pattern (6 to 8 digits after a keyword), change the pattern. A file that raised an error stays in the inbox, is tried again at every pass and is logged once in `runner.log` as `scan <file>`.

**A write was refused.** The Exact writer raised a WriterError (no token, a status that is not 2xx, a value that reads back different, no connection). After two attempts the change is prepared instead: it is in `prepared_changes.csv`, the write-log entry has the `error`, and the case is in review with "prepared change: apply by hand", while every later change of that case is "not applied: an earlier write failed". Check the token, the route and the endpoints, then apply the prepared change by hand and let the buyer close the case.

**A question was not sent.** The case is in review with "no address for the supplier" (add its address on the settings tab under "Leveranciers", section 2.6, or in `supplier_emails`) or "message not sent" (the mail server refused it, or, through the work mailbox, the Microsoft sign-in has lapsed: sign in again on the tab "Instellingen", in the part "Koppelingen: het Microsoft-postvak en de AI-dienst" at its bottom, and send the message by hand; see the case's `audit.json`, `messages`). If questions appear as files in `cases/outbox/` instead of being sent, SMTP is not set, or `AID_MAIL_PASSWORD` was missing when the runner started. Questions asked from the review page are always written to `cases/outbox/` today (section 3.7).

**A reply was not filed.** A reply is filed only when its `In-Reply-To` or `References` header names the Message-ID of a question the aid sent (recorded in the case's `messages/sent.jsonl`). A reply written as a new e-mail, or a forward that drops these headers, is not filed: it is handled like any other e-mail. The mailbox rule must save the reply as a whole `.eml` file. A reply to a re-approval request is never filed to a case.

**The page shows no cases.** The page lists only cases in review and cases with a change made in the system that is not undone (whatever their status). Check that the page was started with the same settings file as the runner, press "Lijst vernieuwen", and look at the digest: cases that wait for a supplier, closed cases and failed cases are not listed unless they hold such a change.

**The digest did not come.** The daily digest is made at the first pass at or after `digest_time` (host time), once a day. If `last_sent` in `cases/digest_state.json` is today, it was made already. Look for `cases/digests/<day>.html` and, without SMTP, an `.eml` in `cases/outbox/`. A failed digest is logged in `runner.log` as `digest` and made again at the next pass. In `per_case` mode a pass without cases or skipped files makes no digest. The runner must be running at that time.

**"Postvak niet verbonden" in the digest.** The runner could not read the work mailbox because the Microsoft sign-in has lapsed or the account was signed out; `runner.log` says `mailbox: not connected` at most once an hour. The digest's line ends "onderaan bij Koppelingen": the buttons are at the bottom of the tab "Instellingen", in the part "Koppelingen: het Microsoft-postvak en de AI-dienst", which opens with a click on its heading. That part still names the account, and says "Aanmelding van ... verlopen" when you press "Verbinding testen" or "Mappen ophalen". Meanwhile the runner handles `inbox/` as usual, a digest it cannot send through the mailbox is written into `cases/outbox/`, and a supplier question or re-approval request is not sent: its case comes to the review page with "Bericht niet verzonden", for you to send by hand (no other way is tried, section 3.14). Open the tab "Instellingen" of the review page on the PC where the runner runs, as the runner's Windows user, open "Koppelingen" at its bottom and press "Aanmelden bij Microsoft" (or "Aanmelden met een code"). At its next pass the runner reads the mailbox again from where it stopped and logs `mailbox: signed in again`, its next messages go through the mailbox again, and the line is gone from the next digest. If the line stays however often you sign in, the runner runs as another Windows user than the page (section 3.14). After "Afmelden" without "Opslaan" the line stays on purpose (section 3.14). Other problems of the mailbox are in `runner.log` under `mailbox`, once in a run of the runner and again after the folder or the mailbox changes: `mail folder not found` (the folder was deleted: choose a folder on the tab and save), `HTTP 429` or `HTTP 503` (Microsoft asked the aid to wait: the next pass asks again), `HTTP 403` (the account has no access to that mailbox in Exchange or, for another mailbox than its own, the app registration lacks the shared permissions or the sign-in was made before the mailbox was filled in: section 3.14), `unexpected link host` (the state file named a link outside Microsoft Graph: it is dropped, and the next pass connects again), `MicrosoftUnreachable: ConnectionError` or `Timeout` (no network to Microsoft: the next pass tries again, and a lapse noted before stays noted), `StoreRefused: ...` (the runner may not use the store of the tokens: it runs as another user, or in another kind of session, than the review page that made the store, for example a scheduled task without the desktop's keychain or keyring, or the store's folder lies inside a git repository; start both the same way, or delete the store's folder named in the line and sign in again from a page started like the runner). The runner goes on with `inbox/` in each case.

**The banner is red or does not appear.** "Inkoopassistent niet bereikbaar": the capture server does not run, or the extension's address is not the server's. "Inkoopassistent weigert de pagina": the reason follows; "missing or wrong token" means the token of the options is not `capture_token`, and "no cart table in ..." means the extension saw a cart table that the cart reader cannot read: the page is kept as `cases/captures/nocart_<...>`, the page to extend the cart reader with, and `cases/capture.log` names it. "Voorcontrole mislukt": with "the pre-check failed; see capture.log" the server met an error, whose traceback is in `cases/capture.log`; with "Herlaad de pagina" the extension was updated while the page was open, no case exists, and a reload is all it needs; otherwise the case's run failed, and its folder says why, as under "A case failed". No banner at all: the shop is not in the extension's list, its access was not granted (the options page names it), its tab was open before the shop was added (reload it), or the page holds no table the extension recognises as a cart. Such a page is never sent, so nothing is kept: save it by hand (Ctrl+S) to extend the cart reader with it.

**"Copilot refused: 403" on the settings tab or in `runner.log`.** Copilot does not let the account use the Chat API. Check, in this order: the account has a Microsoft 365 Copilot licence; the app registration has the seven permissions of section 3.15; an administrator has pressed "Grant admin consent" for the four that need it; and the account signed in for Copilot after those permissions were added (press "Aanmelden voor Copilot" again; like "Copilot testen" it is in the part "Koppelingen" at the bottom of the tab "Instellingen"). The code after the status is Graph's own (for example `Forbidden`); the aid never shows Graph's message. Other statuses: `429`, `503` and `504` (Copilot is busy: the case is tried again at the next pass), `400` (Copilot did not take the request; keep `runner.log` and the case folder for the researcher; a 400 when the conversation is created means the API itself changed). "Copilot gave no answer" fails the reading of that case. "Copilot ended the conversation: disengagedForRai" means Copilot refused that document's content itself; see section 3.15, "When Copilot fails". The digest names a failing Copilot in one line, "Copilot werkt niet", with the number of cases waiting for it, and the documents Copilot could not read while it read others in another, "Copilot kon ... niet lezen"; such a document is finished as failed after its third failure. A `SignInNeeded` under a case id means the sign-in has lapsed, or that the runner runs as another Windows user than the review page and cannot read its tokens (section 3.4): press "Aanmelden voor Copilot" as the runner's Windows user; the case is read at the next pass after that. To work without Copilot meanwhile, switch back to the parser (section 3.15).

**The runner stopped.** If it printed "stopped by .../STOP", the STOP file exists: delete it and start again. Exit code 2 with "aid.runner: ..." means the settings file cannot be read or is not valid (the message names the fields), or `reader` is `"llm"` without the tenant and the client ("reader 'llm' needs the Copilot sign-in": fill them in on the settings tab, in the part "Koppelingen" at its bottom, save and sign in for Copilot). Otherwise see `cases/runner.log` and the log of the scheduled task or service on the host.

## 6. The evaluation commands

**The batch** runs a list of cases headless:

```bash
python3 -m aid.batch --cases <cases.csv> --po-dir <folder> --settings <file> --reader parser|recorded [--recorded-dir <folder>] --out <summary.csv>
```

`cases.csv` has the columns `case_id` (the evaluation's id), `document` (the supplier's document, relative to the folder of `cases.csv` or absolute), `po_number` (blank when unknown) and `kind` (`confirmation` or `cart`). First every `*_po_v*.pdf` and then every `*_export.csv` of `--po-dir` is filed into the PO store, an export with the settings `export_columns`, `export_decimal_comma` and `export_fill_down` (see "The export of PO lines" below). `--reader recorded` replays recorded AI answers from `--recorded-dir` (made-up documents only). A cart saved as HTML is read by the cart reader under either reader. The batch prepares every change in `<cases_root>/prepared/`, sends nothing, and ends the waits that are over. `--out` gets one row per case: `case_id`, `status`, `version`, `flags`, `uncompared`, `actions`, `writes`, `messages`, `review_items`, `error`. Exit code 1 when a case failed. To try it on the made-up set: `python3 -m aid.demo --out <folder>` writes the settings, the `cases.csv` of the 22 made-up cases and runs them (options: `--out`, default `demo`; `--reader parser|recorded|anthropic`, default `parser`, where `anthropic` calls the development AI service with the key in the environment variable `ANTHROPIC_API_KEY` and is for the made-up documents only, never for company documents; `--force`; `--prepare-only`, which writes the folder and files the purchase orders but runs no case, the folder to watch the runner loop work by itself, as the aid README's section "Try it out on the made-up set" shows). A folder that exists and is not empty is refused with exit code 2 unless `--force` is given.

**The export of PO lines** gives a cart pre-check its PO side: a cart is checked before its order is released and sent, so no PO PDF exists yet. It is an overview of the order lines saved from the ERP, read by `aid/readers/po_export.py`. The batch files every `*_export.csv` of `--po-dir`; each PO of the file becomes the next version of that PO in the store, and the same file again files nothing, also after a change of the three export settings: the store keeps a PO as it was read the first time, so try the settings out on an empty PO store. What a file may look like:

- **The file.** An Excel workbook (.xlsx; its first sheet is read, hidden rows included) or a CSV file in UTF-8, Windows-1252 or UTF-16 with its byte order mark, as some programs save CSV. An old Excel file (.xls) is not read: save it as .xlsx in Excel. The batch picks up only files named `*_export.csv`; a workbook is read by the same reader (`PoStore.from_export`), which the batch does not call for it. A CSV file is cut at semicolons, commas or tabs: the aid tries them in that order and takes the first under which a row holds every column title of the map, so a title line above the column titles may hold another mark or none.
- **The columns.** One row per order line. `export_columns` names the column title of each value the aid reads. It must name the PO number (`po_number`) and the item code (`item_code`), and may name `line_id`, `description`, `quantity`, `unit`, `unit_price`, `price_basis`, `discount_pct`, `net_price`, `delivery_date` and `supplier`. Titles are compared without regard to case and to spaces around them. A column the map does not name is not read, and a value the map leaves out is missing on the PO. With the map empty, each column that is named after one of these twelve fields is read, as in the made-up exports; the file must then have the columns `po_number` and `item_code`.
- **The row of column titles** is the first row that holds every title of the map. Rows above it, such as a title or the date of the overview, are passed over. A title of the map that stands twice in that row is refused. The row of column titles repeated lower in the file, as in a report saved page by page or in two exports pasted into one file, is passed over as well.
- **Totals, group rows and text lines.** A blank row is ignored. A filled row without an item code is passed over: a total, a group row, or an order line that has no item code in the ERP, a text line for example, which is then not on the PO the aid knows. When the quantity is read (`quantity` is in the map, or the map is empty and the file has a column `quantity`), a row with an item code and a blank quantity is passed over too, so a total or a group row that prints its word ("Totaal") in the item code column is no order line; a quantity that is a text but no number keeps its line, with the quantity not read. Without `quantity` in the map such a row is read as an order line, so map the quantity when the export prints a total in the item code column.
- **The PO number** stands on every order line; or, with `export_fill_down` set to `true`, once for each PO, on its first line or on a row of its own above its lines (a group row, or a text line without an item code). A line without a PO number then belongs to the PO whose number stands nearest above it, on an order line or on a row that is passed over, but not on a repeated row of column titles. A PO number holds at least one digit: a word in the PO column, such as the "Totaal" of a total, is no PO number, and a line that would take it refuses the file by the row of the word. A PO number names a folder of the PO store, so on any computer it may hold at most 100 characters, none of `: * ? " < > | / \` and no control character, no space at either end and no dot at the end, and may not be a device name of Windows (CON, PRN, AUX, NUL, COM1 to COM9, LPT1 to LPT9, in any case and also with an extension); this holds for the PO number of a PO PDF as well.
- **Line numbers.** With a `line_id` column each line keeps its number; a line whose number is blank, or is the number of an earlier line of the same PO, is refused. Without one the lines of each PO are numbered 1, 2, 3 in the order of the file.
- **The supplier.** With a `supplier` column the PO gets the first supplier its rows name: its lines and, with `export_fill_down`, the row they take the PO number from (a text line or a group row). The column must hold the supplier exactly as the supplier lists of the settings name it (`review_suppliers`, `messages_off_suppliers`, `supplier_emails` and `supplier_lead_times`: the name on the purchase order), because those settings are keyed by that name, and a creditor number or another spelling would leave them out of the pre-check without a word. If the export has no such column, leave `supplier` out of the map. Without one, a PO known from an export has no supplier.
- **Numbers.** A number cell of a workbook is read as the number it holds. A number written as text, and every number of a CSV file, is read with `export_decimal_comma`: with `true`, "1.000" is a thousand and "1,125" one and an eighth; with `false`, the other way round. A discount is a number of percent (5 for five percent). A cell that Excel shows as a percentage holds 0.05 where it shows 5%, and is read as the 5 it shows.
- **The total of a PO** is the sum over its lines of the quantity times the unit price over the price basis; a discount is not taken off. A PO read from an export whose map names `net_price` and not `unit_price` therefore has no total, and with `authorization_limit` set every change the aid would make on it by itself goes to the buyer instead ("PO total unknown", R10).
- **Dates.** A date cell of a workbook is read as its day. A date written as text is read day first (24-11-2026) or year first (2026-11-24), and a time of day behind it is left out. A time or a duration cell (08:30, or a number of hours) is no value in any column.
- **Error cells.** In a workbook, an error cell (#N/A, #VALUE!, #REF!, #DIV/0!, #NAME?, #NUM!, #NULL!), or a text cell that holds one of these codes, is no value in any column; a date cell whose number lies beyond the dates counts as one, and the warning the reading library gives about it is kept from the console. In a CSV file such a code is a text like any other.
- **The PO number, the line number, the item code and the supplier** should be text cells in a workbook. A number cell holds no zeros in front, so a PO number shown as 0250301 is read as 250301 and an item code shown as 000123 as 123; a whole number is read as its digits. A date, a time, a duration or an error cell there (Excel makes a date of an item code such as 12-10 when it opens a CSV file) is no value: a PO number or a line number so kept refuses the file, an item code so kept is not read and its row is passed over, and a supplier so kept is not read.
- **A cell that is no value**, such as a word where a quantity is expected, keeps its text and is not compared; its line stays.
- **Nothing is filled in.** A line that states no unit or no price basis has none.

The whole file is refused, and nothing of it is filed, when the map names a field the aid does not have, or lacks the PO number or the item code; when the file is neither .xlsx nor .csv, is a workbook that cannot be read (a damaged file, or a cell whose content does not fit its type), or is a CSV file cut at a mark or in an encoding the aid does not read (the error then names the marks and the encodings it reads); when a column of the map is in no row of the file, or stands twice in the row of column titles; when a line has an item code and no PO number (unless `export_fill_down` is set and a PO number stands above it); when a PO number is a date, a time or an error cell, holds no digit, or cannot be the name of a folder; or when the line number of a line in a `line_id` column is blank, is a date, a time or an error cell, or is the number of an earlier line of the same PO. The error stops the batch before any case runs; it names the column or the row, where it is known, and never what a cell holds. `aid/madeup/tryout/EXPORT_nl.csv` is a made-up export in a Dutch form to try this on: semicolons, decimal commas, a title row above the column titles, the PO number on the first line of each PO only and a total row. It is read with `"export_columns": {"po_number": "Inkooporder", "supplier": "Leverancier", "item_code": "Artikel", "description": "Omschrijving", "quantity": "Aantal", "unit": "Eenheid", "unit_price": "Prijs", "price_basis": "Per", "delivery_date": "Leverdatum"}`, `"export_decimal_comma": true` and `"export_fill_down": true`.

**The disposition log** is each case's `dispositions.csv` with the columns `case_id`, `version_selected`, `line_id`, `field`, `decision`, `source`, `note` (the header of `evaluation/templates/dispositions.csv`). The aid's own decisions read "Exact update executed (R04)", "prepared (R01)", "write failed (...)" and "ask the supplier (R06)"; the buyer's decisions on the page carry "B01": "accept the PO value (B01)", "accept the supplier's value (B01)", "accept (B01)", "ask the supplier (B01)", "unresolved (B01)". The batch writes the `case_id` of `cases.csv`.

**The necessity check** decides whether the AI reader is needed:

```bash
python3 -m aid.evalscripts.necessity --cases <folder> --spec <folder> --recorded <folder> [--out <file>]
```

It has the parser and the AI reader read every `*_conf.pdf`, scores both against the specs and prints the decision (`keep_llm` or `parser_only`). The default output is `aid/evalscripts/results/necessity_<date>.csv`. As the command stands it replays recorded answers; a run on the made-up set is development evidence, not the check. The command replays recorded answers only: the recorded fixtures of the made-up set, or answers recorded with the development client on made-up documents. Recording Copilot's answers is not built yet (the record command of `aid/readers/supplier_llm.py` uses the development client only), so the check on the development cases, which are company documents, waits for it.

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

**Instellingen wijzigen.** Uw eigen regels staan op het tabblad "Instellingen" van de beoordelingspagina; een instellingenbestand hoeft u nooit te openen. Het tabblad laat zien wat er in het instellingenbestand staat, telkens wanneer u de pagina laadt of het tabblad kiest. Bovenaan staat wat de assistent zelf mag afhandelen. De drempel voor de prijs is een percentage en een bedrag in euro per regel: blijft een prijsverschil binnen beide, dan neemt de assistent de prijs van de leverancier over op de inkooporder en verandert ook de prijs van het artikel; een verschil boven de drempel komt naar u. De marge voor de leverdatum is een aantal dagen bovenop de levertijd van de leverancier, geteld vanaf de leverdatum van de regel op de inkooporder. Een voorbeeld: vraagt de regel om levering op 16 november, is de levertijd 10 dagen en de marge 7 dagen, dan neemt de assistent een latere datum over tot en met 3 december, en komt 4 december naar u. Zonder bekende levertijd komt elke latere datum naar u; een eerdere leverdatum neemt de assistent over. Bij "Eerste zaken van een leverancier" vult u in tot hoeveel afgesloten zaken elke zaak van een leverancier naar u komt (de reden heet dan "Nieuwe leverancier"); 0 zet dit uit. Daaronder staan de wachttijd op een antwoord van een leverancier en uw naam onder de berichten, dan het dagoverzicht (elke dag op een vaste tijd of na elke zaak), uw e-mailadres en de taal, en het e-mailadres van de fiatteur. Een percentage of een bedrag heeft hoogstens twee decimalen; schrijf duizend als 1000, want "1.000" wordt geweigerd.

Druk op "Opslaan": de pagina schrijft alleen wat u hebt gewijzigd, en bij "Opgeslagen" gebruikt de assistent het binnen een minuut. Hebt u niets gewijzigd, dan wordt er niets opgeslagen. Klopt een waarde niet, dan wordt niets opgeslagen en noemt de pagina het veld. Wat vóór het opslaan al gecontroleerd was, houdt zijn uitkomst: een zaak die op u wacht houdt haar reden, en een winkelwagen wordt op dezelfde dag pas opnieuw gecontroleerd als de winkelwagen zelf verandert.

Onder "Leveranciers" kiest u een leverancier, of "Leverancier toevoegen" met de naam zoals op de inkooporder, en vult u het e-mailadres voor vragen en de levertijd in. Met het eerste vinkje komt elke zaak van die leverancier naar u. Met het tweede stuurt de assistent die leverancier uit zichzelf geen vragen: die vragen komen dan naar u, en een vraag die u zelf op het tabblad "Beoordelen" kiest ("Leverancier vragen"), gaat wel naar de leverancier. Druk op "Leverancier opslaan" of "Leverancier verwijderen". Een leverancier die al in de lijst staat, kiest u in de lijst; als nieuwe leverancier wordt die naam niet opgeslagen. Een nieuwe leverancier heeft naast de naam een e-mailadres, een levertijd of een vinkje nodig.

Staat bovenaan het tabblad dat het instellingenbestand niet kan worden gelezen, dan kunt u niets opslaan: geef dat door aan de onderzoeker. De fiatteringsgrens is bij de installatie ingesteld. De koppelingen met het postvak en met Copilot staan onderaan het tabblad, onder "Koppelingen: het Microsoft-postvak en de AI-dienst". Dat deel opent u met een klik op die regel; u hebt het alleen nodig als het dagoverzicht u vraagt om u opnieuw aan te melden.

**Het postvak koppelen.** Onderaan het tabblad "Instellingen" van de beoordelingspagina staat "Koppelingen: het Microsoft-postvak en de AI-dienst"; klik op die regel om het deel te openen. Daar meldt u zich aan met uw Microsoft-werkaccount: "Aanmelden bij Microsoft" opent een browservenster op de pc waar de pagina draait. Kies daarna de map van uw postvak; dat moet een map bovenaan het postvak zijn, naast "Postvak IN", want mappen daarbinnen staan niet in de lijst. In die map moeten de e-mails met de inkooporders, de orderbevestigingen en de antwoorden op de vragen van de assistent komen: de antwoorden bijvoorbeeld met een Outlook-regel op "Vraag over orderbevestiging PO", de rest met regels op de afzenders van Exact en van de leveranciers, of kies "Postvak IN" zelf. Een e-mail die niet in de map komt, wordt niet gecontroleerd. Druk op "Verbinding testen" en dan op "Opslaan". Vanaf dat moment leest de controle die map zelf, bij elke ronde: wat er al in stond laat zij liggen, en zij verplaatst of verwijdert niets. Lag de controle lang stil, dan leest zij daarna wat sinds haar laatste ronde is ontvangen; een ouder bericht dat u in die tijd naar de map verplaatste, verplaatst u eruit en er weer in. Staat in het dagoverzicht "Postvak niet verbonden: meld je opnieuw aan op het tabblad Instellingen, onderaan bij Koppelingen", open dan dat deel onderaan het tabblad en meld u daar opnieuw aan. Zolang de aanmelding verlopen is, verstuurt de assistent geen vragen: die komen op de beoordelingspagina als "Bericht niet verzonden", zodat u ze zelf verstuurt. "Afmelden" alleen laat de map staan, zodat die regel in elk dagoverzicht blijft staan; druk daarna op "Opslaan" om de koppeling helemaal weg te halen. "Afmelden" meldt u ook af voor Copilot: leest de assistent met Copilot, dan wachten alle zaken tot u weer op "Aanmelden voor Copilot" drukt of bij "Documenten lezen met" "zonder AI (parser)" kiest. Koppelt u later opnieuw, dan begint de koppeling op dat moment en blijft wat er intussen in de map kwam liggen.

**De voorcontrole in de browser.** Op de webshops die in de extensie staan, verschijnt boven de winkelwagen een balk van de inkoopassistent, ongeveer een seconde nadat de pagina opent of de winkelwagen verandert. Groen: de winkelwagen komt overeen met de inkooporder (kleine prijsverschillen heeft de assistent zelf verwerkt; die regels staan eronder). Oranje: er zijn verschillen, met een regel per verschil; pas de winkelwagen of de inkooporder aan voordat u bestelt. Een regel met "wijziging voorbereid" of "bijgewerkt" heeft de assistent al verwerkt; die hoeft u niet te corrigeren. Grijs: er is geen inkooporder gevonden. Rood: de controle is mislukt of de assistent is niet bereikbaar; geef dat door aan de onderzoeker. Staat er "Herlaad de pagina", dan is de extensie bijgewerkt: laad de pagina opnieuw. Met "Bekijk de zaak" opent u de zaak op de beoordelingspagina zolang die op u wacht. Opnieuw laden van dezelfde pagina opent geen nieuwe zaak.

**Copilot.** De assistent leest de orderbevestigingen met Microsoft 365 Copilot via uw eigen werkaccount. Daarvoor meldt u zich aan met "Aanmelden voor Copilot", onderaan het tabblad "Instellingen" in het deel "Koppelingen: het Microsoft-postvak en de AI-dienst". Een gescand document zonder tekst kan Copilot niet lezen, en die zaak ziet u in het dagoverzicht bij "Mislukt". Staat in het dagoverzicht "Copilot werkt niet", open dan dat deel "Koppelingen", meld u opnieuw aan met "Aanmelden voor Copilot", druk op "Copilot testen" en geef het door aan de onderzoeker als het niet lukt. Staat er "Copilot kon 1 document niet lezen", dan probeert de assistent het bij de volgende ronde opnieuw; lukt het na drie keer niet, dan staat die zaak bij "Mislukt" en controleert u dat document zelf.

**De assistent starten.** Is het automatisch starten bij het aanmelden ingesteld (`--install-autostart`, sectie 3.4.1), dan hoeft u niets te typen: na het aanmelden op Windows starten de controle, de beoordelingspagina en de voorcontrole vanzelf, op de achtergrond en zonder venster. De opdrachten hieronder typt u in een PowerShell-venster, in de map waarin de map `aid` staat; ga daar eerst heen met `cd`, bijvoorbeeld `cd C:\Users\<uw naam>\<projectmap>`, waarbij `<projectmap>` de map is waarin `aid` staat. Wilt u de assistent stilzetten, voer dan `python -m aid.start --settings <map>\settings.json --stop` uit; hij blijft stil, ook na opnieuw aanmelden, tot u `python -m aid.start --settings <map>\settings.json --resume --background` uitvoert. Geef `--resume` dezelfde opties mee als waarmee het automatisch starten is ingesteld (bijvoorbeeld `--testshop` of `--poll-seconds`); anders draait de assistent tot de volgende aanmelding met andere instellingen. Met `--background` start de assistent weer op de achtergrond en mag u het venster daarna sluiten; zonder `--background` draait hij in dat venster en stopt hij zodra u het sluit. Met `--status` ziet u of alles draait. Doet het woord `python` niets in het venster (op Windows kan het een verwijzing naar de Microsoft Store zijn), typ dan in plaats daarvan het volledige pad van Python, bijvoorbeeld `C:\Users\<uw naam>\AppData\Local\Programs\Python\Python311\python.exe -m aid.start ...`.

**Wie belt u?** Bij een vraag, een fout, een zaak bij "Mislukt" of een pagina die niet opent: bel of mail de onderzoeker, Yijie.
