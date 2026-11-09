"""Tests of the browser extension's files (aid/extension), read as text: the manifest, the header words the content script shares with
the cart reader, and what the files must never hold. The extension's behaviour in a browser is checked by hand (its README)."""
import json
import re
from pathlib import Path

from aid.readers.cart import COLUMN_NAMES

EXTENSION = Path(__file__).resolve().parents[1] / "extension"
FILES = sorted(path for path in EXTENSION.iterdir() if path.suffix in (".js", ".json", ".html", ".md"))
LOCAL_HOSTS = {"127.0.0.1", "localhost", "*"}                # "*": the pattern of the optional host permissions


def test_the_manifest_is_version_3_with_its_scripts_and_options_page():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["background"] == {"service_worker": "background.js"}
    assert [script["js"] for script in manifest["content_scripts"]] == [["content.js"]]
    assert manifest["content_scripts"][0]["matches"] == ["http://127.0.0.1/*", "http://localhost/*"]
    assert manifest["optional_host_permissions"] == ["https://*/*", "http://*/*"] and "host_permissions" not in manifest
    assert manifest["options_page"] == "options.html"
    assert manifest["permissions"] == ["storage", "scripting"]          # chrome.storage.local and registerContentScripts
    for name in ("background.js", "content.js", "options.html", "options.js", "README.md"):
        assert (EXTENSION / name).is_file(), name
    assert 'src="options.js"' in (EXTENSION / "options.html").read_text(encoding="utf-8")


def test_the_content_script_holds_the_cart_readers_header_words():
    """Every word of the three lists a cart table needs, as a whole quoted word, so that neither side drifts silently."""
    script = (EXTENSION / "content.js").read_text(encoding="utf-8")
    for field in ("item_code", "quantity", "unit_price"):
        for word in COLUMN_NAMES[field]:
            assert f'"{word}"' in script, word
    lists = re.findall(r"const (\w+_WORDS) = \[([^\]]*)\];", script)
    assert [(name, re.findall(r'"([^"]*)"', words)) for name, words in lists] == [
        ("ITEM_WORDS", list(COLUMN_NAMES["item_code"])), ("QUANTITY_WORDS", list(COLUMN_NAMES["quantity"])),
        ("PRICE_WORDS", list(COLUMN_NAMES["unit_price"]))]


def _function(script: str, name: str) -> str:
    """The text of the function `name` of the script, from its "function" line to the closing brace at its indentation."""
    found = re.search(rf"^( *)function {name}\(.*?^\1\}}$", script, re.MULTILINE | re.DOTALL)
    assert found, name
    return found[0]


def test_the_content_script_chooses_the_header_row_as_the_reader_does():
    """The reader's rule (aid/readers/cart.py): among the table's own rows that are lines of the page (a row holding a table is none,
    nor a row without text), the first row of th cells only, else the first; the first table whose header row names the three
    columns is the cart table."""
    import aid.readers.cart as cart

    assert "Its header row is its first row of th cells only, else its" in cart.__doc__
    script = (EXTENSION / "content.js").read_text(encoding="utf-8")
    assert " ".join(_function(script, "headerRow").split()) == (
        'function headerRow(rows) { return rows.find((row) => Array.from(row.cells).every((cell) => cell.tagName === "TH")) '
        "|| rows[0]; }")
    lines = " ".join(_function(script, "rowLines").split())
    assert "Array.from(table.rows)" in lines and '!row.querySelector("table")' in lines and 'cellText(cell) !== ""' in lines
    table = " ".join(_function(script, "cartTable").split())
    assert "headerRow(rows)" in table and "hasColumns(header)" in table and "return rows.slice" in table   # the first such table


def _js_list(script: str, name: str) -> list[str]:
    found = re.search(rf"const {name} = \[([^\]]*)\];", script)
    assert found, name
    return re.findall(r'"([^"]*)"', found[1])


