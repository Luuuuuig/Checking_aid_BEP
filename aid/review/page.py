"""The review page itself, built with gradio (aid/review/app.py has the view it shows, aid/review/close.py the close it calls).

build_app builds the page and page_rows gives the rows of a list as the page shows them. gradio is imported inside build_app, so that
the rest of the review page works without it. gradio makes an asyncio event loop for each lock it creates outside a running loop
and leaves it open (safe_get_lock of gradio/utils.py, 6.31.0), which the garbage collector later reports as unclosed; build_app
therefore builds the page inside a short-lived event loop of its own, which it closes, and directly when a loop is running already
(a notebook). The page shows no rule code and no error text, and no setting value outside the settings tab: fields, reasons, writes
and failures by their labels in i18n.STRINGS.

The cases the page offers (offered_cases) are those in review and any case with a write to undo, whatever its status: a write made
in the system (status "done") that is not undone, so that a write the aid made on its own can be undone from the page. A change only prepared was not made in
the system and is not offered for undo, as the digest gives it no undo link. The page reads the query of its address when it opens
(selection_from_query): ?case=<case id>, the link of the digest, selects that case when the page offers it, and &undo=<entry id>,
the digest's undo link, also selects that write in the list of writes to undo. Opening a link undoes nothing: the buyer undoes the
write with the undo button. A case the page does not offer, or a write it does not offer for undo, selects nothing. A case that waits
for its PO (waiting_for_po of aid/review/close.py, a case with a PO number of its own) shows a hint above its decisions that names
the PO, or the version of it that the confirmation quotes ("Inkooporder 250101 kent de assistent nog niet. ..."), which the
language switch relabels; the runner checks such a case again by itself once that PO comes in, and a case it replaced is closed, so
the page no longer offers it. A decision saved on the case hides the hint at once, since the runner then no longer checks the case
again, and so does "Lijst vernieuwen" for a decision saved in another browser tab or a case replaced meanwhile. A case that was
compared with values entered in Exact (step 03_entered of aid/pipeline.py: the buyer entered and ticked them, or the aid saw them
in a newer print, after the selected version was filed) names them in one line above its decisions (entered_text), "Sinds deze
inkooporder is opgenomen, is in Exact ingevoerd: regel 1: prijs 12,70. De assistent vergelijkt met deze waarden.", since the order
shown beside the document is the version as it was filed; a case that ran while the list of those values could not be read says
that in the line's place. The language switch relabels the line. All this is on the tab "Beoordelen".

A case chosen on that tab stands in three parts, top to bottom, in the order of the work. First what the buyer decides (the column
review-case): the banner, the hint and the line of the values entered in Exact, the notice of a failed case, "Besluiten" and a row
per review item. Then the close (review-close): "Zaak afsluiten", shown with a case in review only, or "Als afgehandeld noteren",
the status line under them, which holds the answer of a close, of that press and of an undo, and the part to undo a write
(review-undo), shown while the case has a write made in Exact and not undone, so with the prepared writer never. Last what the
decisions rest on (review-evidence): under "Documenten" the purchase order on the left and the supplier's document on the right,
each page at the full width of its column (the style rules of aid/review/look.py), with the lines that the differences were read
from marked on both (the marks of the view, aid/review/app.py, which _marked draws: the line's ground in the tint of attention
inside an edge in its mark colour, nothing else of a page changed), then under "Vergelijking" the lists "Afwijkingen", "Niet te
vergelijken" and "Overeenkomsten", each its table or, while it has no row, one sentence in its place ("Geen afwijkingen in deze
zaak."); a case that was not compared, such as one that failed before the comparison, and a document left to the buyer show no
comparison, rather than sentences that are not true of them. The first and the last part show while a case is chosen (case_framed);
the close's part always stands, so that what a close said stays on the screen once the case has left the review and its parts have
closed. record_shown, on the change of the state of the view and on the language switch, gives each list its table or its sentence
and the undo part its visibility, from the case's write log as it is then, as undo_shown does after each undo, so that the part
closes once its last write is undone, and review_texts gives the headings of these parts their language.

A decision row (spec 2.1.1) says on its left what is decided, each fact once (_describe): the field and the line ("Prijs, regel 1"),
the purchase order's value first and the supplier's after it, in the form of the page's language (a decimal comma in Dutch, a day as
16-11-2026), with the reason, and where each value was read, by its page and line in words and the text of that line as it was read
("Gelezen op de inkooporder (pagina 1, regel 8): ..."), the lines whose marks the documents show; no item id. On its right stand the
four decisions, "Waarde van de leverancier overnemen", "Waarde van de inkooporder houden", "Leverancier vragen" and "Onopgelost
laten", and under them the tick of the article price beside the row's note, where a decision answers once it is saved (row_note):
"Opgeslagen.", for "Leverancier vragen" where the question goes when the case is closed ("Bij afsluiten gaat er een vraag naar S07.",
or that no address of the supplier is known, or that sending is set to Bestanden), or why it would not go when the close would have
no way to send it ("Er is geen postvak verbonden: ...", the mail server's password, the settings file), judged as the close judges
it, by the settings as they are then (_asked_text), for "Onopgelost laten" that the item stays open, and last what is left to decide
("Nog 1 punt te besluiten.", "Alles is besloten: u kunt de zaak afsluiten."). A decision that is not saved says why there, in the colour of a problem. When the rows are rendered again (the
language switch, the case shown again), a decision that stands says "Opgeslagen." in its note. The status line answers the close,
"Als afgehandeld noteren" and an undo, and no decision.

The case list of that tab ("Zaak") names each case in one grammar, its order and supplier, what it holds and the time it was
opened, "Inkooporder 250107 (S07): 1 punt, 12-11-2026 09:00" (_case_label), and offers, after the cases offered_cases gives, the
cases that failed and that the buyer has not noted as handled, oldest first, each as "Inkooporder 250105 (S05): mislukt, zelf
bekijken, 12-11-2026 17:03"; offered_cases itself, and so the digest's links and the undo, do not change. A failed case chosen
shows its supplier's document, and above it, in the place of the decisions, why it failed in words, never by its error (fail_text
of aid/review/status.py), with what to do ("De assistent kon deze zaak niet afmaken: de assistent herkent de opmaak van dit
document niet. Bekijk het document van de leverancier en handel de bevestiging zelf af in Exact. ...", which the language switch
relabels: failed_relabel); "Als afgehandeld noteren" stands in place of "Zaak afsluiten", and the title of the decisions goes, as
such a case has none (case_framed, bound beside show to the change of the case list).
That press (handled_marked) writes <case folder>/handled_by_hand.json, {"at": <the time of the clock of the page's Deps>}, the one
file it writes, and only into a case that failed: the case then leaves the list and the counts, and a mark written before, from
another browser tab, stays as it was. A case on screen that leaves the list meanwhile (closed in another browser tab, replaced by the
runner, noted as handled) keeps its place in it, named as the list names it now (_kept_label: a failed case noted as handled as
"Inkooporder 250105 (S05): mislukt, door u afgehandeld, 12-11-2026 17:03"), wherever the list is sent again ("Lijst vernieuwen",
the language switch, the timer), until another case is chosen, and a line under the field says so and why ("Inkooporder 250107
staat niet meer op de lijst: de zaak is intussen afgesloten of vervangen. Kies een andere zaak.", or "... als afgehandeld
genoteerd. ..." for a failed case, whose notice and button the language switch then hide rather than leave the notice in the
language it was shown in): the page library refuses a value that is not among a dropdown's choices. While no case is chosen, a line under the
field says to choose one ("Kies een zaak bij Zaak, of open de oudste.") or that none waits for the buyer, with "Oudste zaak openen"
while one does, which chooses the oldest case in review, else the oldest failed one, else the oldest document left to the buyer
(below), and sends the case list with it, so that the case is among its choices (oldest_opened). The row of that line and that
button shows and hides with the value of "Zaak" alone (case_framed, on its change), never by the timer, whose tick may have been
sent before a case was chosen.

After the failed cases the list offers the documents the intake left to the buyer to look at himself, which open no case: a scan
that nobody could class, a confirmation that names more than one purchase order (the scan's lines of <cases_root>/review_items.jsonl,
aid/sources/mailbox.py, as review_items of aid/review/status.py reads them), those that wait for him, oldest first, each as "Document
MU05_conf.pdf: zelf bekijken, 12-11-2026 17:03" (_item_label), its file as the list of skipped items names it and the time of the
scan, with its id as the value (ITEM_PREFIX and twelve hex digits: no case id begins so). The strip counts them ("Zelf bekijken: 3"),
the tab's label counts them with the cases that wait for the buyer, and the fold lists them among the cases by their time, those that
wait first, and those he noted as handled for 7 days ("Door u afgehandeld"). A document chosen shows why the intake left it to the
buyer in words, never by the scan's own reason (look_text of aid/review/status.py), in the place and the look of the notice of a
failed case ("De assistent kon dit document niet plaatsen: het is een scan zonder leesbare tekst, en de assistent kon niet zien wat
voor document het is. ..."), with "Als afgehandeld noteren" and neither "Zaak afsluiten" nor the title of the decisions; its pages
stand in the gallery of the supplier's document, rendered as a case's document is, from where the scan put it on this PC
(_item_document): a file of its own in processed/ of the watched folder, under its own name or with _2, _3 and so on, and an
attachment inside its e-mail there, of the files of that name the one whose bytes have the sha256 the scan noted, whatever the times
of the files (of a line written before the scan noted one, the one file of that name, and none of several). When it is no longer
there, or cannot be told from another document of the same name, the gallery stays empty, and the notice ends with where to look for
it ("Het document staat niet meer op deze pc: zoek het in uw postvak, in de e-mail van 12-11-2026 17:03."), rather than show another
document of the same name. "Als afgehandeld noteren" on a document (item_marked) appends its mark
(handled_mark of aid/review/status.py) to <cases_root>/review_items_handled.jsonl in one write, the one file it writes, with the
time of the page's clock; the document then leaves the list and the counts, and a mark written before, from another browser tab,
is not written again. Every other handler that takes the value of "Zaak" reads no document's id as a case: show gives the document's
pages and nothing else of a case (no list, no write to undo, no decision), a close and an undo answer as with no case chosen, and
the language switch and "Lijst vernieuwen" treat it as no case; a document on screen that was noted as handled in another browser
tab keeps its place in the list, "door u afgehandeld", and the line under the field says so, until another case is chosen.

Above the case list, a strip shows the state of the aid, read from the aid's own files on this PC and never from Microsoft
(aid/review/status.py): a chip for each state with its number of cases, "Ter beoordeling: 2" always and "Mislukt", "Wacht op Copilot",
"Wacht op leverancier" and "Vanzelf afgesloten" (of the last 7 days) while their number is above 0; while the assistant has a problem,
a chip for each, the most urgent first ("De assistent heeft nog geen ronde gemaakt", "Aanmelden kan niet: de beveiligde opslag is
geweigerd", "Copilot werkt niet sinds 12-11-2026 15:57: 2 zaken wachten", ...), never with an error's text, and one button beside the
strip that names the place that handles the first ("Stand van de assistent openen" for the runner and its launcher, "Postvak en AI
openen" for the sign-in, the mailbox and Copilot) and opens the tab "Instellingen" on that section, by the problem the files tell at
the press (strip_acted; "Stand van de assistent" when it went meanwhile), after which the section "Stand van de assistent" is read
again (assistant_shown); and a last line with the runner's last pass, the mailbox as the settings tab tells it, the reader and the
time the strip was read ("Laatste ronde: 18:19 · Postvak gelezen om 18:18 · Documenten lezen zonder AI · Bijgewerkt om 18:20"). On a
public share link, and without a settings file, the strip holds the chips alone and has no button. Under it, the fold "Alle zaken van
de laatste 7 dagen (22)" holds a table of every case that is not done and of every case done in the last 7 days (case_lines), those
that wait for the buyer first, each with the time it came in, its order, its state and what the buyer does: mostly "Niets te doen.",
and for a closed case whose changes stand on the list "In Exact invoeren" to enter them there. Every name in the two goes through
html.escape. Their style rules stand among the page's own (PAGE_CSS of aid/review/look.py), under the ids of the two, and the page
library's own style of an HTML block (apply_default_css), which rules a table's rows in the text colour, is left off them; on a page
launched without that look (the Colab notebook) they show their words alone. The label of the tab counts the cases that wait for
the buyer, "Beoordelen (3)" (review_label: in review, or failed and not noted as handled, and the documents left to him that wait
for him), and has no number while none waits.

The strip, the fold, the tab's label, the line under the case list and the case list are read again when the page loads, with
"Lijst vernieuwen", at the language switch and every TICK_SECONDS seconds, by one gr.Timer at the top level of the page, through
two handlers of their own (strip_ticked and queue_ticked), which never call Microsoft. Neither sets the value of "Zaak" nor reaches
the view of the case chosen, and the list of the tab "In Exact invoeren", whose read empties the buyer's ticks, is not on the timer:
the timer gives that tab its label and a line of the changes that came in, and nothing else (hand_label_ticked, below). It also
reads the table of the tab "Bestanden" again, and nothing else of that tab (files_ticked, below).
A tick reads again only the case folders whose files changed since it read them: through the cache of aid/review/status.py for the
states, and through offered_cases' own for the cases offered.
queue_ticked sends the case list only when it differs from the one it sent last, which a gr.State keeps, so that an open dropdown
is not closed for nothing; a list that another handler sent meanwhile (a close, "Lijst vernieuwen", the language switch) and that
differs from the kept one is sent once more at the next tick, the same list, which changes nothing on the screen. At a language switch the relabel of the tabs names the tab "Beoordelen" by
review_label as queue_ticked does, so that the label is the same whichever runs last.
The page library's client (gradio 6.31.0) runs the listeners of one click, change, select or tick one after another, each to its end
(those of the page load and of a State's change it starts together), and a listener on the queue costs a round trip of the queue, a
quarter of a second in the browser: the language switch, which some thirty listeners share, is as slow as their number. So the five
handlers that the redesign added to it after its first part (record_shown, review_texts, hand_new_cleared, removal_relabelled,
files_ticked), each a read of a few files or none, skip the queue, which costs a few hundredths of a second, and the switch takes
about as long as before them.

Above the tabs, the head of the page (the Row page-head) names the tool, "Inkoopassistent" in both languages, as the page's one h1
(its class page-title) and as the title of the browser's tab, and holds the language switch (its class language-switch), which the
style rules of aid/review/look.py show on the right as the pair "Nederlands" and "English". The switch keeps its label "Taal" for a
screen reader, which reads it, but the page does not show it.

The page has five tabs in one gr.Tabs (its class page-tabs), each with an id: "Beoordelen" (REVIEW_TAB, "beoordelen"), "In Exact
invoeren" (hand_list.TAB, "invoeren"), "Prijslijst" (pricelist_page.TAB, "prijslijst"), "Bestanden" (FILES_TAB, "bestanden") and
"Instellingen" (SETTINGS_TAB, "instellingen"). The query tab=invoeren, the link of the digest's line of the changes to enter
(aid/notify.py, list_link), selects the second tab when the page opens; any other value of tab selects nothing.

The tab "In Exact invoeren" lists what the buyer enters in Exact by hand: the open changes of <cases_root>/prepared
(aid/execution/by_hand.py), as aid/review/hand_list.py shows them: the rows of the purchase orders, by order, each named
"Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70", and after them the article prices, each named "artikel KM-0801:
inkoopprijs onbekend → 12,70". Its label is "In Exact invoeren (3)" with the number of open changes, without a number when none is
open, and "In Exact invoeren (?)" while the list cannot be read. Its controls are fixed, none made in a rendered function and every
button outside the rows, so that each handler is one of the page's functions (the tests find them by name): the intro, with under it
the line of the test mode while the setting test_mode is true ("Proefstand: voer deze wijzigingen alleen in het test-Exact in."), the
hint of each order above the authorization, "Er is niets in te voeren." when nothing is open and how many lines of the list could not
be read, and "Lijst vernieuwen" beside it; "Aangevinkte regels zijn ingevoerd", the one filled button of the tab, and "Aangevinkte
regels niet invoeren", which mark the ticked changes as entered or as dropped (changes_entered, changes_dropped), with right under
them the note of what a press of the list did ("Vink eerst aan wat u bedoelt." when nothing is ticked or chosen, and "Er is niets
genoteerd: ..." when no ticked row was still open; either way nothing is written), in one column (hand-actions) that the page's style
keeps at the top of the window while the list scrolls; one group of tick boxes with every open change, one under the other (a style
rule given to the page in the head of an HTML block, for the group's class "hand-rows"), so that the rows of one order stand together;
the two buttons again under the rows, small, so that they stay in reach of a long list; the dropdown "Inkooporder of artikelprijzen"
with "Alles hiervan is ingevoerd" (block_entered), which marks the changes of the block chosen as the page showed them when the list
was last read (kept per block in a gr.State that each read fills, with the dropdown's choices), each only while it is open, so that a
change prepared after that read stays on the list; and under it, shown only while a price list has article prices to enter, the
dropdown "Prijslijst" with those price lists, the newest first (hand_list.price_lists), and "Alle artikelprijzen van deze prijslijst
zijn ingevoerd" (pricelist_entered), which marks the article prices of the list chosen as the page showed them when its price lists
were last read (pricelists_opened, which reads them when the list is read and after each press of the tab), each only while it is
open.
The list is read again (list_opened) when the page loads, when the tab is selected and with "Lijst vernieuwen", and after each press.
The page's timer gives the tab its label again every TICK_SECONDS seconds (hand_label_ticked), from a read of the list that writes
nothing and gives neither the rows, the note nor the blocks, so that what the buyer ticked stays as it is; when changes came in that
the list on screen does not hold (none of their entry ids stands in a row the last read showed), a line under the intro says how
many ("Er zijn 2 nieuwe wijzigingen bijgekomen: druk op Lijst vernieuwen."), which goes when the list on screen is read again: the
tab selected, "Lijst vernieuwen", the language switch and each press of the tab (hand_new_cleared).
A mark is a line of <cases_root>/prepared/marks.jsonl, at the time of the clock of the page's Deps; nothing else is written, so a
case's result, its write log, its dispositions and prepared_changes.csv stay as they are. A list that cannot be read (another program
holds a file of it, or any other error) leaves the rows as they were, with the label "In Exact invoeren (?)" and the note "Dit is niet
gelukt (<the class of the error>).", as the settings tab says it; a list that cannot be understood (by_hand.ListUnread: the file
was saved outside the aid and lost the writer's columns) has a sentence of its own in place of that note, which names no class
and says whom to tell. On a public share link and without a settings file the tab offers
nothing, as the settings tab does. The review tab's own list of writes to undo is not this list: it offers writes made in a system.
An undo of such a write that the page's writer only prepares is not undone in Exact: the page then says, as the tab does for a
put-back (below), "Nog niet teruggedraaid in Exact. Op de lijst staat nu: ...", never "Ongedaan gemaakt".

Under the list the tab has two sections, fixed controls as well (aid/review/hand_list.py makes what they show). First, a task of
every day, "Bevestigingen om bij te voegen" lists the confirmations to attach in Exact, under a line that counts them and says what to
do ("2 bevestigingen van de laatste 7 dagen: voeg ze in Exact bij en zet de status op Bevestigd."), each with whether the status is to
be set to Bevestigd as well; it shows nothing when there is none. Last, "Afgehandeld in de laatste 14 dagen" lists the changes
entered, seen or dropped in that time, the newest first, each with how the aid knows it ("(door u afgevinkt op 18-11-2026)",
"(gezien in versie 2 van de inkooporder)", "(gezien in een export uit Exact)", "(niet ingevoerd, op 18-11-2026)"); under it the
dropdown "Afgehandelde wijziging" with the same changes, "Weer op de lijst zetten" (change_reopened), which takes back a tick or a
drop of the buyer's own (the mark reopened) but not a change the aid saw, nor one that was put back ("Deze wijziging is
teruggedraaid; zij komt niet terug op de lijst."), and "Terugdraaien in Exact" (change_put_back), which prepares the old value as a
change to enter (hand_list.take_back), and a note of what a press did; with nothing chosen either button says "Kies eerst een
wijziging bij Afgehandelde wijziging." and writes nothing. Both sections are read again (done_opened) when the page loads, when the
tab is selected, with "Lijst vernieuwen", when the language switches and after each press of the list's buttons; the two buttons
show the list and the sections again.

The tab "Prijslijst" (_pricelist_tab; aid/review/pricelist_page.py has what it shows and writes, aid/pricelist.py the article prices)
compares a supplier's price list with the purchase prices that Exact holds, and puts what changes on the list "In Exact invoeren"; the
aid changes nothing in Exact. Its controls are fixed. While the columns of the export of articles are not set (the setting
article_columns, read as the settings file is now), it shows one sentence and nothing else: "De kolommen van de export van de
artikelen zijn nog niet ingesteld. ...". Else, top to bottom: the introduction; "Leverancier", a dropdown of the suppliers with stored
prices in which a new name can be typed (a name is taken without the spaces around it, and one that differs from a stored name in
capitals or spaces only is that supplier), with "Inkoopprijzen van deze leverancier weghalen" beside it, small, which only asks
(remove_asked): right under that row a question says how many stored prices of which supplier go ("De 12 opgeslagen inkoopprijzen
van S08 worden weggehaald. De lijst In Exact invoeren blijft zoals hij is."), with "Ja, weghalen" (prices_removed), which takes the
stored prices and the kept columns of that supplier away, whole or not at all, and no row of the list (a supplier given under two
spellings), and "Niet weghalen" (remove_kept), which closes the question as another supplier chosen does; the answer of a removal
stands under that row (removal_shown), never with the note of the export far lower; the state of the supplier's stored prices
("Inkoopprijzen van S01 opgenomen op ... uit
...: 12 artikelen.", that none are filed yet, or "Kies eerst een leverancier."); the field "Export van de artikelen uit Exact (Excel
of csv)" with "Inkoopprijzen opnemen" (prices_filed), active only while a file is chosen, which stores the export's prices for its
supplier (store_current: the supplier the export's supplier column names, else the supplier chosen, without whom nothing is stored)
and leaves every other supplier's prices as they are, and a note of what was stored or why not; and, shown only for a supplier
with stored prices, the price list: the field "Prijslijst (Excel of csv)", five column fields that offer the titles of the list's
first row and are filled from the columns kept for the supplier, else from the titles that equal the aid's own (pricelist_file_chosen),
under them one line that says to point out the column for what a price is per when the list has one, "Bekijken"
(pricelist_shown), the table of the list's rows with their outcomes, at most 320 px high, the count line, and "Op de lijst In
Exact invoeren zetten" (pricelist_carried_out), which a step of the browser after "Bekijken" brings into view with the count line.
"Bekijken" writes nothing anywhere: it compares the list with the supplier's current prices
(current_prices: the stored prices with every article price entered or seen on the list since) and keeps what it showed in a
gr.State. The press is active only after a view, and a change of the list's file, of the supplier or of a column field ends the
view. It carries out the view the page showed: the changes are prepared through the prepared writer into <cases_root>/prepared and
logged in the price list's own write log, and the list's columns are kept for the supplier beside the stored article prices
(keep_columns of aid/pricelist.py); the tab never writes the settings file. When the list file, the supplier, the columns, the
supplier's current prices or what the list would change is no longer what the view showed (a mark on the list, a newer export,
an export of another supplier that holds one of the list's articles), nothing is carried out and the note says "De gegevens zijn
veranderd sinds u de lijst bekeek. Bekijk de lijst opnieuw."; a view is carried out once, and a press without one changes
nothing. After the press the table is shown again from what the press did, a change that could not be prepared with "niet
gelukt", and the note names the articles of those rows; when the columns could not be kept, or the price list's own write log
could not be written, the changes stand on the list all the same and the note says so after the count. Four kinds of rows the
view shows and the press leaves out (pricelist_page.held_back): a price of an article that another supplier holds too, by its
stored prices or by an open change on the list "In Exact invoeren", whose outcome names that supplier; a price more than three
times as high or as low as the purchase price in Exact; a price that the list holds as ticked after the supplier's export; and a
price that the buyer took off the list. So what the view shows as changing, the press puts on the list. When a row kept off the
list leaves an older price of the same supplier's earlier list on the list, the count line and the note name its article
(pricelist_page.older_asked). A change that the list holds as entered from before the supplier's newer export is asked again by
the press (pricelist_page.ask_again). A supplier's name of more than 100 characters, or with a line
break or a control character, is refused wherever the tab takes one. Both file fields, 140 px high, take any file, so that the page
library refuses none in its own words: a file of another type, an empty file, a workbook with more than one visible sheet with data (one
sheet is read, the one the buyer sees), a CSV file with a row that holds more cells than its row of titles, a price list with a
heading above its titles, an export whose columns are not those set, an export that gave no article and a file that cannot be
read are each named by a sentence, the file's name shown as it is (_literal), and one press stores one export, as one press of the tab
"Bestanden" gives one file. A stored file
of article prices that cannot be read is nothing stored, and the notice at the top says so. The tab is filled (pricelist_opened) when
the page loads and each time it is selected, and the language switch relabels it (pricelist_relabel), emptying the notes, the table
and the view. On a public share link and without a settings file the tab offers nothing, as the settings tab does.

The tab "Bestanden" (_files_tab; aid/review/intake.py has what it shows and writes) takes a file that does not come in by itself: the
title "Een bestand aan de assistent geven", an intro that names the export from Exact only while the export's columns are set
(export_columns, read as the settings file is now), the upload field "Kies een bestand", which takes any file, so that the page library
refuses none in its own words (its drop zone 140 px high), and "Aan de assistent geven" (file_given), active only while a file is
chosen (file_chosen), which puts the file into the watched folder of the settings through intake.accept, the only file it writes, and
says so ("... is aangenomen. De assistent leest het bij de volgende ronde, binnen een minuut; wat ermee gebeurt, komt dan vanzelf in
Laatst verwerkt.") or why not, in a sentence of its own for a type the scan does not read, a spreadsheet while the export's columns
are not set and an empty file, with the file's name shown as it is (_literal: no mark of it is read as Markdown or HTML); the field
is emptied after each press. One press gives one file: a second press with the same upload (a double click) or with the field the
press before it emptied changes nothing, its note included; a press without a file when none came before it says "Kies eerst een
bestand.". What the tab keeps for the session (a gr.State) is the upload given last and whether the note says that the list failed.
Under it, "Laatst verwerkt" with "Lijst vernieuwen" and a table of the last lines of <cases_root>/intake.jsonl, the newest first: the
time ("12-11-2026 18:48"), the file and what the runner did with it, each outcome by its fixed label and never an error's text, and
behind a line that opened a case what the case is now ("Zaak geopend voor inkooporder 250105. Nu: mislukt.", intake.rows), read again
(files_opened) when the page loads, when the tab is selected and with "Lijst vernieuwen", and after each press. The page's timer reads
the table again every TICK_SECONDS seconds (files_ticked), so that what the runner did with a file shows without a press; that
handler runs with every other read of the table as well, and shows the table while it has a line and in its place a sentence while
it has none ("Nog geen bestanden verwerkt. Wat de assistent met een bestand doet, komt hier vanzelf."). It gives neither the field,
the note nor what the tab keeps, and writes nothing. An intake file that cannot be read leaves the table as it was, with "Dit is niet
gelukt (<the class of the error>).", which the next read of files_opened that succeeds clears. The language switch relabels the tab
(files_relabel) and clears the note; while the intake file cannot be read the table stays as it was, in the language it was read in,
and the note says so. On a public share link and without a settings file the tab offers nothing, as the settings tab does, and
nothing of it is on the timer.

The settings tab ("Instellingen") holds, under the line of a settings file that cannot be read (below), its five sections in a gr.Tabs
of its own (settings-sections), the buyer's things first and the installer's after (finding 7 of the owner's test): "Uw regels" (its
id "regels"), chosen when the page opens, "Leveranciers" ("leveranciers"), "Postvak en AI" ("koppelingen"), "Koppeling met Exact"
("exact", with the block's id exact-block) and "Stand van de assistent" ("assistent"). A click on a section's name opens it, and so
does the button beside the strip of the tab "Beoordelen" for the section that handles the problem it names (strip_acted, _SECTIONS);
the language switch relabels the five. Each save of the tab names what it saves: "Regels opslaan", "Leverancier opslaan", "Postvak
opslaan", "Opmaak opslaan", "Nummerherkenning opslaan", "Exportkolommen opslaan" and "Artikelkolommen opslaan". "Uw regels" shows the
buyer's settings as a form (aid/review/buyer_settings.py has what it shows and writes): in four groups, each field with its label and
a help text that says what the rule does, without a rule code or a setting's name: "Wat de assistent zelf mag afhandelen" (the two
price thresholds, the days a delivery date may pass the supplier's lead time, the first cases of a supplier that always come to the
buyer), "Vragen aan leveranciers" (the wait for an answer, the name under the messages), "Dagoverzicht en taal" (daily at a time or
after every case, the time, the buyer's address, the language) and "Fiattering" (the approver's address, and a line that the limit
itself is set at the installation), under "Regels opslaan". "Leveranciers", whose label names it in place of its heading, which stays
hidden: a table of every supplier the four supplier settings name, and a form for one supplier, chosen from a dropdown, which fills
the form, or added with its choice "Leverancier toevoegen", with "Leverancier opslaan" and "Leverancier verwijderen".

The form shows the file, and a save writes only what the buyer changed. The eleven fields, the table and the dropdown are filled
from the settings file when the page loads and each time the settings tab is selected (buyer_opened), so that a change made in the
file by hand or from another browser tab shows. Two states of the page keep what the forms were filled with: the eleven texts,
and the supplier form with the supplier it was filled for. "Regels opslaan" writes the fields whose text differs from the text the form
was filled with, and no other: a field the buyer changed wins over a change made in the file meanwhile, a field he did not touch
is never written and not checked, and with no field changed nothing is written and the file keeps its modification time. A
refused field writes nothing and is named by its label with its problem ("1.000" for a thousand is refused: a percentage and an
amount take two decimals at most). After a save the fields show the values as the file now holds them. A supplier save builds
the four supplier settings from the file as update_settings_file reads it inside its lock (aid/settings.py), so that two saves
pressed at the same moment both land, and changes of a listed supplier only the values the buyer changed; a listed supplier's name
is used as the file has it (also when another browser tab took the supplier out of the file meanwhile, so that the save adds it
again under that name), a typed name trimmed. Three saves of a supplier write nothing and say why: a typed name that is
listed already (the buyer is told to choose that supplier in the list), a new supplier with a name and nothing else, and a listed
one with nothing changed. When the page no longer knows what a form was filled with (the page was started again while a browser
tab stayed open), it fills the form again from the file and writes nothing. The note of a supplier save is no output of the
dropdown's change, which a save causes itself: only the buyer's own choice in the dropdown (its input event) clears it.

When the settings file cannot be read (it is missing, locked, no JSON, or holds a value the settings refuse), the tab shows one
fixed line at its top that says so, naming the fields (a field of the tab by its label, any other setting by its name) and never a
value; the form then shows the settings in use, those the page's own actions run with, and every save of the tab (the main one,
the suppliers', the one of the purchase order's layout, the one of the export's columns, the mailbox's and the reader's) refuses with
that line and writes nothing, the one of the columns of the export of articles included.
The page's own actions (a close, an undo) use the settings a save wrote at once, and the settings of the file whenever the file
changed on disk, looked at by its modification time when the review tab opens or shows a case, before a close or an undo, and each
time the settings tab reads the file (_Live); a file that became invalid keeps the settings in use. The authorization limit, the
folders, the SMTP server, the tokens and the reserved precheck_suppliers are not on the tab: they are set at the installation. The
three keys of a write route into Exact (exact_base_url, exact_endpoints, exact_fields) are not on it either: no command builds
that route, and a file in which exact_base_url is set is not used, which the tab says with a line of its own
("settings_file_write_route").

The mailer of a close. A page built with `environ` (the review command gives it the environment of its process) sends the questions
and the requests of a close the way the runner sends its own: for each close it makes the mailer that the setting send_via names,
with mailer_of of aid/messaging/microsoft_mailer.py, the one builder the runner makes its mailer with, from the settings as they are
at that close, the clock of the page's Deps and the Microsoft account of the settings' tenant and client, the one the settings tab
signs in with. So a sign-in or a save on the settings tab counts for the next close, without a restart. A message that is not
sent leaves the case in review with a review item of its own, as the close records it (aid/review/close.py), and close_text says
"Een bericht is niet verzonden; de zaak blijft ter beoordeling." and then the kind of its cause with what to do, by a fixed
sentence that holds no address and no text of a server: the sign-in has lapsed; the mailbox or the mail server was not reached,
or refused the message; no mailbox is connected, or the mail server's password is not set (the settings name a way to send that
is not there: nothing is written as a file in its place); the case's document cannot be read at that moment; or the settings
file cannot be read at that moment, in which case the page sends nothing rather than by the settings it read before
(_Live.sending). With send_via "file", the owner's choice for a run in which nothing may leave, the message is written to
<cases_root>/outbox and counts as asked, and close_text says so after the status, with the folder the mailer writes into: "Het
bericht is niet verstuurd maar als bestand klaargezet (cases/outbox): de instellingen laten berichten als bestand schrijven." A
message whose hand-over the mailbox did not confirm counts as asked, and close_text says to look in Sent items. A page built
without `environ` (a test's, the notebook's) keeps the mailer of the Deps it was given, and so does a page on a public share
link, which never makes a mailer: it is for the made-up set only. Nothing about a case made from a try-out document is sent by
either: the close asks no mailer for it (aid/tryout_mark.py).

What the page says after a close stays on the screen. A close that takes the case out of the review empties the case list, and
that change runs the function that shows the chosen case (show): with no case chosen it leaves the status line alone.

The choices shown. The radios of a case show the decisions that stand on its items (standing_decisions of aid/review/close.py):
the last decision of each item, without a "Leverancier vragen" whose question has ended (answered, bounced, or without an answer
in time), also one chosen again while that question was out. Such an item shows no choice, and what the buyer chooses then is a new
decision, also "Leverancier vragen" again. "Leverancier vragen" is offered on an item with a finding, and on the item of a bounce
or a silence of a question the run asked itself ("Bericht kwam terug", "Geen antwoord binnen de wachttijd"), where it asks that
question again (the state's "again", ruling C4 of the clean-up of Task 44).

The section "Koppeling met Exact", after "Postvak en AI", holds the block of the connection to Exact (aid/review/exact_settings.py
has what it shows and writes; _exact_section builds it). It shows the state of the
connection ("Stand van de koppeling": whether the layout of the purchase order is set, the POs the aid knows and the one filed last,
whether the columns of the export are set, when the last export was filed, the files of the watched folder it could not read, and the
number of cases that wait for their PO), read again with "Stand vernieuwen" and each time the tab is selected. Its part 1,
"1. Inkooporders die Exact per e-mail stuurt (pdf)", sets the layout of the PO PDF that Exact e-mails and the senders of those e-mails
(the settings po_layout and po_senders) from one sample PDF of the company's own purchase order. Choosing the sample shows its first
page line by line, the first PAGE_LINES lines with a line under the table when the page has more; "Kopregel zoeken" finds the title row
by one of its words and shows, for each of its words, one of SLOTS dropdowns made once, labelled with the word; "Testen" reads the
sample with the layout of the form and writes nothing; "Opmaak opslaan" reads it the same way and writes the two settings with
update_settings_file, or writes nothing and names each problem. Both show the reading below: the header values and the order lines as
two tables, and a line for each problem and each warning of the reader, never the total or the authorization limit; the summary above
the tables only when there is no problem. The sample field takes any file, so that the page library refuses none in its own words: a
file that is no PDF by its name, and a sample that cannot be opened, are each named by a sentence of their own in each of them. The sample
is read where the page library put it when it was chosen, its temporary folder (GRADIO_TEMP_DIR, which the review command sets to
<cases_root>/tmp, aid/review/__main__.py), and is copied nowhere; nothing is filed from it. The block is filled from the settings file
when the page loads and each time the tab is selected, so that it opens with the saved layout and senders (a saved title row of more
than SLOTS words, which the form cannot show, is left out with a line that says so), and keeps what it was filled with: a save from a
form whose two settings the file no longer holds, because another browser tab saved meanwhile, writes nothing and says so. At the end
of part 1 two fields set how a confirmation's PO number is found, the setting po_number_pattern (pattern_from and pattern_parts of
aid/review/exact_settings.py): the words after which suppliers print it, and the fewest and the most digits, with "Nummerherkenning
opslaan", under a heading of their own ("Hoe de assistent het inkoopordernummer op een orderbevestiging vindt"), so that they do not
read as part of the reading of the sample above them. They are filled from the file at the load and at each select of the tab, a
pattern of another form as empty fields with "eigen patroon (in het instellingenbestand)" standing in the field of the words, and a
gr.State keeps the pattern they were filled with. The save writes po_number_pattern and nothing else through update_settings_file: fields as they were filled write nothing, so an
own pattern is left alone while they are empty; a refused field writes nothing and is named by its label with its problem; and a file
whose pattern changed after the fill writes nothing and gives the fields' own note ("Niet opgeslagen: de nummerherkenning is intussen
op een andere plek gewijzigd. ...").

Its part 2, "2. Inkooporders als export uit Exact (voor de controle voor het bestellen)" (_export_part), sets the export of order lines
that a cart is checked against before ordering, from one sample export of the company, an .xlsx or .csv file: the column of each field
(the setting export_columns), the convention of the numbers written as text (export_decimal_comma) and the tick that the PO number
stands on the first line of each PO only (export_fill_down). Choosing the sample shows its first rows, each with its row number, and
fills "Rij met de kolomnamen" with the row found and one dropdown for each field of EXPORT_FORM with the titles of that row beside
"(staat niet in het bestand)"; a column chosen before stays chosen. Typing another row offers that row's titles; the row field has no
minimum, and an empty field, 0 or a negative number lets the row be found again. One read of the sample gives its first rows and its
titles, and is kept for the same file (its path, size and modification time) and row, so that a select of the settings tab or a
language switch reads the sample no more. "Testen" reads the sample once with the three settings of the form and writes nothing;
"Exportkolommen opslaan" reads it the same way and writes the three with update_settings_file, or writes nothing and names each
problem. Both show
below the buttons what the trial read says and the lines of the first order and, with two orders or more, of the last order: the order
of the file's last order line, where with the tick a total row at the end of the file joins, and the PO the file names last when that
is yet another (exact_settings.export_lines). The sample field takes any file, so that the page library refuses none in its own words:
a file that the export reader refuses as a whole (another type than .xlsx and .csv included), or that cannot be opened, is named by a
sentence of its own, and the sample is read where the page library put it, as in part 1. The part is filled from the settings file when
the page loads and each time the tab is selected, also without a sample (the dropdowns then hold the saved titles), and keeps what it
was filled with: a save from a form whose three settings the file no longer holds writes nothing and says so.

Its part 3, "3. Inkoopprijzen van artikelen als export uit Exact (voor de prijslijst)" (_articles_part), sets the export of a
supplier's articles that the tab "Prijslijst" takes, from one sample of it, as part 2 sets the export of order lines: the column of
the item code, of the purchase price and, when the export names the supplier of each row, of the supplier (the setting
article_columns), and the convention of the numbers written as text (article_decimal_comma). Choosing the sample shows its first rows
and fills "Rij met de kolomnamen" and the three dropdowns with the titles of that row, as in part 2; of a workbook the one sheet
the buyer sees is read, as the tab "Prijslijst" reads the export itself, and a workbook with more than one visible sheet with
data is refused by the names of those sheets. "Testen" (articles_tested) reads
the sample once with the two settings of the form and writes nothing; "Artikelkolommen opslaan" (articles_saved) reads it the same
way and writes
the two with update_settings_file, or writes nothing and names each problem. It stores no price: the export itself is given on the
tab "Prijslijst". Both show what the trial read says ("12 artikelen gelezen; 3 rijen overgeslagen (zonder artikelcode: 2, prijs niet
te lezen: 1).", a count of one in the singular) and a table of the first articles as read. The part is filled from the settings file
when the page loads and each time the tab is selected, and a save from a form whose two settings the file no longer holds writes
nothing and says so, as in part 2.

The block ends with the part "Proefdraaien met een inkooporder uit Exact" (_tryout_section; aid/tryout.py makes the try-out): the
switch "Proefstand: de assistent werkt met het test-Exact", which writes the setting test_mode and nothing else as soon as the buyer
ticks or clears it ("Opgeslagen.", and when it turns the test mode off while the list "In Exact invoeren" holds changes that only
try-out documents asked, how many, to be dropped and not entered in the real Exact) and, as the block's form, writes nothing when the
file no longer holds the value it was filled with (another browser tab, or the file itself, changed it), filling itself again from
the file and saying so in a sentence of its own; and, only while test_mode is true and no authorization limit is set
(offers_tryouts of aid/tryout_mark.py), a try-out confirmation of a purchase order the store knows ("Inkooporder", known_pos, the one
filed last first) with one change ("Wat wijkt af?", aid/tryout.py's CHANGES). Otherwise the part shows one line that says why. Each
try-out is read back before it leaves the page (checked_confirmation of aid/tryout.py), and one that would not read back as the
order with the change alone, or whose order the scan would not find, is refused. "Proefbevestiging maken (pdf)" writes the PDF into a
folder of its own in the page library's temporary folder, the one the download is served from, so that it is the one file written
and goes as the library's other temporary files go; "Proefbevestiging direct aan de assistent geven" puts it into the watched folder
of the settings (drop of aid/sources/dropin.py, from a copy under <cases_root>/tmp/work that goes again at once), for the runner's
next pass, and clears the download, as a new choice of the order or of the change by the buyer does, so that the file offered is
always the one of the choice shown. A purchase order that gives no try-out is named by the sentence of its reason, and nothing is
written. The part is filled when the page loads, each time the tab is selected and with the block's "Stand vernieuwen", which keeps
the order chosen while the store knows it; a button pressed with no order chosen fills the dropdown from the store first and takes
the order filed last. The language switch keeps the note that the store knows no purchase order. A try-out's moment of making, which
gives it its date, its title and the time in its file's name, is the clock's of the page's Deps.

Under the download the part offers a try-out cart for the pre-check in the browser, with a choice of its own under a question of
its own, "Wat wijkt af in de proefwinkelwagen?": "Niets" or the price of the first line 1 percent higher (aid/tryout.py's
CART_CHANGES); the part's introduction says that the assistant makes a made-up cart of the same order. "Proefwinkelwagen maken"
makes the cart page of the order chosen as it stands in the store, which is what a cart is compared with, reads it back
(checked_cart: refused, with the sentence of its reason and nothing written, unless it reads back as the order with the change alone
and the pre-check, asked at that moment, would find its order), writes it as <cases_root>/tryout/<PO number>_<change>.html
(write_cart) and says where to open it: the note holds the link to the page on the capture server of this PC, which serves it while
try-outs are offered (aid/capture_server.py). The moment of making is that of the page's clock, and its day stands on the page.

The review tab answers "Leverancier vragen" on a case made from a try-out document with "Dit is een proefdocument: de assistent stuurt
hierover niets naar een leverancier." (decision_text; decide of aid/review/app.py refuses it), and close_text does not count a message
that the aid does not send about such a case as a message not sent.

The section "Postvak en AI" (its label names no installation, because a sign-in that has lapsed sends the buyer there, as the
digest's two lines say) connects the work mailbox through the buyer's own Microsoft sign-in
(aid/connections/). The part shows its state first: the signed-in account, read from the token cache only, so that opening the tab
asks Microsoft nothing, and under "Stand van het postvak" the state of the trigger, from mailbox_state.json
(aid/sources/microsoft_mail.py), its times in the local time of the PC; both are read when the page loads and again each time the
section is selected (connections_selected). Five numbered steps follow (MAIL_STEPS), each group of buttons with its answer line right under
it (finding 1 of the owner's test): "1. Gegevens van de app-registratie", the Directory (tenant) ID and the Application (client) ID
of the app registration; "2. Aanmelden bij Microsoft", where "Aanmelden bij Microsoft" opens Microsoft's sign-in page in a browser
window on the PC where the page runs, as the tab says, and says that it waits for it, "Aanmelden met een code" shows a code and an
address and then waits until the code has been entered, and "Afmelden" signs out, the three answering on settings-sign-note; "3. Map
van het postvak", the mailbox (empty: the account's own; another mailbox signs in with the shared scopes), the folder, chosen from the
mailbox's folders once signed in ("Mappen ophalen" lists them again, for another mailbox, and a folder listed for another mailbox than
the one in the field is not saved), and "Verbinding testen", which shows how many messages the folder holds and when the newest
arrived, never a subject or a sender, or that the sign-in has lapsed (as "Mappen ophalen" does), the two answering on
settings-folder-note; "4. Verzenden", how the aid sends (send_via), and "Postvak opslaan", the one filled button of the part, which
validates the six settings and writes them into the settings file with update_settings_file (aid/settings.py), and the runner takes
them over at its next pass (this part is filled when the page loads, and its save gives all six to update_settings_file, which writes
those that differ from the file's); a save that turns the trigger off or names another folder removes the stored connection
(forget_connection), so that a folder connected again later is read from that moment; and "5. Documenten lezen", the AI section below.
The tab never shows a token of the sign-in, and an error by its code ("invalid_grant"), its status ("HTTP 404"), its class, a refused
field by its label and a browser sign-in that timed out by a fixed line. A store of the sign-in that the PC refuses is said in words,
with what to do (finding 2 of the owner's test, _refusal_text): a store inside a git work tree by the store's folder and the folder that
holds .git, a store made by another Windows user or in another kind of session by its folder, each shown as it is, and any other failure
to make the account by a fixed line. The refusal of a store inside a git work tree stands on the line under the sign-in buttons before
any press, from the moment the page loads and each time the section is selected (store_checked, which work_tree_refusal tells without making
anything), and so does the refusal of a store of another kind, which making the page's account of the saved IDs tells, as the account line
makes it, while the account line says "Niet aangemeld", so that it stands once; the buttons of the other steps, and the choice of the
reader, then point to that line ("Aanmelden kan niet: onder stap 2, Aanmelden bij Microsoft, staat waarom."). The language switch
relabels the five steps and empties the new answer lines, but for that refusal, which it gives in the language (sections_relabelled).
On a page opened on a public share link the tab says only "Niet beschikbaar op een openbare link" and offers nothing, no folder
included, and on a page built without its settings file (the Colab notebook) it offers nothing either.

The AI section of the settings tab, step 5 of "Postvak en AI", is first the line of what reads the documents now (reader_shown): the
parser or Copilot, and Copilot failing as a whole since when, with its cause by a status code and the cases that wait for it, or
refusing documents, from the runner's digest state and the case folders (aid/review/status.py), read when the page loads, when the
section is selected, at the language switch and after each choice. Then "Documenten lezen met", Copilot or "zonder AI (parser)", writes
the setting reader ("llm" or "parser") into the settings file as soon as it is chosen, as "Postvak opslaan" writes the six settings,
and answers on settings-reader-note right under it. Copilot is written only once the tenant and the client are saved, and only once
Copilot answered the test message of "Copilot testen", sent with them (finding 6 of the owner's test): a refusal writes nothing,
keeps the reader of the file and says why by the status code with a hint (400: most often no licence or a personal account; 401: sign
in for Copilot first; 403: a permission or a consent missing), never by Copilot's own words. It is the one choice of the page that
sends something to Microsoft, at the buyer's own press, and never when the page loads or its language changes. Then Microsoft 365
Copilot (aid/readers/copilot_client.py), under a short text of what Copilot needs: "Aanmelden voor Copilot" signs in in the browser as
"Aanmelden bij Microsoft" does, saying first that it waits, with the permissions of the mailbox in the field (mail_scopes) and of
Copilot together, and "Copilot testen" sends Copilot one message, "Answer with the word OK only.", and shows "Copilot antwoordt" with
the first 20 characters of the answer, or the error of the client by its text, which names a status and Graph's error code ("Copilot
refused: 403 Forbidden") and never a token or a message of Microsoft; the two answer on the line under them.

The part ends with "Code voor de uitbreiding in de browser": the code the browser extension sends to the capture server
(capture_token of the settings file), one line that cannot be edited, with a button to copy it, filled from the settings file each
time the page loads, for the buyer to paste into the extension's options. Like the rest of the tab it is on the page of this PC only:
a page on a public share link, or without its settings file, has none.

The last section, "Stand van de assistent" (_assistant_section, finding 8 of the owner's test), says what the assistant is doing, read
from the files of this PC by aid/review/status.py and never from Microsoft, each time the section is selected, at a press of "Stand
van de assistent vernieuwen" and after a press of the strip's button (assistant_shown): a line each for the runner's last pass (the
modification time of its digest state), or since when it made none once status.PASS_LATE passed without one, or that it made none yet;
whether the launcher runs (its heartbeat), since when and how often it started the runner; a STOP file next to the settings file; a
runner the launcher gave up; the mailbox as "Postvak en AI" tells it, and since when its sign-in has lapsed; what reads the documents
(_reader_text, the line of reader_shown) with the purchase orders that wait for Copilot; and a store of the sign-in that the PC
refuses (_assistant_lines). A line that tells one of the problems of status.problems, the problems the review tab tells too, is in
bold. Under them the last status.LOG_LINES lines of <cases_root>/runner.log stand as the runner wrote them, the newest first, in a box
that cannot be edited (a box and no Markdown, so that an underscore of a case id stays as it is), with a line that says that they are
for the researcher and stay on this PC: the runner's own words, the one place of the page that shows them, on the page of this PC
only. A file that cannot be read leaves the section as it is, and the language switch gives it in the language, its lines and its log
read again (sections_relabelled).
"""
import asyncio
import hashlib
import io
import json
import math
import re
import tempfile
import threading
import warnings
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import NamedTuple, get_args

