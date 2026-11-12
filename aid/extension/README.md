# The browser extension of the checking aid: a passive pre-check of the webshop cart

The extension sends the webshop cart page the buyer has open to the aid's capture server on this computer
(`python3 -m aid.capture_server`), and shows the answer in a banner at the top of the page before the buyer
presses order. It is a Manifest V3 extension for Edge and Chrome, without a framework, a build step or any
resource from the internet. It is for the test account in the BEP; using it on the company's real accounts may
need another request to IT.

## What it sends, and to where

- On a page of a listed shop (and on the test shop, `http://127.0.0.1` and `http://localhost`), the content
  script looks for the cart table: a table whose header row names an item column, a quantity column and a price
  column with the words the aid's cart reader knows (`aid/readers/cart.py`), with at least one row of data.
- Once per change of that table (its text and the values typed into its fields), at most about a second after the
  change, it sends a copy of the page and its address to the background worker. The copy holds the values typed
  into the text fields, the ticked boxes and the chosen options, and leaves the banner out. It empties every
  password field and every field whose name, id or autocomplete holds, in any case and also with its punctuation
  removed, one of: pass, wachtwoord, card, kaart, cc-, ccnum, cardnumber, creditcard, cvc, cvv, csc, securitycode,
  iban, rekening. A select so matched has no option chosen in the copy. A field named otherwise keeps its typed
  value in the copy. The copy declares UTF-8, as the text is sent and saved that way. A page without a cart table,
  or whose table holds only total rows, sends nothing.
- The background worker posts the copy to `<address of the aid>/capture` with the token in the header
  `X-Aid-Token`. Nothing goes anywhere else. The default address is `http://127.0.0.1:7870`: this computer.
- The aid saves the page under `cases/captures/`, runs the pre-check as a cart case and answers with the
  verdict, one line per difference and the link to the case on the review page.

The address, the token and the shop list are kept in the browser profile's local extension storage
(`chrome.storage.local`) on this PC. They are not synchronised to other computers.

## Load it in Edge or Chrome

1. Open `edge://extensions` (Edge) or `chrome://extensions` (Chrome) and switch on the developer mode.
2. Choose "Load unpacked" and select the folder `aid/extension` of the repository.
3. Open the extension's options ("Extension options" in its details).

After an update of the aid, press the reload button of the extension on that page and reload the open tabs of the
webshops: a browser keeps the extension's script as it was loaded until then.

## The options

- **Address of the aid**: where the capture server listens; leave the default `http://127.0.0.1:7870` unless
  the server was started with another `--host` or `--port`.
- **Token**: the value of `capture_token` in the settings file the server was started with. The demo's
  settings hold a made-up token (the demo prints it); a real token is company-specific and is never written into
  the repository, and the extension's files hold none.
- **Shop addresses**, one per line: the start of the address of each webshop, with `https://` or `http://` and the
  shop's host. Saving asks the browser for access to these shops and registers the content script for them. A
  second save updates the registration; a shop taken off the list loses its registration and its access. An
  address on `http://127.0.0.1` or `http://localhost` needs no entry: the test shop is always on, and such a line
  is noted as such. Open tabs of a shop added to the list need a reload. A line that is not an address with `http`
  or `https` and a host is refused with a note, and a shop whose access was not granted is named in a note.

## The test shop, step by step

1. Prepare the demo folder and start the capture server with the test shop:

       python3 -m aid.demo --out testshop --prepare-only
       python3 -m aid.capture_server --settings testshop/settings.json --testshop

   The server prints the address it listens on. It serves the made-up cart pages of `aid/madeup/out` under
   `/testshop/`.
2. Load the extension (above) and copy the value of `capture_token` from `testshop/settings.json` into the token
   of its options.
3. Open `http://127.0.0.1:7870/testshop/CART02.html`. Within about a second the amber banner appears: it names
   the PO line that is not in the cart and the price difference of line 2, and links to the case. Start the
   review page (`python3 -m aid.review --settings testshop/settings.json`) to see the case there.
