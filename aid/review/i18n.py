"""The strings of the review page, in Dutch (the default) and in English, with the same keys in both languages.

A key with a prefix labels a value of the aid: "field:<field>" a field of the comparison, "pair:<kind>" the kind of a matched pair,
"kind:<kind>" a write operation, "status:<status>" the status of a case, and "reason:<text>" a reason of a row (the reason of a
review item, the detail of a flag or the reason of an uncompared item). A reason key that ends in " *" labels every reason that
begins with the text before the asterisk, for the reasons that name a write or a number of days. The page shows these labels and
never the texts themselves, so that no rule code and no setting value reaches it. reason_label gives the label of a reason, for
the page and for the digest (aid/notify.py), which imports these strings and not gradio. "message_not_sent" is what the page says
after a close whose message could not be sent, followed by "not_sent:<cause>", the kind of its cause and what to do about it
(the causes of aid/review/close.py: the sign-in, a mailbox or server not reached or refusing, no mailbox connected, the mail
server's password, the settings file, the case's document); "message_filed" after one whose message was written as a file because
the settings say so, into {folder}; "message_unconfirmed" after one whose hand-over was not confirmed; and "reply_unread" after a
close that did nothing because a reply filed with the case could not be read at that moment. The keys that begin with "settings"
are the
strings of the settings tab, "settings_via:<value>" labelling a value of the setting send_via and "settings_reader:<value>" a value of
the setting reader. Of the buyer's part of that tab, "settings_field:<setting>" is the label of a field and "settings_help:<setting>"
its help, which says what the rule does and no more, in the words the review tab and the banner use ("de drempel", "de marge",
"naar u"); "settings_problem:<kind>" is what a refused field takes; "settings_file_unreadable" is the one line of a settings file
that cannot be read, with "settings_file_fields" for the fields it names, and "settings_file_write_route" the line of a settings
file in which exact_base_url is set, which no command uses. The help of the suppliers says what the tick for no
questions does and does not do: the aid's own questions come to the buyer, and a question the buyer chooses on the review tab is
still sent; a review item that comes from that tick has the tick's own words as its reason ("De assistent stuurt deze leverancier
uit zichzelf geen vragen"). A save on the settings tab that the runner takes over at its next pass says so in one note,
"settings_buyer_saved", whichever part of the tab saved, but for the layout of the block "Koppeling met Exact", whose own note
"exact_saved" says the same and what becomes of the purchase orders that wait in the watched folder; the number recognition of that
block says "settings_buyer_saved", "settings_buyer_unchanged" and "settings_buyer_refused" as the buyer's part does, and its own
"exact_number_changed" when the pattern was changed elsewhere. "settings_extension_code", with its help, labels the code the browser
extension sends, which the connections show. "waiting_hint:po" and "waiting_hint:version" are the hint of the review tab on a case
that waits for its purchase order, {number}, or for the version {version} of it that the confirmation quotes; it sends the buyer to the
tab "Bestanden" when that order stays away. "entered_values" is the line above the lists of the review tab on a case that was
compared with values entered in Exact, {changes} being those values, each as "entered:<field>" names it by its line and its value;
"entered_unread" stands in its place on a case that ran while the list of them could not be read. The keys that begin with "files"
are the strings of the tab "Bestanden"
(aid/review/intake.py): "files_refused:<reason>" the note of a file it refuses ("type_export" the one of a type while the export's
columns are set), "files_column:<column>" a column of its table, "files_outcome:<outcome>" the label of an outcome of the intake file
("export_one" for an export of one purchase order, "case_unknown" for a case without one, "set_aside" with the {n} days a file stayed,
"unread" for a case opened from a scan that failed at reading, by which the digest names that case too),
"files_skipped:<key>" the label of a reason of a skip (intake.SKIPPED; "other" for a reason it does not know), "files_error" the label of
an error the scan does not tell apart and "files_error:<key>" that of an export refused (intake.EXPORT_REFUSED) and of a version the
store holds from another file ("version"), each by intake.ERRORS. The keys that begin with "hand" are the strings of the
tab "In Exact invoeren" (aid/review/hand_list.py), "hand_row:<kind>" the row of a change of that kind and "hand_row:article_price_back"
the row of the undo of an article price whose price before it the aid never read; "hand_tab_unread" is the tab's label while the list
cannot be read, and "hand_nothing_noted" the note of a press that noted nothing. Under the list: "hand_put_back_row" is a row that puts
a change back in Exact and "hand_exact_shows" the note of an open row whose order Exact printed with a third value;
"hand_tick_ended" and "hand_tick_ended_unfound" are the note of a row whose tick a newer print of its order ended, and
"hand_list_changed" what the tab says of a list whose file was changed outside the aid; "hand_done*" are
the section of the changes handled in the last 14 days and how the aid knows each, "hand_reopen*" and "hand_put_back*" its two buttons
and what they say ("hand_reopen_not", "hand_reopen_put_back", "hand_put_back_newer_open", "hand_put_back_newer_entered",
"hand_put_back_newer_never", "hand_put_back_not_needed", "hand_put_back_lost", "hand_put_back_not_entered" and
"hand_put_back_cannot" say why a press did nothing, "hand_done_choose_first" that nothing was chosen; "hand_put_back_other" is the
sentence behind the note of a put-back when the aid knows another value from before the change, {source} one of
"hand_put_back_source:marks" and "hand_put_back_source:look"), and
"hand_confirmation*" and "hand_attach_only" the section of the confirmations to attach in Exact. The keys that begin with "exact" are
the strings of the block "Koppeling met Exact" of the settings tab (aid/review/exact_settings.py), one block in each language:
"exact_field:<field>" the label of a field of its form and "exact_help:<field>" its help, "exact_meaning:<meaning>" what a column of
the title row holds, "exact_problem:<kind>" a problem that refuses a save, "exact_place:<kind>" the lines or pages of the sample that a
problem names, and "exact_warning:<kind>" what the reader of the sample passes by. Of its part 2, the export of order lines,
"exact_export_field:<field>" is the label of a field of the form and "exact_export_help:<field>" its help (the other dropdowns are
labelled as part 1 names what a column holds), "exact_export_problem:<kind>" a problem of the form or of the sample, and
"exact_export_refused:<reason>" the sentence of each reason for which the export reader refuses a file (REFUSALS of
aid/readers/po_export.py), "unreadable_row" the one with the row of a cell that cannot be read. A key of part 2 that ends in "_one" is
the sentence for a count of one, in the singular, beside the key without it for any other count ("exact_export_unread_one",
"exact_export_unread"), or, for the parts of the count line, beside the key that ends in "_many" ("exact_export_lines_one",
"exact_export_lines_many"); the count line "exact_export_read" is put together from those parts: the orders, the lines, the rows passed
over and the order or orders shown below it. The keys that begin with "tryout" are the strings of the part "Proefdraaien" at the end of
that block and of the switch of the test mode (aid/tryout.py), "tryout_change:<change>" the label of a change of a try-out and
"tryout_reason:<reason>" why a purchase order gives none, and "tryout_nothing_sent" the answer of the review tab to "Leverancier vragen"
on a case made from a try-out (aid/tryout_mark.py); "tryout_cart_change:<change>" is the label of a change of a try-out cart, under a
question of its own ("tryout_cart_change", so that the page says which choice is the cart's), "tryout_cart_make" its button and
"tryout_cart_made" its note, with the link to the page on the capture server. They stand in one block of their own after the block's strings, with the state line
of the changes still to enter ("exact_state_to_enter"), the line of the test mode on the tab In Exact invoeren ("hand_test_mode") and
the mark of a change from try-out documents on that tab ("hand_tryout"), which also marks a try-out in the case dropdown of the tab
"Beoordelen" and among the confirmations under the list. The keys that begin with "pricelist" are the strings of the tab "Prijslijst"
(aid/review/pricelist_page.py): "pricelist_refused:<reason>" a refusal of the export of articles or of a price list given there (a
workbook with more than one sheet with data has the export reader's sentence, "exact_export_refused:several_sheets", wherever a
workbook is given), "pricelist_column:<column>" the field of a column of the
price list, "pricelist_header:<column>" a column of its table and "pricelist_outcome:<outcome>" what became of a row of the list, by
its label and never by the import's own reason; a count line is put together from parts, each with a form for a count of one
("pricelist_articles_one", "pricelist_articles_many"). The keys that begin with "exact_articles" are part 3 of the block
"Koppeling met Exact", the columns of the export of articles ("exact_part_articles" its title): "exact_articles_field:<field>" a field,
"exact_articles_left:<reason>" why the trial read left a row out, and "exact_articles_read" its count line, put together from the same
parts. "hand_pricelist" and "hand_pricelist_entered" are the one press of the tab "In Exact invoeren" for the article prices of one
price list.
"""