from msal.oauth2cli.oauth2 import BrowserInteractionTimeoutError

from aid.case import TEMPORARY, WORK, Case, load_case, sha256_file, work_copy_folder
from aid.connections.microsoft import COPILOT_SCOPES, MicrosoftAccount, SignInFailed, SignInNeeded, mail_scopes
from aid.connections.secrets import LazySecretStore, SecretStore, StoreRefused, default_secrets_dir, work_tree_refusal
from aid.execution.by_hand import FIELDS, FOLDER, Change, ListUnread, changes, mark, open_changes, reopen
from aid.execution.ops import WriteOp
from aid.execution.writer import LOG_FILE as WRITE_LOG
from aid.execution.writer import WriteLog, WriteLogEntry, WriterError, read_before
from aid.messaging import FileMailer, Mailer, NoRoute
from aid.messaging import _append_line           # a line appended in one write, a line a crash cut off ended first
from aid.messaging.microsoft_mailer import MicrosoftMailer, mailer_of
from aid.pipeline import LOOKS, NOT_SENT, RESULT_FILE, SENT, UNADDRESSED, UNSENT, CaseResult, Deps, asked_by_the_run
from aid.po_store import PoStore
from aid.pricelist import (NameRefused, NoArticle, also_held, current_prices, is_supplier_name, keep_columns, kept_columns, others_asking,
                           remove_stored, store_current, stored_prices, supplier_named, with_others)
from aid.readers.copilot_client import UNREACHABLE, CopilotClient, CopilotError
from aid.readers.llm_client import ReaderError
from aid.readers.po_export import ExportRefused
from aid.review import hand_list, intake, pricelist_page
from aid.review import status as aid_status      # the module: the page has a status line of its own
from aid.review.app import (CAPTURE_FILE, DECISIONS_FILE, CaseSummary, Decision, EnteredValue, FlagRow, LineMark, NothingAsked, _context,
                            _pages, _saved_result, _summary, decide, load_case_view, marked_replaced)
from aid.review.buyer_settings import (BUYER_FIELDS, SUPPLIER_FIELDS, changed_fields, parse, shown, supplier_changes, supplier_names,
                                       supplier_rows, supplier_shown, supplier_update, without_supplier)
from aid.review.close import _records, close_case, standing_decisions, undo, waiting_for_po
from aid.review.exact_settings import FORM as EXACT_FORM
from aid.review.exact_settings import (ARTICLE_FORM, EXPORT_FORM, MEANINGS, NO_COLUMN, PAGE_LINES, SLOTS, ArticlesTrial, ExportTrial, NotPdf,
                                       SampleUnreadable, Trial, article_label, articles_from_form, articles_headers, articles_rows,
                                       articles_text, articles_trial, as_setting, export_from_form, export_headers, export_lines, export_text,
                                       export_trial, field_label, form_from_articles, form_from_export, form_from_layout, head_headers,
                                       layout_from_form, line_headers, line_widths, page_lines, pattern_from, pattern_parts, reading_shown,
                                       refusal_text, sample_head, saved_articles, saved_export, saved_values, senders_from, state_lines,
                                       title_words, trial, typed_words)
from aid.review.i18n import STRINGS, reason_label
from aid.review.look import TONES
from aid.settings import Settings, SettingsInvalid, WriteRouteSet, is_client_id, is_tenant_id, load_settings, read_settings, update_settings_file
from aid.sources.dropin import drop
from aid.sources.mailbox import PROCESSED_FOLDER, attachment_of
from aid.sources.microsoft_mail import GraphError, connection_of, folder_summary, forget_connection, list_folders, trigger_on
from aid.tryout import (CART_CHANGES, CHANGES, TryoutRefused, as_compared, cart_link, checked_cart, checked_confirmation, file_name,
                        known_pos, made_from, write_cart)
from aid.tryout_mark import TRYOUT_NOT_SENT, case_is_tryout, offers_tryouts

_REVIEW_ITEM = re.compile(r"RI\d+")              # the ids of the review items (aid/pipeline.py)
# The ids of three tabs; "In Exact invoeren" has hand_list.TAB and "Prijslijst" pricelist_page.TAB.
REVIEW_TAB, FILES_TAB, SETTINGS_TAB = "beoordelen", "bestanden", "instellingen"
_ONE_PER_LINE = ".hand-rows .wrap { flex-direction: column; align-items: flex-start; }"    # the tick boxes of that tab, one under the other
# The two tables of part 2 of the block "Koppeling met Exact": a column title breaks between its words only, never inside one ("Rij" stays
# whole), so that a table wider than the block scrolls sideways inside it, as the page library's table does when it is wider.
_WHOLE_TITLES = "#export-head th, #export-head th *, #export-lines th, #export-lines th * { word-break: normal; overflow-wrap: normal; }"
# The step of the browser after "Bekijken" on the tab "Prijslijst": once the view is drawn, the count line under the table stands in the
# middle of the window, with "Op de lijst In Exact invoeren zetten" right under it. It runs in the page alone and names no address.
_COUNT_INTO_VIEW = '() => { requestAnimationFrame(() => document.getElementById("pricelist-note")?.scrollIntoView({block: "center"})); }'
SEND_VIA = get_args(Settings.model_fields["send_via"].annotation)     # the values of send_via, in the order the tab offers them
READERS = get_args(Settings.model_fields["reader"].annotation)        # the values of reader: "llm" (Copilot) and "parser"
COPILOT_TEST = "Answer with the word OK only."                         # the message of "Copilot testen"
MAIL_STEPS = ("ids", "sign_in", "mailbox", "send", "read")             # the five numbered steps of "Postvak en AI" ("mail_step:<step>")
_STATUS_CODE = re.compile(r"\b[1-5][0-9]{2}\b")  # a status code of HTTP in an error of Copilot: three digits, standing on their own
# The label on the tab of each setting the tab writes, by which a refused field is named, never by its value: the six of the mailbox,
# the reader, the eleven of the buyer's form, the four supplier settings by the heading of the suppliers, the three fields of the
# supplier form (the keys of with_supplier's problems), the layout of the purchase order and its senders of the block "Koppeling met
# Exact", the three settings of the export of its part 2, the two of the export of articles of its part 3, and the test mode by its
# switch at the end of that block.
FIELD_LABELS = {"ms_tenant_id": "settings_tenant", "ms_client_id": "settings_client", "mailbox": "settings_mailbox",
                "mail_folder": "settings_folder", "mail_folder_name": "settings_folder", "send_via": "settings_send_via",
                "reader": "settings_ai_reader"} | {field: f"settings_field:{field}" for field in BUYER_FIELDS} | {
                field: "settings_suppliers" for field in SUPPLIER_FIELDS} | {
                "supplier_name": "settings_supplier_name", "supplier_email": "settings_supplier_email",
                "supplier_lead_time": "settings_supplier_lead_time"} | {"po_layout": "exact_layout", "po_senders": "exact_field:senders"} | {
                "export_columns": "exact_export_columns", "export_decimal_comma": "exact_export_field:numbers",
                "export_fill_down": "exact_export_field:fill_down", "test_mode": "tryout_switch"} | {
                "article_columns": "exact_articles_columns", "article_decimal_comma": "exact_export_field:numbers"}
NEW_SUPPLIER = "(new supplier)"                  # the value of "Leverancier toevoegen" in the dropdown of suppliers
# The supplier form as "Leverancier toevoegen" fills it: what a new supplier's values are compared with.
NEW_FORM = {"name": NEW_SUPPLIER, "email": "", "lead_time": "", "review": False, "off": False}
# The groups of the buyer's form, top to bottom, each with its fields in the order of the page.
BUYER_GROUPS = (("handled", ("price_pct", "price_abs", "lead_time_tolerance_days", "first_cases_review")),
                ("questions", ("ask_wait_working_days", "buyer_name")),
                ("digest", ("digest", "digest_time", "buyer_email", "language")),
                ("approval", ("approver_email",)))
_COLUMNS = ("item", "field", "line", "po_value", "doc_value", "reason", "po_source", "doc_source")
# The width of each column of the three lists, in whole percents that make 100 together, so that a list fits the page and its cells
# wrap inside their columns: a long reason (the guess's) never pushes the two sources out of view.
_WIDTHS = {"item": 5, "field": 9, "line": 6, "po_value": 10, "doc_value": 10, "reason": 24, "po_source": 18, "doc_source": 18}
COLUMN_WIDTHS = [f"{_WIDTHS[column]}%" for column in _COLUMNS]
_ENTERED = {field: kind for kind, field in FIELDS.items()}        # the kind of change of each field of an entered value, for its form
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")       # a number in normal form (aid/schema.py), which a row shows with the language's mark
_SOURCE = re.compile(r"p([0-9]+) r([0-9]+): ")     # the page and the line at the head of a source, as the view cites it (aid/review/app.py)
# The fields of "Uw regels" that hold a number or a time, which the style rules of aid/review/look.py keep short (short-field).
_SHORT_FIELDS = ("price_pct", "price_abs", "lead_time_tolerance_days", "first_cases_review", "ask_wait_working_days", "digest_time")
# The strip of the tab "Beoordelen" (see the module docstring): the states it counts, in their order, each with the tone of its chip
# (a key of TONES of aid/review/look.py, whose style rules give each tone's class its colours), "look" for the documents the intake
# left to the buyer that wait for him; the tone of each state in the fold's table, a document's two states among them; the place
# that handles each problem of the assistant (aid/review/status.py), which names the strip's button ("act:<place>"); and the section
# of the settings tab that is that place, which the button opens: the id of its gr.Tab in settings-sections (_settings_tab,
# _assistant_section).
_CHIPS = (("review", "attention"), ("failed", "problem"), ("look", "attention"), ("copilot_wait", "waiting"), ("waiting", "waiting"),
          ("closed_itself", "done"))
_TONE = {"review": "attention", "waiting_po": "waiting", "failed": "problem", "handled": "done", "copilot_wait": "waiting",
         "reading": "waiting", "waiting": "waiting", "closed_itself": "done", "closed_by_you": "done", "look": "attention", "looked": "done"}
_PLACES = {"stopped": "assistant", "given_up": "assistant", "no_pass_ever": "assistant", "no_pass": "assistant", "store": "connections",
           "lapsed": "connections", "copilot_down": "connections", "copilot_refused": "connections"}
_SECTIONS = {"assistant": "assistent", "connections": "koppelingen"}
_CLOSED = ("closed_itself", "closed_by_you")
TICK_SECONDS = 30                                # how often the page reads the strip, the count and the case list again
# A line of a document that a difference was read from, as the page marks it (_marked): its box this many pixels wider on every side,
# and an edge this many pixels wide.
MARK_PAD, MARK_EDGE = 3, 2
WHEN_FORM = "%d-%m-%Y %H:%M"                     # a time of the mailbox on the tab "Postvak en AI" (_when), the day first as below
DAY_FORM = "%d-%m-%Y %H:%M"                      # a time in the section "Stand van de assistent" (_day_time), the mailbox's too, and
                                                 # the time of a case or a document in the case list of "Beoordelen" (_day)
# The files of a case folder whose offer offered_cases tells by (case.json first), and what it read of each folder at its last call,
# by cases root, by folder: the stamp of each of those files (_stamp), and the case as offered then (None: not offered); the folders
# of the last aid_status.CACHED_ROOTS cases roots are kept.
_OFFER_FILES = ("case.json", f"steps/{RESULT_FILE}", WRITE_LOG, CAPTURE_FILE)
_OFFERED: dict[Path, dict[str, tuple[tuple, CaseSummary | None]]] = {}
_OFFERED_LOCK = threading.Lock()                 # the page reads in threads of its own


def page_rows(rows: list[FlagRow], language: str) -> list[list[str]]:
    """The rows of a list as the page shows them in the language: the item, the field, the line, the two values, the reason and the
    two sources. Fields, reasons and the kinds of matched pairs are shown by their labels in i18n.STRINGS, never as their text, which
    may carry a rule code; a reason without a label shows as "other reason". The values stand in the form of the language (_value),
    a price as 12,50 in Dutch and a day as 16-11-2026, as on the tab "In Exact invoeren"; the sources as the line was read."""
    strings = STRINGS[language]
    return [[row.item_id, _field(strings, row.field), row.line, _value(row, row.po_value, language), _value(row, row.doc_value, language),
             _reason(strings, row.reason), row.po_source, row.doc_source] for row in rows]


def entered_text(entered: list[EnteredValue], unread: bool, language: str) -> str:
    """The line above the lists of a case, as Markdown in the language (see the module docstring): the values entered in Exact that
    the case was compared with, each named by its line and its kind, with its value as the tab "In Exact invoeren" shows a value (a
    price or a quantity with the decimal mark of the language, a day as dd-mm-yyyy, a week by its number), joined by semicolons; for
    a case that ran while the list could not be read (unread), the sentence that says so; "" for any other case."""
    strings = STRINGS[language]
    if unread:
        return strings["entered_unread"]
    named = [strings[f"entered:{one.field}"].format(line=hand_list._markdown(one.line),
                                                    value=hand_list._value(one.value, _ENTERED[one.field], language))      # the tab's own form
             for one in entered if one.field in _ENTERED]
    return strings["entered_values"].format(changes="; ".join(named)) if named else ""


def _field(strings: dict[str, str], field: str) -> str:
    return strings.get(f"field:{field}", strings["reason:other"]) if field else ""


def _value(row: FlagRow, value: str, language: str) -> str:
    """A value of a row as the page shows it in the language: the kind of a matched pair (matched, split, merged) by its label, and
    any other value in the form of the language (_formed)."""
    if row.field == "line_presence":
        return STRINGS[language].get(f"pair:{value}", value)
    return _formed(value, row.field, language)


def _reason(strings: dict[str, str], reason: str) -> str:
    """A reason by its label, or by the label of the reason it begins with (a key ending in " *")."""
    if not reason:
        return ""
    label = reason_label(reason, strings)
    return label if label is not None else strings["reason:other"]


def _write_words(op: WriteOp, strings: dict[str, str]) -> str:
    """A write in words: its kind and its line or article."""
    words = strings[f"kind:{op.kind}"]
    if op.line_id:
        return f"{words}, {strings['line_word']} {op.line_id}"
    return f"{words}, {strings['article_word']} {op.article_code}" if op.article_code else words


def _case_label(summary: CaseSummary, strings: dict[str, str]) -> str:
    """A case in the dropdown, in one grammar (spec 4.6): its order and supplier, its number of items and the time it was opened
    (_day), "Inkooporder 250107 (S07): 1 punt, 12-11-2026 09:00", then its status when it is not in review; a case made from a
    try-out has "(proefdocument)" behind it (hand_tryout, the mark of the list "In Exact invoeren"), so that it never looks like a
    supplier's confirmation of the same order opened in the same minute."""
    supplier = f" ({summary.supplier})" if summary.supplier else ""
    items = strings["item_one"] if summary.items == 1 else strings["items_many"].format(n=summary.items)
    status = "" if summary.status == "review" else f", {strings[f'status:{summary.status}']}"
    mark = f" {strings['hand_tryout']}" if summary.tryout else ""
    return f"{strings['po_document']} {summary.po_number or strings['unknown_po']}{supplier}: {items}, {_day(summary.opened_at)}{status}{mark}"


def _day(opened_at: str) -> str:
    """The time a case was opened, as its case.json holds it, as the case list shows it: dd-mm-yyyy HH:MM in both languages
    (DAY_FORM), as the hand list shows a day; a text that is no time, as it is."""
    try:
        return datetime.fromisoformat(opened_at).strftime(DAY_FORM)
    except ValueError:
        return opened_at


def _failed_label(line: aid_status.CaseLine, strings: dict[str, str], tryout: bool, handled: bool = False) -> str:
    """A failed case in the dropdown, which the buyer has not noted as handled, as _case_label names a case, with "mislukt, zelf
    bekijken" in place of the number of items: "Inkooporder 250105 (S05): mislukt, zelf bekijken, 12-11-2026 17:03", and the mark
    of a try-out; once he noted it as handled (it stays on screen until another case is chosen), "mislukt, door u afgehandeld" in
    place of "mislukt, zelf bekijken"."""
    supplier = f" ({line.supplier})" if line.supplier else ""
    mark = f" {strings['hand_tryout']}" if tryout else ""
    return (f"{strings['po_document']} {line.po_number or strings['unknown_po']}{supplier}: "
            f"{strings['case_failed_handled' if handled else 'case_failed_look']}, {line.opened_at.strftime(DAY_FORM)}{mark}")


def _kept_label(cases_root: Path, case_id: str, strings: dict[str, str]) -> str:
    """A case on screen that left the list (closed in another browser tab, replaced by the runner, a failed case noted as handled),
    named as the list names it now, with the mark of a try-out: as _case_label names a case, its status saying why it left, and a
    failed case as _failed_label names one, as handled once its mark (status.HANDLED_FILE) stands; its id when its folder can no
    longer be read."""
    try:
        meta = json.loads((Path(cases_root) / case_id / "case.json").read_text(encoding="utf-8"))
        summary = _summary(case_id, meta, _saved_result(load_case(cases_root, case_id)))
        tryout = case_is_tryout(cases_root, case_id)
        if summary.status != "failed":
            return _case_label(summary.model_copy(update={"tryout": tryout}), strings)
        opened = _local(datetime.fromisoformat(summary.opened_at))       # as the status module reads it for the list (case_lines)
        line = aid_status.CaseLine(case_id, summary.po_number, summary.supplier, opened, "failed", None)
        return _failed_label(line, strings, tryout, handled=(Path(cases_root) / case_id / aid_status.HANDLED_FILE).exists())
    except (OSError, ValueError, KeyError):
        return case_id


def _item_label(item: aid_status.LookItem, strings: dict[str, str]) -> str:
    """A document the intake left to the buyer in the dropdown, in the grammar of _case_label: "Document MU05_conf.pdf: zelf bekijken,
    12-11-2026 17:03", its file as the list of skipped items names it and the time of the scan that left it, written as
    _failed_label writes a time; once the buyer noted it as handled (it stays on screen until another case is chosen), "door u
    afgehandeld" in place of "zelf bekijken"."""
    return strings["item_label" if item.state == "look" else "item_label_handled"].format(file=item.file, time=item.time.strftime(DAY_FORM))


class _Found(NamedTuple):
    """Where the document of an item lies on this PC (_item_document)."""
    path: Path                                   # the file in processed/: the document itself, or the e-mail that holds it
    attachment: str | None                       # the attachment's name in that e-mail; None for a document that is a file of its own
    data: bytes | None                           # the attachment's bytes; None for a document that is a file of its own


def _item_document(item: aid_status.LookItem, watched: Path) -> _Found | None:
    """Where the document of an item lies on this PC (see the module docstring), found as the scan of the watched folder put it there
    (aid/sources/mailbox.py): a file of its own, named in the item as it was named in the folder, moved to processed/ in the folder
    under its own name, or with _2, _3 and so on before its extension when processed/ held that name already; an attachment, named
    "<e-mail>/<attachment>", inside its e-mail, which moved there in the same way (attachment_of). Of the files of that name, the one
    whose bytes (an attachment's own) have the sha256 that the scan noted in the item's line, whatever the times of the files say;
    for a line written before the scan noted one, the one file of that name, and none of several, as the page cannot tell which of
    them is the document. None when no such file is there, or it cannot be read, rather than another document of the same name."""
    email, slash, attachment = item.file.partition("/")
    name = Path(email if slash else item.file)
    numbered = re.compile(rf"{re.escape(name.stem)}_([2-9]|[1-9][0-9]+){re.escape(name.suffix)}")
    try:
        candidates = sorted(path for path in (Path(watched) / PROCESSED_FOLDER).iterdir() if path.name == name.name or numbered.fullmatch(path.name))
    except OSError:
        return None
    found = []
    for path in candidates:
        try:
            if not path.is_file():
                continue
            data = attachment_of(path, attachment) if slash else None
            if slash and data is None:           # an e-mail of that name without the attachment
                continue
            if item.sha256 is not None and (hashlib.sha256(data).hexdigest() if slash else sha256_file(path)) != item.sha256:
                continue
        except Exception:                        # an e-mail that cannot be read holds no document: the scan reads it as an error too
            continue
        found.append(_Found(path, attachment if slash else None, data))
        if item.sha256 is not None:              # its own bytes: the document itself
            break
    return found[0] if len(found) == 1 else None


def _item_pages(item: aid_status.LookItem, watched: Path, cases_root: Path) -> list[bytes]:
    """The pages of the document of an item, as _pages of aid/review/app.py renders the document of a case: those of a PDF, the bytes
    of a PNG. An attachment is rendered from a copy in a folder of its own under <cases_root>/tmp/work (work_copy_folder of aid/case.py),
    which goes again at once. None for a document that is not on this PC (_item_document), or that cannot be rendered."""
    found = _item_document(item, watched)
    if found is None:
        return []
    try:
        if found.data is None:
            return _pages(found.path)
        with work_copy_folder(Path(cases_root) / TEMPORARY / WORK, "aid_review_item_") as folder:
            copy = folder / found.attachment
            copy.write_bytes(found.data)
            return _pages(copy)
    except Exception:                            # a file that cannot be rendered shows no page, and no error's text
        return []


def review_label(lines: list[aid_status.CaseLine], language: str, items: Sequence[aid_status.LookItem] = ()) -> str:
    """The label of the tab "Beoordelen" for the cases as case_lines of aid/review/status.py gives them, and the documents the intake
    left to the buyer as review_items gives them: the tab's name with the number of cases that wait for the buyer (in review, or
    failed and not noted as handled: NEEDS) and of documents that wait for him, "Beoordelen (3)", and its name alone while none waits.
    The timer's queue_ticked and the relabel of the tabs at a language switch both name the tab by it, so that the label is the same
    whichever of the two runs last."""
    strings = STRINGS[language]
    waiting = sum(1 for line in lines if line.state in aid_status.NEEDS) + sum(1 for item in items if item.state == "look")
    return strings["review_tab_count"].format(n=waiting) if waiting else strings["review_tab"]


def _strip_html(lines: list[aid_status.CaseLine], aid: aid_status.Assistant | None, problems: list[aid_status.Problem], now: datetime,
                language: str, items: Sequence[aid_status.LookItem] = ()) -> str:
    """The strip of the tab "Beoordelen" (see the module docstring) as HTML: a chip for each state it counts, "Ter beoordeling" always
    and any other only while its count is above 0, the documents the intake left to the buyer (`items`) that wait for him among them
    ("Zelf bekijken"); and when `aid` is given (the assistant as aid/review/status.py reads it, on the PC's own page with its settings
    file), a chip for each problem, the most urgent first, and a last line with the runner's last pass, the mailbox, the reader and
    the time of `now`. Every word goes through html.escape."""
    strings = STRINGS[language]
    counted = Counter(line.state for line in lines) + Counter(item.state for item in items)
    parts = [_chips((strings[f"chip:{state}"].format(n=counted[state]), tone) for state, tone in _CHIPS if counted[state] or state == "review")]
    if aid is not None:
        if problems:
            parts.append(_chips((_problem_text(problem, strings), "problem") for problem in problems))
        parts.append(f'<div class="meta">{escape(" · ".join(_strip_meta(aid, now, strings)))}</div>')
    return f'<div class="strip">{"".join(parts)}</div>'


def _chips(chips) -> str:
    """A line of chips, each (its words, its tone)."""
    return '<div class="chips">' + "".join(f'<span class="chip {tone}">{escape(words)}</span>' for words, tone in chips) + "</div>"


def _problem_text(problem: aid_status.Problem, strings: dict[str, str]) -> str:
    """A problem of the assistant in words: since when and for how many cases, as aid/review/status.py tells it, never an error's
    text."""
    if problem.kind == "no_pass":
        return strings["problem:no_pass"].format(time=_moment(problem.since))
    if problem.kind in ("copilot_down", "copilot_refused"):
        since = _moment(problem.since) if problem.since is not None else ""
        return strings[f"problem:{problem.kind}_{'one' if problem.n == 1 else 'many'}"].format(since=since, n=problem.n)
    return strings[f"problem:{problem.kind}"]


def _strip_meta(aid: aid_status.Assistant, now: datetime, strings: dict[str, str]) -> list[str]:
    """The last line of the strip, in its parts: the runner's last pass (none before the first), the mailbox as the settings tab
    tells it, the reader, and the time the strip was read."""
    parts = [strings["strip_pass"].format(time=_clock(aid.last_pass, now))] if aid.last_pass is not None else []
    mailbox = aid.mailbox
    if mailbox.state == "read":
        parts.append(strings["strip_mailbox:read"].format(last=_clock(mailbox.last_sync, now)))
    elif mailbox.state == "unread":
        parts.append(strings["strip_mailbox:unread"].format(since=_clock(mailbox.connected_at, now) if mailbox.connected_at else "?"))
    else:
        parts.append(strings[f"strip_mailbox:{mailbox.state}"])
    return [*parts, strings[f"strip_reader:{aid.reader}"], strings["strip_updated"].format(time=_clock(now, now))]


def _all_html(lines: list[aid_status.CaseLine], asking: set[str], language: str, items: Sequence[aid_status.LookItem] = ()) -> str:
    """The table of the fold under the strip (see the module docstring): for each case of `lines` and each document the intake left to
    the buyer (`items`), the time it came in, its order or "Document <its file>", its state as a chip and what the buyer does, which
    for a closed case is to enter its changes while they stand on the list In Exact invoeren (`asking` holds those cases). Those that
    wait for the buyer come first, then the others, each newest first, the cases in the order of `lines` where two times are the same;
    one sentence when there is none. Every name goes through html.escape."""
    strings = STRINGS[language]
    if not lines and not items:
        return f'<p class="empty">{escape(strings["review_all_none"])}</p>'
    head = "".join(f"<th>{escape(strings[f'all_column:{column}'])}</th>" for column in ("time", "case", "state", "todo"))
    rows = [(line.state in aid_status.NEEDS, line.opened_at, _all_row(line.opened_at, _order(line, strings), line.state, _todo(line, asking), language))
            for line in lines]
    rows += [(item.state == "look", item.time, _all_row(item.time, strings["item_name"].format(file=item.file), item.state,
                                                        "look" if item.state == "look" else "nothing", language)) for item in items]
    rows.sort(key=lambda row: row[1], reverse=True)          # stable: the order of `lines` stays where two times are the same
    rows.sort(key=lambda row: not row[0])
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(row for _, _, row in rows)}</tbody></table>"


def _all_row(moment: datetime, name: str, state: str, todo: str, language: str) -> str:
    """A row of the fold's table: the time, the name of the case or document, its state as a chip and what the buyer does ("todo:")."""
    return (f"<tr><td>{_moment(moment)}</td><td>{escape(name)}</td>"
            f'<td><span class="chip {_TONE[state]}">{escape(aid_status.state_text(state, language))}</span></td>'
            f"<td>{escape(STRINGS[language][f'todo:{todo}'])}</td></tr>")


def _order(line: aid_status.CaseLine, strings: dict[str, str]) -> str:
    """The order of a case in words, "Inkooporder 250107 (S07)", the supplier once the case has a result."""
    supplier = f" ({line.supplier})" if line.supplier else ""
    return f"{strings['po_document']} {line.po_number or strings['unknown_po']}{supplier}"


def _todo(line: aid_status.CaseLine, asking: set[str]) -> str:
    """What the buyer does for a case, by its state: the key after "todo:"."""
    if line.state in _CLOSED:
        return "closed_changes" if line.case_id in asking else "nothing"
    return "nothing" if line.state == "handled" else line.state


def _moment(moment: datetime) -> str:
    """A time of the strip and of its fold as dd-mm-yyyy HH:MM, in the local time of this PC."""
    return _local(moment).strftime("%d-%m-%Y %H:%M")


def _clock(moment: datetime, now: datetime) -> str:
    """A time of the strip's last line: HH:MM on the day of `now`, else as _moment."""
    moment = _local(moment)
    return moment.strftime("%H:%M") if moment.date() == _local(now).date() else _moment(moment)


def _local(moment: datetime) -> datetime:
    """The time in the local time of this PC, without a zone; a time without a zone is local time already."""
    return moment.astimezone().replace(tzinfo=None) if moment.utcoffset() is not None else moment


def _formed(value: str, field: str, language: str) -> str:
    """A value of a row in the form of the language, as the tab "In Exact invoeren" shows a value (hand_list._value): a price or a
    quantity in normal form (aid/schema.py) with the decimal mark of the language, 12,50 in Dutch and 12.50 in English, and a day, a
    week or a range of days as 16-11-2026, "week 47 (2026)" or its two days joined by "/", in both languages; a value that is not in
    normal form (the text as printed, where the reader could not read a value) and a value of any other field, as it is."""
    if field in ("price", "quantity") and _NUMBER.fullmatch(value):
        return hand_list._value(value, "po_line_price", language)
    return hand_list._value(value, "po_line_date", language) if field == "delivery_date" else value


def _place(source: str, strings: dict[str, str]) -> str:
    """Where a value was read, as a decision row says it after "Gelezen op de inkooporder" or "Gelezen bij de leverancier": its page
    and line in words and the text of that line as it was read, " (pagina 1, regel 8): 1 KM-0701 ...", from a source as the view
    cites it ("p1 r8: <text>", aid/review/app.py); a source in another form as it is. The text is escaped for Markdown."""
    found = _SOURCE.match(source)
    if found is None:
        return f": {hand_list._markdown(source)}"
    text = hand_list._markdown(source[found.end():])
    return f" ({strings['source_place'].format(page=found[1], line=found[2])})" + (f": {text}" if text else "")


