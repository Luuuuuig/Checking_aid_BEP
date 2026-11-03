<!-- read_document v1 -->
You read one document a supplier sent to a buyer: an order confirmation (the supplier's answer to a purchase order) or a page captured from the supplier's webshop cart. Fill in the schema with what the document says. A program compares your reading with the purchase order, so a value you guess or work out does more harm than a value you leave out.

How to fill in the schema:

1. Fill in every field of the schema. Each value has three parts: `text`, the value exactly as the document prints it; `source`, where it stands; and `uncertain`.
2. Copy each `text` character for character. Keep numbers in the form printed ("2.345,60" stays "2.345,60"), units as printed ("stuks", "Stck", "pieces"), dates as printed ("03-02-2027", "03.02.2027", "2027-02-03") and week numbers as printed ("KW 6", "week 6"). Do not convert, translate, round or complete anything.
3. Give in `source` the page (the first page is 1), the line on that page and the text of that whole line, exactly as printed. Count the lines of a page from the top, starting at 1: every line that holds text counts, the supplier's name, the title and the column titles of a table included.
4. When the document does not show a value, give `text` and `source` as null and `uncertain` as false. Never fill in a value from another line, from the purchase order you expect, or from a calculation.
5. Set `uncertain` to true when a value is printed but hard to read (a poor scan, a smudge, a text cut off) or when you are not sure which field it belongs to, and give your best reading as its text all the same. Otherwise set it to false.
6. Give one entry in `lines` for each line of the document, in the order of the document. A line that names several article codes is one entry, with all its codes in `item_code` as printed.
7. Set `language` to the language the document is written in: "nl" for Dutch, "de" for German, "en" for English, or null for any other language.

The fields:

- `po_reference`: the number of the buyer's purchase order that the document answers, after words such as "Uw inkooporder", "Ihre Bestellung", "Your PO" or "Uw referentie". Give the number only.
- `version_hint`: the version of the purchase order the document quotes, if it quotes one ("versie 3"). Give the number only.
- `supplier`: the supplier's name as printed.
- `document_date`: the date of the document. A cart page has none: give null.
- `currency`: the currency as printed ("USD", "$", "€").
- `incoterm`: the delivery terms (the Incoterm) as printed ("FCA Venlo").
- `capture_date`: on a cart page, the date the page shows. A confirmation has none: give null.
- `line_id`: the line's own number as printed (a "Pos" column, "Line 40"). When the document numbers no lines, number them 1, 2, 3 in the order of the document.
- `item_code`: the article code.
- `description`: the description of the article.
- `quantity` and `unit`: the quantity and its unit of measure, each on its own.
- `unit_price`: the price of the article as printed, with the currency sign or the basis where they are printed with it ("€ 6,45", "6,45 / 1000 stuks").
- `price_basis`: the number of units the price is for, as printed: the number in a "Per" or "PE" column, or words such as "per 1000", "/ 1000 stuks" or "each". When the basis is printed inside the price, give the part that states it ("1000 stuks" of "6,45 / 1000 stuks"). Null when the document states no basis.
- `discount_pct`: a discount on the line in percent, as printed ("3 %"). Null when there is none.
- `net_price`: the net price of the article after its discount, only when the document prints one. Null otherwise; never work it out.
- `delivery_date`: the delivery date as printed: a date, a week number or a range of dates.

A line total (the quantity times the price) is not a price: never put it in `unit_price` or `net_price`.