4. Open `http://127.0.0.1:7870/testshop/CART01.html`: the green banner, the cart matches its purchase order.

## The try-out cart

In the test mode, and while no authorization limit is set, the settings tab of the review page makes a try-out cart
of a purchase order the aid knows ("Proefwinkelwagen maken" in the part "Proefdraaien met een inkooporder uit
Exact"), and the capture server serves it under `/tryout/`, for example
`http://127.0.0.1:7870/tryout/250201_price_small.html`. The extension is always on for that address. The page says
"Proefwinkel (verzonnen pagina)" in its heading; the case it opens is a try-out, about which the aid sends nothing
and whose changes are marked "(proefdocument)" (the manual's section 3.17). The server serves the page to this
computer's own host only (127.0.0.1 or localhost), and refuses a try-out cart that is sent outside the test mode
with a sentence for the bar: "Dit is een proefwinkelwagen: de assistent controleert hem alleen in de proefstand."

## The manual check of the banner

- CART02: amber, "Let op: de winkelwagen wijkt af van de inkooporder (the cart differs from the purchase
  order)", with the lines "regel 2 (KM-2002): prijs 41.00 in de winkelwagen, 40.00 op de inkooporder
  (prijsverschil boven de drempel)" and "regel 3 (KM-2003): kon niet in de winkelwagen worden geplaatst", and
  the link to the case.
- CART01: green, "Winkelwagen komt overeen met de inkooporder (cart matches the purchase order)".
- Reload either page: the banner shows the same case; no second case folder appears under `testshop/cases`.
- The red banner needs the pages from another server, since the capture server serves the test shop itself:
  start `python3 -m http.server 8000 --bind 127.0.0.1 --directory aid/madeup/out` in a second terminal and
  open `http://127.0.0.1:8000/CART02.html` (the amber banner again, from the same case). Stop the capture
  server (Ctrl-C) and reload that page: red, "Inkoopassistent niet bereikbaar (purchasing aid not reachable)".
- A wrong token in the options: red, "Inkoopassistent weigert de pagina (purchasing aid refused the page)" with
  the server's reason.
- A field that must not be copied: on the page of port 8000, add one in the browser's console with
  `document.body.insertAdjacentHTML("beforeend", '<input name="kaartnummer">')` and type a number into it. Then
  change a quantity, a change the cart reader sees: `document.querySelectorAll("tbody td")[2].textContent = "4"`.
  Within about a second the amber banner names a new case; the newest file under `testshop/cases/captures/` is
  that case's copy, and it does not hold the typed number.
- A banner the page removes: in the console, `document.getElementById("inkoopassistent-banner").remove()`. Within
  about a second the same banner is back, and no new case opens.
- An extension updated while the page is open: press the reload button of the extension on `chrome://extensions`
  (or `edge://extensions`), then set a quantity the cart does not hold yet:
  `document.querySelectorAll("tbody td")[2].textContent = "6"`. The change is sent and the banner turns red,
  "Voorcontrole mislukt (pre-check failed)" with "Herlaad de pagina (reload the page)".

The colours: green for a cart that matches (the lines the aid adjusted on the purchase order are listed under the
title), amber for differences, and for a cart of whose purchase order the aid is not sure by the cart's items (its
title, which the aid sends with its answer, is then "Let op: kijk na of dit de goede inkooporder is (check that this
is the right purchase order)", and its first line says why, until the case is closed on the review page), grey when
no purchase order was found (its line says to give the export of the order lines from
Exact and to reload the page; the aid then checks the cart again), red when the pre-check failed ("Voorcontrole
mislukt", also for an error of the server), when the aid cannot be reached, or when it refuses the page (a wrong
token, a page without a cart table it can read, a try-out cart outside the test mode). The fixed titles are in Dutch with the English in brackets; the
lines come from the server in the language of its settings. The last line under a green and under an amber banner
says what a cart check does not compare: "Niet vergeleken: levertijd, minimale afname of verpakkingseenheid,
verzendkosten en leveringsvoorwaarden." The banner's content lives in a closed shadow root, so
that the shop's own scripts cannot read the purchase order values it shows.
