// The background worker of the purchasing aid's extension. It receives a captured cart page from content.js, posts it to the
// aid's capture server (POST <address>/capture, with the token in the header X-Aid-Token) and returns the server's JSON answer,
// or the error, to content.js. The address and the token come from chrome.storage.local, which the options page fills: they stay
// in this browser profile on this computer and are never synchronised to another.
"use strict";

const DEFAULTS = {address: "http://127.0.0.1:7870", token: "", domains: []};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "capture") {
    return false;
  }
  capture(String(message.url || ""), String(message.html || "")).then(sendResponse);
  return true;  // the answer is sent later, when the fetch is done
});

async function capture(url, html) {
  const settings = await chrome.storage.local.get(DEFAULTS);
  const address = String(settings.address || DEFAULTS.address).replace(/\/+$/, "");
  let response;
  try {
    response = await fetch(address + "/capture", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Aid-Token": String(settings.token || "")},
      body: JSON.stringify({url: url, html: html}),
    });
  } catch (error) {
    return {ok: false, status: 0, error: "unreachable"};
  }
  let body = {};
  try {
    body = await response.json();
  } catch (error) {
    body = {};
  }
  if (!response.ok) {
    return {ok: false, status: response.status, error: String(body.error || "HTTP " + response.status)};
  }
  return {ok: true, status: response.status, answer: body};
}