def test_the_content_script_reads_a_cell_as_the_reader_does():
    """The elements the reader leaves out, the inputs that show no text and the blocks that end a line are the reader's own sets, and
    the header words are folded from that text."""
    import aid.readers.cart as cart

    script = (EXTENSION / "content.js").read_text(encoding="utf-8")
    assert set(_js_list(script, "HIDDEN_TAGS")) == cart._HIDDEN
    assert set(_js_list(script, "TEXTLESS_INPUTS")) == cart._TEXTLESS_INPUTS
    assert set(_js_list(script, "BLOCK_TAGS")) == cart._BLOCKS
    assert set(_js_list(script, "TOTAL_TEXTS")) == cart.TOTAL_WORDS
    assert "fold(cellText(cell))" in " ".join(_function(script, "hasColumns").split())
    assert "return text.toLowerCase().replace(/\\s+/g, \" \").trim().replace(/^[ .:]+|[ .:]+$/g, \"\");" in _function(script, "fold")
    data_row = " ".join(_function(script, "isDataRow").split())
    assert 'section === "THEAD" || section === "TFOOT"' in data_row and "cell.colSpan <= 1" in data_row
    assert 'TOTAL_TEXTS.includes(fold(text(item))) && text(quantity) === ""' in data_row


def test_the_reader_chooses_the_header_row_by_th_cells_in_its_code():
    """The reader's side of the parity, in its code and on a page: the first row of th cells only is the header row, rows above it
    are no cart lines."""
    import inspect
    import aid.readers.cart as cart

    assert 'all(cell.name == "th" for cell in _cells(row.element))' in inspect.getsource(cart._cart_table)
    assert ", 0)" in inspect.getsource(cart._cart_table)


def test_the_reader_skips_rows_above_a_th_header(tmp_path):
    from aid.readers.cart import read_cart_html

    page = tmp_path / "cart.html"
    page.write_text("<html><body><table><tr><td>Bestelling</td><td>week 45</td><td></td></tr>"
                    "<tr><th>Artikel</th><th>Aantal</th><th>Prijs</th></tr>"
                    "<tr><td>KM-1</td><td>2</td><td>3,00</td></tr></table>"
                    "<table><tr><th>Artikel</th><th>Aantal</th><th>Prijs</th></tr><tr><td>KM-9</td><td>1</td><td>1,00</td></tr></table>"
                    "</body></html>", encoding="utf-8")
    assert [line.item_code.value for line in read_cart_html(page).lines] == ["KM-1"]


def test_the_snapshot_keeps_secrets_out_and_declares_utf_8():
    snapshot = " ".join(_function((EXTENSION / "content.js").read_text(encoding="utf-8"), "snapshot").split())
    assert 'type === "password"' in snapshot and 'twin.removeAttribute("value")' in snapshot
    assert 'Array.from(twin.options).forEach((option) => option.removeAttribute("selected"));' in snapshot
    assert 'charset.setAttribute("charset", "utf-8")' in snapshot
    script = (EXTENSION / "content.js").read_text(encoding="utf-8")
    words = re.search(r"const SECRET_FIELD = /([^/]*)/i;", script)[1].split("|")
    assert words == ["pass", "wachtwoord", "card", "kaart", "cc-", "ccnum", "cardnumber", "creditcard", "cvc", "cvv", "csc",
                     "securitycode", "iban", "rekening"]
    assert 'word.replace(/[^A-Za-z0-9]/g, "")' in _function(script, "isSecret")


def test_the_settings_stay_on_this_computer():
    for path in FILES:
        assert "storage.sync" not in path.read_text(encoding="utf-8"), path.name
    assert "chrome.storage.local" in (EXTENSION / "background.js").read_text(encoding="utf-8")
    assert "chrome.storage.local" in (EXTENSION / "options.js").read_text(encoding="utf-8")


def test_no_token_key_or_other_address_in_the_extensions_files():
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        hosts = set(re.findall(r"\bhttps?://([A-Za-z0-9.*-]+)", text))
        assert hosts <= LOCAL_HOSTS, (path.name, hosts - LOCAL_HOSTS)
        assert "made-up-token" not in text and not re.search(r"[A-Za-z0-9+/_-]{32,}", text), path.name
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert "key" not in manifest and "externally_connectable" not in manifest