def _describe(row: FlagRow, language: str) -> str:
    """The left of the decision row of a review item (spec 2.1.1), as Markdown in the language, each fact once: the field and the line
    in bold ("Prijs, regel 1"); the purchase order's value first and the supplier's after it, in the form of the language (_formed),
    with the reason by its label ("Inkooporder 12,50 → leverancier 14,00 · Prijsverschil boven de drempel"); and as meta, a paragraph
    each, where each value was read (_place), the lines whose marks the documents show. A row without values has no line of values,
    and one whose item names no field and no line (an item about the whole case) is headed by its reason, which is then not said
    again. No item id: the column Punt of the lists has it."""
    strings = STRINGS[language]
    line = f"{strings['line_word']} {hand_list._markdown(row.line)}" if row.line else ""
    head, reason = ", ".join(part for part in (_field(strings, row.field), line) if part), _reason(strings, row.reason)
    po, doc = (hand_list._markdown(_value(row, value, language)) for value in (row.po_value, row.doc_value))
    supplier = strings["doc_value"]
    if po:
        values = f"{strings['po_value']} {po}" + (f" → {supplier[:1].lower()}{supplier[1:]} {doc}" if doc else "")
    else:
        values = f"{supplier} {doc}" if doc else ""
    title = head or reason
    paragraphs = [f"**{title}**" if title else "", " · ".join(part for part in (values, reason if head else "") if part)]
    paragraphs += [f"{strings[key]}{_place(source, strings)}" for key, source in (("source_po", row.po_source), ("source_doc", row.doc_source))
                   if source]
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _decision_choices(row: FlagRow, kind: str, strings: dict[str, str], again: bool = False) -> list[tuple[str, str]]:
    """The decisions offered on a row: "ask" only on an item with a finding, or on the item of a bounce or a silence of a question
    the run asked itself (`again`: asked_by_the_run of aid/pipeline.py, the question is asked again), not on a cart, and not on an
    item that asks for a look in Exact after a write of the aid (see decide)."""
    asked = (bool(row.field) or again) and kind != "cart" and row.reason not in LOOKS
    return [(strings[decision], decision) for decision in get_args(Decision) if decision != "ask" or asked]


def _marked(images: list, marks: list[LineMark]) -> list:
    """The page images of a document with the marks of the view drawn on them (see the module docstring): the box of each mark,
    MARK_PAD pixels wider on every side, keeps the line's ink and takes the tint of attention as its ground, multiplied in as a marker
    does, inside an edge of MARK_EDGE pixels in the mark colour of attention (TONES of aid/review/look.py); nothing else of an image
    changes. A line that two rows cite is marked once, and a page without a mark is its image as it was. A box that does not lie on
    its image, which the view never gives (aid/review/app.py), is not drawn, nor is one that is no box of numbers, so that drawing a
    mark never raises into the page."""
    from PIL import Image, ImageChops, ImageDraw

    tone = TONES["attention"]
    boxes: dict[int, dict] = {}
    for found in marks:
        boxes.setdefault(found.page, {})[found.box] = None        # each line once, in the order of the rows
    shown = []
    for number, image in enumerate(images, 1):
        if number not in boxes:
            shown.append(image)
            continue
        image = image.convert("RGB")
        draw = ImageDraw.Draw(image)
        for box in boxes[number]:
            if not (all(math.isfinite(value) for value in box) and box[0] < box[2] and box[1] < box[3]
                    and box[0] < image.width and box[2] > 0 and box[1] < image.height and box[3] > 0):
                continue                         # not on the image: nothing of it to draw
            left, top = max(0, math.floor(box[0]) - MARK_PAD), max(0, math.floor(box[1]) - MARK_PAD)
            right, bottom = min(image.width - 1, math.ceil(box[2]) + MARK_PAD), min(image.height - 1, math.ceil(box[3]) + MARK_PAD)
            area = (left, top, right + 1, bottom + 1)
            image.paste(ImageChops.multiply(image.crop(area), Image.new("RGB", (right + 1 - left, bottom + 1 - top), tone.tint)), area[:2])
            draw.rectangle((left, top, right, bottom), outline=tone.mark, width=MARK_EDGE)
        shown.append(image)
    return shown


def _undoable(case: Case) -> list[WriteLogEntry]:
    """The entries of the write log the page offers to undo (see _made_and_not_undone)."""
    return _made_and_not_undone(case.root)


def _made_and_not_undone(root: Path) -> list[WriteLogEntry]:
    """The entries of the write log of the case folder that were made in the system (status "done"), are not an undo and are not
    undone. undo_entry would also reverse a prepared change, but a change only prepared was not made, and the digest gives it no undo
    link."""
    entries = WriteLog(root).entries()
    undone = {entry.undo_of for entry in entries if entry.undo_of is not None and entry.status != "failed"}
    return [entry for entry in entries if entry.undo_of is None and entry.status == "done" and entry.entry_id not in undone]


def offered_cases(cases_root: Path) -> list[CaseSummary]:
    """The cases the page offers, in the order they were opened: those in review, and any case with a write to undo
    (_made_and_not_undone), whatever its status (closed, waiting, failed). A case whose write log cannot be read is offered only
    when it is in review. The page asks for them at every tick of its timer, so a case folder is read again only when one of the
    files its offer is told by (_OFFER_FILES: case.json, the result, the write log and capture.json) changed (_stamp) since the last
    call for the same cases root, or could not be opened then (_OFFERED)."""
    root = Path(cases_root)
    key = root.absolute()
    known = _OFFERED.get(key, {})
    seen: dict[str, tuple[tuple, CaseSummary | None]] = {}
    for path in root.glob(f"*/steps/{RESULT_FILE}"):
        folder = path.parents[1]
        stamps = tuple(_stamp(folder / name) for name in _OFFER_FILES)
        hit = known.get(folder.name)
        if hit is None or hit[0] != stamps:
            try:
                hit = (stamps, _offer(root, folder))
            except OSError:                      # a file held for a moment: not offered now, and read again at the next call
                continue
        seen[folder.name] = hit
    with _OFFERED_LOCK:
        _OFFERED.pop(key, None)
        _OFFERED[key] = seen
        while len(_OFFERED) > aid_status.CACHED_ROOTS:
            _OFFERED.pop(next(iter(_OFFERED)))
    return sorted((summary for _, summary in seen.values() if summary is not None), key=lambda summary: (summary.opened_at, summary.case_id))