STRINGS: dict[str, dict[str, str]] = {
    "nl": {
        "title": "Zaken ter beoordeling",
        "language": "Taal",
        "case": "Zaak",
        "refresh": "Lijst vernieuwen",
        "no_case": "Kies eerst een zaak.",
        "document": "Document van de leverancier",
        "po_document": "Inkooporder",
        "flags": "Afwijkingen",
        "matches": "Overeenkomsten",
        "uncompared": "Niet te vergelijken",
        "decisions": "Besluiten",
        "decision": "Besluit",
        "confirm": "Bevestigen: waarde van de leverancier overnemen",
        "keep": "Waarde van de inkooporder houden",
        "ask": "Leverancier vragen",
        "unresolved": "Onopgelost",
        "article_update": "Ook de artikelprijs bijwerken",
        "banner": "Deze wijziging stuurt de inkooporder opnieuw door de fiattering",
        "close": "Zaak afsluiten",
        "undo": "Ongedaan maken",
        "undo_choice": "Schrijfactie",
        "item": "Punt",
        "field": "Veld",
        "line": "Regel",
        "po_value": "Inkooporder",
        "doc_value": "Leverancier",
        "reason": "Reden",
        "po_source": "Bron inkooporder",
        "doc_source": "Bron leverancier",
        "saved": "Besluit over {item} opgeslagen.",
        "refused": "Dit besluit kan hier niet worden genomen.",
        "new_status": "De zaak is nu: {status}.",
        "write_stopped": "Een schrijfactie is niet gelukt ({write}): {error}. De zaak blijft ter beoordeling.",
        "not_closed": "Deze zaak kan niet worden afgesloten: zij is niet meer ter beoordeling.",
        "undone": "Ongedaan gemaakt: {write}.",
        "not_undone": "Niets ongedaan gemaakt: deze schrijfactie is al ongedaan gemaakt of kan niet ongedaan worden gemaakt.",
        "undo_failed": "Ongedaan maken is mislukt.",
        "write_refused": "De schrijfactie is geweigerd",
        "message_not_sent": "Een bericht is niet verzonden; de zaak blijft ter beoordeling.",
        "message_filed": ("Het bericht is niet verstuurd maar als bestand klaargezet ({folder}): de instellingen laten berichten als "
                          "bestand schrijven."),
        "not_sent:sign_in": ("De aanmelding bij Microsoft is verlopen: meld u opnieuw aan onder Instellingen, Koppelingen, en sluit de zaak "
                             "opnieuw af."),
        "not_sent:unreachable": "Het postvak of de mailserver was niet bereikbaar: sluit de zaak later opnieuw af.",
        "not_sent:refused": "Het postvak of de mailserver weigerde het bericht: geef dit door aan de onderzoeker.",
        "not_sent:no_mailbox": ("Er is geen postvak verbonden: verbind een postvak onder Instellingen, Koppelingen, en sluit de zaak opnieuw "
                                "af."),
        "not_sent:no_password": "Het wachtwoord van de mailserver is niet ingesteld: geef dit door aan de onderzoeker.",
        "not_sent:settings": "De instellingen kunnen nu niet worden gelezen: er is niets verstuurd. Geef dit door aan de onderzoeker.",
        "not_sent:document": "Het document van de zaak kan nu niet worden gelezen: er is niets verstuurd.",
        "message_unconfirmed": ("Niet zeker of het bericht is verstuurd: het postvak gaf geen bevestiging. Kijk bij Verzonden items. Staat het "
                                "er niet, dan komt de zaak na de wachttijd bij u terug en stelt u de vraag opnieuw."),
        "reply_unread": "Een antwoord bij deze zaak kan nu niet worden gelezen: sluit de zaak zo opnieuw af.",
        "question_open": "Vraag aan de leverancier staat open",
        "decisions_damaged": "De besluiten van deze zaak kunnen niet worden gelezen ({file}).",
        "waiting_hint:po": ("Inkooporder {number} kent de assistent nog niet. Komt die inkooporder uit Exact binnen, dan controleert de "
                            "assistent deze bevestiging vanzelf opnieuw; u hoeft hier niets te besluiten. Blijft zij weg, geef dan de pdf "
                            "van de inkooporder of de e-mail van Exact op het tabblad Bestanden. Is {number} niet een van uw "
                            "inkoopordernummers, besluit dan zelf over deze zaak."),
        "waiting_hint:version": ("Versie {version} van inkooporder {number} kent de assistent nog niet. Komt die versie uit Exact binnen, dan "
                                 "controleert de assistent deze bevestiging vanzelf opnieuw; u hoeft hier niets te besluiten. Blijft zij "
                                 "weg, geef dan de pdf van die versie of de e-mail van Exact op het tabblad Bestanden."),
        "entered_values": ("Sinds deze inkooporder is opgenomen, is in Exact ingevoerd: {changes}. De assistent vergelijkt met deze "
                           "waarden."),
        "entered:price": "regel {line}: prijs {value}",
        "entered:delivery_date": "regel {line}: leverdatum {value}",
        "entered:quantity": "regel {line}: aantal {value}",
        "entered_unread": ("De lijst In Exact invoeren kon niet worden gelezen: de assistent vergelijkt met de inkooporder zoals zij is "
                           "opgenomen."),
        "choose_write": "Kies eerst een schrijfactie.",
        "review_tab": "Beoordelen",
        "hand_tab": "In Exact invoeren",
        "hand_tab_count": "In Exact invoeren ({n})",
        "hand_tab_unread": "In Exact invoeren (?)",
        "hand_intro": ("De assistent verandert zelf niets in Exact. Hieronder staat wat u in Exact invoert. Vink aan wat u hebt "
                       "ingevoerd."),
        "hand_nothing": "Er is niets in te voeren.",
        "hand_block_po": "Inkooporder {number} ({supplier})",
        "hand_block_po_alone": "Inkooporder {number}",
        "hand_block_articles": "Artikelprijzen",
        "hand_row:po_line_price": "regel {line} ({item}): prijs {old} → {new}",
        "hand_row:po_line_date": "regel {line} ({item}): leverdatum {old} → {new}",
        "hand_row:po_line_quantity": "regel {line} ({item}): aantal {old} → {new}",
        "hand_row:article_price": "artikel {item}: inkoopprijs {old} → {new}",
        "hand_row:article_price_back": "artikel {item}: inkoopprijs {old} terugzetten naar de prijs van daarvoor",
        "hand_unknown": "onbekend",
        "hand_empty": "leeg",
        "hand_week": "week {week} ({year})",
        "hand_hint": "Let op: na deze wijziging moet de inkooporder opnieuw gefiatteerd worden.",
        "hand_entered": "Aangevinkte regels zijn ingevoerd",
        "hand_dropped": "Aangevinkte regels niet invoeren",
        "hand_block": "Blok",
        "hand_block_entered": "Alles van dit blok is ingevoerd",
        "hand_pricelist": "Prijslijst",
        "hand_pricelist_entered": "Alle artikelprijzen van deze prijslijst zijn ingevoerd",
        "hand_tick_first": "Vink eerst aan wat u bedoelt.",
        "hand_entered_one": "1 wijziging genoteerd als ingevoerd in Exact.",
        "hand_entered_many": "{n} wijzigingen genoteerd als ingevoerd in Exact.",
        "hand_dropped_one": "1 wijziging vervalt: de assistent vraagt er niet meer om.",
        "hand_dropped_many": "{n} wijzigingen vervallen: de assistent vraagt er niet meer om.",
        "hand_nothing_noted": ("Er is niets genoteerd: de aangevinkte regels staan niet meer open (al afgehandeld, of vervangen door een "
                               "nieuwere waarde). De lijst is bijgewerkt."),
        "hand_unreadable_one": "1 regel van de lijst kon niet worden gelezen; vernieuw de lijst over een minuut.",
        "hand_unreadable_many": "{n} regels van de lijst konden niet worden gelezen; vernieuw de lijst over een minuut.",
        "hand_put_back_row": "Terugzetten: {row}",
        "hand_exact_shows": "Exact toont nu {value}.",
        "hand_tick_ended": ("U vinkte dit af, maar de inkooporder die daarna binnenkwam toont {value}. Voer de wijziging in Exact in, of "
                            "vink opnieuw af als zij er al in staat."),
        "hand_tick_ended_unfound": ("U vinkte dit af, maar in de inkooporder die daarna binnenkwam is deze regel niet terug te vinden. "
                                    "Kijk in Exact of de wijziging er staat en vink dan opnieuw af, of haal de regel van de lijst."),
        "hand_list_changed": ("De lijst In Exact invoeren kan niet worden gelezen: het bestand is buiten de assistent gewijzigd. Geef dit "
                              "door aan de onderzoeker."),
        "hand_done": "Afgehandeld in de laatste 14 dagen",
        "hand_done_ticked": "(door u afgevinkt op {date})",
        "hand_done_seen": "(gezien in versie {n} van de inkooporder)",
        "hand_done_dropped": "(niet ingevoerd, op {date})",
        "hand_done_choice": "Afgehandelde wijziging",
        "hand_reopen": "Weer op de lijst zetten",
        "hand_put_back": "Terugdraaien in Exact",
        "hand_reopened": "De regel staat weer op de lijst.",
        "hand_reopen_seen": ("Deze wijziging heeft de assistent zelf in Exact gezien. Kies Terugdraaien in Exact als de oude waarde terug "
                             "moet."),
        "hand_reopen_not": ("De wijziging komt niet terug op de lijst: er is intussen een andere wijziging voor dezelfde regel of hetzelfde "
                            "artikel."),
        "hand_put_back_listed": ("Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row}. Voer de oude waarde in Exact in en vink de "
                                 "regel af."),
        "hand_put_back_other": ("Let op: {old} is de waarde van de inkooporder zoals de assistent die las; volgens {source} stond er "
                                "{value}. Kijk in Exact welke waarde terug moet."),
        "hand_put_back_source:marks": "de lijst (een eerdere wijziging die is ingevoerd)",
        "hand_put_back_source:look": "de inkooporder zoals de assistent haar het laatst zag",
        "hand_put_back_listed_empty": ("Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row}. Maak het veld in Exact leeg en vink de "
                                       "regel af."),
        "hand_put_back_on_list": "Het terugzetten staat al op de lijst.",
        "hand_put_back_done": "Deze wijziging is al teruggedraaid.",
        "hand_put_back_unknown": "Van dit artikel kent de assistent de oude prijs niet; zet die zelf terug in Exact.",
        "hand_put_back_not_entered": "Deze wijziging is niet ingevoerd; in Exact is er niets terug te draaien.",
        "hand_put_back_cannot": "De assistent kan deze wijziging niet terugdraaien; zet de oude waarde zelf terug in Exact.",
        "hand_confirmations": ("Bevestigd door de leverancier (laatste 7 dagen): voeg in Exact de bevestiging bij en zet de status op "
                               "Bevestigd"),
        "hand_confirmation": "{date}: inkooporder {number} ({supplier}), bestand {file}",
        "hand_confirmation_alone": "{date}: inkooporder {number}, bestand {file}",
        "hand_attach_only": "alleen bijvoegen: er staat nog iets open",
        "hand_put_back_newer_open": ("Terugdraaien kan nu niet: er staat een nieuwere wijziging op de lijst, {row}. Laat die eerst vallen; "
                                     "daarna kunt u deze wijziging terugdraaien."),
        "hand_put_back_newer_entered": ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, {row}. Draai die eerst terug en "
                                        "vink het terugzetten af; daarna kunt u deze wijziging terugdraaien."),
        "hand_put_back_lost": ("Terugdraaien kan nu niet: het terugzetten zou niet op de lijst komen. Er is niets geschreven; geef dit door "
                               "aan de onderzoeker."),
        "hand_put_back_not_needed": ("Terugdraaien is niet nodig: op de lijst staat al een wijziging naar de oude waarde, {row}. Voer die in "
                                     "Exact in en vink die af."),
        "hand_put_back_newer_never": ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, {row}, die de assistent niet kan "
                                      "terugdraaien. Zet de oude waarde zelf terug in Exact."),
        "hand_reopen_put_back": "Deze wijziging is teruggedraaid; zij komt niet terug op de lijst.",
        "hand_done_choose_first": "Kies eerst een wijziging bij Afgehandelde wijziging.",
        "hand_done_seen_export": "(gezien in een export uit Exact)",
        "files_tab": "Bestanden",
        "files_title": "Een bestand aan de assistent geven",
        "files_intro": ("Komt een document niet vanzelf uit uw postvak, geef het dan hier: een inkooporder uit Exact (pdf) of een "
                        "orderbevestiging (pdf of e-mail). De assistent verwerkt het bij de volgende ronde, binnen een minuut."),
        "files_intro_export": ("Komt een document niet vanzelf uit uw postvak, geef het dan hier: een inkooporder uit Exact (pdf), een "
                               "orderbevestiging (pdf of e-mail) of een export uit Exact (Excel of csv). De assistent verwerkt het bij de "
                               "volgende ronde, binnen een minuut."),
        "files_choose": "Kies een bestand",
        "files_give": "Aan de assistent geven",
        "files_accepted": "{name} is aangenomen. Hieronder staat binnen een minuut wat ermee is gedaan.",
        "files_refused:type": "{name} is niet aangenomen: de assistent leest pdf, png en e-mail (.eml).",
        "files_refused:type_export": ("{name} is niet aangenomen: de assistent leest pdf, png, e-mail (.eml) en een export uit Exact (Excel "
                                      "of csv)."),
        "files_refused:empty": "{name} is niet aangenomen: het bestand is leeg.",
        "files_refused:export": ("{name} is niet aangenomen: de assistent leest een export uit Exact pas als de kolommen zijn ingesteld "
                                 "onder Instellingen, Koppeling met Exact."),
        "files_choose_first": "Kies eerst een bestand.",
        "files_done": "Laatst verwerkt",
        "files_column:time": "Tijd",
        "files_column:file": "Bestand",
        "files_column:what": "Wat de assistent deed",
        "files_outcome:po": "Inkooporder {number} opgenomen (versie {version})",
        "files_outcome:export": "Export uit Exact opgenomen: {n} inkooporders, waarvan {m} nieuw of gewijzigd",
        "files_outcome:export_one": "Export uit Exact opgenomen: 1 inkooporder, waarvan {m} nieuw of gewijzigd",
        "files_outcome:case": "Zaak geopend voor inkooporder {number}",
        "files_outcome:case_unknown": "Zaak geopend, inkooporder onbekend",
        "files_outcome:guess": "Zaak geopend voor inkooporder {number}, afgeleid: kijk de zaak na",
        "files_outcome:answer": "Antwoord van een leverancier bij de zaak gelegd",
        "files_outcome:bounce": "Een bericht van de assistent kwam terug als onbestelbaar: bij de zaak gelegd",
        "files_outcome:auto": "Automatisch antwoord (bijvoorbeeld een afwezigheidsmelding) bij de zaak gelegd",
        "files_outcome:own": "Uw eigen bericht bij de zaak gelegd; dit telt niet als antwoord van de leverancier",
        "files_outcome:set_aside": "Na {n} dagen nog niet gelezen: de assistent probeert dit bestand niet meer",
        "files_outcome:recheck": "Inkooporder {number} is binnen: de wachtende zaak is opnieuw gecontroleerd",
        "files_outcome:unread": "Scan zonder leesbare tekst: de assistent kon dit document niet lezen; bekijk het zelf",
        "files_skipped:duplicate": "Al eerder ontvangen: niets gedaan",
        "files_skipped:class": "Geen inkooporder of orderbevestiging: niets gedaan",
        "files_skipped:type": "Dit soort bestand leest de assistent niet",
        "files_skipped:numbers": "Meer dan een inkoopordernummer gevonden: bekijk dit document zelf",
        "files_skipped:scan": "Scan zonder leesbare tekst: bekijk dit document zelf",
        "files_skipped:no_attachment": "E-mail zonder bijlage: niets gedaan",
        "files_skipped:not_an_export": "Een rekenblad zonder de kolomtitels van de export: niets gedaan",
        "files_skipped:tryout": "Proefdocument buiten de proefstand: niets gedaan",
        "files_skipped:no_order_lines": "Export uit Exact zonder orderregels: niets gedaan",
        "files_skipped:other": "Overgeslagen: niets gedaan",
        "files_error": ("Kon niet worden gelezen. Is het een inkooporder uit Exact, controleer dan de opmaak onder Instellingen, Koppeling "
                        "met Exact."),
        "files_error:no_po_number": "Export uit Exact niet opgenomen: een rij heeft een artikelcode maar geen inkoopordernummer",
        "files_error:columns": "Export uit Exact niet opgenomen: de kolommen van dit bestand zijn anders dan ingesteld",
        "files_error:cells": ("Export uit Exact niet opgenomen: een rij heeft een ander aantal cellen dan de rij met kolomtitels. Sla het "
                              "bestand op als Excel-bestand (.xlsx) en geef het opnieuw."),
        "files_error:sheets": ("Export uit Exact niet opgenomen: het Excel-bestand heeft meer dan één werkblad met gegevens. Sla het werkblad "
                               "met de orderregels op als een eigen bestand en geef dat."),
        "files_error:hidden_sheet": ("Export uit Exact niet opgenomen: het Excel-bestand heeft alleen gegevens op een verborgen werkblad. Maak "
                                     "het werkblad zichtbaar en geef het bestand opnieuw."),
        "files_error:export": "Export uit Exact niet opgenomen: controleer de kolommen onder Instellingen, Koppeling met Exact.",
        "files_error:version": "Inkooporder niet opgenomen: de assistent heeft deze versie al uit een ander bestand",
        "pricelist_tab": "Prijslijst",
        "pricelist_unset": ("De kolommen van de export van de artikelen zijn nog niet ingesteld. Dat gebeurt bij de installatie: Instellingen, "
                            "Koppeling met Exact, deel 3."),
        "pricelist_intro": ("Een prijslijst van een leverancier vergelijkt u hier met de inkoopprijzen in Exact. De assistent heeft daarvoor de "
                            "export van de artikelen van die leverancier uit Exact nodig. Wat verandert, zet u met een druk op de knop op de "
                            "lijst In Exact invoeren; in Exact verandert de assistent niets."),
        "pricelist_supplier": "Leverancier",
        "pricelist_supplier_help": "Kies de leverancier, of typ de naam bij een nieuwe leverancier.",
        "pricelist_state": "Inkoopprijzen van {supplier} opgenomen op {time} uit {file}: {articles}.",
        "pricelist_state_none": ("Van {supplier} zijn nog geen inkoopprijzen opgenomen. Geef eerst de export van de artikelen van deze "
                                 "leverancier uit Exact."),
        "pricelist_choose_supplier": "Kies eerst een leverancier.",
        "pricelist_stored_unreadable": "De opgenomen inkoopprijzen kunnen niet worden gelezen. Geef de export van de artikelen opnieuw.",
        "pricelist_export": "Export van de artikelen uit Exact (Excel of csv)",
        "pricelist_file_prices": "Inkoopprijzen opnemen",
        "pricelist_remove": "Inkoopprijzen van deze leverancier weghalen",
        "pricelist_removed": ("De opgenomen inkoopprijzen van {supplier} zijn weggehaald. Wat al op de lijst In Exact invoeren staat, blijft "
                              "staan."),
        "pricelist_filed": "Inkoopprijzen van {supplier} opgenomen: {articles}; {passed}.",
        "pricelist_filed_suppliers": "Inkoopprijzen opgenomen van {m} leveranciers: {articles}; {passed}.",
        "pricelist_articles_one": "1 artikel",
        "pricelist_articles_many": "{n} artikelen",
        "pricelist_passed_one": "1 rij overgeslagen",
        "pricelist_passed_many": "{k} rijen overgeslagen",
        "pricelist_refused:type": "{name} is niet aangenomen: de assistent leest hier Excel (.xlsx) en csv.",
        "pricelist_refused:columns": ("De kolommen van dit bestand zijn anders dan ingesteld: controleer Instellingen, Koppeling met Exact, "
                                      "deel 3."),
        "pricelist_refused:supplier": "Kies eerst de leverancier van wie deze artikelen zijn.",
        "pricelist_refused:name": "De naam van de leverancier is te lang of bevat tekens die niet in een naam horen.",
        "pricelist_refused:name_row": ("In rij {row} van de export staat bij de leverancier een naam die te lang is of tekens bevat die niet "
                                       "in een naam horen. Er is niets opgenomen."),
        "pricelist_refused:no_article": ("Deze export gaf geen artikel: geen rij heeft een artikelcode en een prijs die te lezen is. Er is "
                                         "niets opgenomen."),
        "pricelist_refused:heading": ("De kolomtitels moeten in de eerste rij van het bestand staan: haal de regels erboven weg en geef het "
                                      "bestand opnieuw."),
        "pricelist_list": "Prijslijst (Excel of csv)",
        "pricelist_column:item_code": "Kolom met de artikelcode",
        "pricelist_column:price": "Kolom met de prijs",
        "pricelist_column:basis": "Kolom met de prijs per (mag leeg)",
        "pricelist_column:valid_from": "Geldig vanaf (mag leeg)",
        "pricelist_column:valid_to": "Geldig tot (mag leeg)",
        "pricelist_columns_hint": ("Heeft de lijst een kolom voor waar de prijs per geldt (per 100, per doos), wijs die dan aan: anders "
                                   "leest de assistent elke prijs als een prijs per stuk."),
        "pricelist_problem:price": "Kies de kolom met de prijs.",
        "pricelist_view": "Bekijken",
        "pricelist_carry_out": "Op de lijst In Exact invoeren zetten",
        "pricelist_header:article": "Artikel",
        "pricelist_header:current": "Inkoopprijs in Exact",
        "pricelist_header:list": "Prijs op de lijst",
        "pricelist_header:outcome": "Uitkomst",
        "pricelist_outcome:changes": "prijs verandert",
        "pricelist_outcome:unchanged": "ongewijzigd",
        "pricelist_outcome:unknown_article": "artikel niet bekend in Exact",
        "pricelist_outcome:unreadable_price": "prijs niet te lezen",
        "pricelist_outcome:other_basis": "prijs per ander aantal: niet vergeleken",
        "pricelist_outcome:not_yet_valid": "nog niet geldig",
        "pricelist_outcome:expired": "verlopen",
        "pricelist_outcome:duplicate": "dubbele rij",
        "pricelist_outcome:no_item_code": "geen artikelcode",
        "pricelist_outcome:unreadable_valid_from": "geldig vanaf niet te lezen",
        "pricelist_outcome:unreadable_valid_to": "geldig tot niet te lezen",
        "pricelist_outcome:failed": "niet gelukt",
        "pricelist_outcome:shared": "ook bij leverancier {name}: voer deze prijs zelf in Exact in",
        "pricelist_shared_more": "{names} en nog {n}",
        "pricelist_outcome:differs": "wijkt sterk af: controleer de kolom \"prijs per\"",
        "pricelist_outcome:ticked": "al afgevinkt op de lijst In Exact invoeren",
        "pricelist_outcome:dropped": "van de lijst gehaald: zet de regel daar terug als de prijs toch moet worden ingevoerd",
        "pricelist_row": "rij {n}",
        "pricelist_counts": "{changes}, {same}, {passed}.",
        "pricelist_changes_one": "1 prijs verandert",
        "pricelist_changes_many": "{a} prijzen veranderen",
        "pricelist_same_one": "1 blijft gelijk",
        "pricelist_same_many": "{b} blijven gelijk",
        "pricelist_carried_one": "1 prijswijziging staat op de lijst In Exact invoeren.",
        "pricelist_carried_many": "{n} prijswijzigingen staan op de lijst In Exact invoeren.",
        "pricelist_expired": "Sommige rijen zijn verlopen: vraag de leverancier om een actuele lijst.",
        "pricelist_changed": "De gegevens zijn veranderd sinds u de lijst bekeek. Bekijk de lijst opnieuw.",
        "pricelist_differs": ("Een prijs die meer dan drie keer zo hoog of zo laag is, zet de assistent niet op de lijst: controleer de "
                              "lijst, en voer een echte wijziging zelf in Exact in."),
        "pricelist_older": ("Let op: voor {articles} staat op de lijst In Exact invoeren nog de prijs van een eerdere prijslijst van deze "
                            "leverancier. Deze lijst vraagt een andere prijs, die de assistent niet op de lijst zet: haal die regel daar van "
                            "de lijst en voer de goede prijs zelf in Exact in."),
        "pricelist_not_prepared": "Niet op de lijst gezet: {articles}. Bekijk de lijst opnieuw om het nog eens te proberen.",
        "pricelist_columns_lost": "De kolommen van deze lijst konden niet worden bewaard: wijs ze de volgende keer opnieuw aan.",
        "pricelist_log_lost": ("De assistent kon deze wijzigingen niet in zijn eigen logboek van de prijslijst schrijven en kan ze daardoor "
                               "later niet zelf terugzetten: geef dit door aan de onderzoeker."),
        "settings_tab": "Instellingen",
        "settings_share": "Niet beschikbaar op een openbare link",
        "settings_no_file": "Niet beschikbaar: de pagina is gestart zonder instellingenbestand",
        "settings_intro": ("Met een aanmelding bij Microsoft leest de controle zelf een map van het postvak en verstuurt zij haar e-mails "
                           "vanuit dat postvak. Zonder aanmelding werkt zij zoals voorheen met de bewaakte map."),
        "settings_tenant": "Map-id (tenant)",
        "settings_client": "Toepassings-id (client)",
        "settings_sign_in": "Aanmelden bij Microsoft",
        "settings_sign_in_code": "Aanmelden met een code",
        "settings_browser_note": ("Aanmelden bij Microsoft opent een browservenster met de aanmeldpagina van Microsoft op de pc waar deze "
                                  "pagina draait. Opent er geen venster, kies dan Aanmelden met een code."),
        "settings_waiting": ("Er opent nu een venster van Microsoft: meld u daar aan. Deze pagina wacht er hoogstens vijf minuten op; "
                             "een tweede druk op de knop wacht tot dan."),
        "settings_browser_timeout": ("Er is binnen vijf minuten niet aangemeld. Opende er geen venster, kies dan Aanmelden met een "
                                     "code."),
        "settings_code": "Ga naar {address} en voer de code {code} in. Deze pagina wacht tot de aanmelding klaar is.",
        "settings_signed_in": "Aangemeld als {account}",
        "settings_not_signed_in": "Niet aangemeld",
        "settings_lapsed": "Aanmelding van {account} verlopen: meld u opnieuw aan",
        "settings_store_refused": "De beveiligde opslag op deze pc is geweigerd",
        "settings_sign_out": "Afmelden",
        "settings_signed_out": "Afgemeld.",
        "settings_sign_in_failed": "Aanmelden is niet gelukt ({code}).",
        "settings_ids_needed": "Vul eerst een geldige map-id (tenant) en toepassings-id (client) in.",
        "settings_mailbox": "Postvak (leeg: uw eigen postvak)",
        "settings_folder": "Map",
        "settings_folders": "Mappen ophalen",
        "settings_folders_loaded": "Mappen opgehaald: {count}.",
        "settings_folder_needed": "Kies eerst een map.",
        "settings_folders_first": "Druk eerst op Mappen ophalen voor dit postvak en kies een map.",
        "settings_test": "Verbinding testen",
        "settings_tested": "Berichten in de map: {count}; nieuwste ontvangen: {newest}.",
        "settings_tested_empty": "De map is leeg.",
        "settings_graph_failed": "Microsoft gaf geen bruikbaar antwoord ({code}).",
        "settings_failed": "Dit is niet gelukt ({code}).",
        "settings_send_via": "Verzenden via",
        "settings_via:auto": ("Automatisch: het postvak als u bent aangemeld en een map is gekozen (is de aanmelding verlopen, dan "
                              "wordt niets verzonden), anders de SMTP-server of bestanden"),
        "settings_via:file": "Bestanden: niets verzenden",
        "settings_via:smtp": "De SMTP-server van de instellingen",
        "settings_via:microsoft": "Het postvak van de aanmelding",
        "settings_save": "Opslaan",
        "settings_not_saved": "Niet opgeslagen: controleer {fields}.",
        "settings_trigger": "Stand van de controle",
        "settings_trigger_off": "De controle leest geen postvak: zij werkt met de bewaakte map.",
        "settings_trigger_pending": "Nog niet verbonden: de controle maakt de verbinding bij de volgende ronde.",
        "settings_trigger_on": "Verbonden sinds {since}; laatst gelezen: {last}; opgeslagen berichten: {saved}.",
        "settings_trigger_unread": "Verbonden sinds {since}; nog niet gelezen.",
        "settings_ai": "De AI-dienst: Microsoft 365 Copilot",
        "settings_ai_needs": ("Copilot heeft nodig: een licentie voor Microsoft 365 Copilot op het account; de zeven machtigingen "
                              "Sites.Read.All, Mail.Read, People.Read.All, OnlineMeetingTranscript.Read.All, Chat.Read, "
                              "ChannelMessage.Read.All en ExternalItem.Read.All op de app-registratie; en voor vier daarvan "
                              "(People.Read.All, OnlineMeetingTranscript.Read.All, ChannelMessage.Read.All en ExternalItem.Read.All) "
                              "eenmalig de toestemming van een beheerder."),
        "settings_ai_sign_in": "Aanmelden voor Copilot",
        "settings_ai_signed_in": "Aangemeld voor het postvak en voor Copilot.",
        "settings_ai_test": "Copilot testen",
        "settings_ai_answer": "Copilot antwoordt: {answer}",
        "settings_ai_failed": "Copilot testen is niet gelukt: {error}",
        "settings_ai_reader": "Documenten lezen met",
        "settings_reader:llm": "Copilot",
        "settings_reader:parser": "zonder AI (parser)",
        "settings_ai_ids_needed": "Sla eerst de map-id en de toepassings-id op; daarna kan Copilot de documenten lezen.",
        "settings_connections": "Koppelingen: het Microsoft-postvak en de AI-dienst",
        "settings_extension_code": "Code voor de uitbreiding in de browser",
        "settings_extension_code_help": "Plak deze code in de opties van de uitbreiding Inkoopassistent in Edge of Chrome.",
        "settings_file_unreadable": ("Het instellingenbestand kan niet worden gelezen{fields}. Hieronder staan de instellingen die de "
                                     "assistent nu gebruikt. Opslaan kan pas weer als het bestand is hersteld."),
        "settings_file_fields": ": controleer {fields}",
        "settings_file_write_route": ('exact_base_url staat ingesteld, maar deze versie van de assistent schrijft niets in Exact: elke '
                                      'wijziging wordt klaargezet op het tabblad "In Exact invoeren". Haal exact_base_url uit het '
                                      'instellingenbestand. Hieronder staan de instellingen die de assistent nu gebruikt. Opslaan kan pas '
                                      'weer als het bestand is hersteld.'),
        "settings_group:handled": "Wat de assistent zelf mag afhandelen",
        "settings_group:questions": "Vragen aan leveranciers",
        "settings_group:digest": "Dagoverzicht en taal",
        "settings_group:approval": "Fiattering",
        "settings_field:price_pct": "Prijsverschil: hoogstens dit percentage",
        "settings_help:price_pct": ("De drempel voor de prijs: is een prijsverschil hoogstens dit percentage van de prijs op de "
                                    "inkooporder en hoogstens het bedrag hieronder per regel, dan neemt de assistent de prijs van de "
                                    "leverancier over op de inkooporder en verandert ook de prijs van het artikel; een verschil boven de "
                                    "drempel komt naar u."),
        "settings_field:price_abs": "Prijsverschil: hoogstens dit bedrag per regel (euro)",
        "settings_help:price_abs": ("Het bedrag van de drempel: het verschil per stuk maal het aantal van de regel; is dat meer dan "
                                    "dit bedrag, dan komt het verschil naar u, ook als het binnen het percentage blijft."),
        "settings_field:lead_time_tolerance_days": "Leverdatum: dagen bovenop de levertijd",
        "settings_help:lead_time_tolerance_days": ("De marge in dagen: een latere leverdatum neemt de assistent over tot en met de "
                                                   "levertijd van de leverancier plus deze marge, geteld vanaf de leverdatum van de "
                                                   "regel op de inkooporder; een nog latere datum komt naar u, en zonder bekende "
                                                   "levertijd van de leverancier komt elke latere datum naar u. Een eerdere leverdatum "
                                                   "neemt de assistent over, ook zonder bekende levertijd."),
        "settings_field:first_cases_review": "Eerste zaken van een leverancier",
        "settings_help:first_cases_review": ("Tot zoveel zaken van een leverancier zijn afgesloten, komt elke zaak van die leverancier naar u, "
                                             "wat er ook in staat; 0 zet dit uit. Op het tabblad \"Beoordelen\" heet de reden "
                                             "\"Nieuwe leverancier\"."),
        "settings_field:ask_wait_working_days": "Wachttijd op een antwoord (werkdagen)",
        "settings_help:ask_wait_working_days": ("Zoveel werkdagen wacht de assistent op het antwoord van een leverancier op een vraag; "
                                                "komt er geen antwoord, dan komt de zaak naar u."),
        "settings_field:buyer_name": "Uw naam onder de berichten",
        "settings_help:buyer_name": ("Deze naam staat onder \"Inkoop\", of onder \"Purchasing\" als de taal van de assistent English is, "
                                     "in de vragen aan leveranciers en in het verzoek aan de fiatteur; leeg: alleen \"Inkoop\" of "
                                     "\"Purchasing\"."),
        "settings_field:digest": "Dagoverzicht",
        "settings_help:digest": "Eén overzicht per dag vanaf de tijd hieronder, of een overzicht na elke zaak.",
        "settings_digest:daily": "Elke dag op een vaste tijd",
        "settings_digest:per_case": "Na elke zaak",
        "settings_field:digest_time": "Tijd van het dagoverzicht (UU:MM)",
        "settings_help:digest_time": "Het dagoverzicht komt bij de eerste ronde van de assistent vanaf deze tijd.",
        "settings_field:buyer_email": "Uw e-mailadres",
        "settings_help:buyer_email": ("Hierheen gaat het dagoverzicht, en u staat in de kopie van elke vraag aan een leverancier en van "
                                      "het verzoek aan de fiatteur."),
        "settings_field:language": "Taal van de assistent",
        "settings_help:language": ("De taal van het dagoverzicht, van de berichten aan leveranciers en aan de fiatteur, en van de balk in "
                                   "de webshop."),
        "settings_language:nl": "Nederlands",
        "settings_language:en": "English",
        "settings_field:approver_email": "E-mailadres van de fiatteur",
        "settings_help:approver_email": ("Verandert de assistent een inkooporder boven de fiatteringsgrens, dan vraagt de assistent deze "
                                         "persoon de order opnieuw te fiatteren."),
        "settings_limit": "De fiatteringsgrens zelf is bij de installatie ingesteld en staat niet op deze pagina.",
        "settings_buyer_save": "Opslaan",
        "settings_buyer_saved": "Opgeslagen. De assistent gebruikt dit vanaf de volgende ronde, binnen een minuut.",
        "settings_buyer_unchanged": "Er is niets gewijzigd; er is niets opgeslagen.",
        "settings_buyer_refilled": ("Niet opgeslagen: de pagina heeft het formulier opnieuw gevuld met de instellingen van nu. Vul uw "
                                    "wijziging opnieuw in en sla dan op."),
        "settings_buyer_refused": "Niet opgeslagen: {fields}.",
        "settings_suppliers": "Leveranciers",
        "settings_suppliers_help": ("Per leverancier, met de naam zoals op de inkooporder: het e-mailadres voor de vragen van de "
                                    "assistent (zonder adres stelt de assistent geen vragen en komt de zaak naar u), de levertijd in "
                                    "dagen, of elke zaak naar u komt, en of de assistent deze leverancier uit zichzelf geen vragen "
                                    "stuurt: die vragen komen dan naar u, en een vraag die u zelf op het tabblad \"Beoordelen\" kiest, "
                                    "gaat wel naar de leverancier."),
        "settings_supplier_column:name": "Leverancier",
        "settings_supplier_column:email": "E-mailadres",
        "settings_supplier_column:lead_time": "Levertijd (dagen)",
        "settings_supplier_column:review": "Altijd naar u",
        "settings_supplier_column:messages_off": "Geen vragen uit zichzelf",
        "settings_tick": "ja",
        "settings_supplier_choice": "Leverancier",
        "settings_supplier_new": "Leverancier toevoegen",
        "settings_supplier_name": "Naam (zoals op de inkooporder)",
        "settings_supplier_email": "E-mailadres voor vragen",
        "settings_supplier_lead_time": "Levertijd in dagen",
        "settings_supplier_review": "Elke zaak van deze leverancier komt naar u",
        "settings_supplier_messages_off": "De assistent stuurt deze leverancier uit zichzelf geen vragen",
        "settings_supplier_save": "Leverancier opslaan",
        "settings_supplier_remove": "Leverancier verwijderen",
        "settings_supplier_choose": "Kies eerst een leverancier.",
        "settings_supplier_listed": ("Niet opgeslagen: er staat al een leverancier met deze naam in de lijst. Kies die leverancier bij "
                                     "\"Leverancier\" om hem te wijzigen."),
        "settings_supplier_empty": "Niet opgeslagen: een leverancier heeft een e-mailadres, een levertijd of een vinkje nodig.",
        "settings_problem:percent": "een getal van 0 tot 100",
        "settings_problem:amount": "een getal van 0 of meer",
        "settings_problem:decimals": "hoogstens twee decimalen (schrijf duizend als 1000, zonder punt of komma)",
        "settings_problem:days": "een heel getal van 0 tot 365",
        "settings_problem:cases": "een heel getal van 0 tot 1000",
        "settings_problem:wait": "een heel getal van 1 tot 60",
        "settings_problem:digest": "elke dag of na elke zaak",
        "settings_problem:time": "een tijd van 00:00 tot 23:59",
        "settings_problem:address": "een e-mailadres",
        "settings_problem:name": "één regel van hoogstens 80 tekens",
        "settings_problem:supplier_name": "een naam van 1 tot 80 tekens",
        "settings_problem:language": "Nederlands of English",
        "exact_title": "Koppeling met Exact (bij de installatie)",
        "exact_intro": ("De assistent leest wat Exact zelf levert en verandert zelf niets in Exact. Hier stelt u in hoe de documenten uit "
                        "Exact eruitzien. Uw voorbeeldbestanden blijven op deze pc."),
        "exact_part_po": "1. Inkooporders die Exact per e-mail stuurt (pdf)",
        "exact_layout": "Opmaak van de inkooporder",
        "exact_state_title": "Stand van de koppeling",
        "exact_refresh": "Stand vernieuwen",
        "exact_state_set": "Opmaak van de inkooporder: ingesteld.",
        "exact_state_unset": ("Opmaak van de inkooporder: nog niet ingesteld. Tot dan leest de assistent alleen de verzonnen "
                              "voorbeeldinkooporders."),
        "exact_state_known": "Inkooporders die de assistent kent: {count}. Laatst opgenomen: inkooporder {number}, versie {version}, {time}.",
        "exact_state_known_export": "Inkooporders die de assistent kent: {count}. Laatst opgenomen: inkooporder {number} uit een export, {time}.",
        "exact_state_none": "De assistent kent nog geen inkooporders.",
        "exact_state_unread": "Bestanden in de bewaakte map die de assistent niet kon lezen: {n}.",
        "exact_sample": "Voorbeeld: een inkooporder uit Exact als pdf",
        "exact_page": "De eerste pagina van het voorbeeld, regel voor regel",
        "exact_page_line": "Regel",
        "exact_page_text": "Tekst",
        "exact_page_more": "De tabel toont de eerste {n} regels van de eerste pagina.",
        "exact_field:po_label": "Tekst voor het inkoopordernummer",
        "exact_help:po_label": ("Het woord dat op de pdf direct voor het nummer staat. Neem het over uit de regels hierboven. Het mogen ook "
                                "meer woorden zijn, zoals \"Inkooporder nr.\": die zoekt de assistent samen, als één geheel."),
        "exact_help:label": ("De tekst die op de pdf direct voor de waarde staat; neem hem over uit de regels hierboven. Het mogen ook meer "
                             "woorden zijn, zoals \"Inkooporder nr.\": die zoekt de assistent samen, als één geheel."),
        "exact_field:date_label": "Tekst voor de datum (mag leeg)",
        "exact_field:supplier_label": "Tekst voor de leverancier",
        "exact_field:supplier_line": "Of: het nummer van de regel waarop alleen de leverancier staat",
        "exact_field:version_label": ("Tekst voor het versienummer (leeg: de pdf noemt geen versie; de assistent nummert dan in de volgorde "
                                      "van binnenkomst)"),
        "exact_field:currency_label": "Tekst voor de valuta (mag leeg)",
        "exact_field:incoterm_label": "Tekst voor de leveringsconditie (mag leeg)",
        "exact_field:title_word": "Een woord uit de kopregel boven de orderregels, bijvoorbeeld Artikel",
        "exact_find": "Kopregel zoeken",
        "exact_title_found": "Kopregel gevonden. Kies bij elk woord wat er in die kolom staat.",
        "exact_title_missing": "Op de eerste pagina staat geen regel met dit woord.",
        "exact_title_long": "De kopregel heeft meer dan 20 woorden; dat kan dit formulier niet aan.",
        "exact_title_saved_long": ("De opgeslagen kopregel heeft meer dan 20 woorden; dat kan dit formulier niet tonen. De opmaak blijft "
                                   "gelden tot u een nieuwe opslaat."),
        "exact_meaning:line_id": "Regelnummer",
        "exact_meaning:item_code": "Artikelcode",
        "exact_meaning:description": "Omschrijving",
        "exact_meaning:quantity": "Aantal",
        "exact_meaning:unit": "Eenheid",
        "exact_meaning:unit_price": "Prijs",
        "exact_meaning:price_basis": "Prijs per (aantal)",
        "exact_meaning:discount_pct": "Korting in procenten",
        "exact_meaning:net_price": "Nettoprijs",
        "exact_meaning:delivery_date": "Leverdatum",
        "exact_meaning:same": "Hoort bij de kolom links ervan",
        "exact_meaning:skip": "Niet nodig",
        "exact_field:table_end": "De tabel houdt op bij de regel met deze tekst (leeg: tot het eind)",
        "exact_help:table_end": ("Neem de hele tekst over van de regel direct onder de laatste orderregel, bijvoorbeeld de totaalregel. Een "
                                 "subtotaal per pagina met dezelfde tekst beëindigt de tabel op die pagina."),
        "exact_field:numbers": "Getallen op de pdf",
        "exact_numbers:comma": "1.234,50 (komma voor de decimalen)",
        "exact_numbers:point": "1,234.50 (punt voor de decimalen)",
        "exact_field:per_piece": "De pdf heeft geen kolom prijs per: elke prijs is per 1 stuk",
        "exact_field:senders": "Afzender van de e-mails met inkooporders uit Exact",
        "exact_help:senders": ("Het e-mailadres waarvan Exact de inkooporders stuurt, of alleen het deel na het apenstaartje; meer dan een "
                               "met een komma ertussen. Vul dit in: een pdf in deze opmaak van een andere afzender behandelt de assistent "
                               "dan als orderbevestiging, zodat een bevestiging die op uw inkooporder lijkt nooit als inkooporder wordt "
                               "opgenomen."),
        "exact_test": "Testen",
        "exact_save": "Opslaan",
        "exact_reading": "Zo leest de assistent het voorbeeld",
        "exact_read": ("Het voorbeeld is gelezen: {n} orderregels; het totaal is te berekenen: {answer}. Vergelijk de tabel met de "
                       "inkooporder in Exact. Klopt alles, druk dan op Opslaan."),
        "exact_yes": "ja",
        "exact_no": "nee",
        "exact_read_what": "Wat",
        "exact_read_value": "Gelezen",
        "exact_header:po_reference": "Inkoopordernummer",
        "exact_header:version_hint": "Versie",
        "exact_header:document_date": "Datum",
        "exact_header:supplier": "Leverancier",
        "exact_header:currency": "Valuta",
        "exact_header:incoterm": "Leveringsconditie",
        "exact_problem:no_sample": "Kies eerst een voorbeeld.",
        "exact_problem:scan": "Dit pdf-bestand heeft geen tekst (een scan of een afbeelding); de assistent kan het niet lezen.",
        "exact_problem:unopenable": "Dit pdf-bestand kan niet worden geopend. Sla het opnieuw op uit de e-mail en kies het nog eens.",
        "exact_problem:not_pdf": ("Dit bestand is geen pdf-bestand: zijn naam eindigt niet op .pdf. Kies de pdf van een inkooporder zoals "
                                  "Exact die verstuurt."),
        "exact_problem:po_label": "Vul de tekst voor het inkoopordernummer in.",
        "exact_problem:supplier": "Vul de tekst voor de leverancier in of het regelnummer, niet allebei.",
        "exact_problem:supplier_line": "Het nummer van de regel van de leverancier is een heel getal vanaf 1.",
        "exact_problem:no_title": "Zoek eerst de kopregel: vul een woord eruit in en druk op Kopregel zoeken.",
        "exact_problem:short_title": "Deze kopregel heeft maar één woord; de kopregel boven de orderregels heeft er minstens twee.",
        "exact_problem:meanings": "Kies bij elk woord van de kopregel wat er in de kolom staat.",
        "exact_problem:first_same": "Het eerste woord van de kopregel kan niet bij de kolom links ervan horen.",
        "exact_problem:twice": "Twee kolommen hebben dezelfde betekenis: {meaning}.",
        "exact_problem:item_code": "Kies een kolom als Artikelcode.",
        "exact_problem:quantity": "Zonder kolom Regelnummer is een kolom Aantal nodig.",
        "exact_problem:title_differs": "De kopregel van het voorbeeld is anders dan de kopregel die u hebt ingevuld.",
        "exact_problem:no_number": "Geen inkoopordernummer gevonden: controleer de tekst voor het nummer.",
        "exact_problem:two_pos": ("Na de tekst voor het inkoopordernummer staan verschillende nummers, op {places}: zo zou de assistent "
                                  "deze pdf voor meer dan een inkooporder houden, en hij neemt hem niet op. Staat het inkoopordernummer "
                                  "maar op een van die regels, neem dan een langere tekst voor het nummer over die alleen daar staat, "
                                  "zoals \"Inkooporder:\"."),
        "exact_place:line": "regel {lines} van de eerste pagina",
        "exact_place:lines": "regels {lines} van de eerste pagina",
        "exact_place:page": "pagina {pages}",
        "exact_place:pages": "pagina's {pages}",
        "exact_and": "en",
        "exact_problem:folder": ("Het inkoopordernummer na de tekst voor het nummer kan niet de naam van een map zijn; de assistent kan deze "
                                 "inkooporder niet opnemen."),
        "exact_problem:confirmation": ("Regel {line} van de eerste pagina, boven de kopregel, bevat een woord van een orderbevestiging; "
                                       "de assistent zou deze pdf voor een orderbevestiging houden."),
        "exact_problem:no_lines": "Geen orderregels gevonden: controleer de kopregel en de kolommen.",
        "exact_problem:no_item_code": "Regel {line}: geen artikelcode gelezen.",
        "exact_problem:no_quantity": "Regel {line}: geen aantal gelezen.",
        "exact_problem:convention_comma": ("In de kolom {column} staan getallen met een komma voor de decimalen (zoals 12,50): kies "
                                           "\"1.234,50 (komma voor de decimalen)\"."),
        "exact_problem:convention_point": ("In de kolom {column} staan getallen met een punt voor de decimalen (zoals 12.50): kies "
                                           "\"1,234.50 (punt voor de decimalen)\"."),
        "exact_problem:refused": "De assistent kan dit voorbeeld met deze opmaak niet opnemen.",
        "exact_problem:sender": "Dit is geen e-mailadres en geen domein: {text}.",
        "exact_warning:passed": ("Regel {line} van de eerste pagina: na de tekst voor het inkoopordernummer staat geen woord met een cijfer; "
                                 "de assistent leest daar geen nummer."),
        "exact_warning:continued": ("Pagina {page}: de kopregel staat niet op deze pagina; de assistent leest haar regels met de kolommen van "
                                    "de kopregel op een eerdere pagina."),
        "exact_warning:ended": ("Pagina {page}: de kopregel staat niet op deze pagina en de tabel hield op een eerdere pagina al op; de "
                                "assistent leest deze pagina niet."),
        "exact_saved": ("Opgeslagen. De assistent leest inkooporders in deze opmaak vanaf de volgende ronde, binnen een minuut. "
                        "Inkooporders die nog in de bewaakte map liggen omdat de assistent ze niet kon lezen, worden dan opgenomen; "
                        "andere geeft u opnieuw."),
        "exact_not_saved": "Niet opgeslagen: {problems}",
        "exact_changed": ("Niet opgeslagen: de koppeling is intussen op een andere plek gewijzigd. Open het tabblad Instellingen opnieuw "
                          "en kijk de opmaak na."),
        "exact_field:po_words": "Woorden waar leveranciers uw inkoopordernummer achter zetten",
        "exact_help:po_words": "Met een komma ertussen, bijvoorbeeld: Inkooporder, Uw referentie, Uw ordernummer.",
        "exact_field:po_digits_least": "Een inkoopordernummer heeft minstens zoveel cijfers",
        "exact_field:po_digits_most": "en hoogstens zoveel cijfers",
        "exact_po_number_save": "Nummerherkenning opslaan",
        "exact_problem:po_words": "vul minstens een woord in",
        "exact_problem:po_digits": "een heel getal van 1 tot 20, en het kleinste niet groter dan het grootste",
        "exact_po_number_own": "eigen patroon (in het instellingenbestand)",
        "exact_numbers_title": "Hoe de assistent het inkoopordernummer op een orderbevestiging vindt",
        "exact_number_changed": ("Niet opgeslagen: de nummerherkenning is intussen op een andere plek gewijzigd. Open het tabblad Instellingen "
                                 "opnieuw en kijk de woorden en de cijfers na."),
        "exact_state_waiting": "Zaken die op hun inkooporder wachten: {n}.",
        "exact_part_export": "2. Inkooporders als export uit Exact (voor de controle voor het bestellen)",
        "exact_export_intro": ("Voor het bestellen bestaat de pdf van de inkooporder nog niet. Geef de assistent dan een export van de "
                               "orderregels uit Exact (Excel of csv). Hier stelt u een keer in welke kolom wat bevat."),
        "exact_export_columns": "Kolommen van de export",
        "exact_state_columns_set": "Kolommen van de export: ingesteld.",
        "exact_state_columns_unset": "Kolommen van de export: nog niet ingesteld.",
        "exact_state_export": "Laatste export opgenomen: {time}.",
        "exact_state_no_export": "Nog geen export opgenomen.",
        "exact_export_sample": "Voorbeeld: een export van orderregels uit Exact",
        "exact_export_head": "De eerste rijen van het voorbeeld",
        "exact_export_row": "Rij",
        "exact_export_header_row": "Rij met de kolomnamen",
        "exact_export_field:po_number": "Inkoopordernummer (verplicht)",
        "exact_export_field:item_code": "Artikelcode (verplicht)",
        "exact_export_no_column": "(staat niet in het bestand)",
        "exact_export_help:line_id": "Zonder kolom telt de assistent de regels van elke inkooporder in de volgorde van het bestand.",
        "exact_export_field:numbers": "Getallen die als tekst in het bestand staan",
        "exact_export_field:fill_down": "Het inkoopordernummer staat alleen op de eerste regel van elke inkooporder",
        "exact_export_read": "Gelezen: {orders}; {passed}. {below}",
        "exact_export_orders_one": "1 inkooporder met {lines}",
        "exact_export_orders_many": "{n} inkooporders met samen {lines}",
        "exact_export_lines_one": "1 regel",
        "exact_export_lines_many": "{m} regels",
        "exact_export_passed_one": "1 rij zonder artikelcode overgeslagen",
        "exact_export_passed_many": "{k} rijen zonder artikelcode overgeslagen",
        "exact_export_first": "Hieronder de eerste inkooporder: vergelijk haar met Exact.",
        "exact_export_first_and_last": "Hieronder de eerste en de laatste inkooporder: vergelijk ze met Exact.",
        "exact_export_no_quantity": ("{count} rijen met een artikelcode maar zonder aantal overgeslagen (eerste: rij {row}). Zijn dat orderregels, "
                                     "kies dan bij \"{field}\" de kolom met het bestelde aantal."),
        "exact_export_no_quantity_one": ("1 rij met een artikelcode maar zonder aantal overgeslagen (rij {row}). Is dat een orderregel, kies dan "
                                         "bij \"{field}\" de kolom met het bestelde aantal."),
        "exact_export_unread": ("Niet gelezen: {count} waarden in de kolom {column} (eerste: rij {row}). Die waarden vergelijkt de assistent "
                                "niet."),
        "exact_export_unread_one": "Niet gelezen: 1 waarde in de kolom {column} (rij {row}). Die waarde vergelijkt de assistent niet.",
        "exact_export_problem:po_number": "Kies de kolom met het inkoopordernummer.",
        "exact_export_problem:item_code": "Kies de kolom met de artikelcode.",
        "exact_export_problem:more_than_once": "De kolom {column} is meer dan een keer gekozen.",
        "exact_export_problem:no_lines": ("Het voorbeeld gaf geen orderregel: geen rij onder de kolomnamen heeft een artikelcode en, als u die "
                                          "kolom koos, een aantal. Controleer de gekozen kolommen."),
        "exact_export_problem:item_code_date": ("In {count} rijen is de artikelcode een datum, een tijd of een foutwaarde (eerste: rij {row}), "
                                                "zodat de assistent die rijen overslaat. Kies de kolom met de artikelcode; staat die goed, "
                                                "kijk die rijen dan na in het bestand: Excel kan een code in een datum omzetten."),
        "exact_export_problem:item_code_date_one": ("In 1 rij is de artikelcode een datum, een tijd of een foutwaarde (rij {row}), zodat de "
                                                    "assistent die rij overslaat. Kies de kolom met de artikelcode; staat die goed, kijk die "
                                                    "rij dan na in het bestand: Excel kan een code in een datum omzetten."),
        "exact_export_unopenable": "Het voorbeeld kan niet meer worden geopend. Kies het bestand nog eens.",
        "exact_export_refused:map": ("De gekozen kolommen zijn niet volledig: kies in elk geval de kolom met het inkoopordernummer en de kolom "
                                     "met de artikelcode."),
        "exact_export_refused:file_type": ("Dit bestand is geen Excel- of csv-bestand. Een oud Excel-bestand (.xls) slaat u eerst in Excel op "
                                           "als .xlsx."),
        "exact_export_refused:unreadable": ("De assistent kan dit bestand niet lezen: het is beschadigd, of een cel bevat een waarde die niet bij "
                                            "het soort cel past. Open het in Excel, sla het opnieuw op en kies het nog eens."),
        "exact_export_refused:unreadable_row": ("Rij {row}: de assistent kan een cel van dit bestand niet lezen. Open het in Excel, sla het "
                                                "opnieuw op en kies het nog eens."),
        "exact_export_refused:delimiter_or_encoding": ("De assistent kan dit csv-bestand niet in kolommen verdelen: het gebruikt een ander "
                                                       "scheidingsteken of een andere tekenset dan de assistent leest. Sla het in Excel op als "
                                                       ".xlsx en kies dat bestand."),
        "exact_export_refused:no_column": ("In dit bestand ontbreekt de kolom {column}. Kies bij dat veld een kolom uit dit bestand, of \"(staat "
                                           "niet in het bestand)\"."),
        "exact_export_refused:double_column": ("Rij {row}: de kolom {column} staat twee keer in de rij met de kolomnamen, zodat de assistent "
                                               "niet weet welke bedoeld is. Kies een kolom waarvan de naam maar een keer voorkomt."),
        "exact_export_refused:no_po_number": ("Rij {row}: een artikelcode zonder inkoopordernummer. Staat het nummer alleen op de eerste regel "
                                              "van elke inkooporder, vink dat dan aan."),
        "exact_export_refused:po_number_date": ("Rij {row}: het inkoopordernummer is een datum, een tijd of een foutwaarde. Kies de kolom met "
                                                "het inkoopordernummer; staat die goed, kijk die rij dan na in het bestand."),
        "exact_export_refused:po_number_no_digit": ("Rij {row}: het inkoopordernummer heeft geen cijfer. Kies de kolom met het "
                                                    "inkoopordernummer; staat die goed, kijk die rij dan na in het bestand."),
        "exact_export_refused:po_number_no_folder": ("Rij {row}: dit inkoopordernummer kan niet de naam van een map zijn, zodat de assistent "
                                                     "het niet kan opnemen. Kies de kolom met het inkoopordernummer; staat die goed, kijk "
                                                     "die rij dan na in het bestand."),
        "exact_export_refused:no_line_id": ("Rij {row}: een artikelcode zonder regelnummer. Kies de kolom met het regelnummer, of kies \"(staat "
                                            "niet in het bestand)\": dan telt de assistent de regels zelf."),
        "exact_export_refused:line_id_date": ("Rij {row}: het regelnummer is een datum, een tijd of een foutwaarde. Kies de kolom met het "
                                              "regelnummer, of kies \"(staat niet in het bestand)\": dan telt de assistent de regels zelf."),
        "exact_export_refused:double_line_id": ("Rij {row}: dit regelnummer staat al op een eerdere regel van dezelfde inkooporder. Kies de "
                                                "kolom met het regelnummer, of kies \"(staat niet in het bestand)\": dan telt de assistent de "
                                                "regels zelf."),
        "exact_export_refused:surplus_cells": ("Rij {row} van dit bestand heeft meer cellen dan de rij met kolomtitels. Meestal staat er dan "
                                               "een komma in een getal of in een omschrijving zonder aanhalingstekens. Sla het bestand op als "
                                               "Excel-bestand (.xlsx) of met puntkomma's als scheidingsteken, en geef het opnieuw."),
        "exact_export_refused:cell_count": ("Rij {row} van dit bestand heeft een ander aantal cellen dan de rij met kolomtitels. In een bestand "
                                            "met komma's als scheidingsteken staat er dan meestal een komma in een getal of in een "
                                            "omschrijving zonder aanhalingstekens. Sla het bestand op als Excel-bestand (.xlsx) of met "
                                            "puntkomma's als scheidingsteken, en geef het opnieuw."),
        "exact_export_refused:hidden_sheet": ("Dit Excel-bestand heeft alleen gegevens op een verborgen werkblad. De assistent leest geen "
                                              "verborgen werkbladen: maak het werkblad zichtbaar, of sla het op als een eigen bestand, en geef "
                                              "het opnieuw."),
        "exact_export_refused:several_sheets": ("Dit Excel-bestand heeft meer dan één werkblad met gegevens ({names}). De assistent leest één "
                                                "werkblad: sla het werkblad dat u bedoelt op als een eigen bestand en geef dat."),
        "exact_export_saved": "Opgeslagen.",
        "exact_export_changed": ("Niet opgeslagen: de koppeling is intussen op een andere plek gewijzigd. Open het tabblad Instellingen "
                                 "opnieuw en kijk de kolommen na."),
        "exact_part_articles": "3. Inkoopprijzen van artikelen als export uit Exact (voor de prijslijst)",
        "exact_articles_intro": ("Kies een voorbeeld van de export van artikelen uit Exact en wijs de kolommen aan. Heeft de export geen "
                                 "kolom met de leverancier, dan kiest u de leverancier wanneer u de export op het tabblad Prijslijst geeft."),
        "exact_articles_sample": "Voorbeeld: een export van artikelen uit Exact",
        "exact_articles_columns": "Kolommen van de export van de artikelen",
        "exact_articles_field:item_code": "Artikelcode (verplicht)",
        "exact_articles_field:price": "Inkoopprijs (verplicht)",
        "exact_articles_field:supplier": "Leverancier",
        "exact_articles_price": "Inkoopprijs",
        "exact_articles_problem:price": "Kies de kolom met de inkoopprijs.",
        "exact_articles_problem:none": ("Het voorbeeld gaf geen artikel: geen rij onder de kolomnamen heeft een artikelcode en een prijs die "
                                        "te lezen is."),
        "exact_articles_read": "{articles} gelezen; {passed}.",
        "exact_articles_read_reasons": "{articles} gelezen; {passed} ({reasons}).",
        "exact_articles_left:no_item_code": "zonder artikelcode",
        "exact_articles_left:no_supplier": "zonder leverancier",
        "exact_articles_left:no_price": "prijs niet te lezen",
        "exact_articles_left:twice": "artikel dubbel",
        "exact_state_to_enter": "Nog in te voeren in Exact: {n}.",
        "hand_test_mode": "Proefstand: voer deze wijzigingen alleen in het test-Exact in.",
        "hand_tryout": "(proefdocument)",
        "tryout_title": "Proefdraaien met een inkooporder uit Exact",
        "tryout_intro": ("Zonder leverancier kunt u de assistent toch proberen: bij een inkooporder die de assistent kent, maakt de "
                         "assistent een verzonnen orderbevestiging. Voor de voorcontrole in de browser maakt de assistent van dezelfde "
                         "inkooporder een verzonnen winkelwagen. U kiest wat er afwijkt. Het proefdocument blijft op deze pc."),
        "tryout_switch": "Proefstand: de assistent werkt met het test-Exact",
        "tryout_saved": "Opgeslagen.",
        "tryout_saved_open": ("Opgeslagen. Op de lijst In Exact invoeren staan nog {n} wijzigingen uit proefdocumenten. Voer die niet in het "
                              "echte Exact in; laat ze vallen."),
        "tryout_saved_open_one": ("Opgeslagen. Op de lijst In Exact invoeren staat nog 1 wijziging uit een proefdocument. Voer die niet in het "
                                  "echte Exact in; laat die vallen."),
        "tryout_switch_refilled": ("De proefstand is intussen op een andere plek gewijzigd; de schakelaar toont nu wat het instellingenbestand "
                                   "bevat."),
        "tryout_not_offered": "Proefdraaien kan alleen in de proefstand en zolang er geen fiatteringsgrens is ingesteld.",
        "tryout_po": "Inkooporder",
        "tryout_change": "Wat wijkt af?",
        "tryout_change:none": "Niets: alles komt overeen",
        "tryout_change:price_small": "De prijs van de eerste regel is 1 procent hoger",
        "tryout_change:price_large": "De prijs van de eerste regel is 10 procent hoger",
        "tryout_change:date_later": "De leverdatum van de eerste regel is drie dagen later",
        "tryout_change:date_much_later": "De leverdatum van de eerste regel is zestig dagen later",
        "tryout_make": "Proefbevestiging maken (pdf)",
        "tryout_give": "Proefbevestiging direct aan de assistent geven",
        "tryout_made": ("De proefbevestiging is gemaakt. Stuur haar als bijlage naar uw eigen postvak, naar de map die de assistent leest, "
                        "of geef haar direct aan de assistent."),
        "tryout_given": "De proefbevestiging ligt in de bewaakte map. De assistent opent de zaak bij de volgende ronde, binnen een minuut.",
        "tryout_cart_change": "Wat wijkt af in de proefwinkelwagen?",
        "tryout_cart_change:none": "Niets",
        "tryout_cart_change:price_small": "De prijs van de eerste regel is 1 procent hoger",
        "tryout_cart_make": "Proefwinkelwagen maken",
        "tryout_cart_made": ("De proefwinkelwagen staat klaar. Open {link} in de browser waarin de uitbreiding van de assistent staat: de "
                             "balk bovenaan de pagina laat de uitkomst zien."),
        "tryout_none_known": "De assistent kent nog geen inkooporders. Laat eerst een inkooporder uit Exact binnenkomen.",
        "tryout_refused": "Van deze inkooporder kan geen proefdocument worden gemaakt: {reason}.",
        "tryout_reason:no_lines": "zij heeft geen orderregels",
        "tryout_reason:no_price": "de eerste regels hebben geen prijs",
        "tryout_reason:no_date": "de eerste regels hebben geen leverdatum",
        "tryout_reason:character": "een tekst bevat een teken dat het proefdocument niet kan afdrukken",
        "tryout_reason:item_code": "een artikelcode is te lang",
        "tryout_reason:value": "een waarde past niet in het proefdocument",
        "tryout_reason:read_back": "het proefdocument zou niet precies zo worden gelezen als de inkooporder",
        "tryout_reason:not_found": "de assistent zou de inkooporder van het proefdocument niet vinden",
        "tryout_reason:version": "de assistent zou niet weten met welke versie van de inkooporder zij moet vergelijken",
        "tryout_nothing_sent": "Dit is een proefdocument: de assistent stuurt hierover niets naar een leverancier.",
        "item_one": "1 punt",
        "items_many": "{n} punten",
        "unknown_po": "onbekend",
        "line_word": "regel",
        "article_word": "artikel",
        "status:closed": "afgesloten",
        "status:review": "ter beoordeling",
        "status:waiting": "wacht op de leverancier",
        "status:failed": "mislukt",
        "field:price": "Prijs",
        "field:quantity": "Aantal",
        "field:unit": "Eenheid",
        "field:delivery_date": "Leverdatum",
        "field:line_presence": "Regel aanwezig",
        "field:required_info": "Vereiste informatie",
        "pair:matched": "aanwezig",
        "pair:split": "gesplitst",
        "pair:merged": "samengevoegd",
        "kind:po_line_price": "Prijs van de orderregel",
        "kind:po_line_date": "Leverdatum van de orderregel",
        "kind:po_line_quantity": "Aantal van de orderregel",
        "kind:article_price": "Artikelprijs",
        "kind:attach_document": "Bevestiging bijgevoegd",
        "kind:detach_document": "Bevestiging verwijderd",
        "kind:set_status": "Status van de inkooporder",
        "reason:different": "Verschil",
        "reason:missing on the document": "Ontbreekt op het document",
        "reason:missing line": "Regel ontbreekt",
        "reason:extra line": "Extra regel",
        "reason:not in cart": "Niet in de winkelwagen",
        "reason:later": "Later dan besteld",
        "reason:earlier": "Eerder dan besteld",
        "reason:different currency": "Andere valuta",
        "reason:currency missing": "Valuta ontbreekt op het document",
        "reason:unknown price basis": "Prijseenheid onbekend",
        "reason:unknown unit conversion": "Omrekening van de eenheid onbekend",
        "reason:total only": "Alleen een totaalbedrag",
        "reason:match uncertain": "Koppeling van de regels onzeker",
        "reason:unreadable or unverified source": "Bron onleesbaar of niet gecontroleerd",
        "reason:no PO date": "Geen leverdatum op de inkooporder",
        "reason:merged line": "Samengevoegde regel",
        "reason:no PO price": "Geen prijs op de inkooporder",
        "reason:no PO quantity": "Geen aantal op de inkooporder",
        "reason:no PO unit": "Geen eenheid op de inkooporder",
        "reason:new supplier": "Nieuwe leverancier",
        "reason:supplier marked for review": "Leverancier gemarkeerd voor beoordeling",
        "reason:no version selected": "Geen versie van de inkooporder gekozen",
        "reason:PO found otherwise than by the number recognition": ("Kijk na of dit de goede inkooporder is: de assistent vond haar niet "
                                                                     "aan het nummer achter een van de woorden van de nummerherkenning."),
        "reason:the list of changes to enter in Exact could not be read": ("De lijst In Exact invoeren kon niet worden gelezen: de "
                                                                           "assistent vergelijkt met de inkooporder zoals zij is "
                                                                           "opgenomen."),
        "reason:PO chosen by the items in the cart": ("Kijk na of dit de goede inkooporder is: de assistent koos haar op de artikelen in de "
                                                      "winkelwagen."),
        "reason:split line: parts differ": "Gesplitste regel: de delen verschillen",
        "reason:no delivery date on the document": "Geen leverdatum op het document",
        "reason:no lead time known for the supplier": "Geen levertijd bekend voor de leverancier",
        "reason:delivery date beyond the lead time plus the tolerance": "Leverdatum later dan de levertijd plus de marge",
        "reason:no price on the document": "Geen prijs op het document",
        "reason:quantity unknown for the price rule": "Aantal onbekend voor de prijscontrole",
        "reason:price above threshold": "Prijsverschil boven de drempel",
        "reason:PO total unknown": "Totaalbedrag van de inkooporder onbekend",
        "reason:cart: for the buyer": "Winkelwagen: voor de inkoper",
        "reason:messages off for this supplier": "De assistent stuurt deze leverancier uit zichzelf geen vragen",
        "reason:could not be placed": "Kon niet in de winkelwagen worden geplaatst",
        "reason:not covered by a rule": "Voor dit verschil is geen vaste aanpak",
        "reason:prepared change: apply by hand": "Voorbereide wijziging: met de hand doorvoeren",
        "reason:write failed: *": "Schrijfactie mislukt",
        "reason:write made, not verified: check in Exact": ("Wijziging in Exact gemaakt, maar de controle erna lukte niet: kijk in Exact of "
                                                            "de waarde klopt"),
        "reason:write outcome not known: check in Exact": ("Niet zeker of de wijziging in Exact is gemaakt: kijk in Exact welke waarde er "
                                                           "staat"),
        "reason:not applied: an earlier write failed *": "Niet uitgevoerd: een eerdere schrijfactie is mislukt",
        "reason:not applied: an earlier change waits for a look in Exact *": "Niet uitgevoerd: kijk eerst in Exact naar de eerdere wijziging",
        "reason:no address for the supplier": "Geen e-mailadres van de leverancier",
        "reason:message not sent": "Bericht niet verzonden",
        "reason:try-out document: nothing is sent": "Dit is een proefdocument: de assistent stuurt hierover niets naar een leverancier.",
        "reason:message bounced": "Bericht kwam terug",
        "reason:no answer in *": "Geen antwoord binnen de wachttijd",
        "reason:supplier answered: read the reply": "De leverancier heeft geantwoord: lees het antwoord",
        "reason:no question is open": "Er staat geen vraag meer open: besluit over deze zaak",
        "reason:other": "Andere reden",
    },
    "en": {
        "title": "Cases to review",
        "language": "Language",
        "case": "Case",
        "refresh": "Refresh the list",
        "no_case": "Choose a case first.",
        "document": "Supplier's document",
        "po_document": "Purchase order",
        "flags": "Differences",
        "matches": "Matches",
        "uncompared": "Could not compare",
        "decisions": "Decisions",
        "decision": "Decision",
        "confirm": "Confirm: take the supplier's value",
        "keep": "Keep the purchase order's value",
        "ask": "Ask the supplier",
        "unresolved": "Unresolved",
        "article_update": "Also update the article price",
        "banner": "This change sends the purchase order through approval again",
        "close": "Close the case",
        "undo": "Undo",
        "undo_choice": "Write",
        "item": "Item",
        "field": "Field",
        "line": "Line",
        "po_value": "Purchase order",
        "doc_value": "Supplier",
        "reason": "Reason",
        "po_source": "Purchase order source",
        "doc_source": "Supplier source",
        "saved": "Decision on {item} saved.",
        "refused": "This decision cannot be taken here.",
        "new_status": "The case is now: {status}.",
        "write_stopped": "A write did not go through ({write}): {error}. The case stays for review.",
        "not_closed": "This case cannot be closed: it is no longer for review.",
        "undone": "Undone: {write}.",
        "not_undone": "Nothing undone: this write was undone already or cannot be undone.",
        "undo_failed": "The undo failed.",
        "write_refused": "The write was refused",
        "message_not_sent": "A message was not sent; the case stays for review.",
        "message_filed": "The message was not sent but written as a file ({folder}): the settings have messages written as files.",
        "not_sent:sign_in": "The Microsoft sign-in has lapsed: sign in again under Settings, Connections, and close the case again.",
        "not_sent:unreachable": "The mailbox or the mail server could not be reached: close the case again later.",
        "not_sent:refused": "The mailbox or the mail server refused the message: pass this on to the researcher.",
        "not_sent:no_mailbox": "No mailbox is connected: connect a mailbox under Settings, Connections, and close the case again.",
        "not_sent:no_password": "The mail server's password is not set: pass this on to the researcher.",
        "not_sent:settings": "The settings cannot be read now: nothing was sent. Pass this on to the researcher.",
        "not_sent:document": "The case's document cannot be read now: nothing was sent.",
        "message_unconfirmed": ("Not sure whether the message was sent: the mailbox gave no confirmation. Look in Sent items. If it is not "
                                "there, the case comes back to you after the wait and you ask the question again."),
        "reply_unread": "A reply filed with this case cannot be read now: close the case again in a moment.",
        "question_open": "A question to the supplier is open",
        "decisions_damaged": "The decisions of this case cannot be read ({file}).",
        "waiting_hint:po": ("The assistant does not know purchase order {number} yet. When that purchase order comes in from Exact, the "
                            "assistant checks this confirmation again by itself; you need not decide anything here. If it stays away, give "
                            "the PDF of the purchase order or the e-mail from Exact on the tab Files. If {number} is not one of your "
                            "purchase order numbers, decide on this case yourself."),
        "waiting_hint:version": ("The assistant does not know version {version} of purchase order {number} yet. When that version comes in "
                                 "from Exact, the assistant checks this confirmation again by itself; you need not decide anything here. If "
                                 "it stays away, give the PDF of that version or the e-mail from Exact on the tab Files."),
        "entered_values": ("Since this purchase order was filed, this was entered in Exact: {changes}. The assistant compares with these "
                           "values."),
        "entered:price": "line {line}: price {value}",
        "entered:delivery_date": "line {line}: delivery date {value}",
        "entered:quantity": "line {line}: quantity {value}",
        "entered_unread": ("The list Enter in Exact could not be read: the assistant compares with the purchase order as it was "
                           "filed."),
        "choose_write": "Choose a write first.",
        "review_tab": "Review",
        "hand_tab": "Enter in Exact",
        "hand_tab_count": "Enter in Exact ({n})",
        "hand_tab_unread": "Enter in Exact (?)",
        "hand_intro": "The assistant changes nothing in Exact itself. Below is what you enter in Exact. Tick what you have entered.",
        "hand_nothing": "There is nothing to enter.",
        "hand_block_po": "Purchase order {number} ({supplier})",
        "hand_block_po_alone": "Purchase order {number}",
        "hand_block_articles": "Article prices",
        "hand_row:po_line_price": "line {line} ({item}): price {old} → {new}",
        "hand_row:po_line_date": "line {line} ({item}): delivery date {old} → {new}",
        "hand_row:po_line_quantity": "line {line} ({item}): quantity {old} → {new}",
        "hand_row:article_price": "article {item}: purchase price {old} → {new}",
        "hand_row:article_price_back": "article {item}: purchase price {old} back to the price before it",
        "hand_unknown": "unknown",
        "hand_empty": "empty",
        "hand_week": "week {week} ({year})",
        "hand_hint": "Note: after this change the purchase order must be approved again.",
        "hand_entered": "The ticked rows have been entered",
        "hand_dropped": "Do not enter the ticked rows",
        "hand_block": "Block",
        "hand_block_entered": "Everything of this block has been entered",
        "hand_pricelist": "Price list",
        "hand_pricelist_entered": "All article prices of this price list have been entered",
        "hand_tick_first": "Tick what you mean first.",
        "hand_entered_one": "1 change noted as entered in Exact.",
        "hand_entered_many": "{n} changes noted as entered in Exact.",
        "hand_dropped_one": "1 change is dropped: the assistant no longer asks for it.",
        "hand_dropped_many": "{n} changes are dropped: the assistant no longer asks for them.",
        "hand_nothing_noted": ("Nothing was noted: the ticked rows are no longer open (already handled, or replaced by a newer value). The "
                               "list has been updated."),
        "hand_unreadable_one": "1 line of the list could not be read; refresh the list in a minute.",
        "hand_unreadable_many": "{n} lines of the list could not be read; refresh the list in a minute.",
        "hand_put_back_row": "Put back: {row}",
        "hand_exact_shows": "Exact now shows {value}.",
        "hand_tick_ended": ("You ticked this, but the purchase order that came in afterwards shows {value}. Enter the change in Exact, or "
                            "tick again if it is already there."),
        "hand_tick_ended_unfound": ("You ticked this, but this line cannot be found in the purchase order that came in afterwards. Look in "
                                    "Exact whether the change is there and tick again, or take the row off the list."),
        "hand_list_changed": ("The list Enter in Exact cannot be read: the file was changed outside the assistant. Pass this on to the "
                              "researcher."),
        "hand_done": "Handled in the last 14 days",
        "hand_done_ticked": "(ticked by you on {date})",
        "hand_done_seen": "(seen in version {n} of the purchase order)",
        "hand_done_dropped": "(not entered, on {date})",
        "hand_done_choice": "Handled change",
        "hand_reopen": "Put on the list again",
        "hand_put_back": "Put back in Exact",
        "hand_reopened": "The row is on the list again.",
        "hand_reopen_seen": "The assistant saw this change in Exact itself. Choose Put back in Exact if the old value must return.",
        "hand_reopen_not": "The change does not return to the list: there is another change for the same line or article by now.",
        "hand_put_back_listed": "Not put back in Exact yet. The list now holds: {row}. Enter the old value in Exact and tick the row.",
        "hand_put_back_other": ("Note: {old} is the value of the purchase order as the assistant read it; {source} showed {value}. "
                                "Look in Exact which value goes back."),
        "hand_put_back_source:marks": "the list (an earlier change that was entered)",
        "hand_put_back_source:look": "the purchase order as the assistant last saw it",
        "hand_put_back_listed_empty": "Not put back in Exact yet. The list now holds: {row}. Empty the field in Exact and tick the row.",
        "hand_put_back_on_list": "The put-back is on the list already.",
        "hand_put_back_done": "This change was put back already.",
        "hand_put_back_unknown": "The assistant does not know the old price of this article; put it back in Exact yourself.",
        "hand_put_back_not_entered": "This change was not entered; there is nothing to put back in Exact.",
        "hand_put_back_cannot": "The assistant cannot put this change back; put the old value back in Exact yourself.",
        "hand_confirmations": "Confirmed by the supplier (last 7 days): attach the confirmation in Exact and set the status to Bevestigd",
        "hand_confirmation": "{date}: purchase order {number} ({supplier}), file {file}",
        "hand_confirmation_alone": "{date}: purchase order {number}, file {file}",
        "hand_attach_only": "attach only: something is still open",
        "hand_put_back_newer_open": ("Cannot put back now: a newer change is on the list, {row}. Drop that one first; then you can put this "
                                     "change back."),
        "hand_put_back_newer_entered": ("Cannot put back now: a newer change was entered, {row}. Put that one back first and tick its put-back "
                                        "off; then you can put this change back."),
        "hand_put_back_lost": ("Cannot put back now: the put-back would not reach the list. Nothing was written; pass this on to the "
                               "researcher."),
        "hand_put_back_not_needed": ("No need to put back: the list already holds a change to the old value, {row}. Enter it in Exact and "
                                     "tick it off."),
        "hand_put_back_newer_never": ("Cannot put back now: a newer change was entered, {row}, which the assistant cannot put back. Put the "
                                      "old value back in Exact yourself."),
        "hand_reopen_put_back": "This change was put back; it does not come back on the list.",
        "hand_done_choose_first": "Choose a change under Handled change first.",
        "hand_done_seen_export": "(seen in an export from Exact)",
        "files_tab": "Files",
        "files_title": "Give a file to the assistant",
        "files_intro": ("If a document does not come from your mailbox by itself, give it here: a purchase order from Exact (PDF) or an "
                        "order confirmation (PDF or e-mail). The assistant handles it at its next pass, within a minute."),
        "files_intro_export": ("If a document does not come from your mailbox by itself, give it here: a purchase order from Exact (PDF), "
                               "an order confirmation (PDF or e-mail) or an export from Exact (Excel or CSV). The assistant handles it at "
                               "its next pass, within a minute."),
        "files_choose": "Choose a file",
        "files_give": "Give to the assistant",
        "files_accepted": "{name} was accepted. Below you see within a minute what was done with it.",
        "files_refused:type": "{name} was not accepted: the assistant reads PDF, PNG and e-mail (.eml).",
        "files_refused:type_export": "{name} was not accepted: the assistant reads PDF, PNG, e-mail (.eml) and an export from Exact (Excel or CSV).",
        "files_refused:empty": "{name} was not accepted: the file is empty.",
        "files_refused:export": ("{name} was not accepted: the assistant reads an export from Exact only once its columns are set under "
                                 "Settings, Connection to Exact."),
        "files_choose_first": "Choose a file first.",
        "files_done": "Handled last",
        "files_column:time": "Time",
        "files_column:file": "File",
        "files_column:what": "What the assistant did",
        "files_outcome:po": "Purchase order {number} filed (version {version})",
        "files_outcome:export": "Export from Exact filed: {n} purchase orders, of which {m} new or changed",
        "files_outcome:export_one": "Export from Exact filed: 1 purchase order, of which {m} new or changed",
        "files_outcome:case": "Case opened for purchase order {number}",
        "files_outcome:case_unknown": "Case opened, purchase order unknown",
        "files_outcome:guess": "Case opened for purchase order {number}, a guess: check the case",
        "files_outcome:answer": "A supplier's answer filed with its case",
        "files_outcome:bounce": "A message of the assistant came back as undeliverable: filed with its case",
        "files_outcome:auto": "An automatic reply (an out-of-office notice, for example) filed with its case",
        "files_outcome:own": "Your own message filed with the case; this does not count as the supplier's answer",
        "files_outcome:set_aside": "Not read after {n} days: the assistant no longer tries this file",
        "files_outcome:recheck": "Purchase order {number} is in: the waiting case was checked again",
        "files_outcome:unread": "A scan without readable text: the assistant could not read this document; look at it yourself",
        "files_skipped:duplicate": "Received before: nothing done",
        "files_skipped:class": "No purchase order or order confirmation: nothing done",
        "files_skipped:type": "The assistant does not read this kind of file",
        "files_skipped:numbers": "More than one purchase order number found: look at this document yourself",
        "files_skipped:scan": "A scan without readable text: look at this document yourself",
        "files_skipped:no_attachment": "An e-mail without an attachment: nothing done",
        "files_skipped:not_an_export": "A spreadsheet without the export's column titles: nothing done",
        "files_skipped:tryout": "A try-out document outside the test mode: nothing done",
        "files_skipped:no_order_lines": "An export from Exact without order lines: nothing done",
        "files_skipped:other": "Skipped: nothing done",
        "files_error": "Could not be read. If it is a purchase order from Exact, check the layout under Settings, Connection to Exact.",
        "files_error:no_po_number": "Export from Exact not filed: a row has an item code but no purchase order number",
        "files_error:columns": "Export from Exact not filed: the columns of this file differ from those set",
        "files_error:cells": ("Export from Exact not filed: a row has another number of cells than the row of column titles. Save the file as "
                              "an Excel file (.xlsx) and give it again."),
        "files_error:sheets": ("Export from Exact not filed: the Excel file has more than one sheet with data. Save the sheet with the order "
                               "lines as a file of its own and give that."),
        "files_error:hidden_sheet": ("Export from Exact not filed: the Excel file holds data on a hidden sheet only. Unhide the sheet and give "
                                     "the file again."),
        "files_error:export": "Export from Exact not filed: check the columns under Settings, Connection to Exact.",
        "files_error:version": "Purchase order not filed: the assistant holds this version already from another file",
        "pricelist_tab": "Price list",
        "pricelist_unset": ("The columns of the export of the articles are not set yet. That is done at the installation: Settings, "
                            "Connection to Exact, part 3."),
        "pricelist_intro": ("Here you compare a supplier's price list with the purchase prices in Exact. For that the assistant needs the "
                            "export of that supplier's articles from Exact. What changes, you put on the list Enter in Exact with one press; "
                            "the assistant changes nothing in Exact."),
        "pricelist_supplier": "Supplier",
        "pricelist_supplier_help": "Choose the supplier, or type the name for a new supplier.",
        "pricelist_state": "Purchase prices of {supplier} filed on {time} from {file}: {articles}.",
        "pricelist_state_none": ("No purchase prices of {supplier} are filed yet. First give the export of this supplier's articles from "
                                 "Exact."),
        "pricelist_choose_supplier": "Choose a supplier first.",
        "pricelist_stored_unreadable": "The filed purchase prices cannot be read. Give the export of the articles again.",
        "pricelist_export": "Export of the articles from Exact (Excel or CSV)",
        "pricelist_file_prices": "File purchase prices",
        "pricelist_remove": "Remove this supplier's purchase prices",
        "pricelist_removed": "The stored purchase prices of {supplier} were removed. What already stands on the list Enter in Exact stays.",
        "pricelist_filed": "Purchase prices of {supplier} filed: {articles}; {passed}.",
        "pricelist_filed_suppliers": "Purchase prices filed of {m} suppliers: {articles}; {passed}.",
        "pricelist_articles_one": "1 article",
        "pricelist_articles_many": "{n} articles",
        "pricelist_passed_one": "1 row passed over",
        "pricelist_passed_many": "{k} rows passed over",
        "pricelist_refused:type": "{name} was not accepted: the assistant reads Excel (.xlsx) and CSV here.",
        "pricelist_refused:columns": "The columns of this file differ from those set: check Settings, Connection to Exact, part 3.",
        "pricelist_refused:supplier": "First choose the supplier whose articles these are.",
        "pricelist_refused:name": "The supplier's name is too long or holds characters that do not belong in a name.",
        "pricelist_refused:name_row": ("Row {row} of the export holds a supplier's name that is too long or holds characters that do not "
                                       "belong in a name. Nothing was filed."),
        "pricelist_refused:no_article": ("This export gave no article: no row has an item code and a price that can be read. Nothing was "
                                         "filed."),
        "pricelist_refused:heading": ("The column titles must stand in the first row of the file: remove the lines above them and give the "
                                      "file again."),
        "pricelist_list": "Price list (Excel or CSV)",
        "pricelist_column:item_code": "Column with the item code",
        "pricelist_column:price": "Column with the price",
        "pricelist_column:basis": "Column with the price per (may be empty)",
        "pricelist_column:valid_from": "Valid from (may be empty)",
        "pricelist_column:valid_to": "Valid to (may be empty)",
        "pricelist_columns_hint": ("If the list has a column for what a price is per (per 100, per box), point it out: otherwise the "
                                   "assistant reads every price as a price per piece."),
        "pricelist_problem:price": "Choose the column with the price.",
        "pricelist_view": "View",
        "pricelist_carry_out": "Put on the list Enter in Exact",
        "pricelist_header:article": "Article",
        "pricelist_header:current": "Purchase price in Exact",
        "pricelist_header:list": "Price on the list",
        "pricelist_header:outcome": "Outcome",
        "pricelist_outcome:changes": "price changes",
        "pricelist_outcome:unchanged": "unchanged",
        "pricelist_outcome:unknown_article": "article not known in Exact",
        "pricelist_outcome:unreadable_price": "price cannot be read",
        "pricelist_outcome:other_basis": "price per another number: not compared",
        "pricelist_outcome:not_yet_valid": "not valid yet",
        "pricelist_outcome:expired": "expired",
        "pricelist_outcome:duplicate": "duplicate row",
        "pricelist_outcome:no_item_code": "no item code",
        "pricelist_outcome:unreadable_valid_from": "valid from cannot be read",
        "pricelist_outcome:unreadable_valid_to": "valid to cannot be read",
        "pricelist_outcome:failed": "failed",
        "pricelist_outcome:shared": "also with supplier {name}: enter this price in Exact yourself",
        "pricelist_shared_more": "{names} and {n} more",
        "pricelist_outcome:differs": "differs strongly: check the column \"price per\"",
        "pricelist_outcome:ticked": "already ticked on the list Enter in Exact",
        "pricelist_outcome:dropped": "taken off the list: put the row back there if the price must be entered after all",
        "pricelist_row": "row {n}",
        "pricelist_counts": "{changes}, {same}, {passed}.",
        "pricelist_changes_one": "1 price changes",
        "pricelist_changes_many": "{a} prices change",
        "pricelist_same_one": "1 stays the same",
        "pricelist_same_many": "{b} stay the same",
        "pricelist_carried_one": "1 price change is on the list Enter in Exact.",
        "pricelist_carried_many": "{n} price changes are on the list Enter in Exact.",
        "pricelist_expired": "Some rows are expired: ask the supplier for a current list.",
        "pricelist_changed": "The data changed since you viewed the list. View the list again.",
        "pricelist_differs": ("A price more than three times as high or as low is not put on the list by the assistant: check the list, "
                              "and enter a real change in Exact yourself."),
        "pricelist_older": ("Note: for {articles} the list Enter in Exact still holds the price of an earlier price list of this supplier. "
                            "This list asks another price, which the assistant does not put on the list: take that row off the list there "
                            "and enter the right price in Exact yourself."),
        "pricelist_not_prepared": "Not put on the list: {articles}. View the list again to try once more.",
        "pricelist_columns_lost": "The columns of this list could not be kept: point them out again next time.",
        "pricelist_log_lost": ("The assistant could not write these changes into its own log of the price list, so it cannot put them back "
                               "itself later: pass this on to the researcher."),
        "settings_tab": "Settings",
        "settings_share": "Not available on a public link",
        "settings_no_file": "Not available: the page was started without a settings file",
        "settings_intro": ("With a Microsoft sign-in the check reads a folder of the mailbox itself and sends its e-mails from that "
                           "mailbox. Without a sign-in it works as before, from the watched folder."),
        "settings_tenant": "Directory (tenant) ID",
        "settings_client": "Application (client) ID",
        "settings_sign_in": "Sign in with Microsoft",
        "settings_sign_in_code": "Sign in with a code",
        "settings_browser_note": ("Sign in with Microsoft opens a browser window with Microsoft's sign-in page on the PC where this page "
                                  "runs. If no window opens, choose Sign in with a code."),
        "settings_waiting": ("A window of Microsoft opens now: sign in there. This page waits for it for at most five minutes; a "
                             "second press of the button waits until then."),
        "settings_browser_timeout": "No sign-in within five minutes. If no window opened, choose Sign in with a code.",
        "settings_code": "Go to {address} and enter the code {code}. This page waits until the sign-in is done.",
        "settings_signed_in": "Signed in as {account}",
        "settings_not_signed_in": "Not signed in",
        "settings_lapsed": "The sign-in of {account} has lapsed: sign in again",
        "settings_store_refused": "The protected store on this PC was refused",
        "settings_sign_out": "Sign out",
        "settings_signed_out": "Signed out.",
        "settings_sign_in_failed": "The sign-in did not succeed ({code}).",
        "settings_ids_needed": "Fill in a valid tenant ID and client ID first.",
        "settings_mailbox": "Mailbox (empty: your own)",
        "settings_folder": "Folder",
        "settings_folders": "Load the folders",
        "settings_folders_loaded": "Folders loaded: {count}.",
        "settings_folder_needed": "Choose a folder first.",
        "settings_folders_first": "Press Load the folders for this mailbox first and choose a folder.",
        "settings_test": "Test the connection",
        "settings_tested": "Messages in the folder: {count}; newest received: {newest}.",
        "settings_tested_empty": "The folder is empty.",
        "settings_graph_failed": "Microsoft gave no usable answer ({code}).",
        "settings_failed": "This did not work ({code}).",
        "settings_send_via": "Send through",
        "settings_via:auto": ("Automatic: the mailbox when you are signed in and a folder is chosen (while the sign-in has lapsed, "
                              "nothing is sent), else the SMTP server or files"),
        "settings_via:file": "Files: send nothing",
        "settings_via:smtp": "The SMTP server of the settings",
        "settings_via:microsoft": "The mailbox of the sign-in",
        "settings_save": "Save",
        "settings_not_saved": "Not saved: check {fields}.",
        "settings_trigger": "State of the check",
        "settings_trigger_off": "The check reads no mailbox: it works from the watched folder.",
        "settings_trigger_pending": "Not connected yet: the check connects at its next pass.",
        "settings_trigger_on": "Connected since {since}; last read: {last}; messages saved: {saved}.",
        "settings_trigger_unread": "Connected since {since}; not read yet.",
        "settings_ai": "The AI service: Microsoft 365 Copilot",
        "settings_ai_needs": ("Copilot needs: a Microsoft 365 Copilot licence on the account; the seven permissions "
                              "Sites.Read.All, Mail.Read, People.Read.All, OnlineMeetingTranscript.Read.All, Chat.Read, "
                              "ChannelMessage.Read.All and ExternalItem.Read.All on the app registration; and for four of them "
                              "(People.Read.All, OnlineMeetingTranscript.Read.All, ChannelMessage.Read.All and ExternalItem.Read.All) "
                              "an administrator's consent, once."),
        "settings_ai_sign_in": "Sign in for Copilot",
        "settings_ai_signed_in": "Signed in for the mailbox and for Copilot.",
        "settings_ai_test": "Test Copilot",
        "settings_ai_answer": "Copilot answers: {answer}",
        "settings_ai_failed": "The test of Copilot did not succeed: {error}",
        "settings_ai_reader": "Read documents with",
        "settings_reader:llm": "Copilot",
        "settings_reader:parser": "without AI (parser)",
        "settings_ai_ids_needed": "Save the tenant ID and the client ID first; then Copilot can read the documents.",
        "settings_connections": "Connections: the Microsoft mailbox and the AI service",
        "settings_extension_code": "Code for the browser extension",
        "settings_extension_code_help": "Paste this code into the options of the Inkoopassistent extension in Edge or Chrome.",
        "settings_file_unreadable": ("The settings file cannot be read{fields}. Below are the settings the aid uses now. Saving works "
                                     "again once the file is repaired."),
        "settings_file_fields": ": check {fields}",
        "settings_file_write_route": ('exact_base_url is set, but this version of the aid writes nothing in Exact: every change is '
                                      'prepared for the tab "In Exact invoeren". Remove exact_base_url from the settings file. Below are '
                                      'the settings the aid uses now. Saving works again once the file is repaired.'),
        "settings_group:handled": "What the aid may settle by itself",
        "settings_group:questions": "Questions to suppliers",
        "settings_group:digest": "Digest and language",
        "settings_group:approval": "Approval",
        "settings_field:price_pct": "Price difference: at most this percentage",
        "settings_help:price_pct": ("The threshold for the price: when a price difference is at most this percentage of the purchase "
                                    "order's price and at most the amount below per line, the aid takes the supplier's price over on the "
                                    "purchase order and changes the article's price too; a difference above the threshold comes to you."),
        "settings_field:price_abs": "Price difference: at most this amount per line (euro)",
        "settings_help:price_abs": ("The amount of the threshold: the difference per piece times the quantity of the line; if that is "
                                    "more than this amount, the difference comes to you, even when it stays within the percentage."),
        "settings_field:lead_time_tolerance_days": "Delivery date: days on top of the lead time",
        "settings_help:lead_time_tolerance_days": ("The margin in days: a later delivery date is taken over up to and including the "
                                                   "supplier's lead time plus this margin, counted from the delivery date of the purchase "
                                                   "order's line; a still later date comes to you, and without a known lead time of the "
                                                   "supplier every later date comes to you. An earlier delivery date is taken over, also "
                                                   "without a known lead time."),
        "settings_field:first_cases_review": "First cases of a supplier",
        "settings_help:first_cases_review": ("Until this many cases of a supplier are closed, every case of that supplier comes to you, "
                                             "whatever it holds; 0 turns this off. On the tab \"Review\" the reason reads \"New supplier\"."),
        "settings_field:ask_wait_working_days": "Wait for an answer (working days)",
        "settings_help:ask_wait_working_days": ("This many working days the aid waits for a supplier's answer to a question; without "
                                                "an answer the case comes to you."),
        "settings_field:buyer_name": "Your name under the messages",
        "settings_help:buyer_name": ("This name stands under \"Purchasing\", or under \"Inkoop\" when the language of the aid is "
                                     "Nederlands, in the questions to suppliers and in the request to the approver; empty: \"Purchasing\" "
                                     "or \"Inkoop\" alone."),
        "settings_field:digest": "Digest",
        "settings_help:digest": "One digest a day from the time below, or a digest after every case.",
        "settings_digest:daily": "Every day at a set time",
        "settings_digest:per_case": "After every case",
        "settings_field:digest_time": "Time of the digest (HH:MM)",
        "settings_help:digest_time": "The digest comes at the aid's first pass from this time.",
        "settings_field:buyer_email": "Your e-mail address",
        "settings_help:buyer_email": ("The digest goes here, and you are in copy of every question to a supplier and of the request to "
                                      "the approver."),
        "settings_field:language": "Language of the aid",
        "settings_help:language": ("The language of the digest, of the messages to suppliers and to the approver, and of the banner in the "
                                   "webshop."),
        "settings_language:nl": "Nederlands",
        "settings_language:en": "English",
        "settings_field:approver_email": "The approver's e-mail address",
        "settings_help:approver_email": ("When the aid changes a purchase order above the authorization limit, it asks this person to "
                                         "approve the order again."),
        "settings_limit": "The authorization limit itself is set at the installation and is not shown on this page.",
        "settings_buyer_save": "Save",
        "settings_buyer_saved": "Saved. The aid uses this from its next pass, within a minute.",
        "settings_buyer_unchanged": "Nothing was changed; nothing was saved.",
        "settings_buyer_refilled": ("Not saved: the page has filled the form again with the settings as they are now. Enter your change "
                                    "again and then save."),
        "settings_buyer_refused": "Not saved: {fields}.",
        "settings_suppliers": "Suppliers",
        "settings_suppliers_help": ("Per supplier, with the name as the purchase order gives it: the e-mail address for the aid's "
                                    "questions (without an address the aid asks nothing and the case comes to you), the lead time in "
                                    "days, whether every case comes to you, and whether the aid sends this supplier no questions by "
                                    "itself: those questions then come to you, and a question you choose yourself on the tab "
                                    "\"Review\" is still sent to the supplier."),
        "settings_supplier_column:name": "Supplier",
        "settings_supplier_column:email": "E-mail address",
        "settings_supplier_column:lead_time": "Lead time (days)",
        "settings_supplier_column:review": "Always to you",
        "settings_supplier_column:messages_off": "No questions by itself",
        "settings_tick": "yes",
        "settings_supplier_choice": "Supplier",
        "settings_supplier_new": "Add a supplier",
        "settings_supplier_name": "Name (as on the purchase order)",
        "settings_supplier_email": "E-mail address for questions",
        "settings_supplier_lead_time": "Lead time in days",
        "settings_supplier_review": "Every case of this supplier comes to you",
        "settings_supplier_messages_off": "The aid sends this supplier no questions by itself",
        "settings_supplier_save": "Save the supplier",
        "settings_supplier_remove": "Remove the supplier",
        "settings_supplier_choose": "Choose a supplier first.",
        "settings_supplier_listed": ("Not saved: a supplier with this name is already listed. Choose that supplier under \"Supplier\" to "
                                     "change it."),
        "settings_supplier_empty": "Not saved: a supplier needs an e-mail address, a lead time or a tick.",
        "settings_problem:percent": "a number from 0 to 100",
        "settings_problem:amount": "a number of 0 or more",
        "settings_problem:decimals": "at most two decimals (write a thousand as 1000, without a point or a comma)",
        "settings_problem:days": "a whole number from 0 to 365",
        "settings_problem:cases": "a whole number from 0 to 1000",
        "settings_problem:wait": "a whole number from 1 to 60",
        "settings_problem:digest": "every day or after every case",
        "settings_problem:time": "a time from 00:00 to 23:59",
        "settings_problem:address": "an e-mail address",
        "settings_problem:name": "one line of at most 80 characters",
        "settings_problem:supplier_name": "a name of 1 to 80 characters",
        "settings_problem:language": "Nederlands or English",
        "exact_title": "Connection to Exact (at the installation)",
        "exact_intro": ("The assistant reads what Exact itself delivers and changes nothing in Exact. Here you set what the documents from "
                        "Exact look like. Your sample files stay on this PC."),
        "exact_part_po": "1. Purchase orders that Exact sends by e-mail (PDF)",
        "exact_layout": "Layout of the purchase order",
        "exact_state_title": "State of the connection",
        "exact_refresh": "Refresh the state",
        "exact_state_set": "Layout of the purchase order: set.",
        "exact_state_unset": "Layout of the purchase order: not set yet. Until then the assistant reads the made-up sample purchase orders only.",
        "exact_state_known": "Purchase orders the assistant knows: {count}. Filed last: purchase order {number}, version {version}, {time}.",
        "exact_state_known_export": "Purchase orders the assistant knows: {count}. Filed last: purchase order {number} from an export, {time}.",
        "exact_state_none": "The assistant knows no purchase orders yet.",
        "exact_state_unread": "Files in the watched folder that the assistant could not read: {n}.",
        "exact_sample": "Sample: a purchase order from Exact as a PDF",
        "exact_page": "The first page of the sample, line by line",
        "exact_page_line": "Line",
        "exact_page_text": "Text",
        "exact_page_more": "The table shows the first {n} lines of the first page.",
        "exact_field:po_label": "Text before the purchase order number",
        "exact_help:po_label": ("The word that stands right before the number on the PDF. Copy it from the lines above. It may also be several "
                                "words, such as \"Inkooporder nr.\": the assistant looks for them together, as a whole."),
        "exact_help:label": ("The text that stands right before the value on the PDF; copy it from the lines above. It may also be several "
                             "words, such as \"Inkooporder nr.\": the assistant looks for them together, as a whole."),
        "exact_field:date_label": "Text before the date (may be empty)",
        "exact_field:supplier_label": "Text before the supplier",
        "exact_field:supplier_line": "Or: the number of the line that holds the supplier alone",
        "exact_field:version_label": ("Text before the version number (empty: the PDF names no version; the assistant then numbers in the order "
                                      "of arrival)"),
        "exact_field:currency_label": "Text before the currency (may be empty)",
        "exact_field:incoterm_label": "Text before the delivery terms (may be empty)",
        "exact_field:title_word": "A word from the title row above the order lines, for example Artikel",
        "exact_find": "Find title row",
        "exact_title_found": "Title row found. Choose for each word what its column holds.",
        "exact_title_missing": "No line of the first page holds this word.",
        "exact_title_long": "The title row has more than 20 words, which this form cannot take.",
        "exact_title_saved_long": ("The saved title row has more than 20 words, which this form cannot show. The layout stays in force "
                                   "until you save a new one."),
        "exact_meaning:line_id": "Line number",
        "exact_meaning:item_code": "Item code",
        "exact_meaning:description": "Description",
        "exact_meaning:quantity": "Quantity",
        "exact_meaning:unit": "Unit",
        "exact_meaning:unit_price": "Price",
        "exact_meaning:price_basis": "Price per (number)",
        "exact_meaning:discount_pct": "Discount in percent",
        "exact_meaning:net_price": "Net price",
        "exact_meaning:delivery_date": "Delivery date",
        "exact_meaning:same": "Belongs to the column on its left",
        "exact_meaning:skip": "Not needed",
        "exact_field:table_end": "The table ends at the row with this text (empty: to the end)",
        "exact_help:table_end": ("Copy the whole text of the row right under the last order line, for example the total row. A subtotal per "
                                 "page with the same text ends the table on that page."),
        "exact_field:numbers": "Numbers on the PDF",
        "exact_numbers:comma": "1.234,50 (decimal comma)",
        "exact_numbers:point": "1,234.50 (decimal point)",
        "exact_field:per_piece": "The PDF has no price-per column: every price is per one unit",
        "exact_field:senders": "Sender of the e-mails with purchase orders from Exact",
        "exact_help:senders": ("The e-mail address from which Exact sends the purchase orders, or only the part after the at sign; several with "
                               "a comma between them. Fill this in: a PDF in this layout from another sender is then handled as an order "
                               "confirmation, so that a confirmation that looks like your purchase order is never filed as a purchase "
                               "order."),
        "exact_test": "Test",
        "exact_save": "Save",
        "exact_reading": "This is how the assistant reads the sample",
        "exact_read": ("The sample was read: {n} order lines; the total can be worked out: {answer}. Compare the table with the purchase "
                       "order in Exact. If all is right, press Save."),
        "exact_yes": "yes",
        "exact_no": "no",
        "exact_read_what": "What",
        "exact_read_value": "Value read",
        "exact_header:po_reference": "Purchase order number",
        "exact_header:version_hint": "Version",
        "exact_header:document_date": "Date",
        "exact_header:supplier": "Supplier",
        "exact_header:currency": "Currency",
        "exact_header:incoterm": "Delivery terms",
        "exact_problem:no_sample": "Choose a sample first.",
        "exact_problem:scan": "This PDF has no text (a scan or a picture); the assistant cannot read it.",
        "exact_problem:unopenable": "This PDF cannot be opened. Save it again from the e-mail and choose it once more.",
        "exact_problem:not_pdf": ("This file is no PDF: its name does not end in .pdf. Choose the PDF of a purchase order as Exact "
                                  "sends it."),
        "exact_problem:po_label": "Fill in the text before the purchase order number.",
        "exact_problem:supplier": "Fill in the text before the supplier or the line number, not both.",
        "exact_problem:supplier_line": "The number of the supplier's line is a whole number from 1.",
        "exact_problem:no_title": "Find the title row first: fill in a word of it and press Find title row.",
        "exact_problem:short_title": "This title row has one word only; the title row above the order lines has two at least.",
        "exact_problem:meanings": "Choose for each word of the title row what its column holds.",
        "exact_problem:first_same": "The first word of the title row cannot belong to the column on its left.",
        "exact_problem:twice": "Two columns have the same meaning: {meaning}.",
        "exact_problem:item_code": "Choose one column as Item code.",
        "exact_problem:quantity": "Without a Line number column a Quantity column is needed.",
        "exact_problem:title_differs": "The title row of the sample differs from the one you filled in.",
        "exact_problem:no_number": "No purchase order number found: check the text before the number.",
        "exact_problem:two_pos": ("The text before the purchase order number is followed by different numbers, on {places}: so the assistant "
                                  "would take this PDF for more than one purchase order, and it does not file it. If only one of those lines "
                                  "holds the purchase order number, copy a longer text before the number that stands only there, such as "
                                  "\"Inkooporder:\"."),
        "exact_place:line": "line {lines} of the first page",
        "exact_place:lines": "lines {lines} of the first page",
        "exact_place:page": "page {pages}",
        "exact_place:pages": "pages {pages}",
        "exact_and": "and",
        "exact_problem:folder": ("The purchase order number after the text before the number cannot be the name of a folder; the assistant "
                                 "cannot file this purchase order."),
        "exact_problem:confirmation": ("Line {line} of the first page, above the title row, holds a word of an order confirmation; the "
                                       "assistant would take this PDF for an order confirmation."),
        "exact_problem:no_lines": "No order lines found: check the title row and the columns.",
        "exact_problem:no_item_code": "Line {line}: no item code read.",
        "exact_problem:no_quantity": "Line {line}: no quantity read.",
        "exact_problem:convention_comma": ("The column {column} holds numbers with a decimal comma (such as 12,50): choose "
                                           "\"1.234,50 (decimal comma)\"."),
        "exact_problem:convention_point": ("The column {column} holds numbers with a decimal point (such as 12.50): choose "
                                           "\"1,234.50 (decimal point)\"."),
        "exact_problem:refused": "The assistant cannot file this sample with this layout.",
        "exact_problem:sender": "This is neither an e-mail address nor a domain: {text}.",
        "exact_warning:passed": ("Line {line} of the first page: the text before the purchase order number is followed by no word with a "
                                 "digit; the assistant reads no number there."),
        "exact_warning:continued": ("Page {page}: the title row is not on this page; the assistant reads its rows with the columns of the title "
                                    "row on an earlier page."),
        "exact_warning:ended": ("Page {page}: the title row is not on this page and the table ended on an earlier page; the assistant does "
                                "not read this page."),
        "exact_saved": ("Saved. The assistant reads purchase orders in this layout from its next pass, within a minute. Purchase orders "
                        "that still lie in the watched folder because it could not read them are then filed; give any others again."),
        "exact_not_saved": "Not saved: {problems}",
        "exact_changed": "Not saved: the connection was changed elsewhere meanwhile. Open the tab Settings again and check the layout.",
        "exact_field:po_words": "Words after which suppliers print your purchase order number",
        "exact_help:po_words": "With a comma between them, for example: Inkooporder, Uw referentie, Uw ordernummer.",
        "exact_field:po_digits_least": "A purchase order number has at least this many digits",
        "exact_field:po_digits_most": "and at most this many digits",
        "exact_po_number_save": "Save number recognition",
        "exact_problem:po_words": "fill in at least one word",
        "exact_problem:po_digits": "a whole number from 1 to 20, the smallest not above the largest",
        "exact_po_number_own": "own pattern (in the settings file)",
        "exact_numbers_title": "How the assistant finds the purchase order number on an order confirmation",
        "exact_number_changed": ("Not saved: the number recognition was changed elsewhere meanwhile. Open the tab Settings again and check "
                                 "the words and the digits."),
        "exact_state_waiting": "Cases that wait for their purchase order: {n}.",
        "exact_part_export": "2. Purchase orders as an export from Exact (for the check before ordering)",
        "exact_export_intro": ("Before ordering the PDF of the purchase order does not exist yet. Then give the assistant an export of the "
                               "order lines from Exact (Excel or CSV). Here you set once which column holds what."),
        "exact_export_columns": "Columns of the export",
        "exact_state_columns_set": "Columns of the export: set.",
        "exact_state_columns_unset": "Columns of the export: not set yet.",
        "exact_state_export": "Last export filed: {time}.",
        "exact_state_no_export": "No export filed yet.",
        "exact_export_sample": "Sample: an export of order lines from Exact",
        "exact_export_head": "The first rows of the sample",
        "exact_export_row": "Row",
        "exact_export_header_row": "Row with the column titles",
        "exact_export_field:po_number": "Purchase order number (required)",
        "exact_export_field:item_code": "Item code (required)",
        "exact_export_no_column": "(not in the file)",
        "exact_export_help:line_id": "Without a column the assistant counts the lines of each purchase order in the order of the file.",
        "exact_export_field:numbers": "Numbers that stand in the file as text",
        "exact_export_field:fill_down": "The purchase order number stands on the first line of each purchase order only",
        "exact_export_read": "Read: {orders}; {passed}. {below}",
        "exact_export_orders_one": "1 purchase order with {lines}",
        "exact_export_orders_many": "{n} purchase orders with {lines} in all",
        "exact_export_lines_one": "1 line",
        "exact_export_lines_many": "{m} lines",
        "exact_export_passed_one": "1 row without an item code passed over",
        "exact_export_passed_many": "{k} rows without an item code passed over",
        "exact_export_first": "Below is the first purchase order: compare it with Exact.",
        "exact_export_first_and_last": "Below are the first and the last purchase order: compare them with Exact.",
        "exact_export_no_quantity": ("{count} rows with an item code but no quantity passed over (first: row {row}). If they are order lines, "
                                     "choose the column with the ordered quantity for \"{field}\"."),
        "exact_export_no_quantity_one": ("1 row with an item code but no quantity passed over (row {row}). If it is an order line, choose the "
                                         "column with the ordered quantity for \"{field}\"."),
        "exact_export_unread": "Not read: {count} values in the column {column} (first: row {row}). The assistant does not compare those values.",
        "exact_export_unread_one": "Not read: 1 value in the column {column} (row {row}). The assistant does not compare that value.",
        "exact_export_problem:po_number": "Choose the column with the purchase order number.",
        "exact_export_problem:item_code": "Choose the column with the item code.",
        "exact_export_problem:more_than_once": "The column {column} was chosen more than once.",
        "exact_export_problem:no_lines": ("The sample gave no order line: no row below the column titles has an item code and, if you chose "
                                          "that column, a quantity. Check the columns chosen."),
        "exact_export_problem:item_code_date": ("In {count} rows the item code is a date, a time or an error value (first: row {row}), so the "
                                                "assistant passes those rows over. Choose the column with the item code; if it is right, check "
                                                "those rows in the file: Excel can turn a code into a date."),
        "exact_export_problem:item_code_date_one": ("In 1 row the item code is a date, a time or an error value (row {row}), so the assistant "
                                                    "passes that row over. Choose the column with the item code; if it is right, check that row "
                                                    "in the file: Excel can turn a code into a date."),
        "exact_export_unopenable": "The sample can no longer be opened. Choose the file once more.",
        "exact_export_refused:map": ("The columns chosen are not complete: choose at least the column with the purchase order number and the "
                                     "column with the item code."),
        "exact_export_refused:file_type": "This file is no Excel or CSV file. Save an old Excel file (.xls) as .xlsx in Excel first.",
        "exact_export_refused:unreadable": ("The assistant cannot read this file: it is damaged, or a cell holds what its type does not allow. "
                                            "Open it in Excel, save it again and choose it once more."),
        "exact_export_refused:unreadable_row": ("Row {row}: the assistant cannot read a cell of this file. Open it in Excel, save it again and "
                                                "choose it once more."),
        "exact_export_refused:delimiter_or_encoding": ("The assistant cannot cut this CSV file into columns: it uses another separator or "
                                                       "another character set than the assistant reads. Save it as .xlsx in Excel and choose "
                                                       "that file."),
        "exact_export_refused:no_column": ("This file lacks the column {column}. Choose a column of this file for that field, or \"(not in the "
                                           "file)\"."),
        "exact_export_refused:double_column": ("Row {row}: the column {column} stands twice in the row with the column titles, so the assistant "
                                               "does not know which one is meant. Choose a column whose title stands there once."),
        "exact_export_refused:no_po_number": ("Row {row}: an item code without a purchase order number. If the number stands on the first line "
                                              "of each purchase order only, tick that."),
        "exact_export_refused:po_number_date": ("Row {row}: the purchase order number is a date, a time or an error value. Choose the column "
                                                "with the purchase order number; if that is right, check that row in the file."),
        "exact_export_refused:po_number_no_digit": ("Row {row}: the purchase order number holds no digit. Choose the column with the purchase "
                                                    "order number; if that is right, check that row in the file."),
        "exact_export_refused:po_number_no_folder": ("Row {row}: this purchase order number cannot be the name of a folder, so the assistant "
                                                     "cannot file it. Choose the column with the purchase order number; if that is right, "
                                                     "check that row in the file."),
        "exact_export_refused:no_line_id": ("Row {row}: an item code without a line number. Choose the column with the line number, or choose "
                                            "\"(not in the file)\": the assistant then counts the lines itself."),
        "exact_export_refused:line_id_date": ("Row {row}: the line number is a date, a time or an error value. Choose the column with the line "
                                              "number, or choose \"(not in the file)\": the assistant then counts the lines itself."),
        "exact_export_refused:double_line_id": ("Row {row}: this line number stands on an earlier line of the same purchase order already. "
                                                "Choose the column with the line number, or choose \"(not in the file)\": the assistant then "
                                                "counts the lines itself."),
        "exact_export_refused:surplus_cells": ("Row {row} of this file has more cells than the row of column titles. Usually a number or a "
                                               "description then holds a comma without quotation marks. Save the file as an Excel file (.xlsx) "
                                               "or with semicolons as the separator, and give it again."),
        "exact_export_refused:cell_count": ("Row {row} of this file has another number of cells than the row of column titles. In a file with "
                                            "commas as the separator a number or a description then usually holds a comma without quotation "
                                            "marks. Save the file as an Excel file (.xlsx) or with semicolons as the separator, and give it "
                                            "again."),
        "exact_export_refused:hidden_sheet": ("This Excel file holds data on a hidden sheet only. The assistant reads no hidden sheets: unhide the "
                                              "sheet, or save it as a file of its own, and give it again."),
        "exact_export_refused:several_sheets": ("This Excel file has more than one sheet with data ({names}). The assistant reads one sheet: "
                                                "save the sheet you mean as a file of its own and give that."),
        "exact_export_saved": "Saved.",
        "exact_export_changed": "Not saved: the connection was changed elsewhere meanwhile. Open the tab Settings again and check the columns.",
        "exact_part_articles": "3. Purchase prices of articles as an export from Exact (for the price list)",
        "exact_articles_intro": ("Choose a sample of the export of articles from Exact and point out the columns. If the export has no column "
                                 "with the supplier, you choose the supplier when you give the export on the tab Price list."),
        "exact_articles_sample": "Sample: an export of articles from Exact",
        "exact_articles_columns": "Columns of the export of the articles",
        "exact_articles_field:item_code": "Item code (required)",
        "exact_articles_field:price": "Purchase price (required)",
        "exact_articles_field:supplier": "Supplier",
        "exact_articles_price": "Purchase price",
        "exact_articles_problem:price": "Choose the column with the purchase price.",
        "exact_articles_problem:none": ("The sample gave no article: no row below the column titles has an item code and a price that can be "
                                        "read."),
        "exact_articles_read": "{articles} read; {passed}.",
        "exact_articles_read_reasons": "{articles} read; {passed} ({reasons}).",
        "exact_articles_left:no_item_code": "no item code",
        "exact_articles_left:no_supplier": "no supplier",
        "exact_articles_left:no_price": "price cannot be read",
        "exact_articles_left:twice": "article twice",
        "exact_state_to_enter": "Still to enter in Exact: {n}.",
        "hand_test_mode": "Test mode: enter these changes in the test Exact only.",
        "hand_tryout": "(try-out document)",
        "tryout_title": "Trying out with a purchase order from Exact",
        "tryout_intro": ("Without a supplier you can still try the assistant: for a purchase order it knows, the assistant makes a made-up "
                         "order confirmation. For the pre-check in the browser the assistant makes a made-up cart of the same purchase "
                         "order. You choose what differs. The try-out document stays on this PC."),
        "tryout_switch": "Test mode: the assistant works with the test Exact",
        "tryout_saved": "Saved.",
        "tryout_saved_open": ("Saved. The list Enter in Exact still holds {n} changes from try-out documents. Do not enter them in the real "
                              "Exact; drop them."),
        "tryout_saved_open_one": "Saved. The list Enter in Exact still holds 1 change from a try-out document. Do not enter it in the real Exact; drop it.",
        "tryout_switch_refilled": "The test mode was changed elsewhere meanwhile; the switch now shows what the settings file holds.",
        "tryout_not_offered": "Trying out is possible only in the test mode and while no authorization limit is set.",
        "tryout_po": "Purchase order",
        "tryout_change": "What differs?",
        "tryout_change:none": "Nothing: everything agrees",
        "tryout_change:price_small": "The price of the first line is 1 percent higher",
        "tryout_change:price_large": "The price of the first line is 10 percent higher",
        "tryout_change:date_later": "The delivery date of the first line is three days later",
        "tryout_change:date_much_later": "The delivery date of the first line is sixty days later",
        "tryout_make": "Make try-out confirmation (PDF)",
        "tryout_give": "Give the try-out confirmation to the assistant directly",
        "tryout_made": ("The try-out confirmation was made. Send it as an attachment to your own mailbox, to the folder the assistant reads, "
                        "or give it to the assistant directly."),
        "tryout_given": "The try-out confirmation lies in the watched folder. The assistant opens the case at its next pass, within a minute.",
        "tryout_cart_change": "What differs in the try-out cart?",
        "tryout_cart_change:none": "Nothing",
        "tryout_cart_change:price_small": "The price of the first line is 1 percent higher",
        "tryout_cart_make": "Make try-out cart",
        "tryout_cart_made": ("The try-out cart is ready. Open {link} in the browser that holds the assistant's extension: the bar at the top "
                             "of the page shows the outcome."),
        "tryout_none_known": "The assistant knows no purchase orders yet. Let a purchase order from Exact come in first.",
        "tryout_refused": "No try-out document can be made of this purchase order: {reason}.",
        "tryout_reason:no_lines": "it has no order lines",
        "tryout_reason:no_price": "the first lines have no price",
        "tryout_reason:no_date": "the first lines have no delivery date",
        "tryout_reason:character": "a text holds a character the try-out document cannot print",
        "tryout_reason:item_code": "an item code is too long",
        "tryout_reason:value": "a value does not fit the try-out document",
        "tryout_reason:read_back": "the try-out document would not read back exactly as the purchase order",
        "tryout_reason:not_found": "the assistant would not find the purchase order of the try-out document",
        "tryout_reason:version": "the assistant would not know which version of the purchase order to compare with",
        "tryout_nothing_sent": "This is a try-out document: the assistant sends nothing about it to a supplier.",
        "item_one": "1 item",
        "items_many": "{n} items",
        "unknown_po": "unknown",
        "line_word": "line",
        "article_word": "article",
        "status:closed": "closed",
        "status:review": "for review",
        "status:waiting": "waiting for the supplier",
        "status:failed": "failed",
        "field:price": "Price",
        "field:quantity": "Quantity",
        "field:unit": "Unit",
        "field:delivery_date": "Delivery date",
        "field:line_presence": "Line present",
        "field:required_info": "Required information",
        "pair:matched": "present",
        "pair:split": "split",
        "pair:merged": "merged",
        "kind:po_line_price": "Price of the PO line",
        "kind:po_line_date": "Delivery date of the PO line",
        "kind:po_line_quantity": "Quantity of the PO line",
        "kind:article_price": "Article price",
        "kind:attach_document": "Confirmation attached",
        "kind:detach_document": "Confirmation removed",
        "kind:set_status": "Status of the purchase order",
        "reason:different": "Different",
        "reason:missing on the document": "Missing on the document",
        "reason:missing line": "Line missing",
        "reason:extra line": "Extra line",
        "reason:not in cart": "Not in the cart",
        "reason:later": "Later than ordered",
        "reason:earlier": "Earlier than ordered",
        "reason:different currency": "Another currency",
        "reason:currency missing": "Currency missing on the document",
        "reason:unknown price basis": "Price basis unknown",
        "reason:unknown unit conversion": "Unit conversion unknown",
        "reason:total only": "A total amount only",
        "reason:match uncertain": "Line match uncertain",
        "reason:unreadable or unverified source": "Source unreadable or not verified",
        "reason:no PO date": "No delivery date on the purchase order",
        "reason:merged line": "Merged line",
        "reason:no PO price": "No price on the purchase order",
        "reason:no PO quantity": "No quantity on the purchase order",
        "reason:no PO unit": "No unit on the purchase order",
        "reason:new supplier": "New supplier",
        "reason:supplier marked for review": "Supplier marked for review",
        "reason:no version selected": "No version of the purchase order chosen",
        "reason:PO found otherwise than by the number recognition": ("Check that this is the right purchase order: the assistant did not "
                                                                     "find it by the number after a word of the number recognition."),
        "reason:the list of changes to enter in Exact could not be read": ("The list Enter in Exact could not be read: the assistant "
                                                                           "compares with the purchase order as it was filed."),
        "reason:PO chosen by the items in the cart": "Check that this is the right purchase order: the assistant chose it by the items in the cart.",
        "reason:split line: parts differ": "Split line: the parts differ",
        "reason:no delivery date on the document": "No delivery date on the document",
        "reason:no lead time known for the supplier": "No lead time known for the supplier",
        "reason:delivery date beyond the lead time plus the tolerance": "Delivery date later than the lead time plus the margin",
        "reason:no price on the document": "No price on the document",
        "reason:quantity unknown for the price rule": "Quantity unknown for the price check",
        "reason:price above threshold": "Price difference above the threshold",
        "reason:PO total unknown": "Purchase order total unknown",
        "reason:cart: for the buyer": "Cart: for the buyer",
        "reason:messages off for this supplier": "The aid sends this supplier no questions by itself",
        "reason:could not be placed": "Could not be placed in the cart",
        "reason:not covered by a rule": "No set way of handling this difference",
        "reason:prepared change: apply by hand": "Prepared change: apply it by hand",
        "reason:write failed: *": "Write failed",
        "reason:write made, not verified: check in Exact": ("Change made in Exact, but the check after it failed: look in Exact whether "
                                                            "the value is right"),
        "reason:write outcome not known: check in Exact": ("Not sure whether the change was made in Exact: look in Exact which value is "
                                                           "there"),
        "reason:not applied: an earlier write failed *": "Not applied: an earlier write failed",
        "reason:not applied: an earlier change waits for a look in Exact *": "Not applied: look in Exact at the earlier change first",
        "reason:no address for the supplier": "No e-mail address of the supplier",
        "reason:message not sent": "Message not sent",
        "reason:try-out document: nothing is sent": "This is a try-out document: the assistant sends nothing about it to a supplier.",
        "reason:message bounced": "Message bounced",
        "reason:no answer in *": "No answer within the wait",
        "reason:supplier answered: read the reply": "The supplier answered: read the reply",
        "reason:no question is open": "No question is open any more: decide on this case",
        "reason:other": "Another reason",
    },
}


def reason_label(reason: str, strings: dict[str, str]) -> str | None:
    """The label of a reason among the strings of a language (STRINGS["nl"] or STRINGS["en"]): that of "reason:<reason>", else that
    of the first key ending in " *" whose text the reason begins with; None for a reason without a label."""
    if (label := strings.get(f"reason:{reason}")) is not None:
        return label
    for key, label in strings.items():
        if key.startswith("reason:") and key.endswith(" *") and reason.startswith(key[len("reason:"):-1]):
            return label
    return None
