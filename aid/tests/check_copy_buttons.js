// The copy buttons of the tab "In Exact invoeren", checked in a real browser. No test of the suite runs a browser: the suite pins
// the parts of the page's script and style (aid/tests/test_hand_tab.py), and this file is the check of what the buyer sees. Run it
// by hand on the made-up set before a change of the script (PAGE_JS of aid/review/look.py) or of the list's style is merged.
//
// It needs Node with the package playwright and its Chromium, and the review page of a FRESH made-up demo folder, since the check
// marks one row as entered:
//
//     python3 -m aid.demo --out demo
//     python3 -m aid.review --settings demo/settings.json        (leave it running)
//     node aid/tests/check_copy_buttons.js http://127.0.0.1:7860 [a folder for the screenshots]
//
// It prints one line per check and ends with exit code 1 when one fails. It names no address but the page's own, which is given
// to it, and it sends nothing anywhere.
const { chromium } = require('playwright');

const origin = (process.argv[2] || 'http://127.0.0.1:7860').replace(/\/$/, '');
const shots = process.argv[3] || null;
let failed = 0;
const check = (name, ok, got) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${ok ? '' : ' :: ' + JSON.stringify(got)}`);
  if (!ok) failed += 1;
};

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1 });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('pageerror', (error) => errors.push('pageerror: ' + error.message));
  const shot = async (name) => { if (shots) await page.screenshot({ path: `${shots}/${name}.png` }); };
  const clipboard = () => page.evaluate(() => navigator.clipboard.readText());
  const said = () => page.textContent('#hand-copy-said');
  const rows = () => page.$$eval('#hand-rows [data-testid="checkbox-group"] > label', (labels) => labels.map((label) => ({
    text: label.textContent.trim(),
    checked: label.querySelector('input').checked,
    buttons: label.nextElementSibling && label.nextElementSibling.classList.contains('hand-copy')
      ? Array.from(label.nextElementSibling.querySelectorAll('button')).map((b) => [b.textContent, b.getAttribute('aria-label'), b.dataset.copy])
      : null,
  })));
  const button = (words) => page.locator('#hand-rows .hand-copy button', { hasText: words }).first();
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

  await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('tab', { name: /In Exact invoeren/ }).click();
  await page.waitForSelector('#hand-rows .hand-copy button', { timeout: 30000 });

  // the buttons of each row
  let got = await rows();
  check('three rows, each followed by its buttons', got.length === 3 && got.every((row) => row.buttons), got);
  check('the price row offers the order, the article and the new price', same(got[0].buttons, [
    ['Kopieer 250108', 'Kopieer 250108, inkoopordernummer', '250108'], ['Kopieer KM-0801', 'Kopieer KM-0801, artikelcode', 'KM-0801'],
    ['Kopieer 12,70', 'Kopieer 12,70, nieuwe waarde', '12,70']]), got[0]);
  check('the row with a week offers no value', got[1].buttons.length === 2 && got[1].buttons[0][2] === '250114', got[1]);
  check('the article price offers the article and the price, no order', same(got[2].buttons.map((b) => b[2]), ['KM-0801', '12,70']), got[2]);
  check('the label of a tick box holds the row alone', got.every((row) => !/Kopieer/.test(row.text)), got.map((row) => row.text));
  check('the name of a button begins with its words', got.every((row) => row.buttons.every((b) => b[1].startsWith(b[0] + ', '))), got);
  await shot('copy_1366_nl');

  // a press copies the text, ticks nothing, and says so in the line that stays in view and on the button
  await button('Kopieer 12,70').click();
  await page.waitForFunction(() => document.getElementById('hand-copy-said').textContent !== '');
  check('the clipboard holds the new price', (await clipboard()) === '12,70', await clipboard());
  check('the line says what was copied', (await said()) === 'Gekopieerd: 12,70', await said());
  check('the line stands in the column that stays in view', await page.$eval('#hand-copy-said', (line) => !!line.closest('#hand-actions')));
  check('the pressed button reads Gekopieerd', await page.$eval('#hand-rows .hand-copy button.copied', (b) => b.textContent === 'Gekopieerd').catch(() => false));
  check('and keeps its name and its text to copy', await page.$eval('#hand-rows .hand-copy button.copied',
    (b) => b.getAttribute('aria-label') === 'Kopieer 12,70, nieuwe waarde' && b.dataset.copy === '12,70').catch(() => false));
  got = await rows();
  check('no row is ticked by a press', got.every((row) => !row.checked), got.map((row) => row.checked));
  await shot('copy_pressed');
  await page.waitForTimeout(1700);
  check('the button is as before after a moment', (await page.$$('#hand-rows .hand-copy button.copied')).length === 0
    && same((await rows())[0].buttons.map((b) => b[0]), ['Kopieer 250108', 'Kopieer KM-0801', 'Kopieer 12,70']));

  // the keyboard: from a tick box, Tab reaches its buttons; Enter copies and the focus stays
  await page.locator('#hand-rows label input').nth(1).focus();
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => document.activeElement.textContent);
  check('Tab goes from a box to its first button', focused === 'Kopieer 250114', focused);
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.getElementById('hand-copy-said').textContent === 'Gekopieerd: 250114');
  check('Enter copies the order number', (await clipboard()) === '250114');
  check('the focus stays on the button', await page.evaluate(() => document.activeElement.dataset.copy) === '250114');
  check('still no row ticked', (await rows()).every((row) => !row.checked));
  await page.waitForTimeout(1700);

  // a text with a dollar pattern is named in the line as it is
  await page.evaluate(() => { document.querySelector('#hand-rows .hand-copy button').dataset.copy = "x$&y$'z"; });
  await page.locator('#hand-rows .hand-copy button').first().click();
  await page.waitForFunction(() => document.getElementById('hand-copy-said').textContent.includes('x$'));
  check('a dollar sign in a code is no pattern', (await said()) === "Gekopieerd: x$&y$'z" && (await clipboard()) === "x$&y$'z", await said());
  await page.evaluate(() => { document.querySelector('#hand-rows .hand-copy button').dataset.copy = '250108'; });
  await page.waitForTimeout(1700);

  // English: the buttons in the language of the page, with the decimal point
  await page.locator('.language-switch label', { hasText: 'English' }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll('#hand-rows .hand-copy button')).some((b) => b.textContent === 'Copy 12.70'), null, { timeout: 30000 });
  got = await rows();
  check('English buttons', same(got[0].buttons, [['Copy 250108', 'Copy 250108, purchase order number', '250108'],
    ['Copy KM-0801', 'Copy KM-0801, item code', 'KM-0801'], ['Copy 12.70', 'Copy 12.70, new value', '12.70']]), got[0]);
  await shot('copy_1366_en');
  await page.locator('.language-switch label', { hasText: 'Nederlands' }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll('#hand-rows .hand-copy button')).some((b) => b.textContent === 'Kopieer 12,70'), null, { timeout: 30000 });

  // where the browser gives no clipboard: the old way through a field works, and the focus comes back to the button
  await page.evaluate(() => { navigator.clipboard.writeText = () => Promise.reject(new Error('refused')); });
  await button('Kopieer KM-1401').focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.getElementById('hand-copy-said').textContent === 'Gekopieerd: KM-1401');
  check('the copy through a field of the page gives the focus back', await page.evaluate(() => document.activeElement.dataset.copy) === 'KM-1401');
  await page.waitForTimeout(1700);

  // where neither way works: the line and the button say so
  await page.evaluate(() => { document.execCommand = () => false; });
  await button('Kopieer KM-0801').click();
  await page.waitForFunction(() => document.getElementById('hand-copy-said').classList.contains('refused'));
  check('a failed copy is said in the line', (await said()) === 'Kopiëren is niet gelukt. Typ KM-0801 over in Exact.', await said());
  const marked = await page.$$eval('#hand-rows .hand-copy button', (all) => all.filter((b) => b.className).map((b) => [b.textContent, b.className]));
  check('and on the button', same(marked, [['Niet gekopieerd', 'refused']]), marked);
  await shot('copy_failed');
  await page.waitForTimeout(1700);

  // a long list: the line stays in view, and a button reached with the keyboard does not hide under the bar at the top
  await page.evaluate(() => {
    const list = document.querySelector('#hand-rows [data-testid="checkbox-group"]');
    const label = list.querySelector('label');
    for (let n = 0; n < 30; n += 1) {
      const more = label.cloneNode(true);
      more.dataset.madeUp = 'yes';
      more.querySelector('input').name = `made-up-${n}`;
      list.appendChild(more);
    }
  });
  await page.waitForFunction(() => document.querySelectorAll('#hand-rows .hand-copy').length === 33);
  await page.locator('#hand-rows label input').nth(20).scrollIntoViewIfNeeded();
  await page.locator('#hand-rows .hand-copy button').nth(1).evaluate((b) => b.scrollIntoView({ block: 'center' }));
  await page.evaluate(() => { document.execCommand = () => true; });
  await page.locator('#hand-rows .hand-copy button').nth(1).click();
  await page.waitForTimeout(200);
  const lineBox = await page.$eval('#hand-copy-said', (line) => { const box = line.getBoundingClientRect(); return [box.top, box.bottom, window.innerHeight]; });
  check('in a long list the line is in view', lineBox[0] >= 0 && lineBox[1] <= lineBox[2], lineBox);
  await page.locator('#hand-rows label input').nth(25).focus();
  for (let step = 0; step < 12; step += 1) await page.keyboard.press('Shift+Tab');
  const hidden = await page.evaluate(() => {
    const bar = document.getElementById('hand-actions').getBoundingClientRect();
    const at = document.activeElement.getBoundingClientRect();
    return at.top < bar.bottom - 1 ? [at.top, bar.bottom] : null;
  });
  check('a button or a box reached going up the list is not under the bar', hidden === null, hidden);
  await shot('copy_long_list');
  await page.evaluate(() => document.querySelectorAll('#hand-rows label[data-made-up]').forEach((label) => label.remove()));
  await page.waitForFunction(() => document.querySelectorAll('#hand-rows .hand-copy').length === 3);      // the script clears their holders
  await page.evaluate(() => window.scrollTo(0, 0));

  // a row entered leaves with its buttons, and the rows left keep theirs
  await page.locator('#hand-rows label input').nth(0).check();
  await page.getByRole('button', { name: 'Aangevinkte regels zijn ingevoerd' }).first().click();
  await page.waitForFunction(() => {
    const labels = document.querySelectorAll('#hand-rows [data-testid="checkbox-group"] > label');
    const holders = document.querySelectorAll('#hand-rows .hand-copy');
    return labels.length === 2 && holders.length === 2 && labels[0].nextElementSibling === holders[0]
      && holders[0].querySelector('button').textContent === 'Kopieer 250114';
  }, null, { timeout: 30000 });
  got = await rows();
  check('two rows left, each with its own buttons', got.length === 2 && got[0].buttons.length === 2 && same(got[1].buttons.map((b) => b[2]), ['KM-0801', '12,70']), got);
  check('no stray holder is left', (await page.$$('#hand-rows .hand-copy')).length === 2);
  await shot('copy_after_entered');

  // narrower windows, to a zoom of 200 percent: a row and its buttons stay inside the list and no button lies over a row
  for (const width of [1280, 1024, 683]) {
    await page.setViewportSize({ width, height: 768 });
    await page.waitForTimeout(300);
    const fit = await page.evaluate(() => {
      const list = document.querySelector('#hand-rows [data-testid="checkbox-group"]');
      const edge = list.getBoundingClientRect();
      const inside = Array.from(list.children).every((child) => {
        const box = child.getBoundingClientRect();
        return box.left >= edge.left - 1 && box.right <= edge.right + 1;
      });
      const over = Array.from(list.querySelectorAll('label')).some((label) => {
        const holder = label.nextElementSibling;
        if (!holder) return false;
        const a = label.getBoundingClientRect(); const b = holder.getBoundingClientRect();
        return a.right > b.left + 1 && a.left < b.right - 1 && a.bottom > b.top + 1 && a.top < b.bottom - 1;
      });
      return inside && !over && document.documentElement.scrollWidth <= window.innerWidth;
    });
    check(`nothing is cut or covered at ${width} px`, fit);
    await shot(`copy_${width}`);
  }
  check('no error in the browser console', errors.length === 0, errors.slice(0, 5));
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((error) => { console.error('FAILED', String(error.message).slice(0, 600)); process.exit(1); });