def _offer(cases_root: Path, folder: Path) -> CaseSummary | None:
    """The case of the folder as offered_cases offers it, with its try-out mark (case_is_tryout of aid/tryout_mark.py), or None when
    it does not, as _list_cases of aid/review/app.py lists a case: a case.json or a result that cannot be understood is not offered,
    and neither is a capture case marked as replaced (marked_replaced). Raises OSError for a file that cannot be opened at that
    moment, which offered_cases then reads again at its next call."""
    try:
        meta = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        result = CaseResult.model_validate_json((folder / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
        summary = _summary(folder.name, meta, result)
    except (ValueError, KeyError):
        return None
    if result.status != "review":
        try:
            if not _made_and_not_undone(folder):
                return None
        except ValueError:                       # a write log that cannot be understood offers no undo
            return None
    if marked_replaced(folder):
        return None
    return summary.model_copy(update={"tryout": case_is_tryout(cases_root, folder.name)})


def _stamp(path: Path) -> tuple[int, int, int] | None:
    """The file's number in its file system, its size and its modification time in nanoseconds, so that a result written again
    through a new file (_write_json of aid/case.py) within the same tick of the file system's clock counts as changed; None for a
    file that is not there or cannot be looked at."""
    try:
        found = path.stat()
    except OSError:
        return None
    return found.st_ino, found.st_size, found.st_mtime_ns


def _readable_undoable(root: Path) -> list[WriteLogEntry]:
    """_made_and_not_undone, or none when the write log cannot be read: an unreadable log offers no undo."""
    try:
        return _made_and_not_undone(root)
    except (OSError, ValueError):
        return []


def selection_from_query(cases_root: Path, params: Mapping[str, str]) -> tuple[str | None, str | None]:
    """The case and the write the query of the page's address selects (see the module docstring): (case id, entry id). The case is
    the parameter "case" when the page offers it (a case in review, or any case with a write to undo), else None; the entry is the
    parameter "undo" when it is a write of that case that the page offers to undo, else None (also when the case's write log
    cannot be read). Nothing is undone."""
    case_id, entry_id = params.get("case"), params.get("undo")
    if not case_id or case_id not in {summary.case_id for summary in offered_cases(cases_root)}:
        return None, None
    if not entry_id or entry_id not in {entry.entry_id for entry in _readable_undoable(Path(cases_root) / case_id)}:
        return case_id, None
    return case_id, entry_id


def _undo_choices(case: Case, strings: dict[str, str]) -> list[tuple[str, str]]:
    """The writes to undo as the dropdown names them, each with its entry id: the write in words and its old and new value. The
    old value is the one read before the write (the entry's previous), which the undo puts back and the digest shows; "leeg"
    where the field was empty then and "onbekend" where nothing was read, as the put-back's row says it, never the old value of
    the operation, which is the document's. An attached or detached file has no values."""
    choices = []
    for entry in _undoable(case):
        read = read_before(entry)
        old = entry.previous if read == "value" else strings["hand_empty" if read == "empty" else "hand_unknown"]
        values = f": {old} -> {entry.op.new_value or '-'}" if entry.op.old_value or entry.op.new_value else ""
        choices.append((f"{_write_words(entry.op, strings)}{values}", entry.entry_id))
    return choices


def close_text(case: Case, deps: Deps, language: str) -> tuple[str, bool]:
    """Close the case and say how it went, in the language, and whether the case is still open (in review). A write that did not go
    through and a message not sent are named by fixed labels: the error's own text, which may name an endpoint or a folder of the
    settings, stays in the result, step 09 and the audit record. After a message not sent comes the kind of its cause with what to
    do ("not_sent:<cause>", for each cause among the close's messages once, in the order they arose: a supplier without an address
    among them, "no_address"; a cause without a sentence has its review item). A message of the close that the mailer wrote as a file, by the
    settings' choice (_files_folder), is said too, with the folder it lies in, and so is one whose hand-over was not confirmed.
    A close that did nothing because a reply filed with the case could not be read says to close again."""
    strings = STRINGS[language]
    folder = _files_folder(deps)
    try:
        result = close_case(case, deps)
    except ValueError:
        return strings["not_closed"], False
    text = strings["new_status"].format(status=strings[f"status:{result.status}"])
    record = _records(case)[-1]
    if (stop := record["stop"]) is not None:
        error = _reason(strings, stop["message"]) if stop["waiting"] else strings["write_refused"]
        text += " " + strings["write_stopped"].format(write=_write_words(WriteOp.model_validate(stop["write"]), strings), error=error)
    if record.get("put_back_by_hand"):               # "keep" on a look of a status or a file: no list holds its put-back
        text += " " + strings["hand_put_back_cannot"]
    if record.get("unread_reply"):
        text += " " + strings["reply_unread"]
    unsent = [message for message in record["messages"]
              if message["status"].startswith("not sent (") and message["status"] not in (NOT_SENT, TRYOUT_NOT_SENT)]
    if unsent:
        causes = dict.fromkeys(f"not_sent:{message.get('cause')}" for message in unsent)
        text += " " + " ".join([strings["message_not_sent"], *(strings[cause] for cause in causes if cause in strings)])
    if folder is not None and any(message["status"] == SENT for message in record["messages"]):
        text += " " + strings["message_filed"].format(folder=folder)
    if any(message.get("unconfirmed") for message in record["messages"]):
        text += " " + strings["message_unconfirmed"]
    if record.get("question_open"):
        text += f" {strings['question_open']}."
    return text, result.status == "review"


def _files_folder(deps: Deps) -> str | None:
    """The folder that a message of a close is written into as a file, in the words the page names it by; None when the mailer
    sends, or has no route (it then writes nothing: aid/review/close.py). That is the file mailer of send_via "file", or the
    one of the Deps the page was given; the mailer of send_via "auto" never writes a file by choice. A folder inside the cases
    folder is named by its path from there, the cases folder's own name first ("cases/outbox"); any other by its name."""
    mailer = deps.mailer
    if not isinstance(mailer, FileMailer) or mailer.unrouted is not None:
        return None
    cases, out = Path(deps.settings.cases_root).resolve(), mailer.out_dir.resolve()
    return f"{cases.name}/{out.relative_to(cases).as_posix()}" if out.is_relative_to(cases) and out != cases else out.name


def _unrouted(deps: Deps) -> str | None:
    """Why the mailer of `deps` (the Deps of a close, _Live.sending) would send a message of the close nowhere at this moment, as the
    close judges the route of each message before it sends it (aid/review/close.py): the `unrouted` of the mailer a message would go
    through now, "no_mailbox" or "no_password" (mailer_of of aid/messaging/microsoft_mailer.py) or "settings" (the settings file
    cannot be read); None for a mailer with a way to send, and for a page without a mailer (the made-up set's), whose close records
    the question as asked. Asks Microsoft nothing: the mailer of send_via "auto" looks at the token cache on this PC. A mailer of the
    work mailbox with nobody signed in is told by _signed_out, after the supplier's address, where the close meets it."""
    mailer = deps.mailer
    if mailer is None:
        return None
    route = mailer.current() if hasattr(mailer, "current") else mailer
    return getattr(route, "unrouted", None)


def _signed_out(deps: Deps) -> str | None:
    """Why a message of the close would not go through the work mailbox at this moment, as the close meets it when it sends, after the
    supplier's address (aid/review/close.py, MicrosoftMailer.send): "sign_in" when the mailer a message would go through now is that
    of the work mailbox (send_via "microsoft") and nobody is signed in, before the first sign-in or after "Afmelden"; "store" when the
    store of the sign-in is refused (StoreRefused); None otherwise. Reads the token cache on this PC only, as "auto" does, and asks
    Microsoft nothing: a sign-in that Microsoft no longer accepts is found at the close. A cache that cannot be read says nothing
    here, and the close says what happened."""
    mailer = deps.mailer
    route = mailer.current() if mailer is not None and hasattr(mailer, "current") else mailer
    if not isinstance(route, MicrosoftMailer):
        return None
    try:
        return "sign_in" if route.account.account() is None else None
    except StoreRefused:
        return "store"
    except Exception:  # a token cache that cannot be read: the close says what happened
        return None


def _asked_text(case: Case, result: CaseResult, deps: Deps, strings: dict[str, str]) -> str:
    """Where the question of "Leverancier vragen" on the case goes when the case is closed, as the close would send it with `deps`,
    the Deps of a close as they would be now (spec 2.1.1): with the checks the close makes before it sends, in its order. A mailer
    that would have no way to send it says why not and what to do ("row_asks_unrouted:<cause>", _unrouted); a supplier without an
    address (of the case's routing context, as close.py takes it), that the question does not go until one is filled in; the work
    mailbox with nobody signed in or its store refused, that the question would not go and what to do (_signed_out); a mailer that
    writes files by the settings' choice (_files_folder), that the question is kept as a file; and else that it goes to the
    supplier, named and never by its address."""
    supplier = _context(case, result).supplier
    named = _literal(supplier) if supplier else strings["unknown_po"]
    if (cause := _unrouted(deps)) is not None:
        return strings[f"row_asks_unrouted:{cause}"]
    if not deps.settings.supplier_emails.get(supplier):
        return strings["row_asks_no_address"].format(supplier=named)
    if (cause := _signed_out(deps)) is not None:
        return strings[f"row_asks_unrouted:{cause}"]
    if _files_folder(deps) is not None:
        return strings["row_asks_as_file"]
    return strings["row_asks"].format(supplier=named)


def decision_text(case: Case, item_id: str, decision: str, article_update: bool, language: str) -> tuple[str, bool]:
    """Record the buyer's decision on a review item (decide) and say how it went, in the language, and whether it was saved: saved,
    or refused; "ask" on a case made from a try-out document is refused with the sentence that the assistant sends nothing about it
    to a supplier (aid/tryout_mark.py)."""
    strings = STRINGS[language]
    try:
        decide(case, item_id, decision, article_update)
    except NothingAsked:
        return strings["tryout_nothing_sent"], False
    except ValueError:
        return strings["refused"], False
    return strings["saved"].format(item=item_id), True


def undo_text(case: Case, entry_id: str, deps: Deps, language: str) -> str:
    """Undo one write of the case and say how it went, in the language; a WriterError by a fixed label, as close_text does. An undo
    that the page's writer only prepared is not undone in Exact: it is a put-back on the tab "In Exact invoeren", and the page says
    so (hand_list.put_back_note), never "Ongedaan gemaakt"."""
    strings = STRINGS[language]
    entry = next((entry for entry in WriteLog(case.root).entries() if entry.entry_id == entry_id), None)
    try:
        made = undo(case, entry_id, deps)
    except WriterError:
        return strings["undo_failed"]
    if made is None:
        return strings["not_undone"]
    if made.status != "done":
        return hand_list.put_back_note(deps, made, entry_id, deps.clock(), language)
    return strings["undone"].format(write=_write_words(entry.op, strings))


def _asked_again(case: Case) -> list[str]:
    """The review items of the case on which "ask" asks a question of the run again: those of a bounce or a silence of a question
    the run asked itself (asked_by_the_run of aid/pipeline.py)."""
    result = _saved_result(case)
    return [item.item_id for item in result.review_items if asked_by_the_run(result, item) is not None]


def _article_items(case: Case) -> set[str]:
    """The review items whose waiting write is an article price, whose article box the page ticks."""
    kinds = {entry.entry_id: entry.op.kind for entry in WriteLog(case.root).entries()}
    return {item.item_id for item in _saved_result(case).review_items if item.entry_id and kinds.get(item.entry_id) == "article_price"}


# --- the page ---

def import_gradio():
    """gradio, imported with the DeprecationWarning and PendingDeprecationWarning that third-party packages raise during the import
    ignored, so that the suite stays clean under -W error (the aid's own warnings are not ignored). gradio 6.31.0 raises none."""
    with warnings.catch_warnings():
        for category in (DeprecationWarning, PendingDeprecationWarning):
            warnings.filterwarnings("ignore", category=category, module=r"(?!aid(\.|$))")
        import gradio
    return gradio


class _Live:
    """The Deps the page's own actions use: those the page was built with, carrying the settings of its settings file once that file
    changed. now() looks at the file's modification time and reads a changed file again. A file that cannot be read keeps the
    settings in use, and is read again at every look until it reads (unread): one that does not validate, and one that cannot be
    opened or looked at (missing, or held by another program for a moment), also when it comes back with the time it had. take()
    puts the settings a save of the settings tab wrote into the Deps at once, and settings() gives the settings in use, which the
    settings tab shows when its file cannot be read. The Deps the page was given are never changed: a new copy carries the new
    settings. sending() gives the Deps of a close: those of now(), with the mailer that `mailer` makes of their settings at that
    moment, when the page builds its own (see "The mailer of a close" in the module docstring); without `mailer` the Deps keep
    the mailer they were given. While the settings file cannot be read (unread: its last look failed), the Deps of a close carry
    a mailer that sends nothing and writes nothing (NoRoute of aid/messaging), so that no message goes out by settings the owner
    has since changed: the route and the supplier's address of the settings read before may be the very thing the edit was to
    correct."""

    def __init__(self, deps: Deps, file: Path | None, mailer: Callable[[Deps], Mailer] | None = None):
        self.deps, self.file, self.modified, self.mailer = deps, file, _modified(file), mailer
        self.unread = False                      # the settings file could not be read, or did not validate, at the last look
        self.lock = threading.Lock()

    def now(self) -> Deps:
        with self.lock:
            modified = _modified(self.file)
            if self.file is not None and (modified != self.modified or modified is None or self.unread):
                try:
                    self.deps = self.deps.model_copy(update={"settings": load_settings(self.file)})
                except OSError:                  # not opened at this look: the settings in use are kept, and every look tries again
                    self.unread = True
                    return self.deps
                except ValueError:               # a file that does not validate: the settings in use are kept
                    self.unread = True
                else:
                    self.unread = False
                self.modified = modified
            return self.deps

    def sending(self) -> Deps:
        deps = self.now()
        if self.mailer is None:
            return deps
        return deps.model_copy(update={"mailer": NoRoute("settings") if self.unread else self.mailer(deps)})

    def take(self, settings: Settings) -> None:
        with self.lock:
            self.deps, self.modified, self.unread = self.deps.model_copy(update={"settings": settings}), _modified(self.file), False

    def settings(self) -> Settings:
        """The settings in use: those the page's own actions run with now."""
        return self.now().settings


class _Refused(Exception):
    """A save that its handler refuses while update_settings_file holds its lock, with the note the page then shows; nothing is
    written."""

    def __init__(self, note: str):
        super().__init__(note)
        self.note = note


def _modified(path: Path | None) -> int | None:
    """The modification time of the file in nanoseconds; None when there is no file, or it cannot be looked at."""
    try:
        return path.stat().st_mtime_ns if path is not None else None
    except OSError:
        return None


class _Judged(NamedTuple):
    """What a view and a press of the tab "Prijslijst" work from (_pricelist_tab)."""
    name: str                                    # the supplier, by its stored name
    columns: dict[str, str]                      # the column map of the form
    current: dict[str, str]                      # the supplier's current prices
    stored_at: str                               # when the supplier's export was stored
    shared: dict[str, list[str]]                 # the articles that another supplier holds too, each with those suppliers' names
    result: object                               # the import's result of the view, with nothing written
    after: object                                # the state a row would end with on the list (pricelist_page.after_press)
    older: list[str]                             # the articles whose older price a row kept off the list leaves on the list


class _Strip(NamedTuple):
    """The strip of the tab "Beoordelen" and the fold under it, as the page reads them (see the module docstring)."""
    html: str                                    # the strip
    action: str | None                           # the label of its button; None while nothing is wrong, or where it has no button
    fold: str                                    # the fold's label, with the number of cases in it
    table: str                                   # the fold's table, or the sentence in its place when there is no case


class _Sections(NamedTuple):
    """The sections of the settings tab, as an action of another tab opens them (_settings_tab; the strip's button of "Beoordelen")."""
    tabs: object                                 # the gr.Tabs settings-sections, whose `selected` names the section open
    read_after: Callable                         # reads "Stand van de assistent" again once a given event has run (_assistant_section)


class _Tab(NamedTuple):
    """What the settings tab is built with (see build_app)."""
    file: Path | None                            # the settings file it writes; None: the tab offers nothing
    share: bool                                  # the page is opened on a public share link: the tab offers nothing
    account_factory: Callable[[str, str], MicrosoftAccount]
    session: object                              # the requests session that Graph is called through; None: requests
    environ: Mapping[str, str] | None            # where the password of the aid's mailbox is looked up; None: the page makes no mailer


def build_app(deps: Deps, settings_file: Path | None = None, share: bool = False,
              account_factory: Callable[[str, str], MicrosoftAccount] | None = None, session=None,
              environ: Mapping[str, str] | None = None) -> "gr.Blocks":  # noqa: F821
    """The review page, Dutch by default with a switch to English, in five tabs. On "Beoordelen": the strip of the aid's state and
    the fold of every case of the last 7 days, read again every TICK_SECONDS seconds (see the module docstring); a dropdown of the
    cases offered (offered_cases), of the failed cases not noted as handled and of the documents the intake left to the buyer that
    wait for him, with the case and the write of the address's query selected when the page opens (selection_from_query), and under
    it, while no case is chosen, "Oudste zaak openen"; then the case chosen in three parts (see the module docstring): the banner when
    a change sends the PO through approval again, the hint on a case that waits for its PO, the line of the values entered in Exact
    that the case was compared with and the notice of a failed case or of a document left to the buyer, above the four decisions on
    each review item and the tick for the article price on a price; the close button, or for a failed case and a document "Als
    afgehandeld noteren", and the undo buttons; and the PO version and the supplier's document, or the document left to the buyer, as
    image galleries, with the three lists as tables. On "In Exact invoeren", selected when the query holds tab=invoeren: the
    list of the changes to enter in Exact, to tick as entered or as not to enter, and under it the changes handled in the last 14 days,
    to put on the list again or to put back in Exact, and the confirmations to attach in Exact (see the module docstring). On
    "Prijslijst": a supplier's price list compared with the article prices of an export from Exact, and its changes put on that list
    (see the module docstring). On "Bestanden": a file given to the assistant, put into the watched folder, and what the runner did
    with the last files (see the module docstring). On "Instellingen", in five sections: the buyer's settings, the suppliers, the
    settings of the work mailbox, the connection to Exact (see the module docstring), written into settings_file, and the state of the
    assistant, read from the files of this PC. The last four
    tabs show nothing but a line when the page is opened on a public link (share) or without a settings file. account_factory gives
    the Microsoft account of a tenant and a client (by default with the token cache of the store in default_secrets_dir(); the tests
    give a fake one), and session is the requests session Graph is called through. No rule code is shown, and no setting value
    outside the settings tab. Built inside an event loop of its own (see the module docstring). The part "Postvak en AI" holds the AI
    section (see the module docstring), whose Copilot is called through the same session. With `environ`, the environment that the
    password of the aid's mailbox is looked up in, the page makes the mailer of each close from the settings as they are then, as the
    runner makes its own (see "The mailer of a close" in the module docstring); without it, and on a public link whatever is given, a
    close sends through the mailer of deps."""
    gr = import_gradio()
    tab = _Tab(Path(settings_file) if settings_file is not None else None, share, account_factory or _account, session,
               None if share else environ)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_built(gr, deps, tab))
        finally:
            loop.close()
    return _page(gr, deps, tab)


async def _built(gr, deps: Deps, tab: _Tab):
    return _page(gr, deps, tab)


def _page(gr, deps: Deps, tab: _Tab):
    from PIL import Image

    cases_root, first = deps.settings.cases_root, STRINGS["nl"]
    empty = {"flags": [], "matches": [], "uncompared": []}
    accounts: dict[tuple[str, str], MicrosoftAccount] = {}      # the Microsoft accounts of the page, by tenant and client, each made once

    def sending_mailer(now: Deps) -> Mailer:
        """The mailer of a close, for the settings as they are now: the one send_via names (mailer_of), at the page's clock."""
        return mailer_of(now.settings, tab.environ, lambda: _sending_account(tab, now.settings, accounts), tab.session, now.clock)

    # The Deps of the page's actions, with the settings of the file as it is now, and for a close the mailer of those settings.
    live = _Live(deps, tab.file, sending_mailer if tab.environ is not None else None)
    # The strip has its problems, its last line and its button on the page of this PC with its settings file only (see the module
    # docstring).
    acting = not tab.share and tab.file is not None

    def lines_now() -> list[aid_status.CaseLine]:
        """The cases as case_lines of aid/review/status.py gives them, at the time of the page's clock."""
        return aid_status.case_lines(cases_root, live.now().clock())

    def items_now() -> list[aid_status.LookItem]:
        """The documents the intake left to the buyer as review_items of aid/review/status.py gives them, at the time of the page's
        clock: those that wait for him, and those he noted as handled in the last 7 days."""
        return aid_status.review_items(cases_root, live.now().clock())

    def choices(language: str, lines: list[aid_status.CaseLine] | None = None, items: list[aid_status.LookItem] | None = None,
                keep: str | None = None) -> list[tuple[str, str]]:
        """The case list (see the module docstring): the cases offered (offered_cases), then the failed cases that the buyer has not
        noted as handled and that are not offered already, oldest first, then the documents the intake left to him that wait for him,
        oldest first, and last the case or document `keep` (the one chosen) when it is no longer among them, so that it keeps its
        place until another one is chosen (kept). `lines` and `items` are the cases and the documents as lines_now and items_now give
        them, read here when they are not given."""
        strings = STRINGS[language]
        named = [(_case_label(summary, strings), summary.case_id) for summary in offered_cases(cases_root)]
        offered = {case_id for _, case_id in named}
        failed = sorted((line for line in (lines_now() if lines is None else lines) if line.state == "failed" and line.case_id not in offered),
                        key=lambda line: (line.opened_at, line.case_id))
        named += [(_failed_label(line, strings, case_is_tryout(cases_root, line.case_id)), line.case_id) for line in failed]
        named += [(_item_label(item, strings), item.item_id) for item in (items_now() if items is None else items) if item.state == "look"]
        if keep and keep not in {case_id for _, case_id in named}:
            named.append(kept(keep, strings))
        return named

    def kept(case_id: str, strings: dict[str, str]) -> tuple[str, str]:
        """The choice of a case on screen that left the list, as _kept_label names it, or of a document the buyer noted as handled
        meanwhile (in another browser tab), as _item_label names it; a document no line names any more by its id."""
        if aid_status.is_item(case_id):
            item = aid_status.look_item(cases_root, case_id)
            return (_item_label(item, strings) if item is not None else case_id), case_id
        return _kept_label(cases_root, case_id, strings), case_id

    def waits(lines: list[aid_status.CaseLine], items: list[aid_status.LookItem]) -> bool:
        """Whether a case waits for the buyer, one in review or failed and not noted as handled, or a document left to him."""
        return any(line.state in aid_status.NEEDS for line in lines) or any(item.state == "look" for item in items)

    def pick_text(lines: list[aid_status.CaseLine], items: list[aid_status.LookItem], strings: dict[str, str]) -> str:
        """The line under "Zaak" while no case is chosen: to choose one, or that none waits."""
        return strings["review_pick"] if waits(lines, items) else strings["review_pick_none"]

    def failed_text(line: aid_status.CaseLine, language: str) -> str:
        """The notice of a failed case: why it failed in words (fail_text of aid/review/status.py), never its error, and what to do."""
        return STRINGS[language]["failed_notice"].format(reason=aid_status.fail_text(line.reason, language))

    def item_text(item: aid_status.LookItem, language: str) -> str:
        """The notice of a document left to the buyer: why the intake left it to him in words (look_text of aid/review/status.py), never
        the scan's own reason, and what to do; when it is no longer on this PC (_item_document), where to look for it."""
        strings = STRINGS[language]
        text = strings["item_notice"].format(reason=aid_status.look_text(item.reason, language))
        if _item_document(item, live.settings().watched_folder) is None:
            text += " " + strings["item_missing"].format(time=_moment(item.time))
        return text

    def asking(lines: list[aid_status.CaseLine]) -> set[str]:
        """The closed cases among the lines whose changes stand on the list In Exact invoeren, still to enter; none when that list
        cannot be read, or when no case is closed."""
        if not any(line.state in _CLOSED for line in lines):
            return set()
        try:
            return {case_id for change in open_changes(Path(cases_root) / FOLDER) for case_id in change.case_ids}
        except (OSError, ValueError):
            return set()

    def strip_read(language: str) -> _Strip:
        """The strip, the label of its button, the fold's label and its table, as the aid's files are now (see the module docstring):
        the problems and the last line only on the page of this PC with its settings file (acting), from files only, at the time of
        the page's clock."""
        now_deps = live.now()
        now, strings = now_deps.clock(), STRINGS[language]
        lines, items = aid_status.case_lines(cases_root, now), aid_status.review_items(cases_root, now)
        aid = aid_status.assistant(tab.file, cases_root, now_deps.settings, now) if acting else None
        problems = aid_status.problems(aid, lines) if aid is not None else []
        return _Strip(_strip_html(lines, aid, problems, now, language, items), strings[f"act:{_PLACES[problems[0].kind]}"] if problems else None,
                      strings["review_all"].format(n=len(lines) + len(items)), _all_html(lines, asking(lines), language, items))

    def tables(lists: dict, language: str) -> list[dict]:
        headers = [STRINGS[language][column] for column in _COLUMNS]
        return [{"headers": headers, "data": page_rows(lists[name], language)} for name in ("flags", "matches", "uncompared")]

    def opened(language: str, request: gr.Request) -> tuple:
        """The page opened: the cases offered, with the case of the query selected, the write of the query to select among the
        writes to undo once the case is shown (selection_from_query), and the tab "In Exact invoeren" selected when the query holds
        tab=invoeren, the link of the digest (aid/notify.py, list_link)."""
        live.now()
        params = getattr(request, "query_params", None) or {}
        case_id, entry_id = selection_from_query(cases_root, params)
        selected = gr.update(selected=hand_list.TAB) if params.get("tab") == hand_list.TAB else gr.update()
        return gr.update(choices=choices(language), value=case_id), entry_id, selected

    def hint_of(case: Case | None, language: str) -> dict:
        """The hint above the documents: shown on a case that waits for its PO (waiting_for_po of aid/review/close.py), naming the PO,
        or the version of it that the confirmation quotes; hidden otherwise, also once a decision was saved on the case."""
        waits = waiting_for_po(case) if case is not None else None
        if waits is None:
            return gr.update(value="", visible=False)
        strings = STRINGS[language]
        if waits == "po":
            return gr.update(value=strings["waiting_hint:po"].format(number=case.po_number), visible=True)
        return gr.update(value=strings["waiting_hint:version"].format(number=case.po_number, version=waits.removeprefix("version ")),
                         visible=True)

    def entered_of(entered: list[EnteredValue], unread: bool, language: str) -> dict:
        """The line above the lists (entered_text): shown on a case that was compared with values entered in Exact, or that ran while
        the list of them could not be read; hidden otherwise."""
        text = entered_text(entered, unread, language)
        return gr.update(value=text, visible=bool(text))

    def show(case_id: str | None, language: str, wanted: str | None = None) -> tuple:
        """The case chosen: the banner, the documents with the lines a difference was read from marked (_marked), the lists, the writes
        to undo (with the write wanted selected when it is among them), the state the decisions are shown from (those that stand:
        standing_decisions), the hint of a case that waits for its PO, the line of the values entered in Exact, and a message (""
        when there is none). With no case chosen the status line is left as it is: a close that takes its case out of the review
        empties the case list, and what the close said must stay on the screen. A decisions file that cannot be read empties the view
        and says so. A document the intake left to the buyer shows its pages in the place of the supplier's document (_item_pages),
        from where the scan put it, and nothing else of a case: no list, no write to undo and no state, so no decision."""
        nothing = (gr.update(visible=False), [], [], *tables(empty, language), gr.update(choices=[], value=None), None, hint_of(None, language),
                   entered_of([], False, language))
        now = live.now()
        if not case_id:
            return (*nothing, gr.update())
        if aid_status.is_item(case_id):
            item = aid_status.look_item(cases_root, case_id)
            pages = _item_pages(item, now.settings.watched_folder, cases_root) if item is not None else []
            return (nothing[0], [Image.open(io.BytesIO(page)) for page in pages], *nothing[2:], "")
        case = load_case(cases_root, case_id)
        try:
            view = load_case_view(case, now.store)              # the store as it is now: a moved folder keeps its PO pages
            standing = standing_decisions(case, now.settings.ask_wait_working_days)
        except ValueError:                          # a damaged decisions.jsonl (_decisions)
            return (*nothing, STRINGS[language]["decisions_damaged"].format(file=DECISIONS_FILE))
        lists = {"flags": view.flags, "matches": view.matches, "uncompared": view.uncompared}
        state = {"case_id": case_id, "kind": case.kind, "lists": lists, "decisions": standing, "articles": _article_items(case),
                 "entered": (view.entered, view.entered_unread), "again": _asked_again(case), "compared": view.compared}
        images = [_marked([Image.open(io.BytesIO(page)) for page in pages], marks)
                  for pages, marks in ((view.doc_pages, view.doc_marks), (view.po_pages, view.po_marks))]
        undo_choices = _undo_choices(case, STRINGS[language])
        selected = wanted if wanted in [entry_id for _, entry_id in undo_choices] else None
        return (gr.update(visible=view.reapproval_banner), *images, *tables(lists, language),
                gr.update(choices=undo_choices, value=selected), state, hint_of(case, language),
                entered_of(view.entered, view.entered_unread, language), "")

    def relabel(language: str, case_id: str | None, state: dict | None) -> tuple:
        strings = STRINGS[language]
        flags, matches, uncompared = tables(state["lists"] if state else empty, language)
        case = load_case(cases_root, case_id) if case_id and not aid_status.is_item(case_id) else None     # a document: as no case
        undo_choices = _undo_choices(case, strings) if case is not None else []
        return (f"# {strings['title']}", gr.update(label=strings["language"]),
                gr.update(label=strings["case"], choices=choices(language, keep=case_id)), gr.update(value=strings["refresh"]), f"**{strings['banner']}**", gr.update(label=strings["document"]),
                gr.update(label=strings["po_document"]), f"### {strings['flags']}", flags, f"## {strings['decisions']}",
                f"### {strings['matches']}", matches, f"### {strings['uncompared']}", uncompared, gr.update(value=strings["close"]),
                gr.update(label=strings["undo_choice"], choices=undo_choices, value=None), gr.update(value=strings["undo"]),
                hint_of(case, language), entered_of(*(state or {}).get("entered", ([], False)), language))

    def saver(case_id: str, item_id: str) -> Callable:
        def save(decision: str | None, article: bool, language: str, current: dict | None) -> tuple:
            """A decision saved at once: the note of its row (row_note), and the hint of the case as it is now, which a decision
            hides, since the runner no longer checks the case again. A decision that is not saved says why in the note, as a
            refusal (class refused): decision_text's own sentence."""
            if not decision:
                return gr.update(), gr.update()
            if aid_status.is_item(case_id):      # a document left to the buyer has no decision
                return noted(STRINGS[language]["refused"], refused=True), gr.update()
            try:
                case = load_case(cases_root, case_id)
            except ValueError:
                return noted(STRINGS[language]["refused"], refused=True), gr.update()
            text, saved = decision_text(case, item_id, decision, bool(article), language)
            if not saved:
                return noted(text, refused=True), gr.update()
            if current and current["case_id"] == case_id:      # the state the radios are shown from follows the decision
                current["decisions"][item_id] = {"item_id": item_id, "decision": decision, "article_update": bool(article)}
            return noted(row_note(case, decision, language)), hint_of(case, language)
        return save

    def noted(text: str, refused: bool = False) -> dict:
        """The note of a decision row with the text, in the colour of done, or of a problem for a refusal (the class refused)."""
        return gr.update(value=text, elem_classes=["row-note", "refused"] if refused else ["row-note"])

    def row_note(case: Case, decision: str, language: str) -> str:
        """The note of a decision row once decision_text saved its decision (spec 2.1.1): "Opgeslagen."; for "Leverancier vragen"
        where the question goes when the case is closed, or why it would not go, judged with the Deps of a close as they would be
        now (_asked_text); for "Onopgelost laten" that the item stays open; and last what is left to decide on the case: its review
        items without a decision that stands (standing_decisions), but the item of a message not sent, which needs no choice of its
        own, since the next close sends that message again. A case whose files cannot be read at that moment leaves out what is
        left."""
        strings = STRINGS[language]
        now = live.sending() if decision == "ask" else live.now()     # for a question, the mailer a close would have now
        parts = [strings["row_saved"], *([strings["row_open"]] if decision == "unresolved" else [])]
        try:
            result = _saved_result(case)
            if decision == "ask":
                parts.append(_asked_text(case, result, now, strings))
            standing = standing_decisions(case, now.settings.ask_wait_working_days)
        except (OSError, ValueError, KeyError):  # files that cannot be read at this moment: the note leaves out what it cannot tell
            return " ".join(parts)
        left = sum(1 for item in result.review_items if not item.settled and item.reason not in (UNSENT, UNADDRESSED)
                   and item.item_id not in standing)
        return " ".join([*parts, strings["row_all_decided"] if not left else strings["row_left_one"] if left == 1
                         else strings["row_left_many"].format(n=left)])

    def refreshed(language: str, case_id: str | None) -> tuple:
        """"Lijst vernieuwen": the case list as it is now, the case chosen kept in it when it left the list, and the hint of the case
        chosen as it is now (a decision saved in another browser tab, or the runner's check again, may have ended its wait). A case
        whose folder is gone or cannot be read has no hint, and neither has a document the intake left to the buyer."""
        try:
            case = load_case(cases_root, case_id) if case_id and not aid_status.is_item(case_id) else None
        except (OSError, ValueError):
            case = None
        return gr.update(choices=choices(language, keep=case_id)), hint_of(case, language)

    def close(case_id: str | None, language: str) -> tuple:
        if not case_id or aid_status.is_item(case_id):           # a document left to the buyer: as no case
            return STRINGS[language]["no_case"], gr.update()
        text, still_open = close_text(load_case(cases_root, case_id), live.sending(), language)
        return text, gr.update(choices=choices(language), value=case_id if still_open else None)

    def do_undo(case_id: str | None, entry_id: str | None, language: str) -> tuple:
        if not case_id or not entry_id or aid_status.is_item(case_id):     # a document left to the buyer: as no case
            return STRINGS[language]["choose_write"], gr.update()
        case = load_case(cases_root, case_id)
        return undo_text(case, entry_id, live.now(), language), gr.update(choices=_undo_choices(case, STRINGS[language]), value=None)

    # The strip, the fold, the count, the case list and a failed case (see the module docstring): handlers of their own, each bound
    # once. A file that cannot be read leaves on the screen what it showed.

    def strip_ticked(language_now: str) -> tuple:
        """The page loaded, the timer ticked, "Lijst vernieuwen" or the language switch: the strip, its button (where it has one),
        the fold's label and its table, as strip_read reads them."""
        try:
            read = strip_read(language_now)
        except (OSError, ValueError):
            return (gr.update(),) * (4 if acting else 3)
        action = (gr.update(value=read.action, visible=True) if read.action else gr.update(visible=False),) if acting else ()
        return (read.html, *action, gr.update(label=read.fold), read.table)

    def queue_ticked(language_now: str, case_id: str | None, listed: list | None) -> tuple:
        """The page loaded, the timer ticked, "Lijst vernieuwen" or the language switch: the tab's label with its count, the case
        list only when it differs from the one this handler sent last (`listed`, given back as its last output), the line and the
        button under "Zaak", which stand while no case is chosen, and the line of a case on screen that left the list, which keeps
        its place in it (kept), or of a document the buyer noted as handled in another browser tab. Never a value of "Zaak": the case
        chosen stays chosen and its view is not shown again. Nor whether the row of that line and that button shows, which follows
        the value of "Zaak" alone (case_framed): the case this tick was sent with may have been chosen meanwhile, as a page opened
        from the digest's link chooses its case once it has loaded."""
        strings = STRINGS[language_now]
        try:
            lines, items = lines_now(), items_now()
            offered = choices(language_now, lines, items)
        except (OSError, ValueError):
            return (gr.update(),) * 7
        gone = bool(case_id) and case_id not in {value for _, value in offered}
        if gone:
            offered.append(kept(case_id, strings))
        sent = [[label, value] for label, value in offered]
        gone_line = gr.update(value=gone_text(case_id, strings), visible=True) if gone else gr.update(value="", visible=False)
        return (gr.update(label=review_label(lines, language_now, items)), gr.update(choices=offered) if sent != listed else gr.update(),
                gr.update(), pick_text(lines, items, strings),
                gr.update(value=strings["review_open_oldest"], visible=waits(lines, items)), gone_line, sent)

    def gone_text(case_id: str, strings: dict[str, str]) -> str:
        """The line of a case on screen that left the list, by its PO number, saying why: noted as handled meanwhile, for a failed
        case (its state "handled", whatever its age), else closed or replaced; or of a document the buyer noted as handled meanwhile,
        by its file; each as a Markdown text shows it as it is."""
        if aid_status.is_item(case_id):
            item = aid_status.look_item(cases_root, case_id)
            return strings["item_gone"].format(file=_literal(item.file if item is not None else case_id))
        handled = aid_status.case_states(cases_root, [case_id]).get(case_id) == "handled"
        return strings["review_gone_handled" if handled else "review_gone"].format(po=po_of(case_id, strings))

    def po_of(case_id: str, strings: dict[str, str]) -> str:
        """The PO number of a case, as a Markdown text shows it as it is; "onbekend" when it has none or its folder cannot be read."""
        try:
            number = load_case(cases_root, case_id).po_number
        except (OSError, ValueError):
            number = None
        return _literal(number) if number else strings["unknown_po"]

    def case_framed(case_id: str | None, language_now: str) -> tuple:
        """The case chosen, or none after a close that took it out of the review: the line and the button under "Zaak" shown while no
        case is chosen, the line of a case that left the list hidden, the case's parts review-case and review-evidence shown while one
        is chosen (the close's part review-close always stands), and "Zaak afsluiten" with a case in review only; for a failed case
        its notice, with "Als afgehandeld noteren" in place of "Zaak afsluiten" and of the title of the decisions; for a document the
        intake left to the buyer, while it waits for him, its notice (item_text) in the same place, with the same button, and neither
        "Zaak afsluiten" nor the title. A case offered for the undo of its write alone, not in review, keeps the title of its
        decisions."""
        strings = STRINGS[language_now]
        try:
            lines, items = lines_now(), items_now()
        except (OSError, ValueError):
            return (gr.update(),) * 10
        if aid_status.is_item(case_id):
            item = aid_status.look_item(cases_root, case_id)
            closable, titled, handling = False, False, item is not None and item.state == "look"
            notice = gr.update(value=item_text(item, language_now), visible=True) if handling else gr.update(value="", visible=False)
        else:
            line = next((line for line in lines if line.case_id == case_id), None) if case_id else None
            state = line.state if line is not None else None
            closable, titled, handling = state in ("review", "waiting_po"), state not in ("failed", "handled"), state == "failed"
            notice = gr.update(value=failed_text(line, language_now), visible=True) if handling else gr.update(value="", visible=False)
        chosen = gr.update(visible=bool(case_id))
        return (gr.update(visible=not case_id), pick_text(lines, items, strings),
                gr.update(value=strings["review_open_oldest"], visible=waits(lines, items)), gr.update(value="", visible=False), notice,
                gr.update(visible=closable), gr.update(visible=handling), gr.update(visible=titled), chosen, chosen)

    def record_shown(current: dict | None, language_now: str) -> tuple:
        """The state of the view changed (show), or the language switched: the comparison of the case chosen, each of its three lists
        as its table or, while it has no row, one sentence in its place ("empty:<list>"), and the part to undo a write, shown while the
        case has a write made in Exact and not undone, as its write log is now (one that cannot be read offers none). A case that was
        not compared (its state says so), a document the intake left to the buyer and no case (no state) show no comparison: no
        table and no sentence, which would not be true of them."""
        strings = STRINGS[language_now]
        compared = bool(current) and current.get("compared", True)
        parts = []
        for name in ("flags", "uncompared", "matches"):
            rows = compared and bool(current["lists"][name])
            parts += [gr.update(visible=rows), gr.update(value=strings[f"empty:{name}"], visible=compared and not rows)]
        undoable = bool(current) and bool(_readable_undoable(Path(cases_root) / current["case_id"]))
        return (gr.update(visible=compared), *parts, gr.update(visible=undoable))

    def undo_shown(case_id: str | None) -> dict:
        """"Ongedaan maken" pressed: the part to undo a write shown while the case chosen still has a write made in Exact and not
        undone, as record_shown shows it (its write log as it is now; one that cannot be read offers none), so that the part closes
        once its last write is undone; no case, and a document left to the buyer, has none."""
        undoable = bool(case_id) and not aid_status.is_item(case_id) and bool(_readable_undoable(Path(cases_root) / case_id))
        return gr.update(visible=undoable)

    def review_texts(language_now: str) -> tuple:
        """The language switched: the headings of the documents, of the comparison and of the part to undo a write."""
        strings = STRINGS[language_now]
        return f"## {strings['documents']}", f"## {strings['record']}", f"### {strings['undo_title']}"

    def failed_relabel(language_now: str, case_id: str | None) -> tuple:
        """The language switched: the words of the notice of a failed case on screen, or of a document left to the buyer that waits
        for him, and of "Als afgehandeld noteren"; the notice and that button hidden, as choosing the case now would (case_framed),
        when the case or the document no longer waits for the buyer (noted as handled in another browser tab meanwhile), so that no
        notice stays in the language it was shown in and no button for what needs it no more. A file that cannot be read leaves the
        notice as it is."""
        words = STRINGS[language_now]["handled_go"]
        go, gone = gr.update(value=words), (gr.update(value="", visible=False), gr.update(value=words, visible=False))
        if aid_status.is_item(case_id):
            item = aid_status.look_item(cases_root, case_id)
            return (gr.update(value=item_text(item, language_now)), go) if item is not None and item.state == "look" else gone
        try:
            line = next((line for line in lines_now() if line.case_id == case_id), None) if case_id else None
        except (OSError, ValueError):
            return gr.update(), go
        return (gr.update(value=failed_text(line, language_now)), go) if line is not None and line.state == "failed" else gone

    def oldest_opened(language_now: str) -> dict:
        """"Oudste zaak openen": the oldest case in review, else the oldest failed one, else the oldest document the intake left to
        the buyer that waits for him, chosen, with the case list as it is now, so that it is among its choices; nothing when none
        waits."""
        try:
            lines, items = lines_now(), items_now()
            offered = choices(language_now, lines, items)
        except (OSError, ValueError):
            return gr.update()
        ids = {case_id for _, case_id in offered}
        waiting = [line.case_id for line in sorted((line for line in lines if line.state in aid_status.NEEDS and line.case_id in ids),
                                                   key=lambda line: (line.state != "review", line.opened_at, line.case_id))]
        waiting += [item.item_id for item in items if item.state == "look" and item.item_id in ids]       # oldest first already
        return gr.update(choices=offered, value=waiting[0]) if waiting else gr.update()

    def handled_marked(case_id: str | None, language_now: str) -> tuple:
        """"Als afgehandeld noteren" on a failed case: its mark, handled_by_hand.json in its folder with the time of the page's clock,
        the one file written, and the case list without it; a mark that stands already (from another browser tab) stays as it is,
        the first counting. A case that did not fail is refused, and its folder is left as it is. A document the intake left to the
        buyer is marked by item_marked."""
        strings = STRINGS[language_now]
        if not case_id:
            return strings["no_case"], gr.update()
        if aid_status.is_item(case_id):
            return item_marked(case_id, language_now)
        try:
            case = load_case(cases_root, case_id)
            failed = _saved_result(case).status == "failed"
        except (OSError, ValueError):
            failed = False
        if not failed:
            return strings["refused"], gr.update()
        try:
            with open(case.root / aid_status.HANDLED_FILE, "x", encoding="utf-8") as noted:
                noted.write(json.dumps({"at": live.now().clock().isoformat(timespec="seconds")}) + "\n")
        except FileExistsError:
            pass
        except OSError as error:
            return strings["settings_failed"].format(code=type(error).__name__), gr.update()
        return (strings["handled_done"].format(po=_literal(case.po_number) if case.po_number else strings["unknown_po"]),
                gr.update(choices=choices(language_now), value=None))

    def item_marked(item_id: str, language_now: str) -> tuple:
        """"Als afgehandeld noteren" on a document the intake left to the buyer: its mark (handled_mark of aid/review/status.py), with
        the time of the page's clock, appended to review_items_handled.jsonl in the cases folder in one write, the one file written,
        and the case list without it; a mark that stands already (from another browser tab) is not written again, the first counting.
        An id that no line of review_items.jsonl gives is refused, and nothing is written."""
        strings = STRINGS[language_now]
        item = aid_status.look_item(cases_root, item_id)
        if item is None:
            return strings["refused"], gr.update()
        if item.handled_at is None:
            try:
                _append_line(Path(cases_root) / aid_status.HANDLED_ITEMS_FILE,
                             json.dumps(aid_status.handled_mark(item, live.now().clock()), ensure_ascii=False))
            except OSError as error:
                return strings["settings_failed"].format(code=type(error).__name__), gr.update()
        return strings["item_handled_done"].format(file=_literal(item.file)), gr.update(choices=choices(language_now), value=None)

    def strip_acted(language_now: str) -> tuple:
        """The strip's button: the tab Instellingen, on its section that handles the most urgent problem as the aid's files tell it
        now (_PLACES, _SECTIONS), the one the button names, as the strip may be up to TICK_SECONDS old: "Stand van de assistent" for
        the runner and its launcher, "Postvak en AI" for the store of the sign-in, the mailbox and Copilot. A problem that went
        meanwhile, or files that cannot be read, open "Stand van de assistent", which says what the assistant is doing now. That
        section is read again after the press (assistant_shown, chained to it in build_app). Asks Microsoft nothing."""
        try:
            now_deps = live.now()
            now = now_deps.clock()
            found = aid_status.problems(aid_status.assistant(tab.file, cases_root, now_deps.settings, now),
                                        aid_status.case_lines(cases_root, now))
        except (OSError, ValueError):
            found = []
        return gr.update(selected=SETTINGS_TAB), gr.update(selected=_SECTIONS[_PLACES[found[0].kind] if found else "assistant"])

    try:                                         # what the page shows before its load reads it again
        first_lines, first_items, first_strip = lines_now(), items_now(), strip_read("nl")
    except (OSError, ValueError):
        first_lines, first_items, first_strip = [], [], _Strip("", None, first["review_all"].format(n=0), _all_html([], set(), "nl"))

    # delete_cache: every hour the page library removes the temporary files of this page that are older than an hour (an uploaded
    # file, the page images of a document), so a file goes between one and two hours after it was made, and all of them at a stop
    # with Ctrl-C in the page's own window. The launcher ends the page with terminate, which leaves them: the page's next start
    # empties <cases_root>/tmp, where they lie (aid/review/__main__.py).
    with gr.Blocks(analytics_enabled=False, title=first["title"], delete_cache=(3600, 3600)) as app:
        state = gr.State(None)
        wanted = gr.State(None)                      # the write of the query to undo, selected when its case is shown
        listed = gr.State(None)                      # the case list as queue_ticked sent it last
        ticker = gr.Timer(TICK_SECONDS)              # the strip, the count and the case list read again (see the module docstring)
        with gr.Row(elem_id="page-head"):            # the tool's name, and the language switch on its right (see the module docstring)
            title = gr.Markdown(f"# {first['title']}", elem_classes=["page-title"])
            language = gr.Radio(choices=[("Nederlands", "nl"), ("English", "en")], value="nl", label=first["language"], show_label=False,
                                scale=0, elem_classes=["language-switch"])
        with gr.Tabs(elem_classes=["page-tabs"]) as tabs:            # the page's five tabs; the settings tab has a gr.Tabs of its own
            with gr.Tab(first["review_tab"], id=REVIEW_TAB) as review_tab:
                with gr.Row(elem_id="review-strip-row"):
                    strip = gr.HTML(first_strip.html, apply_default_css=False, container=False, padding=False, js_on_load=None,
                                    elem_id="review-strip")
                    if acting:
                        strip_action = gr.Button(first_strip.action or "", visible=first_strip.action is not None, size="sm", scale=0,
                                                 min_width=240, elem_id="review-strip-action")
                with gr.Accordion(first_strip.fold, open=False, elem_id="review-all") as fold:
                    fold_table = gr.HTML(first_strip.table, apply_default_css=False, js_on_load=None, elem_id="review-all-table")
                with gr.Row():
                    case_choice = gr.Dropdown(choices=choices("nl", first_lines, first_items), value=None, label=first["case"], scale=4)
                    refresh = gr.Button(first["refresh"], scale=1)
                gone = gr.Markdown("", visible=False, elem_id="review-gone")
                with gr.Row(elem_id="review-pick-row") as pick_row:
                    pick = gr.Markdown(pick_text(first_lines, first_items, first), elem_id="review-pick", elem_classes=["notice"])
                    oldest = gr.Button(first["review_open_oldest"], visible=waits(first_lines, first_items), scale=0, min_width=200, elem_id="review-oldest")
                # A case in three parts, in the order of the work (see the module docstring): what the buyer decides, the close with
                # its answer, and what the decisions rest on. The first and the last show while a case is chosen (case_framed); the
                # close's part always stands, so that the answer of a close stays on the screen once the case's parts have closed.
                with gr.Column(visible=False, elem_id="review-case") as case_part:
                    banner = gr.Markdown(f"**{first['banner']}**", visible=False, elem_classes=["notice", "attention"])
                    hint = gr.Markdown("", visible=False, elem_id="review-hint", elem_classes=["notice"])
                    entered_line = gr.Markdown("", visible=False, elem_id="review-entered", elem_classes=["notice"])
                    failed_notice = gr.Markdown("", visible=False, elem_id="review-failed", elem_classes=["notice", "attention"])
                    decisions_title = gr.Markdown(f"## {first['decisions']}")

                    @gr.render(inputs=[state, language])
                    def decision_rows(current: dict | None, language_now: str):
                        if not current:
                            return
                        strings = STRINGS[language_now]
                        rows = [row for name in ("flags", "uncompared") for row in current["lists"][name] if _REVIEW_ITEM.fullmatch(row.item_id)]
                        for row in sorted(rows, key=lambda row: int(row.item_id[2:])):
                            chosen = current["decisions"].get(row.item_id, {})
                            # one row per item (spec 2.1.1): what is decided on the left, the choice on the right, and under the
                            # choice the tick of the article price beside the row's note, which answers the choice in the row; the
                            # left is a column of its own, as the browser widens a column by its scale and a Markdown by none
                            with gr.Row(elem_classes=["decision-row"]):
                                with gr.Column(scale=2):
                                    gr.Markdown(_describe(row, language_now), elem_classes=["decision-what"])
                                again = row.item_id in current.get("again", ())
                                with gr.Column(scale=3):
                                    # interactive given: the browser takes a rendered component that holds a value, and whose listener
                                    # it does not know yet, for a display only, and greys out a row on which a choice stands
                                    radio = gr.Radio(choices=_decision_choices(row, current["kind"], strings, again), value=chosen.get("decision"),
                                                     label=strings["decision"], key=f"decision-{current['case_id']}-{row.item_id}", interactive=True)
                                    with gr.Row(elem_classes=["decision-foot"]):
                                        tick = gr.Checkbox(value=chosen.get("article_update", row.item_id in current["articles"]), label=strings["article_update"], visible=row.field == "price",
                                                           key=f"article-{current['case_id']}-{row.item_id}", interactive=True)
                                        # its key begins with "note-", and it keeps none of its last answer when the rows are
                                        # rendered again (the language switch, the case shown again): a decision that stands says
                                        # "Opgeslagen." in the language of the page
                                        note = gr.Markdown(strings["row_saved"] if chosen.get("decision") else "", elem_classes=["row-note"],
                                                           key=f"note-{current['case_id']}-{row.item_id}", preserved_by_key=None)
                            save = saver(current["case_id"], row.item_id)
                            radio.input(save, inputs=[radio, tick, language, state], outputs=[note, hint], show_progress="hidden")
                            tick.input(save, inputs=[radio, tick, language, state], outputs=[note, hint], show_progress="hidden")
                with gr.Column(elem_id="review-close"):
                    with gr.Row():                   # a failed case has the second in place of the first
                        close_button = gr.Button(first["close"], variant="primary", visible=False)
                        handled = gr.Button(first["handled_go"], visible=False, elem_id="review-handled")
                    status = gr.Markdown("", elem_classes=["answer"])
                    with gr.Column(visible=False, elem_id="review-undo") as undo_part:     # its answer is the status line, above it
                        undo_title = gr.Markdown(f"### {first['undo_title']}")
                        with gr.Row():
                            undo_choice = gr.Dropdown(choices=[], value=None, label=first["undo_choice"], scale=4)
                            undo_button = gr.Button(first["undo"], scale=1)
                with gr.Column(visible=False, elem_id="review-evidence") as evidence_part:
                    documents_title = gr.Markdown(f"## {first['documents']}")
                    with gr.Row(elem_id="review-documents"):           # the purchase order on the left, the supplier's document right
                        po_gallery = gr.Gallery(label=first["po_document"], columns=1, height=560, object_fit="contain", elem_classes=["doc-view"])
                        doc_gallery = gr.Gallery(label=first["document"], columns=1, height=560, object_fit="contain", elem_classes=["doc-view"])
                    with gr.Column(elem_id="review-record") as record_part:          # each list, or one sentence in its place
                        record_title = gr.Markdown(f"## {first['record']}")
                        flags_title = gr.Markdown(f"### {first['flags']}")
                        flags_empty = gr.Markdown("", visible=False, elem_id="review-flags-empty", elem_classes=["empty-line"])
                        flags_table = gr.Dataframe(value=tables(empty, "nl")[0], interactive=False, wrap=True, column_widths=COLUMN_WIDTHS)
                        uncompared_title = gr.Markdown(f"### {first['uncompared']}")
                        uncompared_empty = gr.Markdown("", visible=False, elem_id="review-uncompared-empty", elem_classes=["empty-line"])
                        uncompared_table = gr.Dataframe(value=tables(empty, "nl")[2], interactive=False, wrap=True, column_widths=COLUMN_WIDTHS)
                        matches_title = gr.Markdown(f"### {first['matches']}")
                        matches_empty = gr.Markdown("", visible=False, elem_id="review-matches-empty", elem_classes=["empty-line"])
                        matches_table = gr.Dataframe(value=tables(empty, "nl")[1], interactive=False, wrap=True, column_widths=COLUMN_WIDTHS,
                                                     max_height=360)
            with gr.Tab(first["hand_tab"], id=hand_list.TAB) as hand_tab:
                _hand_tab(gr, app, language, hand_tab, live, tab, cases_root, ticker)
            with gr.Tab(first["pricelist_tab"], id=pricelist_page.TAB) as pricelist_tab:
                _pricelist_tab(gr, app, language, pricelist_tab, live, tab, cases_root)
            with gr.Tab(first["files_tab"], id=FILES_TAB) as files_tab:
                _files_tab(gr, app, language, files_tab, live, tab, cases_root, ticker)
            with gr.Tab(first["settings_tab"], id=SETTINGS_TAB) as settings_tab:
                # its five sections (None on a public link and without a settings file), which an action on another tab can select
                sections = _settings_tab(gr, app, language, settings_tab, live, tab, accounts)

        view = [banner, doc_gallery, po_gallery, flags_table, matches_table, uncompared_table, undo_choice, state, hint, entered_line]
        app.load(opened, inputs=language, outputs=[case_choice, wanted, tabs])
        refresh.click(refreshed, inputs=[language, case_choice], outputs=[case_choice, hint])
        case_choice.change(show, inputs=[case_choice, language, wanted], outputs=[*view, status])
        language.change(relabel, inputs=[language, case_choice, state],
                        outputs=[title, language, case_choice, refresh, banner, doc_gallery, po_gallery, flags_title, flags_table, decisions_title,
                                 matches_title, matches_table, uncompared_title, uncompared_table, close_button, undo_choice, undo_button,
                                 hint, entered_line])
        close_button.click(close, inputs=[case_choice, language], outputs=[status, case_choice]).then(
            lambda case_id, language_now: show(case_id, language_now)[:-1], inputs=[case_choice, language], outputs=view)
        undo_button.click(do_undo, inputs=[case_choice, undo_choice, language], outputs=[status, undo_choice]).then(
            undo_shown, inputs=case_choice, outputs=undo_part, show_progress="hidden")       # the part closes with the last write
        language.change(lambda language_now: (gr.update(label=review_label(lines_now(), language_now, items_now())),
                                               gr.update(label=STRINGS[language_now]["settings_tab"])),
                        inputs=language, outputs=[review_tab, settings_tab])
        # the strip, the fold, the count and the case list, read again with the timer, and a failed case (see the module docstring)
        every = [app.load, ticker.tick, refresh.click, language.change]
        gr.on(triggers=every, fn=strip_ticked, inputs=language, outputs=[strip, *([strip_action] if acting else []), fold, fold_table],
              show_progress="hidden")
        gr.on(triggers=every, fn=queue_ticked, inputs=[language, case_choice, listed],
              outputs=[review_tab, case_choice, pick_row, pick, oldest, gone, listed], show_progress="hidden")
        case_choice.change(case_framed, inputs=[case_choice, language],
                           outputs=[pick_row, pick, oldest, gone, failed_notice, close_button, handled, decisions_title, case_part, evidence_part],
                           show_progress="hidden")
        language.change(failed_relabel, inputs=[language, case_choice], outputs=[failed_notice, handled], show_progress="hidden")
        oldest.click(oldest_opened, inputs=language, outputs=case_choice, show_progress="hidden")
        handled.click(handled_marked, inputs=[case_choice, language], outputs=[status, case_choice], show_progress="hidden")
        # the lists of the case, each its table or its sentence, and the part to undo a write; the headings of the new parts
        gr.on(triggers=[state.change, language.change], fn=record_shown, inputs=[state, language],
              outputs=[record_part, flags_table, flags_empty, uncompared_table, uncompared_empty, matches_table, matches_empty, undo_part],
              show_progress="hidden", queue=False)
        language.change(review_texts, inputs=language, outputs=[documents_title, record_title, undo_title], show_progress="hidden", queue=False)
        if acting:              # bound once all tabs are made, as its outputs are the page's Tabs and the sections of Instellingen
            sections.read_after(strip_action.click(strip_acted, inputs=language, outputs=[tabs, sections.tabs], show_progress="hidden"))
    return app


# --- the tab "In Exact invoeren" ---

def _hand_tab(gr, app, language, hand_tab, live: _Live, tab: _Tab, cases_root: Path, ticker) -> None:
    """The content of the tab "In Exact invoeren" and its events, inside the tab (see the module docstring): the intro with the lines
    under it and "Lijst vernieuwen", the line of the changes that came in since the list was read (hand-new, hidden until the timer
    finds one), the two buttons for the ticked rows with the note that answers every press of the list right under them (hand-actions,
    which the page's style keeps at the top of the window while the list scrolls), one group of tick boxes with every open change, one
    under the other, the two buttons again, small, the dropdown of the blocks with its button, the dropdown of the price lists with its
    button (shown while a price list has article prices to enter), the section of the confirmations to attach in Exact, and last the
    section of the handled changes (its text, the dropdown "Afgehandelde wijziging" with its two buttons, a note). hand_tab is the tab
    itself, whose label counts the open changes and whose selection reads the list again; ticker is the page's gr.Timer, whose tick
    gives the label and the line of new changes (hand_label_ticked) and never reads the rows again. The list is read from
    <cases_root>/prepared (aid/execution/by_hand.py) and shown as aid/review/hand_list.py makes it; the rows of each block, and of
    each price list, as the last read showed them are kept in two gr.States, for the block's and the price list's button. A mark
    carries the time of the clock of the page's Deps. On a public share link and without a settings file the tab says only that it
    is not available, as the settings tab does, and offers nothing, nor is anything of it on the timer."""
    first = STRINGS["nl"]
    if tab.share or tab.file is None:
        key = "settings_share" if tab.share else "settings_no_file"
        notice = gr.Markdown(first[key])
        language.change(lambda language_now: (STRINGS[language_now][key], gr.update(label=STRINGS[language_now]["hand_tab"])),
                        inputs=language, outputs=[notice, hand_tab])
        return
    prepared = Path(cases_root) / FOLDER

    def shown(language_now: str, note: str = "", ticked: list[str] | None = None, chosen: str | None = None) -> tuple:
        """The tab as the list is now: its label with the number of open changes, the intro with the lines under it, the tick boxes
        with the rows ticked that are still listed, the dropdown of the blocks with the block chosen while it is listed, the note,
        and the rows of each block as this read shows them, for the block's button."""
        strings = STRINGS[language_now]
        found, passed = changes(prepared)
        open_ = [change for change in found if change.state == "open"]
        deps_now = live.now()
        listed = hand_list.blocks(open_, deps_now.store, cases_root, language_now)
        rows, offered = hand_list.tick_rows(listed), hand_list.block_choices(listed)
        label = strings["hand_tab_count"].format(n=len(open_)) if open_ else strings["hand_tab"]
        kept = [value for value in ticked or [] if value in {value for _, value in rows}]
        chosen = chosen if chosen in {block_id for _, block_id in offered} else None
        above = hand_list.intro(listed, passed, language_now, deps_now.settings.test_mode)     # in the test mode with its line
        return (gr.update(label=label), above, gr.update(choices=rows, value=kept),
                gr.update(choices=offered, value=chosen, label=strings["hand_block"]), note,
                {block_id: [value for _, value in block_rows] for block_id, _, block_rows in listed})

    def marked(values: list[str], what: str, language_now: str) -> tuple:
        """Mark the change of each row's value, by its entry ids, and show the list again with the note of how many changes were
        marked. A change that is no longer open (another browser tab marked it, or a newer document replaced it) is not marked and not
        counted; when none was, the note says that nothing was noted and why."""
        now = live.now().clock()
        done = sum(1 for value in values if mark(prepared, hand_list.ids_of(value), what, now) > 0)
        strings = STRINGS[language_now]
        if not done:
            return shown(language_now, strings["hand_nothing_noted"])
        return shown(language_now, strings[f"hand_{what}_one" if done == 1 else f"hand_{what}_many"].format(n=done))

    def failed(error: Exception, language_now: str) -> str:
        """The note of an error on this tab: for a list that cannot be understood (its first line lost the writer's columns:
        by_hand.ListUnread) a sentence of its own, which says that the file was changed outside the assistant and whom to tell;
        for any other error the page's note with the class of the error."""
        strings = STRINGS[language_now]
        return strings["hand_list_changed"] if isinstance(error, ListUnread) else strings["settings_failed"].format(code=type(error).__name__)

    def unread(error: Exception, language_now: str) -> tuple:
        """A list that cannot be read (a file of it cannot be opened at all, or any other error): the rows as they were, the label
        "In Exact invoeren (?)", and the note of the error (failed)."""
        strings = STRINGS[language_now]
        return (gr.update(label=strings["hand_tab_unread"]), gr.update(), gr.update(), gr.update(), failed(error, language_now), gr.update())

    def list_opened(language_now: str) -> tuple:
        """The page loaded, the tab selected, or "Lijst vernieuwen" pressed: the list read again."""
        try:
            return shown(language_now)
        except Exception as error:
            return unread(error, language_now)

    def changes_entered(ticked: list[str] | None, language_now: str) -> tuple:
        """"Aangevinkte regels zijn ingevoerd", above or below the rows: the ticked changes marked as entered in Exact; with nothing
        ticked nothing is written."""
        try:
            return marked(ticked, "entered", language_now) if ticked else shown(language_now, STRINGS[language_now]["hand_tick_first"])
        except Exception as error:
            return unread(error, language_now)

    def changes_dropped(ticked: list[str] | None, language_now: str) -> tuple:
        """"Aangevinkte regels niet invoeren", above or below the rows: the ticked changes marked as dropped, so that the aid no longer
        asks for them; with nothing ticked nothing is written."""
        try:
            return marked(ticked, "dropped", language_now) if ticked else shown(language_now, STRINGS[language_now]["hand_tick_first"])
        except Exception as error:
            return unread(error, language_now)

    def block_entered(chosen: str | None, seen: dict[str, list[str]] | None, language_now: str) -> tuple:
        """"Alles hiervan is ingevoerd": the changes of the chosen block as the page showed them when the list was last read
        (seen) marked as entered, each only while it is open, so that a change prepared after that read stays on the list; with no
        block chosen nothing is written."""
        try:
            if not chosen:
                return shown(language_now, STRINGS[language_now]["hand_tick_first"])
            return marked((seen or {}).get(chosen, []), "entered", language_now)
        except Exception as error:
            return unread(error, language_now)

    def pricelists_opened(language_now: str, chosen: str | None) -> tuple:
        """The page loaded, the tab selected, "Lijst vernieuwen" pressed, the language switched, or a button of the tab pressed: the
        price lists whose article prices are still to enter (hand_list.price_lists), as (their row, shown while there is one; the
        dropdown, with the list chosen kept while it is offered; the button in the language; the rows of each list as this read
        shows them, for the button). A list that cannot be read leaves the row as it was."""
        strings = STRINGS[language_now]
        try:
            lists = hand_list.price_lists([change for change in changes(prepared)[0] if change.state == "open"], cases_root, language_now)
        except Exception:
            return gr.update(), gr.update(label=strings["hand_pricelist"]), gr.update(value=strings["hand_pricelist_entered"]), gr.update()
        chosen = chosen if chosen in {case_id for _, case_id, _ in lists} else None
        return (gr.update(visible=bool(lists)),
                gr.update(choices=[(label, case_id) for label, case_id, _ in lists], value=chosen, label=strings["hand_pricelist"]),
                gr.update(value=strings["hand_pricelist_entered"]), {case_id: values for _, case_id, values in lists})

    def pricelist_entered(chosen: str | None, lists: dict[str, list[str]] | None, language_now: str) -> tuple:
        """"Alle artikelprijzen van deze prijslijst zijn ingevoerd": the article prices of the price list chosen, as the page showed
        them when its price lists were last read (lists), marked as entered, each only while it is open; with no list chosen nothing
        is written."""
        try:
            if not chosen:
                return shown(language_now, STRINGS[language_now]["hand_tick_first"])
            return marked((lists or {}).get(chosen, []), "entered", language_now)
        except Exception as error:
            return unread(error, language_now)

    def hand_relabel(language_now: str, ticked: list[str] | None, chosen: str | None) -> tuple:
        """The page's language switched: the tab in that language, with the rows ticked and the block chosen kept while they are
        listed, and the note cleared."""
        strings = STRINGS[language_now]
        try:
            listed = shown(language_now, "", ticked, chosen)
        except Exception as error:
            listed = unread(error, language_now)
        buttons = ("refresh", "hand_entered", "hand_dropped", "hand_entered", "hand_dropped", "hand_block_entered")
        return (*listed, *(gr.update(value=strings[key]) for key in buttons))      # an update each: gradio takes its value out as it applies it

    def sections(language_now: str, note: str = "", chosen: str | None = None) -> tuple:
        """The two sections under the list as the files are now: the handled changes (their text, the dropdown with the change chosen
        while it is listed, the two buttons in the language, the note) and the confirmations to attach in Exact."""
        strings = STRINGS[language_now]
        deps = live.now()
        now = deps.clock()
        rows = hand_list.done_rows(changes(prepared)[0], now, language_now, store=deps.store, cases_root=cases_root)
        chosen = chosen if chosen in {value for _, value in rows} else None
        return (hand_list.done_text(rows, language_now), gr.update(choices=rows, value=chosen, label=strings["hand_done_choice"]),
                gr.update(value=strings["hand_reopen"]), gr.update(value=strings["hand_put_back"]), note,
                hand_list.confirmations_text(hand_list.confirmations(prepared, now), cases_root, language_now))

    def sections_unread(error: Exception, language_now: str) -> tuple:
        """Sections that cannot be read: the controls as they were, and the note of the error (failed)."""
        return gr.update(), gr.update(), gr.update(), gr.update(), failed(error, language_now), gr.update()

    def done_opened(language_now: str, chosen: str | None) -> tuple:
        """The page loaded, the tab selected, "Lijst vernieuwen" pressed, the language switched, or a button of the list pressed:
        the two sections under the list read again, the change chosen kept while it is listed."""
        try:
            return sections(language_now, "", chosen)
        except Exception as error:
            return sections_unread(error, language_now)

    def pressed(chosen: str | None, language_now: str, act: Callable[[Change, str], str]) -> tuple:
        """A button of the handled changes: act on the change of the value chosen (the change that holds its first entry id now) and
        show the list and the sections again with its note; with nothing chosen nothing is written. A list that cannot be read is
        shown as list_opened shows it; an error of the press itself (a writer that cannot prepare the put-back) is named in the note
        by its class, as the settings tab names one."""
        strings = STRINGS[language_now]
        ids = hand_list.ids_of(chosen or "")
        try:
            change = next((change for change in changes(prepared)[0] if ids[0] in change.ids), None) if ids else None
        except Exception as error:
            return (*unread(error, language_now), *sections_unread(error, language_now))
        try:
            note = act(change, language_now) if change is not None else strings["hand_done_choose_first"]   # nothing chosen, or gone
        except Exception as error:
            note = failed(error, language_now)
        try:
            return (*shown(language_now), *sections(language_now, note, chosen))
        except Exception as error:
            return (*unread(error, language_now), *sections_unread(error, language_now))

    def taken_back(change: Change, language_now: str) -> str:
        """"Weer op de lijst zetten": a change the buyer ticked or dropped himself put on the list again; not a change the aid saw. A
        change that stays off the list says why: it was put back (its put-back would cancel it: the buyer asked for the value from
        before), or another change of its line or article came in after it."""
        strings = STRINGS[language_now]
        if change.state == "seen":
            return strings["hand_reopen_seen"]
        if change.state not in ("entered", "dropped"):
            return strings["hand_reopened"] if change.state == "open" else strings["hand_reopen_not"]
        state = reopen(prepared, change.ids, live.now().clock())
        if state == "open":
            return strings["hand_reopened"]
        return strings["hand_reopen_put_back"] if state == "undone" else strings["hand_reopen_not"]

    def change_reopened(chosen: str | None, language_now: str) -> tuple:
        """"Weer op de lijst zetten" on the handled change chosen (see taken_back)."""
        return pressed(chosen, language_now, taken_back)

    def change_put_back(chosen: str | None, language_now: str) -> tuple:
        """"Terugdraaien in Exact" on the handled change chosen: the old value prepared as a change to enter (hand_list.take_back)."""
        deps = live.now()
        return pressed(chosen, language_now, lambda change, language_then: hand_list.take_back(deps, change, deps.clock(), language_then))

    def hand_label_ticked(language_now: str, seen: dict[str, list[str]] | None) -> tuple:
        """The page's timer ticked: (the tab's label with the number of open changes, as a read of the list gives it; the line under
        the intro that says how many open changes came in that the list on screen does not hold, hidden while none did). A change is
        held when one of its entry ids stands in a row of `seen`, the rows of each block as the last read of the list showed them, so
        that a change a second case asked too is not new, and a newer value of a row is. It reads the list and writes nothing, and
        gives neither the rows, the note nor the blocks, so that what the buyer ticked stays as it is; a list that cannot be read
        leaves the label and the line as they are."""
        strings = STRINGS[language_now]
        try:
            open_ = [change for change in changes(prepared)[0] if change.state == "open"]
        except Exception:
            return gr.update(), gr.update()
        held = {entry_id for values in (seen or {}).values() for value in values for entry_id in hand_list.ids_of(value)}
        new = sum(1 for change in open_ if not held & set(change.ids))
        label = strings["hand_tab_count"].format(n=len(open_)) if open_ else strings["hand_tab"]
        if not new:
            return gr.update(label=label), gr.update(value="", visible=False)
        line = strings["hand_new_rows_one"] if new == 1 else strings["hand_new_rows_many"].format(n=new)
        return gr.update(label=label), gr.update(value=line, visible=True)

    def hand_new_cleared() -> dict:
        """The list on screen read again (the tab selected, "Lijst vernieuwen", the language switch, a press of the tab): the line of
        the changes that came in hidden, as the list now holds them."""
        return gr.update(value="", visible=False)

    with gr.Row():                                   # in columns, whose scale a row keeps, as it does not keep a Markdown's
        with gr.Column(scale=4):
            intro = gr.Markdown(first["hand_intro"], elem_id="hand-intro")
        with gr.Column(scale=1, min_width=160):
            refresh = gr.Button(first["refresh"])
    # shown by the timer while changes came in that the list does not show, in the look of every standing message
    new_rows = gr.Markdown("", visible=False, elem_id="hand-new", elem_classes=["notice"])
    with gr.Column(elem_id="hand-actions"):          # the pair and its answer, which the page's style keeps in view while the list scrolls
        with gr.Row():
            entered_above = gr.Button(first["hand_entered"], variant="primary")          # the one filled button of the tab
            dropped_above = gr.Button(first["hand_dropped"])
        note = gr.Markdown("", elem_id="hand-note", elem_classes=["answer"])           # the answer of every press of the list
    rows = gr.CheckboxGroup(choices=[], value=[], show_label=False, elem_id="hand-rows", elem_classes=["hand-rows"])
    with gr.Row():                                   # the same pair under a long list, small
        entered = gr.Button(first["hand_entered"], size="sm")
        dropped = gr.Button(first["hand_dropped"], size="sm")
    with gr.Row():
        block = gr.Dropdown(choices=[], value=None, label=first["hand_block"], scale=4)
        block_button = gr.Button(first["hand_block_entered"], scale=1)
    with gr.Row(visible=False, elem_id="hand-pricelists") as pricelist_row:       # shown while a price list has article prices to enter
        pricelist = gr.Dropdown(choices=[], value=None, label=first["hand_pricelist"], scale=4)
        pricelist_button = gr.Button(first["hand_pricelist_entered"], scale=1)
    confirmations = gr.Markdown("", elem_id="hand-confirmations")         # a task of every day, before the handled changes
    done = gr.Markdown(f"**{first['hand_done']}**", elem_id="hand-done")
    with gr.Row():
        done_choice = gr.Dropdown(choices=[], value=None, label=first["hand_done_choice"], scale=4)
        reopen_button = gr.Button(first["hand_reopen"], scale=1)
        put_back_button = gr.Button(first["hand_put_back"], scale=1)
    done_note = gr.Markdown("", elem_id="hand-done-note")
    gr.HTML("", head=f"<style>{_ONE_PER_LINE}</style>", container=False, padding=False, js_on_load=None)   # last, so its space shows nowhere
    seen = gr.State({})                              # {block id: [the values of its rows]} as the last read of the list showed them
    listed_prices = gr.State({})                     # {the case id of a price list: [the values of its rows]} as last read

    listed = [hand_tab, intro, rows, block, note, seen]
    under = [done, done_choice, reopen_button, put_back_button, done_note, confirmations]
    gr.on(triggers=[app.load, hand_tab.select, refresh.click], fn=list_opened, inputs=language, outputs=listed)
    presses = [gr.on(triggers=[entered_above.click, entered.click], fn=changes_entered, inputs=[rows, language], outputs=listed),
               gr.on(triggers=[dropped_above.click, dropped.click], fn=changes_dropped, inputs=[rows, language], outputs=listed),
               block_button.click(block_entered, inputs=[block, seen, language], outputs=listed),
               pricelist_button.click(pricelist_entered, inputs=[pricelist, listed_prices, language], outputs=listed)]
    language.change(hand_relabel, inputs=[language, rows, block],
                    outputs=[*listed, refresh, entered_above, dropped_above, entered, dropped, block_button])
    gr.on(triggers=[app.load, hand_tab.select, refresh.click, language.change], fn=done_opened, inputs=[language, done_choice], outputs=under)
    for press in presses:                            # a tick or a drop moves a change into the handled ones: read them after it
        press.then(done_opened, inputs=[language, done_choice], outputs=under)
    lists = [pricelist_row, pricelist, pricelist_button, listed_prices]
    gr.on(triggers=[app.load, hand_tab.select, refresh.click, language.change], fn=pricelists_opened, inputs=[language, pricelist], outputs=lists)
    presses += [reopen_button.click(change_reopened, inputs=[done_choice, language], outputs=[*listed, *under]),
                put_back_button.click(change_put_back, inputs=[done_choice, language], outputs=[*listed, *under])]
    for press in presses:                            # any press may take a price list's last row off the list, or put one back
        press.then(pricelists_opened, inputs=[language, pricelist], outputs=lists)
    # the label on the page's timer, never the rows (see hand_label_ticked), and its line of new changes gone once the list is read again
    ticker.tick(hand_label_ticked, inputs=[language, seen], outputs=[hand_tab, new_rows], show_progress="hidden")
    gr.on(triggers=[hand_tab.select, refresh.click, language.change, entered_above.click, entered.click, dropped_above.click, dropped.click,
                    block_button.click, pricelist_button.click, reopen_button.click, put_back_button.click],
          fn=hand_new_cleared, outputs=new_rows, show_progress="hidden", queue=False)


# --- the tab "Prijslijst" ---

def _pricelist_tab(gr, app, language, pricelist_tab, live: _Live, tab: _Tab, cases_root: Path) -> None:
    """The content of the tab "Prijslijst" and its events, inside the tab (see the module docstring; aid/review/pricelist_page.py has
    what it shows and writes, aid/pricelist.py the stored article prices): a notice, and under it, shown only while the columns of the
    export of articles are set, the introduction, the supplier with the small press that asks to take its stored prices away, the row
    of that question with "Ja, weghalen" and "Niet weghalen" (shown by the press), the answer of a removal and the state of the
    supplier's stored prices, the export of articles with "Inkoopprijzen opnemen" and a note, and, shown only for a supplier with
    stored prices, the price list with its five column fields, "Bekijken", the table, a note and "Op de lijst In Exact invoeren
    zetten"; after "Bekijken" the browser brings that note, the count line, into view (_COUNT_INTO_VIEW). The two file fields are
    140 px high and the table at most 320 px, so that a view and its press fit the window. pricelist_tab is the tab itself. The
    settings are those of the page's own actions as they are now (live), and a time is the clock's of its Deps. Two states of the
    page keep the upload of the export stored last (so that one press stores one export) and what the last view showed (so that the
    press carries out that view, or nothing). On a public share link and without a settings file the tab says only that it is not
    available, as the settings tab does, and offers nothing."""
    first = STRINGS["nl"]
    if tab.share or tab.file is None:
        key = "settings_share" if tab.share else "settings_no_file"
        notice = gr.Markdown(first[key])
        language.change(lambda language_now: (STRINGS[language_now][key], gr.update(label=STRINGS[language_now]["pricelist_tab"])),
                        inputs=language, outputs=[notice, pricelist_tab])
        return
    columns_of = pricelist_page.LIST_COLUMNS

    def stored_now() -> tuple[dict, bool]:
        """The stored article prices, and whether their file could be read: one that cannot is nothing stored."""
        try:
            return stored_prices(cases_root), True
        except (OSError, ValueError):
            return {}, False

    def table_of(rows: list[list[str]], language_now: str) -> dict:
        return {"headers": pricelist_page.headers(language_now), "data": rows}

    def state_of(name: str, stored: dict, language_now: str) -> str:
        """The state line of the supplier, its name and the file of its export shown as they are (_literal); for a name that is no
        supplier's name (is_supplier_name: too long, or with a line break or a control character) the sentence that says so."""
        if not is_supplier_name(name):
            return STRINGS[language_now]["pricelist_refused:name"]
        held = stored.get(name)
        return pricelist_page.state_line(_literal(name), held._replace(file=_literal(held.file)) if held is not None else None, language_now)

    def notice_of(readable: bool, strings: dict[str, str]) -> dict:
        """The notice above the tab while the columns are set: nothing, or that the stored prices cannot be read."""
        return gr.update(value="" if readable else strings["pricelist_stored_unreadable"], visible=not readable)

    def offered(titles: list[str], strings: dict[str, str]) -> list[tuple[str, str]]:
        """The choices of a column field: the choice for no column, then the titles of the list."""
        return [(strings["exact_export_no_column"], ""), *[(title, title) for title in titles]]

    def fields(list_now: str | None, name: str, language_now: str) -> tuple[list, str]:
        """The five column fields for the list chosen and the supplier (pricelist_page.columns_for, with the map kept for the supplier
        beside the stored article prices: kept_columns of aid/pricelist.py, never the settings file), and the note of a list that
        the tab refuses ("" for none). Without a list, and for a list refused, the fields are emptied. Kept columns that cannot
        be opened leave the fields as for a first list."""
        strings = STRINGS[language_now]
        emptied = [gr.update(choices=offered([], strings), value="") for _ in columns_of]
        if not list_now:
            return emptied, ""
        path = Path(list_now)
        try:
            titles = pricelist_page.list_titles(path)
        except (pricelist_page.ListRefused, ExportRefused) as refused:
            return emptied, pricelist_page.refusal_text(refused, _literal(path.name), language_now, _literal)
        except Exception as error:
            return emptied, strings["settings_failed"].format(code=type(error).__name__)
        try:
            kept = kept_columns(cases_root)
        except OSError:                                  # the kept columns are a help: without them the fields come as for a first list
            kept = {}
        filled = pricelist_page.columns_for(titles, kept.get(supplier_named(name, kept)))
        return [gr.update(choices=offered(titles, strings), value=filled[column]) for column in columns_of], ""

    def unviewed(language_now: str, note: str = "") -> tuple:
        """No view to carry out: (the table emptied, the note, the press inactive, no view kept)."""
        return table_of([], language_now), note, gr.update(interactive=False), None

    def pricelist_opened(supplier_now: str | None, language_now: str) -> tuple:
        """The page loaded, or the tab selected: (the notice, the tab's content, the supplier, the state of its stored prices, the
        part of the price list). While the columns of the export of articles are not set, the notice says so and nothing else is
        shown. Else the dropdown offers the suppliers with stored prices and keeps the supplier chosen, by its stored name when it
        has one; the part of the price list is shown for a supplier with stored prices only."""
        strings = STRINGS[language_now]
        if not live.settings().article_columns:
            return gr.update(value=strings["pricelist_unset"], visible=True), gr.update(visible=False), gr.update(), gr.update(), gr.update()
        stored, readable = stored_now()
        name = supplier_named(supplier_now, stored)
        return (notice_of(readable, strings), gr.update(visible=True), gr.update(choices=list(stored), value=name or None),
                state_of(name, stored, language_now), gr.update(visible=name in stored))

    def pricelist_supplier_chosen(supplier_now: str | None, list_now: str | None, language_now: str) -> tuple:
        """Another supplier chosen or typed: (the state of its stored prices, the part of the price list, the five column fields
        filled for it, the table emptied, the note, the press inactive, no view kept). A view belongs to the supplier it was made
        for."""
        stored, _ = stored_now()
        name = supplier_named(supplier_now, stored)
        filled, refusal = fields(list_now, name, language_now)
        return (state_of(name, stored, language_now), gr.update(visible=name in stored), *filled, *unviewed(language_now, refusal))

    def pricelist_export_chosen(upload: str | None) -> dict:
        """The field of the export changed: "Inkoopprijzen opnemen" is active while a file is chosen, and only then."""
        return gr.update(interactive=bool(upload))

    def prices_filed(upload: str | None, supplier_now: str | None, language_now: str, given: tuple | None) -> tuple:
        """"Inkoopprijzen opnemen": the article prices of the export stored for its supplier (store_current), and (the note, the field
        emptied, the supplier, the state of its stored prices, the part of the price list, the upload stored last, the notice). The
        supplier is the one the export's supplier column names, else the one chosen, without whom nothing is stored. A file that is
        refused is named as it was given, with the reason; an error says so in the note; either way nothing is stored. One press
        stores one export: the same upload again (a double click) and a press that finds the field emptied by the press before it
        change nothing."""
        strings = STRINGS[language_now]
        key = _upload_key(upload) if upload else None
        if (not upload and given is not None) or (key is not None and key == given):
            return tuple([gr.update()] * 7)
        settings = live.settings()
        columns, typed = settings.article_columns, (supplier_now or "").strip()
        filed = None
        if not columns:
            note = strings["pricelist_unset"]
        elif not upload:
            note = strings["files_choose_first"]
        else:
            path = Path(upload)
            try:
                pricelist_page.checked_export(path)
                if "supplier" not in columns and not typed:
                    note = strings["pricelist_refused:supplier"]
                elif "supplier" not in columns and not is_supplier_name(typed):
                    note = strings["pricelist_refused:name"]
                else:
                    filed, kept, left_out = store_current(cases_root, path, columns, settings.article_decimal_comma, live.now().clock(), typed)
                    note = pricelist_page.filed_line([_literal(name) for name in filed], kept, left_out, language_now)
            except (pricelist_page.ListRefused, ExportRefused, NoArticle, NameRefused) as refused:
                note = pricelist_page.refusal_text(refused, _literal(path.name), language_now, _literal)
            except Exception as error:
                note = strings["settings_failed"].format(code=type(error).__name__)
        stored, readable = stored_now()
        typed = typed if is_supplier_name(typed) else ""         # a name that is none is no supplier chosen
        name = filed[0] if filed is not None and len(filed) == 1 else supplier_named(typed, stored)
        return (note, gr.update(value=None), gr.update(choices=list(stored), value=name or None), state_of(name, stored, language_now),
                gr.update(visible=name in stored), key, notice_of(readable, strings) if columns else gr.update())

    def prices_removed(supplier_now: str | None, language_now: str) -> tuple:
        """"Ja, weghalen", under the question that "Inkoopprijzen van deze leverancier weghalen" asks (remove_asked): the stored
        article prices of the supplier chosen or typed and the columns kept for its price list taken away (remove_stored), whole or
        not at all, and nothing else: no row of the list "In Exact invoeren" changes. It gives (the note, the supplier, the state of
        its stored prices, the part of the price list, the table emptied, the list's note, the press inactive, no view kept, the
        notice): after a removal no supplier is chosen, and the note names the supplier removed as it was stored. For no name, a name
        that is none and a name the aid holds no prices of, the note is the tab's own sentence for that and nothing else changes; an
        error says so in the note. The note is that of the export, as before the question was asked; removal_shown, chained after
        this press, moves it right under the supplier's row."""
        strings = STRINGS[language_now]
        typed = (supplier_now or "").strip()
        try:
            name = remove_stored(cases_root, typed) if typed and is_supplier_name(typed) else None
        except Exception as error:
            return (strings["settings_failed"].format(code=type(error).__name__), *[gr.update()] * 8)
        if name is None:
            return (state_of(typed, {}, language_now), *[gr.update()] * 8)
        stored, readable = stored_now()
        return (strings["pricelist_removed"].format(supplier=_literal(name)), gr.update(choices=list(stored), value=None),
                state_of("", stored, language_now), gr.update(visible=False), *unviewed(language_now), notice_of(readable, strings))

    def remove_asked(supplier_now: str | None, language_now: str) -> tuple:
        """"Inkoopprijzen van deze leverancier weghalen": nothing is taken away yet, the tab only asks: (the row of the question right
        under the supplier's field, shown; the question, which says how many stored prices of which supplier go, by its stored name;
        the answer under that row, emptied). For no name, a name that is none and a name the aid holds no prices of, the row stays
        closed and the answer is the tab's own sentence for that, as prices_removed gives it; stored prices that cannot be read are
        named as such. Nothing is written."""
        strings = STRINGS[language_now]
        typed = (supplier_now or "").strip()
        try:
            stored = stored_prices(cases_root) if typed and is_supplier_name(typed) else {}
        except (OSError, ValueError):
            return gr.update(visible=False), "", strings["pricelist_stored_unreadable"]
        name = supplier_named(typed, stored)
        if name not in stored:
            return gr.update(visible=False), "", state_of(typed, {}, language_now)
        count = len(stored[name].prices)
        question = (strings["pricelist_remove_ask_one"].format(supplier=_literal(name)) if count == 1
                    else strings["pricelist_remove_ask"].format(n=count, supplier=_literal(name)))
        return gr.update(visible=True), question, ""

    def remove_kept() -> dict:
        """"Niet weghalen", or another supplier chosen or typed: the row of the question closed, so that "Ja, weghalen" never takes away
        the prices of another supplier than the question named. Nothing is taken away, and the answer under the row stays."""
        return gr.update(visible=False)

    def removal_shown(note: str | None) -> tuple:
        """After "Ja, weghalen" (prices_removed, which gives its sentence as the note of the export, as it always did): (the row of the
        question closed; that sentence right under the supplier's row, where the buyer pressed; the note of the export, far lower on
        the tab, emptied)."""
        return gr.update(visible=False), note or "", ""

    def removal_relabelled(language_now: str) -> tuple:
        """The page's language switched: the row of the question closed, the question and the answer under the row emptied, as
        pricelist_relabel empties the tab's notes, and "Ja, weghalen" and "Niet weghalen" in that language."""
        strings = STRINGS[language_now]
        return (gr.update(visible=False), "", gr.update(value=strings["pricelist_remove_yes"]), gr.update(value=strings["pricelist_remove_no"]),
                "")

    def pricelist_file_chosen(list_now: str | None, supplier_now: str | None, language_now: str) -> tuple:
        """A price list chosen, or taken away: (the five column fields filled for it, the table emptied, the note, the press inactive,
        no view kept). A list that the tab refuses is named by its sentence."""
        filled, refusal = fields(list_now, supplier_named(supplier_now, stored_now()[0]), language_now)
        return (*filled, *unviewed(language_now, refusal))

    def pricelist_column_chosen() -> tuple:
        """A column field changed by the buyer: the press is inactive and no view is kept, until the list is viewed again."""
        return gr.update(interactive=False), None

    def judged(list_now: str | None, supplier_now: str | None, chosen: tuple, language_now: str) -> tuple:
        """What a view and a press work from, or the note that says why there is nothing to view: (a _Judged: the supplier by its
        stored name, the column map of the form, the supplier's current prices, when its export was stored, the articles that
        another supplier holds too, by its stored prices or by an open change on the list "In Exact invoeren", with those
        suppliers' names, the import's result with nothing written, the look ahead that the result was made with, and the
        articles of which a row kept off the list leaves an older price of this supplier on the list; None), or (None, the note). The
        note is: a name that is no supplier's name; no supplier chosen; no stored prices of the supplier; no list chosen; a list
        that the tab refuses; a column field that is missing or chosen twice; or the class of any other error."""
        strings = STRINGS[language_now]
        if not is_supplier_name(supplier_now):
            return None, strings["pricelist_refused:name"]
        stored = stored_now()[0]
        name = supplier_named(supplier_now, stored)
        try:
            current, stored_at = current_prices(cases_root, name) if name else ({}, None)
            if stored_at is None:
                return None, pricelist_page.state_line(_literal(name), None, language_now)
            if not list_now:
                return None, strings["files_choose_first"]
            path = Path(list_now)
            pricelist_page.list_titles(path)
            columns, problems = pricelist_page.column_map(dict(zip(columns_of, chosen)), language_now, _literal)
            if columns is None:
                return None, " ".join(problems)
            pricelist_page.checked_columns(path, columns)
            on_the_list = pricelist_page.listed(cases_root, stored_at)
            shared = with_others(also_held(stored, name), others_asking(on_the_list.asked, name, cases_root))
            result = pricelist_page.preview(path, name, current, columns, live.now().clock(), shared, on_the_list.after)
            older = pricelist_page.older_asked(result, name, on_the_list.asked)
            return _Judged(name, columns, current, stored_at, shared, result, on_the_list.after, older), None
        except (pricelist_page.ListRefused, ExportRefused) as refused:
            return None, pricelist_page.refusal_text(refused, _literal(Path(list_now).name), language_now, _literal)
        except Exception as error:
            return None, strings["settings_failed"].format(code=type(error).__name__)

    def view_of(list_now: str, found: _Judged) -> dict:
        """What a view showed, as the page keeps it: the upload, the supplier, the columns, the supplier's current prices, what the
        list would change (pricelist_page.plan), which holds the rows that the tab's own check keeps off the list by their reason,
        and the other suppliers that the table named for the rows kept off as shared."""
        plan = pricelist_page.plan(found.result)
        return {"upload": _upload_key(list_now), "supplier": found.name, "columns": found.columns, "current": found.current, "plan": plan,
                "shared": {row[0]: found.shared.get(row[0], []) for row in plan if row[1:] == [pricelist_page.SHARED]}}

    def pricelist_shown(list_now: str | None, supplier_now: str | None, *values) -> tuple:
        """"Bekijken": the price list compared with the supplier's current prices, with nothing written anywhere: (the table, the count
        line, the press active, the view kept), or the table emptied and the note of why there is nothing to view."""
        *chosen, language_now = values
        found, note = judged(list_now, supplier_now, tuple(chosen), language_now)
        if found is None:
            return unviewed(language_now, note)
        lines = [pricelist_page.count_line(found.result, language_now), pricelist_page.older_line(found.older, language_now, _literal)]
        return (table_of(pricelist_page.shown(found.result, found.current, language_now, found.shared), language_now),
                " ".join(line for line in lines if line), gr.update(interactive=True), view_of(list_now, found))

    def pricelist_carried_out(list_now: str | None, supplier_now: str | None, *values) -> tuple:
        """"Op de lijst In Exact invoeren zetten": the view that the page showed carried out (pricelist_page.carry_out), and the
        list's columns kept for the supplier beside the stored article prices (keep_columns): (the table, the note, the press
        inactive, no view kept). Without a view nothing happens, the note and the table included: one view is carried out once.
        When the list, the supplier, the columns, the supplier's current prices or what the list would change is no longer what the
        view showed, nothing is carried out and the note says to view the list again. After the press the table says what became
        of each row: a change that could not be prepared reads "niet gelukt", and the note names the articles of those rows. When
        the price list's own write log could not be written, the changes stand on the list all the same: the table is shown, the
        columns are kept, and the note says after the count line that the aid cannot put these changes back itself. When the
        columns could not be kept, the note says so after the count line. The settings file is never written."""
        *chosen, viewed, language_now = values
        strings = STRINGS[language_now]
        if not isinstance(viewed, dict):
            return gr.update(), gr.update(), gr.update(interactive=False), None
        found, _ = judged(list_now, supplier_now, tuple(chosen), language_now)
        if found is None or view_of(list_now, found) != viewed:
            return gr.update(), strings["pricelist_changed"], gr.update(interactive=False), None
        try:
            try:
                done, logged = pricelist_page.carry_out(Path(list_now), found.name, found.current, found.columns, cases_root,
                                                        live.now().clock(), found.shared, found.stored_at, found.after), True
            except pricelist_page.LogNotWritten as lost:     # the changes are on the list; only the list's own log is not written
                done, logged = lost.result, False
            notes = [pricelist_page.carried_line(pricelist_page.on_list(done, cases_root), language_now)]
            notes += [line for line in [pricelist_page.not_prepared_line(done, language_now, _literal)] if line]
            notes += [] if logged else [strings["pricelist_log_lost"]]
        except Exception as error:
            return gr.update(), strings["settings_failed"].format(code=type(error).__name__), gr.update(interactive=False), None
        try:
            keep_columns(cases_root, found.name, found.columns)
        except Exception:                                # the changes are on the list; only the columns were not kept
            notes.append(strings["pricelist_columns_lost"])
        notes += [line for line in [pricelist_page.older_line(found.older, language_now, _literal)] if line]
        return (table_of(pricelist_page.shown(done, found.current, language_now, found.shared), language_now), " ".join(notes),
                gr.update(interactive=False), None)

    def pricelist_relabel(language_now: str, supplier_now: str | None, list_now: str | None) -> tuple:
        """The page's language switched: the tab in that language, with the supplier and the files kept; the notes and the table are
        emptied and no view is kept, for "Bekijken" to compare the list again in that language."""
        strings = STRINGS[language_now]
        stored, readable = stored_now()
        name = supplier_named(supplier_now, stored)
        unset = not live.settings().article_columns
        notice_now = gr.update(value=strings["pricelist_unset"], visible=True) if unset else notice_of(readable, strings)
        try:
            titles = pricelist_page.list_titles(Path(list_now)) if list_now else []
        except Exception:
            titles = None                                # a list that the tab refuses: the fields as they are
        labelled = [gr.update(label=strings[f"pricelist_column:{column}"], **({} if titles is None else {"choices": offered(titles, strings)}))
                    for column in columns_of]
        return (gr.update(label=strings["pricelist_tab"]), notice_now, strings["pricelist_intro"],
                gr.update(label=strings["pricelist_supplier"], info=strings["pricelist_supplier_help"]),
                gr.update(value=strings["pricelist_remove"]), state_of(name, stored, language_now),
                gr.update(label=strings["pricelist_export"]), gr.update(value=strings["pricelist_file_prices"]), "",
                gr.update(label=strings["pricelist_list"]), *labelled, strings["pricelist_columns_hint"],
                gr.update(value=strings["pricelist_view"]), table_of([], language_now), "",
                gr.update(value=strings["pricelist_carry_out"], interactive=False), None)

    notice = gr.Markdown("", visible=False, elem_id="pricelist-notice")
    with gr.Column(visible=False, elem_id="pricelist-body") as body:
        intro = gr.Markdown(first["pricelist_intro"], elem_id="pricelist-intro")
        with gr.Row():                               # the press that asks to take a supplier's stored prices away, beside its field
            supplier = gr.Dropdown(choices=[], value=None, label=first["pricelist_supplier"], info=first["pricelist_supplier_help"],
                                   allow_custom_value=True, elem_id="pricelist-supplier", scale=4)
            # small, at the width of its words (scale 0 and the page's style), level with the field
            remove = gr.Button(first["pricelist_remove"], elem_id="pricelist-remove", scale=0, variant="stop", size="sm")
        with gr.Row(visible=False, elem_id="pricelist-remove-confirm") as confirm:      # its question, shown by the press, and the answers
            with gr.Column(scale=4):
                ask = gr.Markdown("", elem_id="pricelist-remove-ask", latex_delimiters=[])                    # a name as it is
            yes_button = gr.Button(first["pricelist_remove_yes"], variant="stop", elem_id="pricelist-remove-yes", scale=1)
            no_button = gr.Button(first["pricelist_remove_no"], elem_id="pricelist-remove-no", scale=1)
        remove_note = gr.Markdown("", elem_id="pricelist-remove-note", elem_classes=["answer"], latex_delimiters=[])   # what a removal did
        state = gr.Markdown(first["pricelist_choose_supplier"], elem_id="pricelist-state", latex_delimiters=[])   # a name as it is
        export = gr.File(label=first["pricelist_export"], type="filepath", elem_id="pricelist-export", height=140)  # any file: the tab names another type
        file_button = gr.Button(first["pricelist_file_prices"], interactive=False, elem_id="pricelist-file-prices")
        export_note = gr.Markdown("", elem_id="pricelist-export-note", latex_delimiters=[])
        with gr.Column(visible=False, elem_id="pricelist-list-part") as list_part:
            list_file = gr.File(label=first["pricelist_list"], type="filepath", elem_id="pricelist-list", height=140)   # any file, as the export's
            with gr.Row():
                chosen = [gr.Dropdown(choices=offered([], first), value="", label=first[f"pricelist_column:{column}"],
                                      elem_id=f"pricelist-column-{column}") for column in columns_of]
            hint = gr.Markdown(first["pricelist_columns_hint"], elem_id="pricelist-columns-hint")
            view = gr.Button(first["pricelist_view"], elem_id="pricelist-view")
            table = gr.Dataframe(value=table_of([], "nl"), interactive=False, wrap=True, elem_id="pricelist-table", max_height=320)
            note = gr.Markdown("", elem_id="pricelist-note", latex_delimiters=[])
            carry = gr.Button(first["pricelist_carry_out"], variant="primary", interactive=False, elem_id="pricelist-carry-out")
    given = gr.State(None)                           # the upload of the export stored last: one press stores one export
    viewed = gr.State(None)                          # what the last view showed; None: there is no view to carry out

    gr.on(triggers=[app.load, pricelist_tab.select], fn=pricelist_opened, inputs=[supplier, language],
          outputs=[notice, body, supplier, state, list_part])
    supplier.change(pricelist_supplier_chosen, inputs=[supplier, list_file, language],
                    outputs=[state, list_part, *chosen, table, note, carry, viewed])
    export.change(pricelist_export_chosen, inputs=export, outputs=file_button)
    file_button.click(prices_filed, inputs=[export, supplier, language, given],
                      outputs=[export_note, export, supplier, state, list_part, given, notice])
    # the press beside the supplier's field asks only; "Ja, weghalen" takes the prices away, and its sentence stands under the row
    remove.click(remove_asked, inputs=[supplier, language], outputs=[confirm, ask, remove_note], show_progress="hidden")
    yes_button.click(prices_removed, inputs=[supplier, language],
                     outputs=[export_note, supplier, state, list_part, table, note, carry, viewed, notice]).then(
        removal_shown, inputs=export_note, outputs=[confirm, remove_note, export_note], show_progress="hidden")
    gr.on(triggers=[no_button.click, supplier.change], fn=remove_kept, outputs=confirm, show_progress="hidden")
    list_file.change(pricelist_file_chosen, inputs=[list_file, supplier, language], outputs=[*chosen, table, note, carry, viewed])
    gr.on(triggers=[field.input for field in chosen], fn=pricelist_column_chosen, outputs=[carry, viewed])
    view.click(pricelist_shown, inputs=[list_file, supplier, *chosen, language], outputs=[table, note, carry, viewed]).then(
        None, js=_COUNT_INTO_VIEW, show_progress="hidden")                     # the count line and the press under it into view
    carry.click(pricelist_carried_out, inputs=[list_file, supplier, *chosen, viewed, language], outputs=[table, note, carry, viewed])
    language.change(pricelist_relabel, inputs=[language, supplier, list_file],
                    outputs=[pricelist_tab, notice, intro, supplier, remove, state, export, file_button, export_note, list_file, *chosen, hint, view,
                             table, note, carry, viewed])
    language.change(removal_relabelled, inputs=language, outputs=[confirm, ask, yes_button, no_button, remove_note], show_progress="hidden",
                    queue=False)


# --- the tab "Bestanden" ---

def _files_tab(gr, app, language, files_tab, live: _Live, tab: _Tab, cases_root: Path, ticker) -> None:
    """The content of the tab "Bestanden" and its events, inside the tab (see the module docstring): the title, the intro, the upload
    field with "Aan de assistent geven" and a note, then the heading "Laatst verwerkt" with "Lijst vernieuwen" and the table of what the
    runner did with the last files (aid/review/intake.py), with a sentence in its place while it has no line (files-empty). files_tab
    is the tab itself; ticker is the page's gr.Timer, whose tick reads the table again (files_ticked) and gives nothing else. A file
    given goes into the watched folder of the settings as the page's own actions have them now (live), through intake.accept; nothing
    else is written. On a public share link and without a settings file the tab says only that it is not available, as the settings
    tab does, and offers nothing, nor is anything of it on the timer."""
    first = STRINGS["nl"]
    if tab.share or tab.file is None:
        key = "settings_share" if tab.share else "settings_no_file"
        notice = gr.Markdown(first[key])
        language.change(lambda language_now: (STRINGS[language_now][key], gr.update(label=STRINGS[language_now]["files_tab"])),
                        inputs=language, outputs=[notice, files_tab])
        return

    def headers(language_now: str) -> list[str]:
        return [STRINGS[language_now][f"files_column:{column}"] for column in ("time", "file", "what")]

    def table(language_now: str) -> dict:
        """The table as the intake file is now: the last lines, the newest first (intake.rows)."""
        return {"headers": headers(language_now), "data": intake.rows(cases_root, language_now)}

    def intro(language_now: str) -> str:
        """The intro, which names the export from Exact only while its columns are set in the settings as they are now."""
        return STRINGS[language_now]["files_intro_export" if live.settings().export_columns else "files_intro"]

    def files_opened(language_now: str, kept: dict) -> tuple:
        """The page loaded, the tab selected, or "Lijst vernieuwen" pressed: (the intro, the table read again, the note, what the tab
        keeps). An intake file that cannot be read leaves the table as it was, and the note names the class of the error; the next read
        that succeeds clears that note (kept["failed"]) and leaves any other note as it is."""
        try:
            listed = table(language_now)
        except Exception as error:
            return (intro(language_now), gr.update(), STRINGS[language_now]["settings_failed"].format(code=type(error).__name__),
                    {**kept, "failed": True})
        return intro(language_now), listed, "" if kept["failed"] else gr.update(), {**kept, "failed": False}

    def file_given(upload: str | None, language_now: str, kept: dict) -> tuple:
        """"Aan de assistent geven": the file chosen put into the watched folder (intake.accept), and (the note, the table read again,
        the field emptied, what the tab keeps). The note names the file as it now lies in the watched folder, or, for a file refused, as
        it was given, with the reason, the types it names with the export while the export's columns are set; an error says so in the
        note, and nothing is written. One press gives one file: the same upload again (a double click sends it twice, kept["last"]) and
        a press that finds the field emptied by the press before it change nothing, the note of that press included; a press without a
        file when none came before it says so. When the intake file cannot be read the table stays as it was."""
        strings = STRINGS[language_now]
        key = _upload_key(upload) if upload else None
        if (not upload and kept["last"] is not None) or (key is not None and key == kept["last"]):
            return gr.update(), gr.update(), gr.update(), kept
        if not upload:
            note = strings["files_choose_first"]
        else:
            path, settings = Path(upload), live.settings()
            try:
                given = intake.accept(path, path.name, settings.watched_folder, exports=bool(settings.export_columns))
            except intake.NotAccepted as refused:
                reason = "type_export" if refused.reason == "type" and settings.export_columns else refused.reason
                note = strings[f"files_refused:{reason}"].format(name=_literal(refused.name))
            except Exception as error:
                note = strings["settings_failed"].format(code=type(error).__name__)
            else:
                note = strings["files_accepted"].format(name=_literal(given.name))
        try:
            listed = table(language_now)
        except Exception:
            listed = gr.update()
        return note, listed, gr.update(value=None), {"last": key, "failed": False}

    def file_chosen(upload: str | None) -> dict:
        """The field changed: "Aan de assistent geven" is active while a file is chosen, and only then."""
        return gr.update(interactive=bool(upload))

    def files_ticked(language_now: str) -> tuple:
        """The page's timer ticked, or the table is read again otherwise (the page loaded, the tab selected, "Lijst vernieuwen" or
        "Aan de assistent geven" pressed, the language switched): (the table as files_opened reads it, shown while it has a line; the
        sentence in its place, shown while it has none). So what the runner did with a file shows without a press, and the table and
        the sentence never stand together. It gives neither the field, the note nor what the tab keeps, and writes nothing; an intake
        file that cannot be read leaves both as they are."""
        try:
            listed_now = table(language_now)
        except Exception:
            return gr.update(), gr.update()
        lines = bool(listed_now["data"])
        return (gr.update(value=listed_now, visible=lines),
                gr.update(value=STRINGS[language_now]["files_empty"], visible=not lines))

    def files_relabel(language_now: str, kept: dict) -> tuple:
        """The page's language switched: the tab in that language, the table read again in it, and the note cleared. When the intake
        file cannot be read the table stays as it was, its headers and its lines in the language it was read in, and the note says so
        in the new language, until a read of the list succeeds (files_opened)."""
        strings = STRINGS[language_now]
        try:
            listed, note, failed = table(language_now), "", False
        except Exception as error:
            listed, note, failed = gr.update(), strings["settings_failed"].format(code=type(error).__name__), True
        return (gr.update(label=strings["files_tab"]), f"## {strings['files_title']}", intro(language_now),
                gr.update(label=strings["files_choose"]), gr.update(value=strings["files_give"]), note, f"## {strings['files_done']}",
                gr.update(value=strings["refresh"]), listed, {**kept, "failed": failed})

    title = gr.Markdown(f"## {first['files_title']}", elem_id="files-title")
    intro_text = gr.Markdown(first["files_intro"], elem_id="files-intro")
    # any file: the tab names another type itself
    upload = gr.File(label=first["files_choose"], type="filepath", height=140, elem_id="files-upload")
    give = gr.Button(first["files_give"], variant="primary", elem_id="files-give", interactive=False)
    note = gr.Markdown("", elem_id="files-note", latex_delimiters=[])          # a name is shown as it is: no formula either
    with gr.Row():                                   # in columns, whose scale a row keeps, as it does not keep a Markdown's
        with gr.Column(scale=4):
            done = gr.Markdown(f"## {first['files_done']}", elem_id="files-done")
        with gr.Column(scale=1, min_width=160):
            refresh = gr.Button(first["refresh"], elem_id="files-refresh")
    listed = gr.Dataframe(value={"headers": headers("nl"), "data": []}, interactive=False, wrap=True, elem_id="files-table",
                          column_widths=["18%", "32%", "50%"])
    # in the table's place while it has no line; files_ticked shows the one or the other from the page's load on
    empty = gr.Markdown(first["files_empty"], visible=False, elem_id="files-empty", elem_classes=["empty-line"])
    kept = gr.State({"last": None, "failed": False})      # for the session: the upload given last, and whether the note says the list failed

    gr.on(triggers=[app.load, files_tab.select, refresh.click], fn=files_opened, inputs=[language, kept],
          outputs=[intro_text, listed, note, kept])
    give.click(file_given, inputs=[upload, language, kept], outputs=[note, listed, upload, kept])
    upload.change(file_chosen, inputs=upload, outputs=give)
    language.change(files_relabel, inputs=[language, kept],
                    outputs=[files_tab, title, intro_text, upload, give, note, done, refresh, listed, kept])
    # the table on the page's timer, and with every other read of it, never the field, the note or what the tab keeps
    gr.on(triggers=[app.load, ticker.tick, files_tab.select, refresh.click, give.click, language.change], fn=files_ticked, inputs=language,
          outputs=[listed, empty], show_progress="hidden", queue=False)


def _upload_key(upload: str) -> tuple | None:
    """What tells one upload from another: the path the page library saved it under (a folder named by its content, the name made
    safe), with the time and the size of that file, which the page library writes anew for each upload, also of the same file again;
    None when the file cannot be looked at."""
    try:
        status = Path(upload).stat()
    except OSError:
        return None
    return str(upload), status.st_mtime_ns, status.st_size


_LITERAL = re.compile(r"([!-/:-@\[-`{-~])")         # every ASCII mark: Markdown takes each, escaped, as itself


def _literal(name: str) -> str:
    """A file's name as Markdown shows it as it is, at the start of a line too: every ASCII mark escaped (a heading, a list, a quote,
    code, emphasis, a link, an HTML tag or entity, a web or e-mail address that the page library would make a link of), and a space
    after a space, or at the start, as a space that HTML does not fold away."""
    return re.sub(r"(?:^|(?<= )) ", "\u00a0", _LITERAL.sub(r"\\\1", name))


# --- the settings tab ---

def _settings_tab(gr, app, language, settings_tab, live: _Live, tab: _Tab, accounts: dict[tuple[str, str], MicrosoftAccount]):
    """The content of the settings tab and its events, inside the tab (see the module docstring): the line of a settings file that
    cannot be read, then the tab's five sections in a gr.Tabs of their own (settings-sections, see the module docstring): the buyer's
    rules and the suppliers (_buyer_section), the part "Postvak en AI", the block of the connection to Exact (_exact_section) and the
    state of the assistant (_assistant_section). settings_tab is the tab itself, whose selection fills the buyer's sections and the Exact
    block again; live holds the Deps of the page's own actions: its clock is the block's time, and its settings are those the tab
    shows when its file cannot be read, and it is given the settings each save wrote, so that the page's own actions use them at
    once. accounts holds the Microsoft accounts of the page by tenant and client, which the tab makes and signs in with and a close
    sends through (_sending_account). The folders listed are kept as {"mailbox": the mailbox they were listed for, "folders": id ->
    display name}, so that a folder is never saved with another mailbox's address. Returns the sections as an action of another tab
    opens them (_Sections: the gr.Tabs of the sections, whose selected section such an action sets, and what reads the section
    "Stand van de assistent" again after it); None on a public share link and without a settings file, where the tab shows one line
    and nothing else."""
    first = STRINGS["nl"]
    if tab.share or tab.file is None:
        key = "settings_share" if tab.share else "settings_no_file"
        notice = gr.Markdown(first[key])
        language.change(lambda language_now: STRINGS[language_now][key], inputs=language, outputs=notice)
        return None

    def unreadable(fields: list[str], strings: dict[str, str], error: Exception | None = None) -> str:
        """The one line of a settings file that cannot be read, naming the fields at fault by their labels and never a value; for
        a file in which exact_base_url is set, the line that says that this version writes nothing in Exact."""
        if isinstance(error, WriteRouteSet):
            return strings["settings_file_write_route"]
        named = _labels([field for field in fields if field != "the file"], strings)
        return strings["settings_file_unreadable"].format(fields=strings["settings_file_fields"].format(fields=named) if named else "")

    def read(strings: dict[str, str]) -> tuple[Settings, str | None]:
        """The settings the file holds, and None. When the file cannot be read (it is missing, locked, no JSON, or holds a value
        the settings refuse): the settings in use, those the page's own actions run with, and the line that says so. A file the
        tab read is also the page's own actions' from then on (live.now()), so that the settings in use are those of the last file
        the page could read, never those of the page's start."""
        try:
            settings = read_settings(tab.file)
        except SettingsInvalid as error:
            return live.settings(), unreadable(error.fields, strings, error)
        except (OSError, ValueError):
            return live.settings(), unreadable([], strings)
        live.now()
        return settings, None

    def write(build: Callable[[Settings], Mapping[str, object]], strings: dict[str, str]) -> tuple[Settings | None, str | None, str | None]:
        """Write into the settings file what build makes of the settings the file holds, read inside the lock of
        update_settings_file, so that a save never builds on a file another save is changing: (the settings the file then holds,
        None, None). A save that is refused writes nothing and gives (None, the note, the line of a file that cannot be read or
        None): the line when the file as it is cannot be read, the note of a _Refused that build raised, the labels of the fields
        whose new values the settings refuse (never a value), or the class of the error of a write that failed. The settings
        written are handed to the page's own actions at once."""
        reached = []                                 # whether the file was read and build was given its settings

        def values(held: Settings) -> Mapping[str, object]:
            reached.append(held)
            return build(held)

        try:
            saved = update_settings_file(tab.file, values)
        except _Refused as refused:
            return None, refused.note, None
        except SettingsInvalid as error:
            if not reached:                          # the file as it is: by the labels, never a value
                return None, (line := unreadable(error.fields, strings, error)), line
            return None, strings["settings_not_saved"].format(fields=_labels(error.fields, strings)), None
        except (OSError, ValueError) as error:
            if not reached:
                return None, (line := unreadable([], strings)), line
            return None, strings["settings_failed"].format(code=type(error).__name__), None
        live.take(saved)
        return saved, None, None

    def account_of(tenant: str | None, client: str | None) -> MicrosoftAccount | None:
        """The account of the tenant and the client, made once for the page; None when they are no valid IDs. Raises when the
        account cannot be made (its store is refused: a folder inside a git repository)."""
        tenant, client = (tenant or "").strip(), (client or "").strip()
        if not (is_tenant_id(tenant) and is_client_id(client)):
            return None
        if (tenant, client) not in accounts:
            accounts[(tenant, client)] = tab.account_factory(tenant, client)
        return accounts[(tenant, client)]

    def known(tenant: str | None, client: str | None) -> MicrosoftAccount | None:
        """account_of for the line that names the account; None also when the account cannot be made."""
        try:
            return account_of(tenant, client)
        except Exception:
            return None

    def begin(tenant: str, client: str, strings: dict[str, str], elsewhere: bool = False) -> tuple[MicrosoftAccount | None, str | None]:
        """The account for an action, or None with the note that says why there is none: a store the PC refuses in words
        (_refusal_text). For a button of another step than the sign-in (`elsewhere`), while the line under the sign-in buttons
        holds that refusal already (store_line), the note points to that line, so that the sentence stands once."""
        try:
            account = account_of(tenant, client)
        except Exception as error:
            refused = _refusal_text(error, strings)
            return None, strings["settings_store_refused_see"] if elsewhere and refused == store_line(strings) else refused
        return (account, None) if account is not None else (None, strings["settings_ids_needed"])

    def store_line(strings: dict[str, str]) -> str | None:
        """Why no sign-in can be made, told before any press: while the store of the sign-in (in default_secrets_dir(), where the
        page's accounts keep their tokens) lies inside a git work tree, which work_tree_refusal tells without making anything; and
        while the store that is there refuses this page (made by another Windows user or in another kind of session), which making
        the page's account of the saved tenant and client tells, as the account line makes it when the page loads; else None, also
        when the folder cannot be looked at or the account cannot be made for another reason, which a press names."""
        try:
            refused = work_tree_refusal(default_secrets_dir())
        except OSError:
            return None
        if refused is None:
            saved = current()
            try:
                account_of(saved.ms_tenant_id, saved.ms_client_id)
            except StoreRefused as error:
                refused = error
            except Exception:
                return None
        return _refusal_text(refused, strings) if refused is not None else None

    def store_checked(language_now: str):
        """The page loaded or the part opened: the line under the sign-in buttons says why no sign-in can be made while the store is
        refused (store_line), so that the buyer reads it before he presses anything; else that line keeps the answer it holds."""
        line = store_line(STRINGS[language_now])
        return line if line is not None else gr.update()

    def connections_selected(language_now: str, tenant_now: str, client_now: str) -> tuple:
        """The part opened: the signed-in account of the IDs in the fields (as the language switch reads it) and the state of the
        mailbox, read again, as the page reads them when it loads."""
        strings = STRINGS[language_now]
        return _account_text(known(tenant_now, client_now), strings), _trigger_text(current(), strings)

    def sections_relabelled(language_now: str) -> tuple:
        """The language switch: the five steps of "Postvak en AI" in the language, and its answer lines of the sign-in, the folder and
        the reader emptied, as the switch empties the tab's other notes, but for the refusal of the store, which the line under the
        sign-in buttons keeps, in the language, while the store is refused; the labels of the three sections that no other relabel
        names ("Uw regels", "Leveranciers", "Stand van de assistent"); and that last section in the language, its lines and its log
        read again (a file that cannot be read leaves them as they are)."""
        strings = STRINGS[language_now]
        try:
            lines, log = assistant_texts(language_now)
        except OSError:
            lines, log = gr.update(), gr.update(label=strings["assistant_log"])
        else:
            log = gr.update(label=strings["assistant_log"], value=log)
        return (*[f"## {strings[f'mail_step:{step}']}" for step in MAIL_STEPS], store_line(strings) or "", "", "",
                *[gr.update(label=strings[f"settings_section:{key}"]) for key in ("rules", "suppliers", "assistant")],
                lines, gr.update(value=strings["assistant_refresh"]), log, strings["assistant_log_help"])

    def local_now() -> datetime:
        """The time of the page's clock in the local time of this PC, without a zone, as aid/review/status.py reads the times of the
        files it looks at."""
        now = live.now().clock()
        return now.astimezone().replace(tzinfo=None) if now.utcoffset() is not None else now

    def current() -> Settings:
        """The settings in the file, or the settings in use when the file cannot be read."""
        return read(first)[0]

    def folders_shown(account: MicrosoftAccount, mailbox: str | None, strings: dict[str, str]) -> tuple:
        """The folders of the mailbox for the folder list, with the folder saved selected when it is among them: (the list, the
        folders listed, a note)."""
        mailbox = (mailbox or "").strip()
        try:
            listed = list_folders(account, mailbox or None, tab.session)
        except SignInNeeded:
            return gr.update(), gr.update(), _no_token_text(account, strings)
        except GraphError as error:
            return gr.update(), gr.update(), strings["settings_graph_failed"].format(code=error)
        folders, saved = {folder: name for name, folder in listed}, current().mail_folder
        return (gr.update(choices=listed, value=saved if saved in folders else None), {"mailbox": mailbox, "folders": folders},
                strings["settings_folders_loaded"].format(count=len(listed)))

    def settings_opened(language_now: str) -> tuple:
        """The tab opened: the six settings of the file, the signed-in account and the state of the trigger."""
        strings, settings = STRINGS[language_now], current()
        folders = {settings.mail_folder: settings.mail_folder_name or settings.mail_folder} if settings.mail_folder else {}
        account = known(settings.ms_tenant_id, settings.ms_client_id)
        return (settings.ms_tenant_id or "", settings.ms_client_id or "", settings.mailbox or "",
                gr.update(choices=[(name, folder) for folder, name in folders.items()], value=settings.mail_folder), settings.send_via,
                _account_text(account, strings), _trigger_text(settings, strings),
                {"mailbox": settings.mailbox or "", "folders": folders})

    def sign_in_browser(tenant: str, client: str, mailbox: str, language_now: str):
        """Sign in in the browser of this PC, saying first that the page waits for it, then list the folders: (the account, the
        folder list, the folders listed, a note)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings)
        if refused:
            yield gr.update(), gr.update(), gr.update(), refused
            return
        yield gr.update(), gr.update(), gr.update(), strings["settings_waiting"]
        try:
            account.sign_in_browser(mail_scopes((mailbox or "").strip() or None))
        except SignInFailed as error:
            yield _account_text(account, strings), gr.update(), gr.update(), strings["settings_sign_in_failed"].format(code=error)
            return
        except BrowserInteractionTimeoutError:       # nobody signed in within BROWSER_SECONDS, or no window opened
            yield gr.update(), gr.update(), gr.update(), strings["settings_browser_timeout"]
            return
        except Exception as error:
            yield gr.update(), gr.update(), gr.update(), strings["settings_failed"].format(code=type(error).__name__)
            return
        yield (_account_text(account, strings), *folders_shown(account, mailbox, strings))

    def sign_in_code(tenant: str, client: str, mailbox: str, language_now: str):
        """The device sign-in: first the code and the address, then, once the code has been entered, as sign_in_browser."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings)
        if refused:
            yield gr.update(), gr.update(), gr.update(), refused
            return
        try:
            flow = account.start_device_sign_in(mail_scopes((mailbox or "").strip() or None))
            yield gr.update(), gr.update(), gr.update(), strings["settings_code"].format(address=flow["verification_uri"], code=flow["user_code"])
            account.finish_device_sign_in(flow)
        except SignInFailed as error:
            yield _account_text(account, strings), gr.update(), gr.update(), strings["settings_sign_in_failed"].format(code=error)
            return
        except Exception as error:
            yield gr.update(), gr.update(), gr.update(), strings["settings_failed"].format(code=type(error).__name__)
            return
        yield (_account_text(account, strings), *folders_shown(account, mailbox, strings))

    def sign_out(tenant: str, client: str, language_now: str) -> tuple:
        """Remove the account and its tokens from this PC: (the account, the folder list, the folders listed, a note)."""
        strings = STRINGS[language_now]
        try:
            account = account_of(tenant, client)
        except Exception as error:
            return gr.update(), gr.update(), gr.update(), _refusal_text(error, strings)
        try:
            if account is not None:
                account.sign_out()
        except Exception as error:
            return gr.update(), gr.update(), gr.update(), strings["settings_failed"].format(code=type(error).__name__)
        return strings["settings_not_signed_in"], gr.update(choices=[], value=None), {"mailbox": None, "folders": {}}, strings["settings_signed_out"]

    def load_folders(tenant: str, client: str, mailbox: str, language_now: str) -> tuple:
        """List the folders again, of the mailbox now in the field: (the folder list, the folders listed, a note)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings, elsewhere=True)
        if refused:
            return gr.update(), gr.update(), refused
        return folders_shown(account, mailbox, strings)

    def check_connection(tenant: str, client: str, mailbox: str, folder: str | None, language_now: str) -> str:
        """How many messages the folder holds and when the newest arrived; never a subject or a sender."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings, elsewhere=True)
        if refused:
            return refused
        if not folder:
            return strings["settings_folder_needed"]
        try:
            count, newest = folder_summary(account, (mailbox or "").strip() or None, folder, tab.session)
        except SignInNeeded:
            return _no_token_text(account, strings)
        except GraphError as error:
            return strings["settings_graph_failed"].format(code=error)
        return strings["settings_tested"].format(count=count, newest=_when(newest)) if newest else strings["settings_tested_empty"]

    def save_settings(tenant: str, client: str, mailbox: str, folder: str | None, send_via: str, listed: dict, language_now: str) -> tuple:
        """Validate the six settings and write them into the settings file: (a note, the state of the trigger). A folder listed for
        another mailbox than the one in the field is not saved, and nothing is saved into a file that cannot be read: the note is
        then the line that says so."""
        strings, listed = STRINGS[language_now], listed or {}
        before, line = read(strings)
        if line is not None:
            return line, gr.update()
        given = {"ms_tenant_id": tenant, "ms_client_id": client, "mailbox": mailbox, "mail_folder": folder, "mail_folder_name": None,
                 "send_via": send_via}
        values = {key: (value.strip() or None) if isinstance(value, str) else value for key, value in given.items()}
        if values["mail_folder"] and (listed.get("mailbox") or "").lower() != (values["mailbox"] or "").lower():
            return strings["settings_folders_first"], gr.update()
        if values["mail_folder"]:
            values["mail_folder_name"] = listed.get("folders", {}).get(values["mail_folder"]) or (
                before.mail_folder_name if before.mail_folder == values["mail_folder"] else None)
        saved, refused, _ = write(lambda held: values, strings)      # a refused field by its label, never a value
        if saved is None:
            return refused, gr.update()
        if not trigger_on(saved) or connection_of(saved) is None:      # the trigger off, or another folder: a later connection
            try:                                                          # starts from its own moment (the runner does so too)
                forget_connection(saved.cases_root)
            except OSError:
                pass
        return strings["settings_buyer_saved"], _trigger_text(saved, strings)

    def settings_relabel(language_now: str, tenant_now: str, client_now: str, mailbox_now: str, send_via_now: str) -> tuple:
        strings = STRINGS[language_now]
        return (strings["settings_intro"], gr.update(label=strings["settings_tenant"]), gr.update(label=strings["settings_client"]),
                gr.update(value=strings["settings_sign_in"]), gr.update(value=strings["settings_sign_in_code"]),
                gr.update(value=strings["settings_sign_out"]), strings["settings_browser_note"],
                _account_text(known(tenant_now, client_now), strings), gr.update(label=strings["settings_mailbox"]),
                gr.update(label=strings["settings_folder"]), gr.update(value=strings["settings_folders"]),
                gr.update(value=strings["settings_test"]),
                gr.update(label=strings["settings_send_via"], choices=_via_choices(strings), value=send_via_now),
                gr.update(value=strings["settings_save"]), f"## {strings['settings_trigger']}", _trigger_text(current(), strings), "")

    # The line of a settings file that cannot be read stands above the sections, as the tab's first block; the sections follow in the
    # order of the work, the buyer's things first and the installer's after.
    file_line = gr.Markdown("", elem_id="settings-file-line")
    with gr.Tabs(elem_id="settings-sections", selected="regels") as sections:
        rules_section, suppliers_section = _buyer_section(gr, app, language, settings_tab, file_line, read, write)
        with gr.Tab(first["settings_connections"], id="koppelingen") as connections:
            account_shown = gr.Markdown(first["settings_not_signed_in"])          # the state first: the account and the mailbox
            trigger_title = gr.Markdown(f"## {first['settings_trigger']}")
            trigger_shown = gr.Markdown("")
            intro = gr.Markdown(first["settings_intro"])
            steps = [gr.Markdown(f"## {first['mail_step:ids']}")]
            with gr.Row():
                tenant = gr.Textbox(label=first["settings_tenant"])
                client = gr.Textbox(label=first["settings_client"])
            steps.append(gr.Markdown(f"## {first['mail_step:sign_in']}"))
            browser_note = gr.Markdown(first["settings_browser_note"])
            with gr.Row():
                sign_in_button = gr.Button(first["settings_sign_in"])
                code_button = gr.Button(first["settings_sign_in_code"])
                sign_out_button = gr.Button(first["settings_sign_out"])
            sign_note = gr.Markdown("", elem_id="settings-sign-note", elem_classes=["answer"])          # the answer of the three buttons
            steps.append(gr.Markdown(f"## {first['mail_step:mailbox']}"))
            mailbox = gr.Textbox(label=first["settings_mailbox"])
            with gr.Row():
                folder = gr.Dropdown(choices=[], value=None, label=first["settings_folder"], scale=4)
                folders_button = gr.Button(first["settings_folders"], scale=1)
            test_button = gr.Button(first["settings_test"])
            folder_note = gr.Markdown("", elem_id="settings-folder-note", elem_classes=["answer"])      # the answer of the two buttons above
            steps.append(gr.Markdown(f"## {first['mail_step:send']}"))
            send_via = gr.Radio(choices=_via_choices(first), value="auto", label=first["settings_send_via"])
            save_button = gr.Button(first["settings_save"], variant="primary")
            note = gr.Markdown("", elem_classes=["answer"])                       # the answer of "Postvak opslaan"
            listed = gr.State({})                        # the folders listed and the mailbox they were listed for
            steps.append(gr.Markdown(f"## {first['mail_step:read']}"))

            signed = [account_shown, folder, listed, sign_note]
            app.load(settings_opened, inputs=language, outputs=[tenant, client, mailbox, folder, send_via, account_shown, trigger_shown, listed])
            gr.on(triggers=[app.load, connections.select], fn=store_checked, inputs=language, outputs=sign_note, show_progress="hidden")
            connections.select(connections_selected, inputs=[language, tenant, client], outputs=[account_shown, trigger_shown],
                               show_progress="hidden")
            sign_in_button.click(sign_in_browser, inputs=[tenant, client, mailbox, language], outputs=signed)
            code_button.click(sign_in_code, inputs=[tenant, client, mailbox, language], outputs=signed)
            sign_out_button.click(sign_out, inputs=[tenant, client, language], outputs=signed)
            folders_button.click(load_folders, inputs=[tenant, client, mailbox, language], outputs=[folder, listed, folder_note])
            test_button.click(check_connection, inputs=[tenant, client, mailbox, folder, language], outputs=folder_note)
            save_button.click(save_settings, inputs=[tenant, client, mailbox, folder, send_via, listed, language], outputs=[note, trigger_shown])
            language.change(settings_relabel, inputs=[language, tenant, client, mailbox, send_via],
                            outputs=[intro, tenant, client, sign_in_button, code_button, sign_out_button, browser_note, account_shown, mailbox,
                                     folder, folders_button, test_button, send_via, save_button, trigger_title, trigger_shown, note])
            reader_note = _ai_section(gr, app, language, tab, tenant, client, mailbox, account_shown, begin, read, write, connections.select,
                                      local_now)
            _extension_code(gr, app, language, current)
        _exact_section(gr, app, language, settings_tab, read, write, lambda: live.now().clock())
        assistant_section, assistant_parts, assistant_texts, read_after = _assistant_section(gr, language, tab, read, local_now)
    language.change(sections_relabelled, inputs=language,
                    outputs=[*steps, sign_note, folder_note, reader_note, rules_section, suppliers_section, assistant_section, *assistant_parts],
                    show_progress="hidden")
    language.change(lambda language_now: gr.update(label=STRINGS[language_now]["settings_connections"]), inputs=language,
                    outputs=connections)
    return _Sections(sections, read_after)


def _extension_code(gr, app, language, current: Callable[[], Settings]) -> None:
    """The code of the browser extension, at the end of the connections: one line that cannot be edited, with a button to copy it,
    filled with capture_token of the settings file (current gives the settings in the file, or those in use) each time the page
    loads. _settings_tab builds it on the page of this PC only, with its settings file."""
    first = STRINGS["nl"]

    def extension_code_opened() -> str:
        return current().capture_token or ""

    def extension_code_relabel(language_now: str) -> dict:
        strings = STRINGS[language_now]
        return gr.update(label=strings["settings_extension_code"], info=strings["settings_extension_code_help"])

    code = gr.Textbox(label=first["settings_extension_code"], info=first["settings_extension_code_help"], interactive=False, max_lines=1,
                      buttons=["copy"])
    app.load(extension_code_opened, outputs=code)
    language.change(extension_code_relabel, inputs=language, outputs=code)


def _buyer_section(gr, app, language, settings_tab, file_line, read: Callable[[dict[str, str]], tuple[Settings, str | None]],
                   write: Callable) -> tuple:
    """The buyer's two sections of the settings tab and their events, its first two (see the module docstring): "Uw regels", the
    eleven settings of buyer_settings.BUYER_FIELDS in the groups of BUYER_GROUPS, each field with its label and its help, under
    "Regels opslaan"; and "Leveranciers", a table of the suppliers and a form for one supplier. file_line is the line of a settings
    file that cannot be read, which _settings_tab puts above the sections and the handlers here write. read gives the settings in
    the file (or those in use, with the line), and write saves into it (both of _settings_tab). The fill (buyer_opened) runs when the
    page loads and each time settings_tab is selected. Two states of the page hold what the forms were filled with: the eleven texts,
    and the supplier form with the supplier it was filled for, so that a save writes what the buyer changed and nothing else.
    Returns the two sections, which the language switch of _settings_tab relabels."""
    first = STRINGS["nl"]

    def headers(strings: dict[str, str]) -> list[str]:
        return [strings[f"settings_supplier_column:{name}"] for name in ("name", "email", "lead_time", "review", "messages_off")]

    def table(settings: Settings, strings: dict[str, str]) -> dict:
        """The table of the suppliers: their values as text, a tick as the word for yes."""
        data = [[name, email, str(lead), strings["settings_tick"] if review else "", strings["settings_tick"] if off else ""]
                for name, email, lead, review, off in supplier_rows(settings)]
        return {"headers": headers(strings), "data": data}

    def choices(settings: Settings, strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings["settings_supplier_new"], NEW_SUPPLIER)] + [(name, name) for name in supplier_names(settings)]

    def dropdown(settings: Settings, strings: dict[str, str], choice: str | None) -> tuple[dict, str]:
        """The dropdown with the suppliers of the settings, on the choice when it is one of them and else on "Leverancier
        toevoegen": (its update, the value it then has)."""
        value = choice if choice != NEW_SUPPLIER and choice in supplier_names(settings) else NEW_SUPPLIER
        return gr.update(choices=choices(settings, strings), value=value), value

    def supplier_form(settings: Settings, choice: str) -> tuple:
        """The supplier form for a value of the dropdown: (the name, the address, the lead time, the two ticks, what the form is
        filled with). A listed supplier with its values as the settings hold them and its name fixed; "Leverancier toevoegen", or
        a name the settings do not hold: the empty form with the name open."""
        if choice == NEW_SUPPLIER or choice not in supplier_names(settings):
            return gr.update(value="", interactive=True), "", "", False, False, dict(NEW_FORM)
        parts = supplier_shown(settings, choice)
        return (gr.update(value=choice, interactive=False), parts["email"], parts["lead_time"], parts["review"], parts["off"],
                {"name": choice} | parts)

    def refused(problems: dict[str, str], strings: dict[str, str]) -> str:
        """The refused fields of a save, each by its label with its problem, never the value given."""
        return strings["settings_buyer_refused"].format(fields="; ".join(f"{strings[FIELD_LABELS[field]]}: {problem}"
                                                                          for field, problem in problems.items()))

    def top(line: str | None) -> str:
        """The line at the top of the tab: that of a settings file that cannot be read, in bold, or nothing."""
        return f"**{line}**" if line else ""

    def buyer_opened(language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the line of a file that cannot be read, the eleven settings of the file
        and what the form is filled with, the table of the suppliers, their dropdown on "Leverancier toevoegen" and the supplier
        form emptied. With a file that cannot be read, the settings in use."""
        strings = STRINGS[language_now]
        settings, line = read(strings)
        values = shown(settings, language_now)
        return (top(line), *[values[field] for field in BUYER_FIELDS], values, table(settings, strings),
                dropdown(settings, strings, NEW_SUPPLIER)[0], *supplier_form(settings, NEW_SUPPLIER))

    def save_buyer(price_pct, price_abs, lead_time_tolerance_days, first_cases_review, ask_wait_working_days, buyer_name, digest,
                   digest_time, buyer_email, language_setting, approver_email, filled: dict | None, language_now: str) -> tuple:
        """Write the fields whose text differs from the text the form was filled with, and no other: (a note, the line of a file
        that cannot be read, the eleven fields, what they are filled with). A refused field writes nothing and is named by its
        label, and the form keeps what was typed; nothing changed writes nothing; a file that cannot be read refuses with its
        line. After a save, and when nothing was changed, the fields show the values as the file now holds them. A form whose
        fill the page no longer knows (the page was started again while the browser tab stayed open) is filled again from the
        file, and nothing is written."""
        strings = STRINGS[language_now]
        same = [gr.update()] * (len(BUYER_FIELDS) + 1)       # the eleven fields and what they were filled with, left as they are
        settings, line = read(strings)
        if line is not None:
            return (line, top(line), *same)
        if not isinstance(filled, dict):
            values = shown(settings, language_now)
            return (strings["settings_buyer_refilled"], "", *[values[field] for field in BUYER_FIELDS], values)
        form = dict(zip(BUYER_FIELDS, (price_pct, price_abs, lead_time_tolerance_days, first_cases_review, ask_wait_working_days, buyer_name,
                                       digest, digest_time, buyer_email, language_setting, approver_email)))
        changed = changed_fields(form, filled)
        if not changed:                                      # nothing typed is lost: the form shows the file as it is now
            values = shown(settings, language_now)
            return (strings["settings_buyer_unchanged"], "", *[values[field] for field in BUYER_FIELDS], values)
        values, problems = parse(form, language_now, only=changed)
        if problems:
            return (refused(problems, strings), "", *same)
        saved, note, line = write(lambda held: values, strings)
        if saved is None:
            return (note, top(line), *same)
        values = shown(saved, language_now)
        return (strings["settings_buyer_saved"], "", *[values[field] for field in BUYER_FIELDS], values)

    def supplier_chosen(choice: str | None, language_now: str) -> tuple:
        """The dropdown changed, by the buyer or by a save: the supplier form with the supplier's values as the file holds them
        (its name fixed), or the empty form for "Leverancier toevoegen", and what the form is filled with."""
        return supplier_form(read(STRINGS[language_now])[0], choice or NEW_SUPPLIER)

    def supplier_picked() -> str:
        """The buyer's own choice in the dropdown: the note of the save before it is cleared."""
        return ""

    def save_supplier(choice: str | None, name: str, email: str, lead_time: str, review: bool, off: bool, filled: dict | None,
                      language_now: str) -> tuple:
        """Write the supplier of the form into the four supplier settings, built from the file as it is read inside the lock of the
        save: (a note, the line of a file that cannot be read, the table, the dropdown, the supplier form and what it is filled
        with). Of a listed supplier only the parts the buyer changed are written, its name as the file has it; with
        "Leverancier toevoegen" the typed name, trimmed, with every value typed. Nothing is written for a typed name that is
        listed already, for a new supplier with a name and nothing else, for a listed one with nothing changed, or for a form
        whose fill the page no longer knows (or that was filled for another supplier than the one chosen), which is filled
        again."""
        strings = STRINGS[language_now]
        same = [gr.update()] * 8                             # the table, the dropdown, the five of the form and what it was filled with
        settings, line = read(strings)
        if line is not None:
            return (line, top(line), *same)
        new = not choice or choice == NEW_SUPPLIER
        form = {"email": email, "lead_time": lead_time, "review": review, "off": off}
        if new:
            supplier, changes = (name or "").strip(), supplier_changes(form, NEW_FORM)
            if supplier == NEW_SUPPLIER:                     # the dropdown's own value for "Leverancier toevoegen" names no supplier
                return (refused({"supplier_name": strings["settings_problem:supplier_name"]}, strings), "", *same)
        elif not isinstance(filled, dict) or filled.get("name") != choice:
            return (strings["settings_buyer_refilled"], "", table(settings, strings), dropdown(settings, strings, choice)[0],
                    *supplier_form(settings, choice))
        else:
            supplier, changes = choice, supplier_changes(form, filled)
            if not changes:
                return (strings["settings_buyer_unchanged"], "", *same)
        read_inside = []                                     # the settings of the file as the save read them

        def build(held: Settings) -> dict:
            read_inside.append(held)
            if new and supplier in supplier_names(held):
                raise _Refused(strings["settings_supplier_listed"])
            values, problems = supplier_update(held, supplier, changes, language_now, chosen=not new)      # a chosen name as the file had it
            if problems:
                raise _Refused(refused(problems, strings))
            if new and not changes:
                raise _Refused(strings["settings_supplier_empty"])
            return values

        saved, note, line = write(build, strings)
        if saved is None and note == strings["settings_supplier_listed"]:      # the list as the file has it, to choose the supplier in
            return (note, "", table(read_inside[-1], strings), dropdown(read_inside[-1], strings, NEW_SUPPLIER)[0], *[gr.update()] * 6)
        if saved is None:
            return (note, top(line), *same)
        shown_choice, kept = dropdown(saved, strings, supplier)
        return (strings["settings_buyer_saved"], "", table(saved, strings), shown_choice, *supplier_form(saved, kept))

    def remove_supplier(choice: str | None, language_now: str) -> tuple:
        """Take the supplier chosen out of the four settings, its name as the file has it: (a note, the line of a file that cannot
        be read, the table, the dropdown on "Leverancier toevoegen", the supplier form emptied and what it is filled with)."""
        strings = STRINGS[language_now]
        same = [gr.update()] * 8
        if not choice or choice == NEW_SUPPLIER:
            return (strings["settings_supplier_choose"], gr.update(), *same)
        settings, line = read(strings)
        if line is not None:
            return (line, top(line), *same)
        gone = []                                            # the file no longer holds the supplier: nothing to take out

        def build(held: Settings) -> dict:
            if choice not in supplier_names(held):
                gone.append(choice)
                return {}
            return without_supplier(held, choice)

        saved, note, line = write(build, strings)
        if saved is None:
            return (note, top(line), *same)
        return (strings["settings_buyer_unchanged" if gone else "settings_buyer_saved"], "", table(saved, strings),
                dropdown(saved, strings, NEW_SUPPLIER)[0], *supplier_form(saved, NEW_SUPPLIER))

    def buyer_relabel(language_now: str, digest_now: str | None, language_setting_now: str | None, choice_now: str | None) -> tuple:
        """The page's language switched: the buyer's part in the other language, with what is typed and chosen kept. The note of
        the main save is cleared; the note of a supplier save stays until the buyer's own choice in the dropdown."""
        strings = STRINGS[language_now]
        settings, line = read(strings)
        fields = []
        for field in BUYER_FIELDS:
            update = {"label": strings[f"settings_field:{field}"], "info": strings[f"settings_help:{field}"]}
            if field == "digest":
                update |= {"choices": _digest_choices(strings), "value": digest_now}
            elif field == "language":
                update |= {"choices": _language_choices(strings), "value": language_setting_now}
            fields.append(gr.update(**update))
        relabelled = gr.update(label=strings["settings_supplier_choice"], choices=choices(settings, strings),
                               value=dropdown(settings, strings, choice_now)[1])
        return (top(line), *[f"## {strings[f'settings_group:{group}']}" for group, _ in BUYER_GROUPS], *fields, strings["settings_limit"],
                gr.update(value=strings["settings_buyer_save"]), "", f"### {strings['settings_suppliers']}",
                strings["settings_suppliers_help"], table(settings, strings), relabelled,
                gr.update(label=strings["settings_supplier_name"]), gr.update(label=strings["settings_supplier_email"]),
                gr.update(label=strings["settings_supplier_lead_time"]), gr.update(label=strings["settings_supplier_review"]),
                gr.update(label=strings["settings_supplier_messages_off"]), gr.update(value=strings["settings_supplier_save"]),
                gr.update(value=strings["settings_supplier_remove"]))

    with gr.Tab(first["settings_section:rules"], id="regels") as rules_section:
        headings, fields = [], {}
        for group, names in BUYER_GROUPS:
            headings.append(gr.Markdown(f"## {first[f'settings_group:{group}']}"))
            for field in names:
                label, info = first[f"settings_field:{field}"], first[f"settings_help:{field}"]
                if field == "digest":
                    fields[field] = gr.Radio(choices=_digest_choices(first), value=None, label=label, info=info)
                elif field == "language":
                    fields[field] = gr.Radio(choices=_language_choices(first), value=None, label=label, info=info)
                else:
                    fields[field] = gr.Textbox(label=label, info=info, elem_classes=["short-field"] if field in _SHORT_FIELDS else None)
        limit = gr.Markdown(first["settings_limit"])
        save_button = gr.Button(first["settings_buyer_save"], variant="primary")
        note = gr.Markdown("", elem_id="settings-buyer-note")
        filled = gr.State(None)                      # the eleven texts the form was filled with
    with gr.Tab(first["settings_section:suppliers"], id="leveranciers") as suppliers_section:
        suppliers_title = gr.Markdown(f"### {first['settings_suppliers']}", visible=False)       # the section's label names it
        suppliers_help = gr.Markdown(first["settings_suppliers_help"])
        suppliers = gr.Dataframe(value={"headers": headers(first), "data": []}, interactive=False, wrap=True)
        supplier = gr.Dropdown(choices=[(first["settings_supplier_new"], NEW_SUPPLIER)], value=NEW_SUPPLIER,
                               label=first["settings_supplier_choice"])
        with gr.Row():
            name = gr.Textbox(label=first["settings_supplier_name"])
            email = gr.Textbox(label=first["settings_supplier_email"])
            lead_time = gr.Textbox(label=first["settings_supplier_lead_time"])
        with gr.Row():
            review = gr.Checkbox(value=False, label=first["settings_supplier_review"])
            off = gr.Checkbox(value=False, label=first["settings_supplier_messages_off"])
        with gr.Row():
            supplier_save = gr.Button(first["settings_supplier_save"])
            supplier_remove = gr.Button(first["settings_supplier_remove"])
        supplier_note = gr.Markdown("", elem_id="settings-supplier-note")
        supplier_filled = gr.State(None)             # the supplier form as it was filled, with the supplier it was filled for

    form = [fields[field] for field in BUYER_FIELDS]
    supplier_form_fields = [name, email, lead_time, review, off]
    supplier_shown_again = [supplier_note, file_line, suppliers, supplier, *supplier_form_fields, supplier_filled]
    gr.on(triggers=[app.load, settings_tab.select], fn=buyer_opened, inputs=language,
          outputs=[file_line, *form, filled, suppliers, supplier, *supplier_form_fields, supplier_filled])
    save_button.click(save_buyer, inputs=[*form, filled, language], outputs=[note, file_line, *form, filled])
    # The dropdown's change (the buyer's choice, or the value a save or a removal gave it) fills the form and leaves the note of a
    # save alone; the buyer's own choice (input) clears that note.
    supplier.change(supplier_chosen, inputs=[supplier, language], outputs=[*supplier_form_fields, supplier_filled])
    supplier.input(supplier_picked, outputs=supplier_note)
    supplier_save.click(save_supplier, inputs=[supplier, *supplier_form_fields, supplier_filled, language], outputs=supplier_shown_again)
    supplier_remove.click(remove_supplier, inputs=[supplier, language], outputs=supplier_shown_again)
    language.change(buyer_relabel, inputs=[language, fields["digest"], fields["language"], supplier],
                    outputs=[file_line, *headings, *form, limit, save_button, note, suppliers_title, suppliers_help, suppliers, supplier,
                             *supplier_form_fields, supplier_save, supplier_remove])
    return rules_section, suppliers_section


# The fields of the block "Koppeling met Exact" above the title row, in the order of the page: the six labels and the supplier's line.
_EXACT_FIELDS = ("po_label", "date_label", "supplier_label", "supplier_line", "version_label", "currency_label", "incoterm_label")


def _exact_section(gr, app, language, settings_tab, read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable,
                   clock: Callable[[], datetime]) -> None:
    """The block "Koppeling met Exact" of the settings tab and its events, the tab's section of that name, after "Postvak en AI" (see
    the module docstring; aid/review/exact_settings.py has what it shows and writes). read gives the settings in the file (or those in
    use, with the line of a file that cannot be read) and write saves into it (both of _settings_tab); clock is the time of the page's
    own actions, against which the state lines tell how long a file lies in the watched folder. Every control is made once, the SLOTS
    dropdowns of the title row among them, so that every handler is one of the page's functions. Two states of the page keep the title
    row found and what the form was filled with (saved_values: the two settings as the file held them at the load, at the select of
    the settings tab or after a save), by which a save from a form that another browser tab overtook is refused. Part 2, the export of
    order lines, stands in the block below part 1 (_export_part), and part 3, the export of articles, below part 2 (_articles_part)."""
    first = STRINGS["nl"]

    def state_text(settings: Settings, language_now: str) -> str:
        """The state lines, one paragraph each."""
        return "\n\n".join(state_lines(settings, PoStore(settings.po_store_root), clock(), language_now))

    def page_table(lines: list[list[str]], strings: dict[str, str]) -> dict:
        return {"headers": [strings["exact_page_line"], strings["exact_page_text"]], "data": lines}

    def first_page(sample: str | None, strings: dict[str, str]) -> tuple[dict, str]:
        """The first page of the sample as the table shows it, and the line under the table: that the table shows the first
        PAGE_LINES lines of a longer page, that the PDF has no text, or that it cannot be opened ("" for none)."""
        if not sample:
            return page_table([], strings), ""
        try:
            lines = page_lines(Path(sample), PAGE_LINES + 1)
        except NotPdf:
            return page_table([], strings), strings["exact_problem:not_pdf"]
        except SampleUnreadable:
            return page_table([], strings), strings["exact_problem:unopenable"]
        except Exception as error:                       # anything else, by the class of its error
            return page_table([], strings), strings["settings_failed"].format(code=type(error).__name__)
        if not lines:
            return page_table([], strings), strings["exact_problem:scan"]
        more = strings["exact_page_more"].format(n=PAGE_LINES) if len(lines) > PAGE_LINES else ""
        return page_table(lines[:PAGE_LINES], strings), more

    def header_table(rows: list[list[str]], strings: dict[str, str]) -> dict:
        return {"headers": [strings["exact_read_what"], strings["exact_read_value"]], "data": rows}

    def lines_table(rows: list[list[str]], language_now: str, layout=None) -> dict:
        """The table of order lines, with a width for each of its columns, so that its cells wrap inside the block (line_widths)."""
        return gr.update(value={"headers": line_headers(language_now, layout), "data": rows}, column_widths=line_widths(layout))

    def listed(texts: list[str]) -> str:
        return "\n".join(f"- {text}" for text in texts)

    def cleared(language_now: str) -> tuple:
        """The reading emptied: (the summary, the table of the header, the table of the lines, the problems and warnings)."""
        return "", header_table([], STRINGS[language_now]), lines_table([], language_now), ""

    def reading(tried: Trial | None, layout, language_now: str) -> tuple:
        """The reading of a trial as the block shows it, in the order of cleared: the summary with the number of order lines and
        whether the total can be worked out, only when the trial found no problem; the two tables; and a line for each problem and each
        warning of the reader."""
        if tried is None:
            return cleared(language_now)
        if tried.document is None:                   # a PDF without text: nothing read, and the problem that says so
            return (*cleared(language_now)[:3], listed(tried.problems))
        strings = STRINGS[language_now]
        header, lines = reading_shown(tried.document, language_now, layout)
        summary = "" if tried.problems else strings["exact_read"].format(n=len(tried.document.lines),
                                                                          answer=strings["exact_yes" if tried.total else "exact_no"])
        return summary, header_table(header, strings), lines_table(lines, language_now, layout), listed(tried.problems + tried.warnings)

    def meaning_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"exact_meaning:{meaning}"], meaning) for meaning in MEANINGS]

    def number_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"exact_numbers:{value}"], value) for value in ("comma", "point")]

    def help_of(field: str, strings: dict[str, str]) -> str | None:
        """The help of a field above the title row: the PO label's own, the labels' shared one, none for the supplier's line."""
        return None if field == "supplier_line" else strings["exact_help:po_label" if field == "po_label" else "exact_help:label"]

    def slot_updates(words: list[str], meanings: list, strings: dict[str, str]) -> list:
        """The SLOTS dropdowns for a title row of at most SLOTS words: one for each word, labelled with it and on its meaning, the
        others hidden and empty."""
        meanings = [*meanings, *[None] * len(words)]
        return [gr.update(visible=True, label=word, choices=meaning_choices(strings), value=meanings[n]) for n, word in enumerate(words)] + [
            gr.update(visible=False, value=None) for _ in range(SLOTS - len(words))]

    def given(values: tuple) -> tuple:
        """The inputs of "Testen" and "Opmaak opslaan" after the sample: (the form, the title row, the meanings, the senders, what the form
        was filled with, the language)."""
        form = dict(zip(EXACT_FORM, values))
        meanings = list(values[len(EXACT_FORM) + 1:len(EXACT_FORM) + 1 + SLOTS])
        return form, list(values[len(EXACT_FORM)] or []), meanings, values[-3], values[-2], values[-1]

    def judged(sample: str | None, form: dict, title_row: list, meanings: list, senders: str, language_now: str) -> tuple:
        """The form, the sample and the senders judged: (the layout or None, the senders or None, the trial read of the sample with the
        layout or None, the problems before the reading: of the form and of a missing sample, the problems after it: of the senders).
        A sample that cannot be opened raises SampleUnreadable."""
        strings = STRINGS[language_now]
        layout, refused = layout_from_form(form | {"title_row": title_row}, meanings, language_now)
        listed_senders, sender_problem = senders_from(senders, language_now)
        before = [*refused.values(), *([] if sample else [strings["exact_problem:no_sample"]])]
        tried = trial(Path(sample), layout, language_now) if layout is not None and sample else None
        return layout, listed_senders, tried, before, [sender_problem] if sender_problem else []

    def exact_opened(language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the state lines, and the form filled with the layout and the senders of the
        settings file (the settings in use when the file cannot be read), a dropdown shown on its meaning for each word of the saved
        title row; what the form is filled with; and the line under the search of the title row. A saved title row of more than SLOTS
        words, which the form cannot show, leaves the title row and its dropdowns empty and says so; the layout stays in force."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        form = form_from_layout(settings.po_layout)
        words, meanings, line = form["title_row"], form["columns"], ""
        if len(words) > SLOTS:
            words, meanings, line = [], [], strings["exact_title_saved_long"]
        return (state_text(settings, language_now), *[form[field] for field in EXACT_FORM], form["title_word"], words,
                *slot_updates(words, meanings, strings), ", ".join(settings.po_senders), saved_values(settings), line)

    def exact_sample_shown(sample: str | None, language_now: str) -> tuple:
        """A sample chosen, or taken away: (its first page line by line, the line under the table, the reading emptied, the note
        emptied). The reading of an earlier sample goes."""
        return (*first_page(sample, STRINGS[language_now]), *cleared(language_now), "")

    def exact_title_found(sample: str | None, word: str, title_row: list | None, *values) -> tuple:
        """"Kopregel zoeken": (the words of the first line of page 1 that holds the word, the SLOTS dropdowns, a note): a dropdown for
        each word, labelled with it, on the meaning chosen before when the title row is the one found before. No sample, a PDF that
        cannot be opened or has no text, no line with the word and a title row of more than SLOTS words leave the title row and the
        dropdowns as they are."""
        *meanings, language_now = values
        strings, title_row = STRINGS[language_now], list(title_row or [])
        same = (title_row, *[gr.update()] * SLOTS)
        if not sample:
            return (*same, strings["exact_problem:no_sample"])
        try:
            words = title_words(Path(sample), word)
            if words is None and not page_lines(Path(sample), limit=1):
                return (*same, strings["exact_problem:scan"])
        except NotPdf:
            return (*same, strings["exact_problem:not_pdf"])
        except SampleUnreadable:
            return (*same, strings["exact_problem:unopenable"])
        except Exception as error:
            return (*same, strings["settings_failed"].format(code=type(error).__name__))
        if words is None:
            return (*same, strings["exact_title_missing"])
        if len(words) > SLOTS:
            return (*same, strings["exact_title_long"])
        return (words, *slot_updates(words, meanings[:len(words)] if words == title_row else [], strings), strings["exact_title_found"])

    def exact_tested(sample: str | None, *values) -> tuple:
        """"Testen": the sample read with the layout of the form; nothing is written. (The summary, the two tables, the problems and
        warnings of the reading, and the note with the problems of the form, of a missing sample and of the senders, or that the sample
        cannot be opened.)"""
        form, title_row, meanings, senders, _, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            layout, _, tried, before, after = judged(sample, form, title_row, meanings, senders, language_now)
        except NotPdf:
            return (*cleared(language_now), strings["exact_problem:not_pdf"])
        except SampleUnreadable:
            return (*cleared(language_now), strings["exact_problem:unopenable"])
        except Exception as error:
            return (*cleared(language_now), strings["settings_failed"].format(code=type(error).__name__))
        return (*reading(tried, layout, language_now), " ".join(before + after))

    def exact_saved(sample: str | None, *values) -> tuple:
        """"Opmaak opslaan": the sample read as "Testen" reads it, and po_layout and po_senders written into the settings file when nothing
        is wrong (write, which writes the keys whose value changed): (the note, the state lines, what the form is now filled with, the
        reading). Any problem of the form, the sample or the senders refuses the save, each named in the page's language, and nothing
        is written; so does a settings file that cannot be read, with its line, and a file whose two settings are no longer those the
        form was filled with (another browser tab saved meanwhile), which the save sees inside the lock of update_settings_file."""
        form, title_row, meanings, senders, filled, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            layout, listed_senders, tried, before, after = judged(sample, form, title_row, meanings, senders, language_now)
        except NotPdf:
            return (strings["exact_not_saved"].format(problems=strings["exact_problem:not_pdf"]), gr.update(), filled, *cleared(language_now))
        except SampleUnreadable:
            return (strings["exact_not_saved"].format(problems=strings["exact_problem:unopenable"]), gr.update(), filled, *cleared(language_now))
        except Exception as error:
            return (strings["settings_failed"].format(code=type(error).__name__), gr.update(), filled, *cleared(language_now))
        shown_now = reading(tried, layout, language_now)
        problems = before + (tried.problems if tried is not None else []) + after
        if problems:
            return (strings["exact_not_saved"].format(problems=" ".join(problems)), gr.update(), filled, *shown_now)

        def build(held: Settings) -> dict:
            if not isinstance(filled, dict) or saved_values(held) != filled:
                raise _Refused(strings["exact_changed"])
            return {"po_layout": as_setting(layout), "po_senders": listed_senders}

        saved, note, _ = write(build, strings)
        if saved is None:
            return (note, gr.update(), filled, *shown_now)
        return (strings["exact_saved"], state_text(saved, language_now), saved_values(saved), *shown_now)

    def exact_state_shown(language_now: str) -> str:
        """The state lines as the settings file and the folders hold them now ("Stand vernieuwen")."""
        return state_text(read(STRINGS[language_now])[0], language_now)

    def exact_relabel(language_now: str, sample: str | None, numbers_now: str | None, title_row: list | None, *meanings) -> tuple:
        """The page's language switched: the block in the other language, with what is typed and chosen kept, the first page's table
        and the line under it included; the notes and the reading are emptied, for "Testen" to read the sample again in that language."""
        strings = STRINGS[language_now]
        table, line = first_page(sample, strings)
        return (gr.update(label=strings["exact_title"]), strings["exact_intro"], f"### {strings['exact_state_title']}",
                exact_state_shown(language_now), gr.update(value=strings["exact_refresh"]), f"### {strings['exact_part_po']}",
                gr.update(label=strings["exact_sample"]), gr.update(label=strings["exact_page"], value=table), line,
                *[gr.update(label=strings[f"exact_field:{field}"], info=help_of(field, strings)) for field in _EXACT_FIELDS],
                gr.update(label=strings["exact_field:title_word"]), gr.update(value=strings["exact_find"]), "",
                *[gr.update(choices=meaning_choices(strings), value=meaning) for meaning in meanings],
                gr.update(label=strings["exact_field:table_end"], info=strings["exact_help:table_end"]),
                gr.update(label=strings["exact_field:numbers"], choices=number_choices(strings), value=numbers_now),
                gr.update(label=strings["exact_field:per_piece"]),
                gr.update(label=strings["exact_field:senders"], info=strings["exact_help:senders"]), gr.update(value=strings["exact_test"]),
                gr.update(value=strings["exact_save"]), "", f"### {strings['exact_reading']}", *cleared(language_now))

    def number_fields(pattern: str, strings: dict[str, str]) -> tuple:
        """The two fields of the number recognition for a PO number pattern: (the words, with the line of an own pattern standing in the
        empty field for a pattern of another form, the fewest digits, the most digits)."""
        parts = pattern_parts(pattern)
        if parts is None:
            return gr.update(value="", placeholder=strings["exact_po_number_own"]), "", ""
        return gr.update(value=parts[0], placeholder=""), str(parts[1]), str(parts[2])

    def number_problems(problems: dict[str, str], strings: dict[str, str]) -> str:
        """The refused fields of the number recognition, each by its label with its problem: the digits by their two labels as one."""
        labels = {"words": strings["exact_field:po_words"],
                  "digits": f"{strings['exact_field:po_digits_least']} {strings['exact_field:po_digits_most']}"}
        return strings["settings_buyer_refused"].format(fields="; ".join(f"{labels[field]}: {problem}" for field, problem in problems.items()))

    def exact_numbers_opened(language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the two fields filled with the PO number pattern of the settings file (the
        settings in use when the file cannot be read), and the pattern they were filled with."""
        strings = STRINGS[language_now]
        pattern = read(strings)[0].po_number_pattern
        return (*number_fields(pattern, strings), pattern)

    def exact_numbers_saved(words: str, least: str, most: str, filled: str | None, language_now: str) -> tuple:
        """"Nummerherkenning opslaan": po_number_pattern written from the two fields and nothing else: (the note, the two fields, what
        they are now filled with). Fields as they were filled write nothing, so that an own pattern is left alone while they are empty;
        a refused field writes nothing and is named by its label; and a file whose pattern is no longer the one the fields were filled
        with (another browser tab saved meanwhile, or the file was changed) writes nothing and says so with the fields' own note
        ("exact_number_changed"), which the save sees inside the lock of update_settings_file. A settings file that cannot be read
        refuses with its line."""
        strings = STRINGS[language_now]
        same = (gr.update(), gr.update(), gr.update())
        line = read(strings)[1]
        if line is not None:
            return (line, *same, filled)
        parts = pattern_parts(filled) if isinstance(filled, str) else None
        shown = (typed_words(parts[0]), str(parts[1]), str(parts[2])) if parts is not None else ([], "", "")
        if isinstance(filled, str) and (typed_words(words), (least or "").strip(), (most or "").strip()) == shown:
            return (strings["settings_buyer_unchanged"], *same, filled)
        pattern, problems = pattern_from(words, least, most, language_now)
        if problems:
            return (number_problems(problems, strings), *same, filled)

        def build(held: Settings) -> dict:
            if held.po_number_pattern != filled:
                raise _Refused(strings["exact_number_changed"])
            return {"po_number_pattern": pattern}

        saved, note, _ = write(build, strings)
        if saved is None:
            return (note, *same, filled)
        return (strings["settings_buyer_saved"], *number_fields(saved.po_number_pattern, strings), saved.po_number_pattern)

    def exact_numbers_relabel(language_now: str, filled: str | None) -> tuple:
        """The page's language switched: the heading, the two fields and their button in the other language, with what is typed kept,
        and the note emptied."""
        strings = STRINGS[language_now]
        own = isinstance(filled, str) and pattern_parts(filled) is None
        return (f"### {strings['exact_numbers_title']}", gr.update(label=strings["exact_field:po_words"], info=strings["exact_help:po_words"],
                          placeholder=strings["exact_po_number_own"] if own else ""),
                gr.update(label=strings["exact_field:po_digits_least"]), gr.update(label=strings["exact_field:po_digits_most"]),
                gr.update(value=strings["exact_po_number_save"]), "")

    with gr.Tab(first["exact_title"], id="exact", elem_id="exact-block") as block:
        intro = gr.Markdown(first["exact_intro"])
        state_title = gr.Markdown(f"### {first['exact_state_title']}")
        state = gr.Markdown("", elem_id="exact-state")
        refresh = gr.Button(first["exact_refresh"])
        part = gr.Markdown(f"### {first['exact_part_po']}")
        sample = gr.File(label=first["exact_sample"], type="filepath")      # any file: the block names another type by its sentence
        page_shown = gr.Dataframe(value=page_table([], first), label=first["exact_page"], interactive=False, wrap=True, elem_id="exact-page")
        sample_note = gr.Markdown("", elem_id="exact-sample-note")
        fields = {}
        for names in (("po_label", "date_label"), ("supplier_label", "supplier_line"), ("version_label",), ("currency_label", "incoterm_label")):
            with gr.Row():
                for field in names:
                    fields[field] = gr.Textbox(label=first[f"exact_field:{field}"], info=help_of(field, first))
        with gr.Row():
            title_word = gr.Textbox(label=first["exact_field:title_word"], scale=4)
            find = gr.Button(first["exact_find"], scale=1)
        title_note = gr.Markdown("", elem_id="exact-title-note")
        slots = []
        for start in range(0, SLOTS, 4):
            with gr.Row():
                slots += [gr.Dropdown(choices=meaning_choices(first), value=None, label=str(n), visible=False) for n in range(start + 1, start + 5)]
        fields["table_end"] = gr.Textbox(label=first["exact_field:table_end"], info=first["exact_help:table_end"])
        fields["numbers"] = gr.Radio(choices=number_choices(first), value="comma", label=first["exact_field:numbers"])
        fields["per_piece"] = gr.Checkbox(value=False, label=first["exact_field:per_piece"])
        senders = gr.Textbox(label=first["exact_field:senders"], info=first["exact_help:senders"])
        with gr.Row():
            test_button = gr.Button(first["exact_test"])
            save_button = gr.Button(first["exact_save"], variant="primary")
        note = gr.Markdown("", elem_id="exact-note")
        reading_title = gr.Markdown(f"### {first['exact_reading']}")
        summary = gr.Markdown("", elem_id="exact-summary")
        header = gr.Dataframe(value=header_table([], first), interactive=False, wrap=True, elem_id="exact-header")
        lines = gr.Dataframe(value={"headers": line_headers("nl"), "data": []}, column_widths=line_widths(), interactive=False, wrap=True,
                             elem_id="exact-lines")
        problems = gr.Markdown("", elem_id="exact-problems")
        numbers_title = gr.Markdown(f"### {first['exact_numbers_title']}")
        po_words = gr.Textbox(label=first["exact_field:po_words"], info=first["exact_help:po_words"])
        with gr.Row():
            po_least = gr.Textbox(label=first["exact_field:po_digits_least"])
            po_most = gr.Textbox(label=first["exact_field:po_digits_most"])
        number_save = gr.Button(first["exact_po_number_save"])
        number_note = gr.Markdown("", elem_id="exact-number-note")
        title_row = gr.State([])                     # the words of the title row found, whose dropdowns are shown
        filled = gr.State(None)                      # what the form was filled with: saved_values of the file at that moment
        number_filled = gr.State(None)               # the PO number pattern of the file that the two fields were filled with
        _export_part(gr, app, language, settings_tab, read, write, state, state_text)
        _articles_part(gr, app, language, settings_tab, read, write)                       # part 3, the export of articles
        _tryout_section(gr, app, language, settings_tab, refresh, read, write, clock)      # the part "Proefdraaien", last in the block

    numbers = [po_words, po_least, po_most, number_filled]
    gr.on(triggers=[app.load, settings_tab.select], fn=exact_numbers_opened, inputs=language, outputs=numbers)
    number_save.click(exact_numbers_saved, inputs=[*numbers, language], outputs=[number_note, *numbers])
    language.change(exact_numbers_relabel, inputs=[language, number_filled],
                    outputs=[numbers_title, po_words, po_least, po_most, number_save, number_note])
    form = [fields[field] for field in EXACT_FORM]
    read_out = [summary, header, lines, problems]
    gr.on(triggers=[app.load, settings_tab.select], fn=exact_opened, inputs=language,
          outputs=[state, *form, title_word, title_row, *slots, senders, filled, title_note])
    sample.change(exact_sample_shown, inputs=[sample, language], outputs=[page_shown, sample_note, *read_out, note])
    find.click(exact_title_found, inputs=[sample, title_word, title_row, *slots, language], outputs=[title_row, *slots, title_note])
    tried_with = [sample, *form, title_row, *slots, senders, filled, language]
    test_button.click(exact_tested, inputs=tried_with, outputs=[*read_out, note])
    save_button.click(exact_saved, inputs=tried_with, outputs=[note, state, filled, *read_out])
    refresh.click(exact_state_shown, inputs=language, outputs=state)
    language.change(exact_relabel, inputs=[language, sample, fields["numbers"], title_row, *slots],
                    outputs=[block, intro, state_title, state, refresh, part, sample, page_shown, sample_note,
                             *[fields[field] for field in _EXACT_FIELDS], title_word, find, title_note, *slots, fields["table_end"],
                             fields["numbers"], fields["per_piece"], senders, test_button, save_button, note, reading_title, *read_out])


def _export_part(gr, app, language, settings_tab, read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable, state,
                 state_text: Callable[[Settings, str], str]) -> None:
    """Part 2 of the block "Koppeling met Exact", inside the block below part 1, and its events (see the module docstring;
    aid/review/exact_settings.py has what it shows and writes): the columns of an export of order lines, the number convention and the
    tick that the PO number stands on the first line of each PO only, set from one sample with a trial read. read and write are those
    of _settings_tab; state is the block's state lines, which a save fills again with state_text. Every control is made once, the
    twelve dropdowns among them, so that every handler is one of the page's functions. Two states of the page keep the titles the
    dropdowns offer and what the part was filled with (saved_export: the three settings as the file held them at the load, at the select
    of the settings tab or after a save), by which a save from a form that another browser tab, or a change of the file, overtook is
    refused."""
    first = STRINGS["nl"]
    seen: dict[tuple, tuple[list[list[str]], int, list[str]]] = {}      # the samples read: (file, row) -> (first rows, row, titles)

    def choices(titles: list[str], strings: dict[str, str]) -> list[tuple[str, str]]:
        """The choices of a dropdown: the choice for no column, then the titles offered."""
        return [(strings["exact_export_no_column"], NO_COLUMN), *[(title, title) for title in titles]]

    def number_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"exact_numbers:{value}"], value) for value in ("comma", "point")]

    def help_of(field: str, strings: dict[str, str]) -> str | None:
        """The help under a dropdown: under the line number's only."""
        return strings["exact_export_help:line_id"] if field == "line_id" else None

    def head_table(head: list[list[str]], language_now: str) -> dict:
        return {"headers": head_headers(head, language_now), "data": head}

    def lines_table(rows: list[list[str]], values: dict | None, language_now: str):
        return gr.update(value={"headers": export_headers(values, language_now), "data": rows})

    def offer(titles: list[str], values) -> tuple[list[str], list[str]]:
        """The titles the dropdowns offer and the column each of them holds: the titles of the sample's row with the column titles; a
        column chosen before stays chosen, spelled as the sample spells it where the sample has it (compared as the export reader compares
        titles) and offered after the sample's titles where it has not, so that "Testen" names it."""
        offered = {title.casefold(): title for title in titles}
        kept = []
        for value in values:
            title = (value or "").strip()
            kept.append(NO_COLUMN if title in ("", NO_COLUMN) else offered.setdefault(title.casefold(), title))
        return list(offered.values()), kept

    def updates(offered: list[str], kept: list[str], strings: dict[str, str]) -> list:
        return [gr.update(choices=choices(offered, strings), value=value) for value in kept]

    def file_of(sample: str) -> tuple | None:
        """The sample as what was read of it is kept: its path, size and modification time; None when the file cannot be looked at."""
        try:
            status = Path(sample).stat()
        except OSError:
            return None
        return str(sample), status.st_size, status.st_mtime_ns

    def sample_read(sample: str | None, row, language_now: str) -> tuple[dict, int | None, list[str] | None, str]:
        """The sample as the part shows it: (the table of its first rows, the row with the column titles, its titles, the line under the
        table). The row is the one typed, a whole number from 1; an empty field, 0 or a negative number is none, and the row is then the
        one the sample's titles are found on (sample_head). One read of the file gives the table and the titles, and is kept for the same
        file (the same path, size and modification time) and the same row, so that a select of the settings tab or a language switch
        reads the sample no more. No sample, or one that the export reader refuses as a whole or that cannot be opened: an empty table, no
        row, None for the titles, and the sentence that says why ("" for no sample)."""
        strings = STRINGS[language_now]
        if not sample:
            return head_table([], language_now), None, None, ""
        try:
            typed = int(row) if row is not None and int(row) >= 1 else None
            file = file_of(sample)
            read = seen.get((file, typed)) if file is not None else None
            if read is None:
                read = sample_head(Path(sample), typed)        # the reader's refusal of the file, or the OSError of one that is gone
                if file is not None:
                    if len(seen) > 8:                    # the samples of other files and rows: read again when they come back
                        seen.clear()
                    seen[(file, typed)] = seen[(file, read[1])] = read
            head, found, titles = read
        except ExportRefused as refused:
            return head_table([], language_now), None, None, refusal_text(refused, language_now, _literal)
        except OSError:
            return head_table([], language_now), None, None, strings["exact_export_unopenable"]
        except Exception as error:                       # anything else, by the class of its error
            return head_table([], language_now), None, None, strings["settings_failed"].format(code=type(error).__name__)
        return head_table(head, language_now), found, titles, ""

    def given(values: tuple) -> tuple[dict, object, str]:
        """The inputs of "Testen" and "Exportkolommen opslaan" after the sample: (the form, what the part was filled with, the language)."""
        count = len(EXPORT_FORM)
        return dict(zip(EXPORT_FORM, values)) | {"numbers": values[count], "fill_down": values[count + 1]}, values[-2], values[-1]

    def judged(sample: str | None, form: dict, language_now: str) -> tuple[dict | None, ExportTrial | None, list[str]]:
        """The form and the sample judged: (the three settings or None, the trial read of the sample with them or None, the problems
        before the reading: of the form in the order of its fields, then a missing sample). A file that cannot be opened raises its
        OSError."""
        settings, refused = export_from_form(form, language_now)
        before = [refused[field] for field in EXPORT_FORM if field in refused]
        before += [] if sample else [STRINGS[language_now]["exact_problem:no_sample"]]
        tried = export_trial(Path(sample), settings, language_now, _literal) if settings is not None and sample else None
        return settings, tried, before

    def reading(tried: ExportTrial | None, settings: dict | None, language_now: str) -> tuple:
        """A trial read as the part shows it: (what it says, the table of the first and the last order)."""
        if tried is None:
            return "", lines_table([], None, language_now)
        return export_text(tried, settings, language_now), lines_table(export_lines(tried, settings, language_now), settings, language_now)

    def export_opened(sample: str | None, row, language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the part filled with the three settings of the file (the settings in use when
        the file cannot be read): each dropdown on its saved column, offering the titles of the sample's row with the column titles while
        a sample is chosen, and the saved columns; the convention and the tick; the titles offered; and what the part is filled with."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        form = form_from_export(settings)
        offered, kept = offer(sample_read(sample, row, language_now)[2] or [], [form[field] for field in EXPORT_FORM])
        return (*updates(offered, kept, strings), form["numbers"], form["fill_down"], offered, saved_export(settings))

    def export_sample_shown(sample: str | None, *values) -> tuple:
        """A sample chosen, or taken away: (the table of its first rows, the line under it, the row with the column titles, the twelve
        dropdowns, the titles offered, the reading emptied, the table of the orders emptied, the note emptied). The dropdowns offer
        the titles of the row found and keep each column chosen (offer). A sample that the reader refuses as a whole, or that cannot be
        opened, is named by its sentence and leaves the dropdowns as they are, as does a sample taken away."""
        *chosen, language_now = values
        strings = STRINGS[language_now]
        head, row, titles, line = sample_read(sample, None, language_now)
        cleared = ("", lines_table([], None, language_now), "")
        if titles is None:
            return (head, line, None, *[gr.update()] * (len(EXPORT_FORM) + 1), *cleared)
        offered, kept = offer(titles, chosen)
        return (head, "", row, *updates(offered, kept, strings), offered, *cleared)

    def export_header_shown(sample: str | None, row, *values) -> tuple:
        """Another row typed as the row with the column titles: (the twelve dropdowns offering its titles and keeping each column chosen,
        the titles offered, the line under the table of the first rows). Without a sample nothing changes; a sample that cannot be read
        any more is named by its sentence."""
        *chosen, language_now = values
        if not sample:
            return tuple([gr.update()] * (len(EXPORT_FORM) + 2))
        _, _, titles, line = sample_read(sample, row, language_now)
        if titles is None:
            return (*[gr.update()] * (len(EXPORT_FORM) + 1), line)
        offered, kept = offer(titles, chosen)
        return (*updates(offered, kept, STRINGS[language_now]), offered, "")

    def export_tested(sample: str | None, *values) -> tuple:
        """"Testen": the sample read with the three settings of the form; nothing is written. (What the trial read says, the table of the
        first and the last order, and the note with the problems of the form and of a missing sample, or that the sample cannot be
        opened.)"""
        form, _, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            settings, tried, before = judged(sample, form, language_now)
        except OSError:
            return ("", lines_table([], None, language_now), strings["exact_export_unopenable"])
        except Exception as error:                       # anything else, by the class of its error
            return ("", lines_table([], None, language_now), strings["settings_failed"].format(code=type(error).__name__))
        return (*reading(tried, settings, language_now), " ".join(before))

    def export_saved(sample: str | None, *values) -> tuple:
        """"Exportkolommen opslaan": the sample read as "Testen" reads it, and the three settings written into the settings file when nothing is wrong
        (write, which writes the keys whose value changed): (the note, the state lines, what the part is now filled with, the reading).
        Any problem of the form or of the sample refuses the save, each named in the page's language, and nothing is written; so does a
        settings file that cannot be read, with its line, and a file whose three settings are no longer those the part was filled with
        (another browser tab saved, or the file was changed), which the save sees inside the lock of update_settings_file."""
        form, filled, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            settings, tried, before = judged(sample, form, language_now)
        except OSError:
            return (strings["exact_not_saved"].format(problems=strings["exact_export_unopenable"]), gr.update(), filled,
                    *reading(None, None, language_now))
        except Exception as error:                       # anything else, by the class of its error
            return (strings["settings_failed"].format(code=type(error).__name__), gr.update(), filled, *reading(None, None, language_now))
        shown_now = reading(tried, settings, language_now)
        problems = before + (tried.problems if tried is not None else [])
        if problems:
            return (strings["exact_not_saved"].format(problems=" ".join(problems)), gr.update(), filled, *shown_now)

        def build(held: Settings) -> dict:
            if not isinstance(filled, dict) or saved_export(held) != filled:
                raise _Refused(strings["exact_export_changed"])
            return settings

        saved, note, _ = write(build, strings)
        if saved is None:
            return (note, gr.update(), filled, *shown_now)
        return (strings["exact_export_saved"], state_text(saved, language_now), saved_export(saved), *shown_now)

    def export_relabel(language_now: str, sample: str | None, row, *values) -> tuple:
        """The page's language switched: part 2 in the other language, with what is chosen kept, the table of the first rows and the line
        under it included; the note and the reading are emptied, for "Testen" to read the sample again in that language."""
        *chosen, offered_now, numbers_now = values
        strings = STRINGS[language_now]
        head, _, _, line = sample_read(sample, row, language_now)
        return (f"### {strings['exact_part_export']}", strings["exact_export_intro"], gr.update(label=strings["exact_export_sample"]),
                gr.update(label=strings["exact_export_head"], value=head), line, gr.update(label=strings["exact_export_header_row"]),
                *[gr.update(label=field_label(field, language_now), info=help_of(field, strings), choices=choices(offered_now or [], strings),
                            value=value) for field, value in zip(EXPORT_FORM, chosen)],
                gr.update(label=strings["exact_export_field:numbers"], choices=number_choices(strings), value=numbers_now),
                gr.update(label=strings["exact_export_field:fill_down"]), gr.update(value=strings["exact_test"]),
                gr.update(value=strings["exact_export_save"]), "", "", lines_table([], None, language_now))

    with gr.Column(elem_id="export-part"):
        part = gr.Markdown(f"### {first['exact_part_export']}")
        intro = gr.Markdown(first["exact_export_intro"])
        sample = gr.File(label=first["exact_export_sample"], type="filepath", elem_id="export-sample")      # any file, as part 1's
        head = gr.Dataframe(value=head_table([], "nl"), label=first["exact_export_head"], interactive=False, wrap=True, elem_id="export-head")
        sample_note = gr.Markdown("", elem_id="export-sample-note")
        row = gr.Number(value=None, label=first["exact_export_header_row"], precision=0, elem_id="export-row")   # empty or below 1: found
        fields = {}
        for start in range(0, len(EXPORT_FORM), 4):
            with gr.Row():
                for field in EXPORT_FORM[start:start + 4]:
                    fields[field] = gr.Dropdown(choices=choices([], first), value=NO_COLUMN, label=field_label(field, "nl"),
                                                info=help_of(field, first), elem_id=f"export-field-{field}")
        numbers = gr.Radio(choices=number_choices(first), value="point", label=first["exact_export_field:numbers"], elem_id="export-numbers")
        fill_down = gr.Checkbox(value=False, label=first["exact_export_field:fill_down"], elem_id="export-fill-down")
        with gr.Row():
            test_button = gr.Button(first["exact_test"], elem_id="export-test")
            save_button = gr.Button(first["exact_export_save"], variant="primary", elem_id="export-save")
        note = gr.Markdown("", elem_id="export-note")
        read_text = gr.Markdown("", elem_id="export-read")
        lines = gr.Dataframe(value={"headers": export_headers(None, "nl"), "data": []}, interactive=False, wrap=True, elem_id="export-lines")
        offered = gr.State([])                       # the titles the twelve dropdowns offer
        filled = gr.State(None)                      # what the part was filled with: saved_export of the file at that moment
        gr.HTML("", head=f"<style>{_WHOLE_TITLES}</style>", container=False, padding=False, js_on_load=None)   # last: its space shows nowhere

    chosen = [fields[field] for field in EXPORT_FORM]
    gr.on(triggers=[app.load, settings_tab.select], fn=export_opened, inputs=[sample, row, language],
          outputs=[*chosen, numbers, fill_down, offered, filled])
    sample.change(export_sample_shown, inputs=[sample, *chosen, language], outputs=[head, sample_note, row, *chosen, offered, read_text, lines, note])
    row.input(export_header_shown, inputs=[sample, row, *chosen, language], outputs=[*chosen, offered, sample_note])
    tried_with = [sample, *chosen, numbers, fill_down, filled, language]
    test_button.click(export_tested, inputs=tried_with, outputs=[read_text, lines, note])
    save_button.click(export_saved, inputs=tried_with, outputs=[note, state, filled, read_text, lines])
    language.change(export_relabel, inputs=[language, sample, row, *chosen, offered, numbers],
                    outputs=[part, intro, sample, head, sample_note, row, *chosen, numbers, fill_down, test_button, save_button, note, read_text,
                             lines])


def _articles_part(gr, app, language, settings_tab, read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable) -> None:
    """Part 3 of the block "Koppeling met Exact", inside the block below part 2, and its events (see the module docstring;
    aid/review/exact_settings.py has what it shows and writes): the columns of an export of articles with their purchase prices and the
    convention of its numbers, set from one sample with a trial read, as part 2 sets the export of order lines. It stores no price:
    the export itself is given on the tab "Prijslijst". read and write are those of _settings_tab. Every control is made once, so
    that every handler is one of the page's functions. Two states of the page keep the titles the dropdowns offer and what the part
    was filled with (saved_articles: the two settings as the file held them at the load, at the select of the settings tab or after a
    save), by which a save from a form that another browser tab, or a change of the file, overtook is refused."""
    first = STRINGS["nl"]
    seen: dict[tuple, tuple[list[list[str]], int, list[str]]] = {}      # the samples read: (file, row) -> (first rows, row, titles)

    def choices(titles: list[str], strings: dict[str, str]) -> list[tuple[str, str]]:
        """The choices of a dropdown: the choice for no column, then the titles offered."""
        return [(strings["exact_export_no_column"], NO_COLUMN), *[(title, title) for title in titles]]

    def number_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"exact_numbers:{value}"], value) for value in ("comma", "point")]

    def head_table(head: list[list[str]], language_now: str) -> dict:
        return {"headers": head_headers(head, language_now), "data": head}

    def rows_table(rows: list[list[str]], values: dict | None, language_now: str):
        return gr.update(value={"headers": articles_headers(values, language_now), "data": rows})

    def offer(titles: list[str], values) -> tuple[list[str], list[str]]:
        """The titles the dropdowns offer and the column each of them holds, as part 2 offers them: the titles of the sample's row with
        the column titles; a column chosen before stays chosen, spelled as the sample spells it where the sample has it and offered
        after the sample's titles where it has not, so that "Testen" names it."""
        offered = {title.casefold(): title for title in titles}
        kept = []
        for value in values:
            title = (value or "").strip()
            kept.append(NO_COLUMN if title in ("", NO_COLUMN) else offered.setdefault(title.casefold(), title))
        return list(offered.values()), kept

    def updates(offered: list[str], kept: list[str], strings: dict[str, str]) -> list:
        return [gr.update(choices=choices(offered, strings), value=value) for value in kept]

    def sample_read(sample: str | None, row, language_now: str) -> tuple[dict, int | None, list[str] | None, str]:
        """The sample as the part shows it, as part 2 shows its sample: (the table of its first rows, the row with the column titles,
        its titles, the line under the table). One read of the file gives the table and the titles, and is kept for the same file (its
        path, size and modification time) and the same row. No sample, or one that the export reader refuses as a whole or that cannot
        be opened: an empty table, no row, None for the titles, and the sentence that says why ("" for no sample)."""
        strings = STRINGS[language_now]
        if not sample:
            return head_table([], language_now), None, None, ""
        try:
            typed = int(row) if row is not None and int(row) >= 1 else None
            status = Path(sample).stat()
            file = (str(sample), status.st_size, status.st_mtime_ns)
            if (file, typed) not in seen:
                if len(seen) > 8:                        # the samples of other files and rows: read again when they come back
                    seen.clear()
                found = sample_head(Path(sample), typed)     # the reader's refusal of the file, or the OSError of one that is gone
                seen[(file, typed)] = seen[(file, found[1])] = found
            head, found_row, titles = seen[(file, typed)]
        except ExportRefused as refused:
            return head_table([], language_now), None, None, refusal_text(refused, language_now, _literal)
        except OSError:
            return head_table([], language_now), None, None, strings["exact_export_unopenable"]
        except Exception as error:                       # anything else, by the class of its error
            return head_table([], language_now), None, None, strings["settings_failed"].format(code=type(error).__name__)
        return head_table(head, language_now), found_row, titles, ""

    def given(values: tuple) -> tuple[dict, object, str]:
        """The inputs of "Testen" and "Artikelkolommen opslaan" after the sample: (the form, what the part was filled with, the language)."""
        count = len(ARTICLE_FORM)
        return dict(zip(ARTICLE_FORM, values)) | {"numbers": values[count]}, values[-2], values[-1]

    def judged(sample: str | None, form: dict, language_now: str) -> tuple[dict | None, ArticlesTrial | None, list[str]]:
        """The form and the sample judged: (the two settings or None, the trial read of the sample with them or None, the problems
        before the reading: of the form in the order of its fields, then a missing sample). A file that cannot be opened raises its
        OSError."""
        settings, refused = articles_from_form(form, language_now)
        before = [refused[field] for field in ARTICLE_FORM if field in refused]
        before += [] if sample else [STRINGS[language_now]["exact_problem:no_sample"]]
        tried = articles_trial(Path(sample), settings, language_now, _literal) if settings is not None and sample else None
        return settings, tried, before

    def reading(tried: ArticlesTrial | None, settings: dict | None, language_now: str) -> tuple:
        """A trial read as the part shows it: (what it says, the table of the first articles as read)."""
        if tried is None:
            return "", rows_table([], None, language_now)
        return articles_text(tried, language_now), rows_table(articles_rows(tried, settings, language_now), settings, language_now)

    def articles_opened(sample: str | None, row, language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the part filled with the two settings of the file (the settings in use when
        the file cannot be read): each dropdown on its saved column, offering the titles of the sample's row with the column titles
        while a sample is chosen, and the saved columns; the convention; the titles offered; and what the part is filled with."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        form = form_from_articles(settings)
        offered, kept = offer(sample_read(sample, row, language_now)[2] or [], [form[field] for field in ARTICLE_FORM])
        return (*updates(offered, kept, strings), form["numbers"], offered, saved_articles(settings))

    def articles_sample_shown(sample: str | None, *values) -> tuple:
        """A sample chosen, or taken away: (the table of its first rows, the line under it, the row with the column titles, the three
        dropdowns, the titles offered, the reading emptied, the table of the articles emptied, the note emptied). A sample that the
        reader refuses as a whole, or that cannot be opened, is named by its sentence and leaves the dropdowns as they are, as does a
        sample taken away."""
        *chosen, language_now = values
        head, row, titles, line = sample_read(sample, None, language_now)
        cleared = ("", rows_table([], None, language_now), "")
        if titles is None:
            return (head, line, None, *[gr.update()] * (len(ARTICLE_FORM) + 1), *cleared)
        offered, kept = offer(titles, chosen)
        return (head, "", row, *updates(offered, kept, STRINGS[language_now]), offered, *cleared)

    def articles_header_shown(sample: str | None, row, *values) -> tuple:
        """Another row typed as the row with the column titles: (the three dropdowns offering its titles and keeping each column
        chosen, the titles offered, the line under the table of the first rows). Without a sample nothing changes."""
        *chosen, language_now = values
        if not sample:
            return tuple([gr.update()] * (len(ARTICLE_FORM) + 2))
        _, _, titles, line = sample_read(sample, row, language_now)
        if titles is None:
            return (*[gr.update()] * (len(ARTICLE_FORM) + 1), line)
        offered, kept = offer(titles, chosen)
        return (*updates(offered, kept, STRINGS[language_now]), offered, "")

    def articles_tested(sample: str | None, *values) -> tuple:
        """"Testen": the sample read with the two settings of the form; nothing is written. (What the trial read says, the table of
        the first articles as read, and the note with the problems of the form and of a missing sample, or that the sample cannot be
        opened.)"""
        form, _, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            settings, tried, before = judged(sample, form, language_now)
        except OSError:
            return ("", rows_table([], None, language_now), strings["exact_export_unopenable"])
        except Exception as error:                       # anything else, by the class of its error
            return ("", rows_table([], None, language_now), strings["settings_failed"].format(code=type(error).__name__))
        return (*reading(tried, settings, language_now), " ".join(before))

    def articles_saved(sample: str | None, *values) -> tuple:
        """"Artikelkolommen opslaan": the sample read as "Testen" reads it, and the two settings written into the settings file when nothing is wrong
        (write, which writes the keys whose value changed): (the note, what the part is now filled with, the reading). No price is
        stored. Any problem of the form or of the sample refuses the save, each named in the page's language, and nothing is written;
        so does a settings file that cannot be read, with its line, and a file whose two settings are no longer those the part was
        filled with, which the save sees inside the lock of update_settings_file."""
        form, filled, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            settings, tried, before = judged(sample, form, language_now)
        except OSError:
            return (strings["exact_not_saved"].format(problems=strings["exact_export_unopenable"]), filled, *reading(None, None, language_now))
        except Exception as error:                       # anything else, by the class of its error
            return (strings["settings_failed"].format(code=type(error).__name__), filled, *reading(None, None, language_now))
        shown_now = reading(tried, settings, language_now)
        problems = before + (tried.problems if tried is not None else [])
        if problems:
            return (strings["exact_not_saved"].format(problems=" ".join(problems)), filled, *shown_now)

        def build(held: Settings) -> dict:
            if not isinstance(filled, dict) or saved_articles(held) != filled:
                raise _Refused(strings["exact_export_changed"])
            return settings

        saved, note, _ = write(build, strings)
        if saved is None:
            return (note, filled, *shown_now)
        return (strings["exact_export_saved"], saved_articles(saved), *shown_now)

    def articles_relabel(language_now: str, sample: str | None, row, *values) -> tuple:
        """The page's language switched: part 3 in the other language, with what is chosen kept, the table of the first rows and the
        line under it included; the note and the reading are emptied, for "Testen" to read the sample again in that language."""
        *chosen, offered_now, numbers_now = values
        strings = STRINGS[language_now]
        head, _, _, line = sample_read(sample, row, language_now)
        return (f"### {strings['exact_part_articles']}", strings["exact_articles_intro"], gr.update(label=strings["exact_articles_sample"]),
                gr.update(label=strings["exact_export_head"], value=head), line, gr.update(label=strings["exact_export_header_row"]),
                *[gr.update(label=article_label(field, language_now), choices=choices(offered_now or [], strings), value=value)
                  for field, value in zip(ARTICLE_FORM, chosen)],
                gr.update(label=strings["exact_export_field:numbers"], choices=number_choices(strings), value=numbers_now),
                gr.update(value=strings["exact_test"]), gr.update(value=strings["exact_articles_save"]), "", "",
                rows_table([], None, language_now))

    with gr.Column(elem_id="articles-part"):
        part = gr.Markdown(f"### {first['exact_part_articles']}")
        intro = gr.Markdown(first["exact_articles_intro"])
        sample = gr.File(label=first["exact_articles_sample"], type="filepath", elem_id="articles-sample")      # any file, as part 2's
        head = gr.Dataframe(value=head_table([], "nl"), label=first["exact_export_head"], interactive=False, wrap=True, elem_id="articles-head")
        sample_note = gr.Markdown("", elem_id="articles-sample-note")
        row = gr.Number(value=None, label=first["exact_export_header_row"], precision=0, elem_id="articles-row")   # empty or below 1: found
        with gr.Row():
            fields = {field: gr.Dropdown(choices=choices([], first), value=NO_COLUMN, label=article_label(field, "nl"),
                                         elem_id=f"articles-field-{field}") for field in ARTICLE_FORM}
        numbers = gr.Radio(choices=number_choices(first), value="point", label=first["exact_export_field:numbers"], elem_id="articles-numbers")
        with gr.Row():
            test_button = gr.Button(first["exact_test"], elem_id="articles-test")
            save_button = gr.Button(first["exact_articles_save"], variant="primary", elem_id="articles-save")
        note = gr.Markdown("", elem_id="articles-note")
        read_text = gr.Markdown("", elem_id="articles-read")
        rows = gr.Dataframe(value={"headers": articles_headers(None, "nl"), "data": []}, interactive=False, wrap=True, elem_id="articles-rows")
        offered = gr.State([])                       # the titles the three dropdowns offer
        filled = gr.State(None)                      # what the part was filled with: saved_articles of the file at that moment

    chosen = [fields[field] for field in ARTICLE_FORM]
    gr.on(triggers=[app.load, settings_tab.select], fn=articles_opened, inputs=[sample, row, language], outputs=[*chosen, numbers, offered, filled])
    sample.change(articles_sample_shown, inputs=[sample, *chosen, language], outputs=[head, sample_note, row, *chosen, offered, read_text, rows, note])
    row.input(articles_header_shown, inputs=[sample, row, *chosen, language], outputs=[*chosen, offered, sample_note])
    tried_with = [sample, *chosen, numbers, filled, language]
    test_button.click(articles_tested, inputs=tried_with, outputs=[read_text, rows, note])
    save_button.click(articles_saved, inputs=tried_with, outputs=[note, filled, read_text, rows])
    language.change(articles_relabel, inputs=[language, sample, row, *chosen, offered, numbers],
                    outputs=[part, intro, sample, head, sample_note, row, *chosen, numbers, test_button, save_button, note, read_text, rows])


def _tryout_section(gr, app, language, settings_tab, refresh, read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable,
                    clock: Callable[[], datetime]) -> None:
    """The part "Proefdraaien met een inkooporder uit Exact" at the end of the block "Koppeling met Exact", with the switch of the test
    mode, and their events (see the module docstring; aid/tryout.py makes the try-out and reads it back, aid/sources/dropin.py puts
    it into the watched folder). refresh is the block's button "Stand vernieuwen", which fills the part again; read and write are
    those of _settings_tab; clock is the time of the page's own actions, the moment a try-out is made, which gives it its date, its
    title and the time in its file's name. Every control is made once. A state of the page keeps what the switch was filled with,
    test_mode as the file held it, by which a toggle of a switch that another browser tab overtook is refused, as the block's form is
    (ruling L6)."""
    first = STRINGS["nl"]

    def change_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"tryout_change:{change}"], change) for change in CHANGES]

    def store_of(settings: Settings) -> PoStore:
        return PoStore(settings.po_store_root, settings.authorization_limit)

    def part(settings: Settings, strings: dict[str, str], keep: str | None = None) -> tuple:
        """The part as the settings give it: (the line that says why it offers nothing, the controls shown or hidden, the dropdown of
        the POs the store knows, with `keep` chosen while the store knows it and else the one filed last, a note that the store
        knows none)."""
        if not offers_tryouts(settings):
            return strings["tryout_not_offered"], gr.update(visible=False), gr.update(choices=[], value=None), ""
        known = known_pos(store_of(settings))
        numbers = [number for _, number in known]
        chosen = keep if keep in numbers else (numbers[0] if numbers else None)
        return "", gr.update(visible=True), gr.update(choices=known, value=chosen), "" if known else strings["tryout_none_known"]

    def drawn(po_number: str | None, change: str | None, strings: dict[str, str]) -> tuple[dict, tuple[bytes, str, Settings] | str]:
        """The try-out of the PO chosen with the change chosen, as the settings file and the store now stand, read back before it
        leaves the page (checked_confirmation): (the dropdown, filled from the store when no PO was chosen, with the one filed last
        chosen, which the try-out is then made of; and (its PDF, the name of its file, the settings), or the note that says why
        there is none: the line of a settings file that cannot be read, the part not offered, no PO known, or a PO that gives no
        try-out, by the sentence of its reason). Nothing is written but in the read-back's own folder, which goes again at once."""
        settings, line = read(strings)
        if line is not None:
            return gr.update(), line
        if not offers_tryouts(settings):
            return gr.update(), strings["tryout_not_offered"]
        store, dropdown = store_of(settings), gr.update()
        if not po_number:                                # nothing chosen: the dropdown filled from the store first (ruling Y7)
            known = known_pos(store)
            po_number = known[0][1] if known else None
            dropdown = gr.update(choices=known, value=po_number)
        po = made_from(store, po_number) if po_number else None
        if po is None:
            return dropdown, strings["tryout_none_known"]
        now = clock()
        try:
            return dropdown, (checked_confirmation(po, change or "none", now, settings, store), file_name(po, now), settings)
        except TryoutRefused as refused:
            return dropdown, strings["tryout_refused"].format(reason=strings[f"tryout_reason:{refused.reason}"])

    def tryout_opened(language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the switch as the settings file holds the test mode (the settings in use when
        it cannot be read), what the switch is filled with, the part as those settings give it, and the download emptied."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        why, controls_shown, choices, note = part(settings, strings)
        return settings.test_mode, settings.test_mode, why, controls_shown, choices, gr.update(value=None, visible=False), note

    def tryout_refreshed(language_now: str, po_now: str | None) -> tuple:
        """"Stand vernieuwen" (ruling Y7): the switch and the part filled again as the settings file and the store now stand, the PO
        chosen kept while the store knows it; the download goes when another PO is chosen, or none."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        why, controls_shown, choices, note = part(settings, strings, keep=po_now)
        kept = bool(po_now) and choices.get("value") == po_now
        return (settings.test_mode, settings.test_mode, why, controls_shown, choices, gr.update() if kept else gr.update(value=None, visible=False),
                note)

    def tryout_made(po_number: str | None, change: str | None, language_now: str) -> tuple:
        """"Proefbevestiging maken (pdf)": (the dropdown, the download, the note). The try-out is written into a folder of its own in
        the page library's temporary folder, the one the download is served from, so that it is the one file written; the library
        removes it as it removes its own files. Without a try-out the download is emptied and the note says why, and nothing is
        written."""
        strings = STRINGS[language_now]
        dropdown, nothing = gr.update(), gr.update(value=None, visible=False)
        try:
            dropdown, made = drawn(po_number, change, strings)
            if isinstance(made, str):
                return dropdown, nothing, made
            pdf, name, _ = made
            Path(download.GRADIO_CACHE).mkdir(parents=True, exist_ok=True)
            path = Path(tempfile.mkdtemp(prefix="tryout_", dir=download.GRADIO_CACHE)) / name
            path.write_bytes(pdf)
        except Exception as error:
            return dropdown, nothing, strings["settings_failed"].format(code=type(error).__name__)
        return dropdown, gr.update(value=str(path), visible=True), strings["tryout_made"]

    def tryout_given(po_number: str | None, change: str | None, language_now: str) -> tuple:
        """"Proefbevestiging direct aan de assistent geven": the try-out put into the watched folder of the settings (drop), from a copy
        in a folder of its own under <cases_root>/tmp/work that goes again at once; the runner opens the case at its next pass. (the
        dropdown, the download emptied, so that no file of an earlier choice stays offered, the note); without a try-out the note says
        why, and nothing is written."""
        strings = STRINGS[language_now]
        dropdown, nothing = gr.update(), gr.update(value=None, visible=False)
        try:
            dropdown, made = drawn(po_number, change, strings)
            if isinstance(made, str):
                return dropdown, nothing, made
            pdf, name, settings = made
            with work_copy_folder(settings.cases_root / TEMPORARY / WORK, "aid_tryout_") as folder:
                (folder / name).write_bytes(pdf)
                drop(folder / name, name, settings.watched_folder)
        except Exception as error:
            return dropdown, nothing, strings["settings_failed"].format(code=type(error).__name__)
        return dropdown, nothing, strings["tryout_given"]

    def tryout_chosen() -> dict:
        """Another PO or change chosen by the buyer: the download of the choice before goes (ruling Y14)."""
        return gr.update(value=None, visible=False)

    def cart_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"tryout_cart_change:{change}"], change) for change in CART_CHANGES]

    def tryout_cart_made(po_number: str | None, change: str | None, language_now: str) -> tuple:
        """"Proefwinkelwagen maken": (the dropdown, the note). The try-out cart of the PO chosen, as it stands in the store, with the
        change chosen for the cart, read back before the note offers its link (checked_cart) and then written as
        <cases_root>/tryout/<PO number>_<change>.html (write_cart), the one file written; the note gives the link to it on the capture
        server. Without a PO chosen the dropdown is filled from the store first and the order filed last taken, as for a try-out
        confirmation. Without a cart the note says why (the line of a settings file that cannot be read, the part not offered, no PO
        known, or the sentence of the reason for which the PO gives none), and nothing is written."""
        strings = STRINGS[language_now]
        settings, line = read(strings)
        if line is not None:
            return gr.update(), line
        if not offers_tryouts(settings):
            return gr.update(), strings["tryout_not_offered"]
        store, dropdown = store_of(settings), gr.update()
        try:
            if not po_number:
                known = known_pos(store)
                po_number = known[0][1] if known else None
                dropdown = gr.update(choices=known, value=po_number)
            po = store.as_it_stands(po_number) if po_number else None
            if po is None:
                return dropdown, strings["tryout_none_known"]
            now = clock()
            po = as_compared(po, settings, store, now)   # the order as a try-out's case is compared with it: the cart starts from it
            checked_cart(po, change or "none", now, settings, store)
            name = write_cart(settings.cases_root, po, change or "none", now.date())
        except TryoutRefused as refused:
            return dropdown, strings["tryout_refused"].format(reason=strings[f"tryout_reason:{refused.reason}"])
        except Exception as error:
            return dropdown, strings["settings_failed"].format(code=type(error).__name__)
        link = cart_link(name)
        return dropdown, strings["tryout_cart_made"].format(link=f"[{link}]({link})")

    def tryout_cart_relabel(language_now: str, change_now: str | None) -> tuple:
        """The page's language switched: the choice and the button of the try-out cart in that language, the change chosen kept."""
        strings = STRINGS[language_now]
        return (gr.update(label=strings["tryout_cart_change"], choices=cart_choices(strings), value=change_now),
                gr.update(value=strings["tryout_cart_make"]))

    def test_mode_saved(on: bool, filled_with: bool | None, language_now: str) -> tuple:
        """The switch toggled by the buyer: test_mode written into the settings file and nothing else (write of _settings_tab, inside
        whose lock the file is read), and the part as the saved settings give it: (the switch, what it is filled with, the line why,
        the controls, the dropdown, the note). Turned off while the list "In Exact invoeren" holds changes that only try-out documents
        asked, the note says how many, to be dropped and not entered in the real Exact. Nothing is written for a file that cannot be
        read, which keeps the switch as it was filled and says so by its line, for a file whose test mode is no longer the one the
        switch was filled with (another browser tab or the file itself changed it meanwhile), and for a switch whose fill the page no
        longer knows (the page was started again): those two are filled again from the file, and the switch's own note says so."""
        strings = STRINGS[language_now]
        settings, line = read(strings)
        if line is not None:
            kept = filled_with if isinstance(filled_with, bool) else settings.test_mode
            return kept, gr.update(), gr.update(), gr.update(), gr.update(), line
        if not isinstance(filled_with, bool):
            return settings.test_mode, settings.test_mode, *part(settings, strings)[:3], strings["tryout_switch_refilled"]

        def build(held: Settings) -> dict:
            if held.test_mode != filled_with:
                raise _Refused(strings["tryout_switch_refilled"])
            return {"test_mode": bool(on)}

        saved, note, _ = write(build, strings)
        if saved is None and note == strings["tryout_switch_refilled"]:
            settings = read(strings)[0]
            return settings.test_mode, settings.test_mode, *part(settings, strings)[:3], note
        if saved is None:
            return filled_with, filled_with, gr.update(), gr.update(), gr.update(), note
        made_up = 0 if saved.test_mode else _open_from_tryouts(saved.cases_root)
        note = (strings["tryout_saved"] if not made_up else strings["tryout_saved_open_one"] if made_up == 1
                else strings["tryout_saved_open"].format(n=made_up))
        return saved.test_mode, saved.test_mode, *part(saved, strings)[:3], note

    def tryout_relabel(language_now: str, change_now: str | None) -> tuple:
        """The page's language switched: the part in that language, the change chosen kept, and the note emptied, but for the note
        that the store knows no PO while the part is offered, which is given in that language (ruling Y13)."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        offered = offers_tryouts(settings)
        none_known = offered and not known_pos(store_of(settings), limit=1)
        return (f"### {strings['tryout_title']}", strings["tryout_intro"], gr.update(label=strings["tryout_switch"]),
                "" if offered else strings["tryout_not_offered"], gr.update(label=strings["tryout_po"]),
                gr.update(label=strings["tryout_change"], choices=change_choices(strings), value=change_now),
                gr.update(value=strings["tryout_make"]), gr.update(value=strings["tryout_give"]), strings["tryout_none_known"] if none_known else "")

    title = gr.Markdown(f"### {first['tryout_title']}")
    intro = gr.Markdown(first["tryout_intro"])
    switch = gr.Checkbox(value=False, label=first["tryout_switch"])
    why = gr.Markdown("", elem_id="tryout-why")
    with gr.Column(visible=False, elem_id="tryout-controls") as controls:
        po_choice = gr.Dropdown(choices=[], value=None, label=first["tryout_po"])
        change = gr.Radio(choices=change_choices(first), value="none", label=first["tryout_change"])
        with gr.Row():
            make = gr.Button(first["tryout_make"])
            give = gr.Button(first["tryout_give"], variant="primary")
        download = gr.File(value=None, visible=False, interactive=False, show_label=False, elem_id="tryout-download")
        cart_change = gr.Radio(choices=cart_choices(first), value="none", label=first["tryout_cart_change"], elem_id="tryout-cart-change")
        cart_make = gr.Button(first["tryout_cart_make"])
    note = gr.Markdown("", elem_id="tryout-note")
    filled = gr.State(None)                          # the test mode as the file held it when the switch was filled

    shown = [switch, filled, why, controls, po_choice]
    gr.on(triggers=[app.load, settings_tab.select], fn=tryout_opened, inputs=language, outputs=[*shown, download, note])
    refresh.click(tryout_refreshed, inputs=[language, po_choice], outputs=[*shown, download, note])
    make.click(tryout_made, inputs=[po_choice, change, language], outputs=[po_choice, download, note])
    give.click(tryout_given, inputs=[po_choice, change, language], outputs=[po_choice, download, note])
    gr.on(triggers=[po_choice.input, change.input], fn=tryout_chosen, inputs=None, outputs=download)      # the buyer's choice, not a fill
    switch.input(test_mode_saved, inputs=[switch, filled, language], outputs=[*shown, note])      # the buyer's toggle, not a fill
    language.change(tryout_relabel, inputs=[language, change], outputs=[title, intro, switch, why, po_choice, change, make, give, note])
    cart_make.click(tryout_cart_made, inputs=[po_choice, cart_change, language], outputs=[po_choice, note])
    language.change(tryout_cart_relabel, inputs=[language, cart_change], outputs=[cart_change, cart_make])


def _open_from_tryouts(cases_root: Path) -> int:
    """How many changes of try-out documents (Change.tryout of aid/execution/by_hand.py) are still open on the list "In Exact
    invoeren"; 0 while the list cannot be read."""
    try:
        found, _ = changes(Path(cases_root) / FOLDER)
        return sum(1 for change in found if change.state == "open" and change.tryout)
    except Exception:                                # a file of the list held by another program, or any other error: no count
        return 0


def _ai_section(gr, app, language, tab: _Tab, tenant, client, mailbox, account_shown,
                begin: Callable[..., tuple[MicrosoftAccount | None, str | None]],
                read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable, opened, clock: Callable[[], datetime]):
    """The AI section of the settings tab and its events, step 5 of its part "Postvak en AI" (see the module docstring): the line of
    what reads the documents now, the choice of the reader with its answer line, then Copilot with its two buttons and their answer
    line. tenant, client and mailbox are the fields of the tab, account_shown the line that names the signed-in account, begin gives
    the account for an action or the note that says why there is none, and read and write are those of _settings_tab: the settings in
    the file (or those in use, with the line of a file that cannot be read), and a save into it. opened is the select of the part's
    section, and clock gives the time of the page's Deps in the local time of this PC. Its buttons and its choice take the account as
    a step other than the sign-in (begin's `elsewhere`). Returns the answer line of the choice, which the language switch of
    _settings_tab empties."""
    first = STRINGS["nl"]

    def ai_opened(language_now: str) -> str:
        """The tab opened: the reader of the settings file."""
        return read(STRINGS[language_now])[0].reader

    def sign_in_copilot(tenant_now: str, client_now: str, mailbox_now: str, language_now: str):
        """Sign in in the browser of this PC for the mailbox and for Copilot together, saying first that the page waits for it: (the
        account, a note)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant_now, client_now, strings, elsewhere=True)
        if refused:
            yield gr.update(), refused
            return
        yield gr.update(), strings["settings_waiting"]
        try:
            account.sign_in_browser(mail_scopes((mailbox_now or "").strip() or None) + COPILOT_SCOPES)
        except SignInFailed as error:
            yield _account_text(account, strings), strings["settings_sign_in_failed"].format(code=error)
            return
        except BrowserInteractionTimeoutError:       # nobody signed in within BROWSER_SECONDS, or no window opened
            yield gr.update(), strings["settings_browser_timeout"]
            return
        except Exception as error:
            yield gr.update(), strings["settings_failed"].format(code=type(error).__name__)
            return
        yield _account_text(account, strings), strings["settings_ai_signed_in"]

    def test_copilot(tenant_now: str, client_now: str, language_now: str) -> str:
        """One message to Copilot: the first 20 characters of its answer, or the error of the client (no token, no text of Microsoft)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant_now, client_now, strings, elsewhere=True)
        if refused:
            return refused
        try:
            answer = CopilotClient(account, session=tab.session).ask(COPILOT_TEST).text
        except (CopilotError, ReaderError, SignInNeeded) as error:
            return strings["settings_ai_failed"].format(error=error)
        except Exception as error:
            return strings["settings_failed"].format(code=type(error).__name__)
        return strings["settings_ai_answer"].format(answer=" ".join(answer.split())[:20])

    def copilot_refusal(settings: Settings, strings: dict[str, str]) -> str | None:
        """None once Copilot answered the one test message of "Copilot testen" (COPILOT_TEST, nothing of a document), sent with the
        tenant and the client of the settings; else why it did not: the cause by a status code and its hint (_copilot_cause), never
        Copilot's own words, or why no account could be made."""
        account, refused = begin(settings.ms_tenant_id, settings.ms_client_id, strings, elsewhere=True)
        if refused:
            return refused
        try:
            CopilotClient(account, session=tab.session).ask(COPILOT_TEST)
        except Exception as error:
            cause, code = _copilot_cause(error)
            return strings["settings_ai_switch_refused"].format(cause=_cause_text(cause, code, strings),
                                                                hint=_copilot_hint(cause, code, strings))
        return None

    def save_reader(reader_now: str, language_now: str) -> tuple:
        """Write the reader chosen into the settings file: (the reader the file then holds, a note). Copilot only once the tenant and
        the client are saved, and only once it answered the test message, sent with the saved tenant and client (copilot_refusal),
        so that a Copilot this account cannot use is never chosen (finding 6 of the owner's test): a refusal writes nothing and
        keeps the reader the file holds. This is the one choice of the page that sends something to Microsoft, at the buyer's own
        press. Nothing is written into a file that cannot be read: the note is then the line that says so."""
        strings = STRINGS[language_now]
        before, line = read(strings)
        if line is not None:
            return before.reader, line
        if reader_now == "llm":
            if not (before.ms_tenant_id and before.ms_client_id):
                return before.reader, strings["settings_ai_ids_needed"]
            if (refused := copilot_refusal(before, strings)) is not None:
                return before.reader, refused
        saved, refused, _ = write(lambda held: {"reader": reader_now}, strings)
        if saved is None:
            return before.reader, refused
        return saved.reader, strings["settings_ai_switched" if saved.reader == "llm" else "settings_buyer_saved"]

    def reader_shown(language_now: str):
        """The line of what reads the documents now, by the settings file (_reader_text), read from the files of this PC by
        aid/review/status.py; it asks Microsoft nothing. A file that cannot be read leaves the line as it is."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        if settings.reader != "llm":
            return strings["reader_state:parser"]
        now = clock()
        try:
            state = aid_status.assistant(tab.file, settings.cases_root, settings, now)
            lines = aid_status.case_lines(settings.cases_root, now)
        except OSError:
            return gr.update()
        return _reader_text(state, lines, now, strings)

    def ai_relabel(language_now: str, reader_now: str | None) -> tuple:
        strings = STRINGS[language_now]
        return (f"### {strings['settings_ai']}", strings["settings_ai_needs"], gr.update(value=strings["settings_ai_sign_in"]),
                gr.update(value=strings["settings_ai_test"]),
                gr.update(label=strings["settings_ai_reader"], choices=_reader_choices(strings), value=reader_now), "")

    reader_state = gr.Markdown("", elem_id="settings-reader-state")         # what reads the documents now
    reader = gr.Radio(choices=_reader_choices(first), value=None, label=first["settings_ai_reader"])
    reader_note = gr.Markdown("", elem_id="settings-reader-note", elem_classes=["answer"])       # the answer of the choice
    title = gr.Markdown(f"### {first['settings_ai']}")
    needs = gr.Markdown(first["settings_ai_needs"])
    with gr.Row():
        copilot_button = gr.Button(first["settings_ai_sign_in"])
        copilot_test_button = gr.Button(first["settings_ai_test"])
    note = gr.Markdown("", elem_classes=["answer"])                           # the answer of the two buttons above

    app.load(ai_opened, inputs=language, outputs=reader)
    copilot_button.click(sign_in_copilot, inputs=[tenant, client, mailbox, language], outputs=[account_shown, note])
    copilot_test_button.click(test_copilot, inputs=[tenant, client, language], outputs=note)
    gr.on(triggers=[app.load, opened, language.change], fn=reader_shown, inputs=language, outputs=reader_state, show_progress="hidden")
    # a choice of the user, not the value the tab loads; its test of Copilot takes a few seconds, the line follows the choice
    reader.input(save_reader, inputs=[reader, language], outputs=[reader, reader_note], show_progress="minimal").then(
        reader_shown, inputs=language, outputs=reader_state, show_progress="hidden")
    language.change(ai_relabel, inputs=[language, reader], outputs=[title, needs, copilot_button, copilot_test_button, reader, note])
    return reader_note


def _assistant_section(gr, language, tab: _Tab, read: Callable[[dict[str, str]], tuple[Settings, str | None]],
                       clock: Callable[[], datetime]) -> tuple:
    """The section "Stand van de assistent" of the settings tab and its events, its last (see the module docstring): the lines of the
    assistant's state (_assistant_lines), "Stand van de assistent vernieuwen", the last status.LOG_LINES lines of runner.log, the
    newest first, in a box that cannot be edited, and the line that says whom they are for. They are read from the files of this PC
    by aid/review/status.py when the section is selected, at a press of its button and after the strip's button of the tab
    "Beoordelen" (assistant_shown), never from Microsoft. tab is what the settings tab is built with (its settings file), read gives
    the settings in the file (or those in use), and clock the time of the page's Deps in the local time of this PC. Returns (the
    section, its four blocks, the function that gives its lines and its log in a language), for the language switch of
    _settings_tab, and the function that reads the section again once an event of another tab has run (read_after), for build_app."""
    first = STRINGS["nl"]

    def texts(language_now: str) -> tuple[str, str]:
        """The lines of the assistant's state and the last lines of runner.log, in the language; raises OSError for a file that
        cannot be read."""
        strings = STRINGS[language_now]
        settings, now = read(strings)[0], clock()
        state = aid_status.assistant(tab.file, settings.cases_root, settings, now)
        lines = aid_status.case_lines(settings.cases_root, now)
        log = aid_status.log_tail(settings.cases_root)
        return (_assistant_lines(settings, state, lines, aid_status.problems(state, lines), now, strings),
                "\n".join(log) if log else strings["assistant_log_none"])

    def assistant_shown(language_now: str) -> tuple:
        """The section selected, or its button pressed: (the lines, the log, its help line) as the files of this PC hold them now. A
        file that cannot be read leaves the section as it is."""
        try:
            lines, log = texts(language_now)
        except OSError:
            return gr.update(), gr.update(), gr.update()
        return lines, log, STRINGS[language_now]["assistant_log_help"]

    with gr.Tab(first["settings_section:assistant"], id="assistent") as section:
        lines = gr.Markdown("", elem_id="assistant-lines")
        refresh = gr.Button(first["assistant_refresh"], elem_id="assistant-refresh")
        # a box, not a Markdown: the runner's lines are shown as they stand, an underscore of a case id included
        log = gr.Textbox(label=first["assistant_log"], elem_id="assistant-log", interactive=False, lines=12, max_lines=20)
        help_line = gr.Markdown(first["assistant_log_help"], elem_id="assistant-log-help")
    gr.on(triggers=[section.select, refresh.click], fn=assistant_shown, inputs=language, outputs=[lines, log, help_line],
          show_progress="hidden")

    def read_after(event) -> None:
        """assistant_shown chained after `event`, an event of another tab that opens the section (the strip's button), so that the
        section shows the state at that moment, also when it was open already and its select does not fire again."""
        event.then(assistant_shown, inputs=language, outputs=[lines, log, help_line], show_progress="hidden")

    return section, [lines, refresh, log, help_line], texts, read_after


def _account(tenant: str, client: str) -> MicrosoftAccount:
    """The Microsoft account of the tenant and the client, with the token cache of the store in default_secrets_dir(), which the
    runner reads too."""
    return MicrosoftAccount(tenant, client, SecretStore(default_secrets_dir()))


def _sending_account(tab: _Tab, settings: Settings, accounts: dict[tuple[str, str], MicrosoftAccount]) -> MicrosoftAccount:
    """The Microsoft account that a close sends through: the page's account of the settings' tenant and client, the one the settings
    tab signs in with, made once (accounts). When it cannot be made (its store is refused), the account as the runner makes it,
    whose store is made, and refused, at its first use: the mailer then takes it as the runner's does, as a store refused (of the
    kind "store"), the message is not sent (aid/messaging/microsoft_mailer.py), and the close says so (not_sent:store)."""
    key = (settings.ms_tenant_id, settings.ms_client_id)
    if key not in accounts:
        try:
            accounts[key] = tab.account_factory(*key)
        except Exception:
            return MicrosoftAccount(*key, LazySecretStore(default_secrets_dir()))
    return accounts[key]


def _refusal_text(error: Exception, strings: dict[str, str]) -> str:
    """Why no account can be made, for the line under a button (finding 2 of the owner's test): a store of the sign-in that lies
    inside a git work tree by its folder and the folder that holds .git, and one made by another Windows user or in another kind of
    session by its folder, each shown as it is (_literal) and with what to do; any other failure by the fixed line, which names
    nothing of the error. The folders are shown on the settings tab of this PC only, which a public link does not offer."""
    if isinstance(error, StoreRefused) and error.directory is not None:
        if error.work_tree is not None:
            return strings["settings_store_refused_git"].format(store=_literal(str(error.directory)), work_tree=_literal(str(error.work_tree)))
        if error.recorded is not None:
            return strings["settings_store_refused_kind"].format(store=_literal(str(error.directory)))
    return strings["settings_store_refused"]


def _copilot_cause(error: Exception) -> tuple[str, str | None]:
    """Why Copilot did not answer the test of a choice, by the error of its client (aid/readers/copilot_client.py), as the status
    module tells the runner's note of it: "sign_in" for a sign-in to make (SignInNeeded), "unreachable" for a Copilot not reached,
    "code" with the status code of a refusal, and "other" for anything else; nothing else of the error."""
    if isinstance(error, SignInNeeded):
        return "sign_in", None
    if isinstance(error, CopilotError):
        if str(error).startswith(UNREACHABLE.partition(":")[0]):
            return "unreachable", None
        if (code := _STATUS_CODE.search(str(error))) is not None:
            return "code", code.group()
    return "other", None


def _cause_text(cause: str, code: str | None, strings: dict[str, str]) -> str:
    """The cause of a Copilot that fails, in words: "code 400", or the words of a sign-in, a Copilot not reached or no usable answer."""
    return strings["copilot_cause:code"].format(code=code) if cause == "code" else strings[f"copilot_cause:{cause}"]


def _copilot_hint(cause: str, code: str | None, strings: dict[str, str]) -> str:
    """What the buyer can do about a choice of Copilot that Copilot did not answer, by its status code: 400 (most often no licence
    for Microsoft 365 Copilot, or a personal account), 401 and a sign-in to make (sign in for Copilot first), 403 (a permission or an
    administrator's consent missing), and else to try again later."""
    key = "401" if cause == "sign_in" else code if cause == "code" and f"copilot_hint:{code}" in strings else "other"
    return strings[f"copilot_hint:{key}"]


def _day_time(moment: datetime) -> str:
    """A moment in the local time of this PC as the lines of the state show it: "12-11-2026 15:57" (DAY_FORM)."""
    return moment.strftime(DAY_FORM)


def _reader_text(state: aid_status.Assistant, lines: list[aid_status.CaseLine], now: datetime, strings: dict[str, str]) -> str:
    """What reads the documents now, in words, by the reader of the settings: the parser or Copilot, and while it is Copilot, Copilot
    failing as a whole as the runner notes it in its digest state (since when, its cause by a status code, never the error's text,
    and the cases that wait for it now), or refusing documents (the cases that failed for it in the last status.DAYS days before
    `now`, the page's clock in the local time of this PC, named only while there are any). The line of step 5 of "Postvak en AI"
    (reader_shown) and of the section "Stand van de assistent" (_assistant_lines)."""
    if state.reader != "llm":
        return strings["reader_state:parser"]
    if state.copilot is not None:
        waiting = sum(1 for case in lines if case.state == "copilot_wait")
        return strings["reader_state:down"].format(since=_day_time(state.copilot.since), n=waiting,
                                                   cause=_cause_text(state.copilot.cause, state.copilot.code, strings))
    since = now - timedelta(days=aid_status.DAYS)
    failed = sum(1 for case in lines if case.reason == "copilot" and case.opened_at >= since)
    if failed:
        return strings["reader_state:refused"].format(n=failed)
    return strings["reader_state:refused_none" if state.refused else "reader_state:llm"]


def _assistant_lines(settings: Settings, state: aid_status.Assistant, lines: list[aid_status.CaseLine], problems: list[aid_status.Problem],
                     now: datetime, strings: dict[str, str]) -> str:
    """The lines of the section "Stand van de assistent", a paragraph each, in this order: the runner's last pass, or since when it
    made none, or that it made none yet; whether the launcher runs, since when and how often it started the runner; a STOP file; a
    runner the launcher gave up; the mailbox as the tab tells it (_trigger_text) and since when its sign-in has lapsed; what reads the
    documents (_reader_text) and the purchase orders that wait for Copilot, oldest first; and a store of the sign-in that the PC
    refuses, whose folders "Postvak en AI" names. A line that tells one of `problems` (status.problems: the same as the review tab's)
    is in bold. Every time is in the local time of this PC, and every day in one form, that of _day_time ("12-11-2026 15:57"), the
    mailbox's line too; no line holds an error's text."""
    told = {problem.kind for problem in problems}
    shown: list[tuple[str, str | None]] = []            # each line with the problem it tells, or None
    if state.last_pass is None:
        shown.append((strings["assistant_pass_none"], "no_pass_ever"))
    elif state.late:
        shown.append((strings["assistant_pass_old"].format(time=_day_time(state.last_pass)), "no_pass"))
    else:
        shown.append((strings["assistant_pass"].format(time=_day_time(state.last_pass)), None))
    launcher = state.launcher
    if launcher.running:
        key = "assistant_launcher_on_one" if launcher.starts == 1 else "assistant_launcher_on_many"
        shown.append((strings[key].format(since=_day_time(launcher.since) if launcher.since else "?", n=launcher.starts), None))
    else:
        shown.append((strings["assistant_launcher_off"], None))
    if state.stopped:
        shown.append((strings["assistant_stop"], "stopped"))
    if launcher.given_up:
        shown.append((strings["assistant_given_up"], "given_up"))
    shown.append((f"{strings['settings_trigger']}: {_trigger_text(settings, strings, DAY_FORM)}", None))
    if state.mailbox.lapsed is not None:
        shown.append((strings["assistant_lapsed"].format(since=_day_time(state.mailbox.lapsed)), "lapsed"))
    shown.append((_reader_text(state, lines, now, strings), "copilot_down" if state.copilot is not None else "copilot_refused"))
    waiting = sorted((line for line in lines if line.state == "copilot_wait"), key=lambda line: (line.opened_at, line.case_id))
    if waiting:
        orders = dict.fromkeys(line.po_number or strings["unknown_po"] for line in waiting)
        shown.append((strings["assistant_waiting_copilot"].format(orders=", ".join(orders)), None))
    if state.store_refused:
        shown.append((strings["assistant_store"], "store"))
    return "\n\n".join(f"**{text}**" if kind in told else text for text, kind in shown)


def _account_text(account: MicrosoftAccount | None, strings: dict[str, str]) -> str:
    """The signed-in account in words, read from the token cache only: opening the tab asks Microsoft nothing, so that a network
    that does not answer cannot hold the tab. Whether the sign-in still gives a token shows when the connection is tested or the
    folders are loaded (_no_token_text)."""
    try:
        name = account.account() if account is not None else None
    except Exception:
        name = None
    return strings["settings_signed_in"].format(account=name) if name else strings["settings_not_signed_in"]


def _no_token_text(account: MicrosoftAccount, strings: dict[str, str]) -> str:
    """The note when no token was to be had (SignInNeeded): the sign-in of the account in the cache has lapsed, or none is signed
    in."""
    try:
        name = account.account()
    except Exception:
        name = None
    return strings["settings_lapsed"].format(account=name) if name else strings["settings_not_signed_in"]


def _trigger_text(settings: Settings, strings: dict[str, str], form: str = WHEN_FORM) -> str:
    """The state of the trigger for the settings, from mailbox_state.json (aid/sources/microsoft_mail.py), its times in `form`: that
    of the tab "Postvak en AI" (_when), or DAY_FORM in the section "Stand van de assistent", whose other lines show a day so."""
    if not trigger_on(settings):
        return strings["settings_trigger_off"]
    state = connection_of(settings)
    if state is None:
        return strings["settings_trigger_pending"]
    if not state["last_sync"]:
        return strings["settings_trigger_unread"].format(since=_when(state["connected_at"], form))
    return strings["settings_trigger_on"].format(since=_when(state["connected_at"], form), last=_when(state["last_sync"], form),
                                                 saved=state["saved"])


def _via_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_via:{value}"], value) for value in SEND_VIA]


def _labels(fields: list[str], strings: dict[str, str]) -> str:
    """The refused fields by their labels on the tab (FIELD_LABELS), each once; a field the tab does not write by its name."""
    shown = [strings[FIELD_LABELS[name]] if (name := field.split(".")[0]) in FIELD_LABELS else field for field in fields]
    return ", ".join(dict.fromkeys(shown))


def _digest_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_digest:{value}"], value) for value in ("daily", "per_case")]


def _language_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_language:{value}"], value) for value in ("nl", "en")]


def _reader_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_reader:{value}"], value) for value in READERS]


def _when(moment: datetime | str, form: str = WHEN_FORM) -> str:
    """A time of the mailbox as the tab shows it, in the local time of this PC: "12-11-2026 09:00", or in another form; a time
    without a zone is UTC."""
    moment = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    moment = moment.replace(tzinfo=timezone.utc) if moment.utcoffset() is None else moment
    return moment.astimezone().strftime(form)
