// The content script of the purchasing aid's extension. On a shop page it finds the cart table by the header words the aid's cart
// reader uses (COLUMN_NAMES of aid/readers/cart.py, the three lists below), sends a snapshot of the page and its URL to
// background.js once per change of the cart (a hash of the table's text and field values) and shows the aid's answer in one banner
// at the top of the page. It reads nothing else and changes nothing on the page but its own banner.
(() => {
  "use strict";
  if (window.__inkoopassistentLoaded) {
    return;  // registered twice for one page: run once
  }
  window.__inkoopassistentLoaded = true;

  // The header words of the cart reader, folded (lower case); a cart table has a header cell with one word of each list. They are
  // written here by hand: aid/tests/test_extension.py checks that they are the reader's.
  const ITEM_WORDS = ["artikel", "item", "product", "artikelnummer"];
  const QUANTITY_WORDS = ["aantal", "qty", "quantity", "menge"];
  const PRICE_WORDS = ["prijs", "price", "preis"];
  const BANNER_ID = "inkoopassistent-banner";
  const THROTTLE_MS = 1000;  // a change is checked at most this long after it, however often the page changes meanwhile
  const COLOURS = {
    match: ["#e6f4ea", "#1e7b34"],
    differences: ["#fff4e0", "#b26a00"],
    no_po: ["#eeeeee", "#555555"],
    failed: ["#fdecea", "#b3261e"],
  };
  const TITLES = {
    match: "Winkelwagen komt overeen met de inkooporder (cart matches the purchase order)",
    differences: "Let op: de winkelwagen wijkt af van de inkooporder (the cart differs from the purchase order)",
    no_po: "Geen inkooporder gevonden (no purchase order found)",
    failed: "Voorcontrole mislukt (pre-check failed)",
    unreachable: "Inkoopassistent niet bereikbaar (purchasing aid not reachable)",
    refused: "Inkoopassistent weigert de pagina (purchasing aid refused the page)",
  };
  // The entries each verdict lists, one line each, with the text the server built in the language of its settings.
  const LISTED = {match: ["flag"], differences: ["flag", "uncompared", "review"]};

  // What the reader leaves out of a cell's text (aid/readers/cart.py: _HIDDEN, _TEXTLESS_INPUTS, _BLOCKS), so that the script
  // sees a cell's text as the reader does.
  const HIDDEN_TAGS = ["head", "title", "script", "style", "template", "noscript"];
  const TEXTLESS_INPUTS = ["hidden", "checkbox", "radio", "password", "file", "image", "color", "range"];
  const BLOCK_TAGS = ["address", "article", "aside", "blockquote", "body", "caption", "dd", "details", "dialog", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "html",
    "legend", "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul"];
  // The folded item cells of a row that sums the cart up (aid/readers/cart.py: TOTAL_WORDS): with an empty quantity it is no line.
  const TOTAL_TEXTS = ["totaal", "total", "subtotaal", "subtotal", "btw", "vat", "verzendkosten", "verzending", "shipping"];
  // A field whose value never leaves the page: its name, id or autocomplete holds one of these, in any case, as written or with
  // everything but letters and digits removed ("payment[cc_number]" holds "ccnum", "security_code" "securitycode").
  const SECRET_FIELD = /pass|wachtwoord|card|kaart|cc-|ccnum|cardnumber|creditcard|cvc|cvv|csc|securitycode|iban|rekening/i;

  let lastHash = null;
  let lastReply = null;  // the answer shown for lastHash, drawn again when the page removed the banner
  let timer = null;
  let host = null;    // the banner's element in the page; its content lives in a closed shadow root
  let shadow = null;

  // A header cell's text as the reader folds it: lower case, runs of white space as one space, the spaces, full stops and colons
  // around it removed.
  function fold(text) {
    return text.toLowerCase().replace(/\s+/g, " ").trim().replace(/^[ .:]+|[ .:]+$/g, "");
  }

  // The text of a node as the reader reads it: no hidden elements, a space at each line break and block, a field's value for an
  // input that shows text, a select's selected option.
  function readerText(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return node.data;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }
    const tag = node.tagName.toLowerCase();
    if (HIDDEN_TAGS.includes(tag)) {
      return "";
    }
    if (tag === "br") {
      return " ";
    }
    if (tag === "input") {
      return TEXTLESS_INPUTS.includes((node.type || "text").toLowerCase()) ? "" : " " + node.value + " ";
    }
    if (tag === "select") {
      const option = node.options[node.selectedIndex];
      return " " + (option ? option.text : "") + " ";
    }
    const inner = Array.from(node.childNodes).map(readerText).join("");
    return BLOCK_TAGS.includes(tag) ? " " + inner + " " : inner;
  }

  function cellText(cell) {
    return readerText(cell).replace(/\s+/g, " ").trim();
  }

  // The rows the reader counts as lines of the table: its own rows (not those of a table inside it) that hold no table and have
  // text in a cell.
  function rowLines(table) {
    return Array.from(table.rows).filter((row) => !row.querySelector("table")
      && Array.from(row.cells).some((cell) => cellText(cell) !== ""));
  }

  // The header row as the reader chooses it: the first of those rows whose cells are all th cells, else the first of them.
  function headerRow(rows) {
    return rows.find((row) => Array.from(row.cells).every((cell) => cell.tagName === "TH")) || rows[0];
  }

  function hasColumns(row) {
    const words = Array.from(row.cells).map((cell) => fold(cellText(cell)));
    return [ITEM_WORDS, QUANTITY_WORDS, PRICE_WORDS].every((list) => words.some((word) => list.includes(word)));
  }

  function isSecret(word) {
    return SECRET_FIELD.test(word) || SECRET_FIELD.test(word.replace(/[^A-Za-z0-9]/g, ""));
  }

  // The column of the first header cell whose folded text is a word of the list, counted in columns as the reader counts them.
  function columnOf(header, list) {
    let position = 0;
    for (const cell of header.cells) {
      if (list.includes(fold(cellText(cell)))) {
        return position;
      }
      position += Math.max(cell.colSpan, 1);
    }
    return -1;
  }

  // A data row below the header, as the reader counts a line of the cart: not in the table's head or foot, no cell across several
  // columns (a total or a note), and not a row whose item cell is a total word while its quantity cell is empty.
  function isDataRow(row, item, quantity) {
    const section = row.parentElement ? row.parentElement.tagName : "";
    if (section === "THEAD" || section === "TFOOT" || !Array.from(row.cells).every((cell) => cell.colSpan <= 1)) {
      return false;
    }
    const text = (column) => (row.cells[column] ? cellText(row.cells[column]) : "");
    return !(TOTAL_TEXTS.includes(fold(text(item))) && text(quantity) === "");
  }

  // The cart table: the first table whose header row names the three columns, as the reader takes it; null when there is none,
  // or when that table has no data row (an empty cart is not sent).
  function cartTable() {
    for (const table of document.querySelectorAll("table")) {
      const rows = rowLines(table);
      const header = rows.length ? headerRow(rows) : null;
      if (header && hasColumns(header)) {
        const item = columnOf(header, ITEM_WORDS);
        const quantity = columnOf(header, QUANTITY_WORDS);
        return rows.slice(rows.indexOf(header) + 1).some((row) => isDataRow(row, item, quantity)) ? table : null;
      }
    }
    return null;
  }

  function tableText(table) {
    const values = Array.from(table.querySelectorAll("input, select")).map((field) => field.value);
    return (table.textContent || "") + "\u0000" + values.join("\u0000");
  }

  // FNV-1a, 32 bits: enough to tell one state of the cart from the next.
  function hash(text) {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h;
  }

  // The page as the aid reads it: a copy of the document with the values typed into its fields written as attributes and without
  // the banner. The page itself is left as it is.
  function snapshot() {
    const copy = document.documentElement.cloneNode(true);
    const originals = document.documentElement.querySelectorAll("input, select");
    const copies = copy.querySelectorAll("input, select");
    originals.forEach((field, i) => {
      const twin = copies[i];
      if (!twin) {
        return;
      }
      const type = (field.type || "").toLowerCase();
      if (type === "password" || [field.name, field.id, field.autocomplete].some((word) => isSecret(word || ""))) {
        twin.removeAttribute("value");  // never in the copy: the aid keeps the page on disk
        if (field.tagName === "SELECT") {
          Array.from(twin.options).forEach((option) => option.removeAttribute("selected"));  // no choice at all, not the served one
        }
      } else if (field.tagName === "SELECT") {
        Array.from(twin.options).forEach((option, j) => option.toggleAttribute("selected", j === field.selectedIndex));
      } else if (type === "checkbox" || type === "radio") {
        twin.toggleAttribute("checked", field.checked);
      } else if (!TEXTLESS_INPUTS.includes(type)) {
        twin.setAttribute("value", field.value);
      }
    });
    const banner = copy.querySelector("#" + BANNER_ID);
    if (banner) {
      banner.remove();
    }
    // The copy is the browser's decoded text and is saved as UTF-8: it declares UTF-8, whatever charset the page declared.
    copy.querySelectorAll("meta[charset], meta[http-equiv]").forEach((meta) => {
      if (meta.hasAttribute("charset") || meta.getAttribute("http-equiv").toLowerCase() === "content-type") {
        meta.remove();
      }
    });
    let head = copy.querySelector("head");
    if (!head) {
      head = document.createElement("head");
      copy.insertBefore(head, copy.firstChild);
    }
    const charset = document.createElement("meta");
    charset.setAttribute("charset", "utf-8");
    head.insertBefore(charset, head.firstChild);
    return "<!doctype html>\n" + copy.outerHTML;
  }

  function check() {
    timer = null;
    const table = cartTable();
    if (!table) {
      lastHash = null;  // the cart is gone: its banner goes too, and a cart that comes back is sent again
      lastReply = null;
      removeBanner();
      return;
    }
    const current = hash(tableText(table));
    if (current === lastHash) {
      if (lastReply && (!host || !host.isConnected)) {
        render(lastReply);  // the page removed the banner while the cart stayed the same: draw it again
      }
      return;
    }
    lastHash = current;
    lastReply = null;
    const answered = (reply) => {
      if (current === lastHash) {  // an answer for a cart that is no longer on the page is not shown
        lastReply = reply;
        render(reply);
      }
    };
    try {
      chrome.runtime.sendMessage({type: "capture", url: location.href, html: snapshot()}, (reply) => {
        answered(chrome.runtime.lastError || !reply ? {ok: false, status: 0, error: "unreachable"} : reply);
      });
    } catch (error) {
      answered({ok: false, status: 0, error: "reload"});  // the extension was reloaded or removed since the page opened
    }
  }

  // A trailing throttle: the first change schedules a check THROTTLE_MS later, and further changes before it runs do not postpone
  // it, so that an element of the page that changes every second cannot keep a changed cart from being sent.
  function schedule() {
    if (timer === null) {
      timer = setTimeout(check, THROTTLE_MS);
    }
  }

  function element(tag, text, style) {
    const node = document.createElement(tag);
    if (text) {
      node.textContent = text;
    }
    if (style) {
      node.style.cssText = style;
    }
    return node;
  }

  function removeBanner() {
    if (host) {
      host.remove();
    }
    host = null;
    shadow = null;
  }

  // The banner's host at the top of the page, made again when the page removed it (a replaced body, for one).
  function bannerRoot() {
    if (!host || !host.isConnected) {
      host = element("div", "", "all:initial;display:block;position:relative;z-index:2147483647;");
      host.id = BANNER_ID;
      shadow = host.attachShadow({mode: "closed"});  // closed: the shop's scripts cannot read the values it shows
      const parent = document.body || document.documentElement;
      parent.insertBefore(host, parent.firstChild);
    }
    return shadow;
  }

  function render(reply) {
    const answer = reply.ok ? reply.answer : null;
    let kind;
    let title;
    if (answer && COLOURS[answer.verdict]) {
      kind = answer.verdict;
      title = TITLES[kind];
    } else {
      kind = "failed";
      if (reply.error === "reload") {
        title = TITLES.failed;
      } else if (reply.status === 0) {
        title = TITLES.unreachable;
      } else {
        title = reply.status >= 500 ? TITLES.failed : TITLES.refused;  // 4xx: the aid refused the page; 5xx: the check failed
      }
    }
    const [background, colour] = COLOURS[kind];
    const banner = element("div", "", "margin:0;padding:10px 16px;font:14px/1.4 Arial,sans-serif;text-align:left;"
      + "background:" + background + ";color:#202020;border-bottom:3px solid " + colour + ";");
    banner.appendChild(element("div", title, "font-weight:bold;color:" + colour + ";"));
    if (answer) {
      if (answer.message) {
        banner.appendChild(element("div", String(answer.message)));
      }
      const listed = (answer.lines || []).filter((entry) => (LISTED[kind] || []).includes(entry.kind) && entry.text);
      if (listed.length) {
        const list = element("ul", "", "margin:4px 0;padding-left:20px;");
        listed.forEach((entry) => list.appendChild(element("li", String(entry.text))));
        banner.appendChild(list);
      }
      if (typeof answer.review_url === "string" && /^https?:\/\//.test(answer.review_url)) {
        const link = element("a", "Bekijk de zaak " + answer.case_id + " (open the case)", "color:" + colour + ";");
        link.href = answer.review_url;
        link.target = "_blank";
        link.rel = "noopener";
        banner.appendChild(link);
      }
    } else if (reply.status !== 0 && reply.error) {
      banner.appendChild(element("div", String(reply.error)));
    } else if (reply.error === "reload") {
      banner.appendChild(element("div", "Herlaad de pagina (reload the page): de extensie is bijgewerkt (the extension was updated)."));
    }
    bannerRoot().replaceChildren(banner);
  }

  // Whether a record is only the insertion of the banner's host, which is no change of the page. Its removal is one: the check
  // that follows draws the banner again.
  function onlyBanner(record) {
    const added = Array.from(record.addedNodes);
    return record.target === host
      || (record.removedNodes.length === 0 && added.length > 0 && added.every((node) => node.id === BANNER_ID));
  }

  // The whole document is watched, not only the table or the body, because a shop may draw its cart later, replace the table
  // when it changes or replace the body; a change that leaves the cart's text as it was sends nothing.
  new MutationObserver((records) => {
    if (!records.every(onlyBanner)) {
      schedule();
    }
  }).observe(document.documentElement, {childList: true, subtree: true, characterData: true});
  document.addEventListener("input", schedule, true);
  document.addEventListener("change", schedule, true);
  check();
})();
