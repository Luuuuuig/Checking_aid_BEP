// The options page of the purchasing aid's extension. It keeps the aid's address, the token and the shop addresses in
// chrome.storage.local (in this browser profile on this computer, never synchronised), asks the browser for access to the shops
// the buyer lists and registers content.js for them. The test shop on 127.0.0.1 and localhost needs neither: the manifest names it.
"use strict";

const DEFAULTS = {address: "http://127.0.0.1:7870", token: "", domains: []};
const SCRIPT_ID = "inkoopassistent-shops";
// The test shop's patterns, which the manifest's content script always covers: never requested, registered or removed here.
const TEST_SHOP = ["http://127.0.0.1/*", "http://localhost/*"];

const fields = {
  address: document.getElementById("address"),
  token: document.getElementById("token"),
  domains: document.getElementById("domains"),
};
const note = document.getElementById("note");

function show(text, problem) {
  note.textContent = text;
  note.className = problem ? "problem" : "";
}

// The match pattern of a shop address: its scheme and host, every path; null for an address that is not http or https with a host.
function pattern(address) {
  let url;
  try {
    url = new URL(address);
  } catch (error) {
    return null;
  }
  if ((url.protocol !== "https:" && url.protocol !== "http:") || !url.hostname) {
    return null;
  }
  return url.protocol + "//" + url.hostname + "/*";
}

function listed() {
  return fields.domains.value.split("\n").map((line) => line.trim()).filter((line) => line !== "");
}

async function load() {
  const settings = await chrome.storage.local.get(DEFAULTS);
  fields.address.value = settings.address || DEFAULTS.address;
  fields.token.value = settings.token || "";
  fields.domains.value = (settings.domains || []).join("\n");
}

// The content script registered for the shops whose access was granted; no registration when there are none.
async function register(origins) {
  const existing = await chrome.scripting.getRegisteredContentScripts({ids: [SCRIPT_ID]});
  if (existing.length) {
    await chrome.scripting.unregisterContentScripts({ids: [SCRIPT_ID]});
  }
  if (origins.length) {
    await chrome.scripting.registerContentScripts([{id: SCRIPT_ID, matches: origins, js: ["content.js"], runAt: "document_idle",
                                                    persistAcrossSessions: true}]);
  }
}

function save() {
  const address = fields.address.value.trim() || DEFAULTS.address;
  if (pattern(address) === null) {
    show("Het adres van de inkoopassistent moet met http:// of https:// beginnen en een host hebben "
      + "(the aid's address needs http:// or https:// and a host).", true);
    return;
  }
  const domains = listed();
  const wrong = domains.filter((domain) => pattern(domain) === null);
  if (wrong.length) {
    show("Geen geldig webshopadres (not a valid shop address): " + wrong.join(", ")
      + "\nGebruik http:// of https:// en de host van de winkel (use http:// or https:// and the shop's host).", true);
    return;
  }
  const always = domains.filter((domain) => TEST_SHOP.includes(pattern(domain)));
  const origins = [...new Set(domains.map(pattern))].filter((origin) => !TEST_SHOP.includes(origin));
  // The request comes first, straight from the click: the browser asks for access only during the click itself.
  const asked = origins.length ? chrome.permissions.request({origins: origins}) : Promise.resolve(true);
  asked.then(() => finish(address, domains, origins, always)).catch((error) => show("Opslaan mislukt (saving failed): " + error.message, true));
}

async function finish(address, domains, origins, always) {
  const before = await chrome.storage.local.get(DEFAULTS);
  const removed = [...new Set((before.domains || []).map(pattern)
    .filter((origin) => origin && !origins.includes(origin) && !TEST_SHOP.includes(origin)))];
  const notes = [];
  for (const origin of removed) {  // one at a time, so that one the browser refuses to remove does not stop the save
    try {
      await chrome.permissions.remove({origins: [origin]});
    } catch (error) {
      notes.push("Toegang niet ingetrokken (access not removed): " + origin);
    }
  }
  if (always.length) {
    notes.push("Altijd aan voor de testwinkel (always on for the test shop): " + always.join(", "));
  }
  const granted = [];
  const missing = [];
  for (const origin of origins) {
    (await chrome.permissions.contains({origins: [origin]}) ? granted : missing).push(origin);
  }
  await register(granted);
  await chrome.storage.local.set({address: address, token: fields.token.value.trim(), domains: domains});
  if (missing.length) {
    notes.unshift("Opgeslagen, maar zonder toegang tot (saved, but without access to): " + missing.join(", ")
      + "\nSla opnieuw op en sta de toegang toe (save again and allow the access).");
    show(notes.join("\n"), true);
  } else {
    show(["Opgeslagen (saved).", ...notes].join("\n"), false);
  }
}

document.getElementById("save").addEventListener("click", save);
load();
