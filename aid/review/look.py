"""The look of the review page: one set of tokens for the whole page, each pair checked against the contrast formula of WCAG 2.x;
the gradio theme made of them (page_theme); the style rules the theme cannot set (PAGE_CSS); and the page's load script (PAGE_JS).
Text needs 4.5:1 against its ground; the edge of a control, a focus ring and a state mark need 3:1. The ratios beside the tokens
are those of that formula.

The page stays neutral for the most part: a grey ground on which each part of a tab lies as a white card, ink for the text and
for the head band across the top (the tool's name, the language switch and the page's tabs as pills), and one blue accent for the
one filled button of a view, links, the focus and the chosen option. The four tones (TONES) appear only on chips and lines that carry their word,
so that a colour never says something alone: "attention" for what waits for the buyer ("Ter beoordeling", the approval banner,
the notice of a failed case), "problem" for what went wrong ("Mislukt", the problems of the assistant), "done" for what is
finished ("Vanzelf afgesloten", "Door u afgesloten") and "waiting" for what waits for someone else ("Wacht op leverancier",
"Wacht op Copilot"). Each tone has the colour of its words, the tint of its ground and the colour of its mark (a dot, a bar or
an edge).

The type is the PC's own face (Segoe UI on Windows), in 400 and 600 only, at 13, 14, 16, 20 and 24 px (the tabs of the sections
of Instellingen at 15 px, one step under the page's own tabs); the space between parts is 4, 8, 12, 16, 24, 32 or 48 px; corners
are 4 px on chips and boxes, 6 px on buttons, fields and notices and 8 px on blocks and cards; a card alone has a shadow, a soft
one in ink (CARD_SHADOW); and the only motion is a change of colour, 150 ms in and 120 ms out, none at all where the PC asks for
reduced motion. The theme copies each of its dark values from its light twin, so
that a PC in dark mode shows the same checked page.

The review command gives the theme, the style rules and the script to launch() (launch_options of aid/review/__main__.py), never
to gr.Blocks(), which warns about them in gradio 6.31; the Colab notebook launches the page with the page library's own look.
Nothing here names an address outside the PC: the theme is built in code and never named (gradio asks the Hub for a theme it does
not know by name), its fonts are names of fonts the PC has, so that no font file is fetched or served, and the style rules and the
script hold no address. The style rules name some class names of gradio 6.31.0 itself (aid/requirements.txt pins that version):
where a later version renames one, that part keeps the page library's own look.
"""
from typing import NamedTuple

GROUND = "#F1F5F9"           # the page, on which the cards lie; INK on it 16.30:1, MUTED 6.92:1, and CONTROL as the edge of a control
                             # 4.34:1
SURFACE = "#FFFFFF"          # cards, fields, tables, document frames, folds, the strip
SURFACE_2 = "#F1F5F9"        # the ground of a document's pages, the hover of a secondary button; INK on it 16.30:1
TINT = "#F8FAFC"             # the ground of a notice and of the head of a table, lighter than the page, so that a notice does not read
                             # as a card; INK on it 17.06:1, MUTED 7.24:1, CONTROL 4.55:1
INK = "#0F172A"              # all text, and the head band; 17.85:1 on SURFACE, 16.30:1 on GROUND
INK_HOVER = "#1E293B"        # the hover of a tab on the head band; white on it 14.63:1
MUTED = "#475569"            # help under a field, meta, column heads, tabs not chosen; 7.58:1 on SURFACE, 7.24:1 on TINT, 6.92:1
                             # on GROUND and SURFACE_2, 6.15:1 on LINE (a filled button that cannot be pressed yet)
CONTROL = "#64748B"          # the edge of fields, boxes, radios and secondary buttons, and placeholders; 4.76:1 on SURFACE, 4.55:1 on
                             # TINT
LINE = "#E2E8F0"             # rules between rows and the edges of blocks and cards: 1.23:1, so never the only edge of a control
LINE_STRONG = "#CBD5E1"      # the rule under the head of a table: 1.48:1, decorative too; on the head band the words of the tabs not
                             # chosen and the edge of the "..." press, 12.02:1 on BAND
ACCENT = "#1D4ED8"           # the one filled button of a view with white text (6.70:1), links, the focus ring, the edge of the chosen
                             # option, the bar under the chosen section of Instellingen; 6.70:1 on SURFACE, 6.41:1 on TINT, 6.12:1 on
                             # GROUND; the ring stands 2 px off the filled button, which has its colour
ACCENT_HOVER = "#1E40AF"     # the hover of the filled button; white on it 8.72:1
ACCENT_TINT = "#EFF6FF"      # the ground of the chosen option; INK on it 16.40:1, ACCENT 6.16:1
BAND = INK                   # the head band across the top: the tool's name in white on it 17.85:1, the chosen tab a white pill with ink
                             # words (17.85:1 against the band), the focus ring white
CARD_SHADOW = "0 1px 2px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.04)"     # INK at 6 and 4 percent: decorative only


class Tone(NamedTuple):
    """A tone of a state: the colour of its words, the tint of its ground and the colour of its mark (a dot, a bar or an edge)."""
    text: str
    tint: str
    mark: str


TONES = {
    "attention": Tone("#92400E", "#FFFBEB", "#B45309"),    # the words 6.84:1 on the tint, the mark 4.84:1
    "problem": Tone("#991B1B", "#FEF2F2", "#B91C1C"),      # the words 7.60:1, the mark 5.91:1 on the tint and 6.47:1 on SURFACE
    "done": Tone("#166534", "#F0FDF4", "#15803D"),         # the words 6.81:1 on the tint and on GROUND, the mark 4.79:1
    "waiting": Tone("#334155", SURFACE_2, CONTROL),        # the words 9.45:1, the mark 4.34:1
}

# The PC's own fonts, by name only: Segoe UI on Windows, the system's own face elsewhere, so that no font file is fetched or served.
# The tables take the same face: gradio sets them in its monospace face otherwise. page_theme quotes a name with a space in it
# (gradio's Font) and leaves the others plain, since a browser reads a quoted "system-ui" as the name of a family.
FONTS = ("Segoe UI", "system-ui", "-apple-system", "Helvetica Neue", "Arial", "Noto Sans", "Liberation Sans", "sans-serif")
EASE = "cubic-bezier(.2,.8,.2,1)"      # the one ease of the page's motion, which is a change of colour only
_COLOUR = ("background-color", "border-color", "color")   # what a control changes on hover, in 120 ms on the way out (PAGE_CSS: in)


def page_theme(gr):
    """The theme of the page: gradio's Base (gr is gradio, which build_app and the review command import) with the PC's own fonts,
    the page's sizes and the tokens above, each in its place; then every dark value of the theme is its light twin, so that a PC in
    dark mode shows the same checked colours. A name that Base.set does not know raises."""
    sizes = gr.themes.sizes
    font = [gr.themes.Font(name) if " " in name else name for name in FONTS]
    problem = TONES["problem"]
    theme = gr.themes.Base(
        primary_hue=gr.themes.colors.slate, secondary_hue=gr.themes.colors.blue, neutral_hue=gr.themes.colors.slate,
        text_size=sizes.Size(name="text_aid", xxs="13px", xs="13px", sm="14px", md="16px", lg="20px", xl="24px", xxl="24px"),
        spacing_size=sizes.Size(name="spacing_aid", xxs="2px", xs="4px", sm="4px", md="8px", lg="8px", xl="12px", xxl="16px"),
        radius_size=sizes.Size(name="radius_aid", xxs="2px", xs="2px", sm="4px", md="4px", lg="6px", xl="6px", xxl="6px"),
        font=font, font_mono=font,
    ).set(
        body_background_fill=GROUND, background_fill_primary=SURFACE, background_fill_secondary=SURFACE_2,
        body_text_color=INK, body_text_color_subdued=MUTED, body_text_size="16px", body_text_weight="400",
        color_accent=ACCENT, color_accent_soft=ACCENT_TINT, border_color_accent=ACCENT, border_color_accent_subdued=ACCENT,
        border_color_primary=LINE,
        link_text_color=ACCENT, link_text_color_hover=ACCENT, link_text_color_visited=ACCENT, link_text_color_active=ACCENT,
        block_background_fill=SURFACE, block_border_color=LINE, block_border_width="1px", block_radius="8px", block_shadow="none",
        block_padding="12px 16px", block_title_text_color=INK, block_title_text_size="14px", block_title_text_weight="600",
        block_label_text_color=INK, block_label_text_size="14px", block_label_text_weight="600",
        block_label_background_fill=SURFACE, block_label_border_width="0px", block_label_shadow="none",
        block_info_text_color=MUTED, block_info_text_size="14px", block_info_text_weight="400",
        container_radius="8px", layout_gap="16px", form_gap_width="0px", panel_border_width="0px",
        section_header_text_size="16px", section_header_text_weight="600", prose_text_size="16px", prose_header_text_weight="600",
        input_background_fill=SURFACE, input_border_color=CONTROL, input_border_color_hover=MUTED, input_border_color_focus=ACCENT,
        input_border_width="1px", input_placeholder_color=CONTROL, input_radius="6px", input_shadow="none",
        input_shadow_focus=f"0 0 0 1px {ACCENT}", input_text_size="16px", input_padding="10px 12px",
        checkbox_border_color=CONTROL, checkbox_border_color_hover=MUTED, checkbox_border_color_focus=ACCENT,
        checkbox_border_color_selected=INK, checkbox_background_color_selected=INK, checkbox_border_radius="4px",
        checkbox_label_background_fill=SURFACE, checkbox_label_background_fill_hover=SURFACE_2,
        checkbox_label_background_fill_selected=ACCENT_TINT, checkbox_label_border_color=LINE_STRONG,
        checkbox_label_border_color_hover=CONTROL, checkbox_label_border_color_selected=ACCENT,
        checkbox_label_text_color=INK, checkbox_label_text_color_selected=INK, checkbox_label_text_size="16px",
        checkbox_label_padding="8px 12px", checkbox_label_gap="8px", checkbox_shadow="none",
        table_border_color=LINE, table_even_background_fill=SURFACE, table_odd_background_fill=SURFACE, table_radius="8px",
        table_row_focus=GROUND, table_text_color=INK, accordion_text_color=INK,
        button_border_width="1px", button_transition=", ".join(f"{part} 120ms {EASE}" for part in _COLOUR),
        button_transform_hover="none", button_transform_active="none",
        button_primary_background_fill=ACCENT, button_primary_background_fill_hover=ACCENT_HOVER, button_primary_border_color=ACCENT,
        button_primary_border_color_hover=ACCENT_HOVER, button_primary_text_color=SURFACE, button_primary_shadow="none",
        button_secondary_background_fill=SURFACE, button_secondary_background_fill_hover=SURFACE_2,
        button_secondary_border_color=CONTROL, button_secondary_border_color_hover=MUTED, button_secondary_text_color=INK,
        button_secondary_shadow="none",
        button_cancel_background_fill=SURFACE, button_cancel_background_fill_hover=problem.tint, button_cancel_border_color=problem.mark,
        button_cancel_border_color_hover=problem.text, button_cancel_text_color=problem.mark, button_cancel_text_color_hover=problem.text,
        button_large_padding="10px 20px", button_large_radius="6px", button_large_text_size="16px", button_large_text_weight="600",
        button_medium_padding="8px 16px", button_medium_radius="6px", button_medium_text_size="16px", button_medium_text_weight="600",
        button_small_padding="6px 12px", button_small_radius="6px", button_small_text_size="14px", button_small_text_weight="600",
        shadow_drop="none", shadow_drop_lg=f"0 0 0 1px {LINE_STRONG}", shadow_inset="none", shadow_spread="2px",
        error_background_fill=problem.tint, error_border_color=problem.mark, error_text_color=problem.text, error_icon_color=problem.mark,
        loader_color=ACCENT,
    )
    for name in [name for name in vars(theme) if name.endswith("_dark")]:       # light in dark mode too: the same checked values
        if hasattr(theme, light := name[:-len("_dark")]):
            setattr(theme, name, getattr(theme, light))
    return theme


_ATTENTION, _DONE, _PROBLEM = TONES["attention"], TONES["done"], TONES["problem"]
_CHIPS = "\n".join(f".chip.{name} {{ color: {tone.text}; background: {tone.tint}; }} .chip.{name}::before {{ background: {tone.mark}; }}"
                   for name, tone in TONES.items())

# The style rules the theme cannot set. The page library adds each rule a second time with .gradio-container.gradio-container-6-31-0
# .contain before it, which raises it above the library's own rules inside the page, and keeps only that second one inside @media:
# so a selector here does not begin with .gradio-container, except where it is about the container itself, and a comma stands
# between selectors only, never inside one. A class given to a component is written .block.<class>, because the library puts the
# class of a Markdown on its block and again on the text inside it. The figures line up where they are compared (tables, decision
# rows, the list of In Exact invoeren, chips and the fold), and nowhere else, since in the PC's fallback faces they spread the
# hyphens of a day ("16 - 11 - 2026").
PAGE_CSS = f"""
/* the container cuts off what overflows sideways, as the page library's own overflow: hidden does, but makes no box that scrolls,
   so that a bar can stay at the top of the window while a list under it scrolls (position: sticky) */
.gradio-container {{ overflow: clip; }}

/* focus: a ring of 2 px in the accent, 2 px off the control, at once; a field to type in and a dropdown show it by their own edge,
   2 px in the accent (the theme), since the ring of the field inside a dropdown would stand inside its box; a cell of a table keeps
   the page library's ring inside the cell, which the table's scrolling box would cut off outside it */
:focus-visible {{ outline: 2px solid {ACCENT}; outline-offset: 2px; transition: none; }}
textarea:focus-visible, input[type="text"]:focus-visible, input[type="number"]:focus-visible, [role="combobox"]:focus-visible {{ outline: none; }}
.table-container .body-cell:focus-visible {{ outline-offset: -2px; }}

/* headings: one h1, the tool's name, white on the head band; an h2 for each block of a tab, with 16 px above it besides the gap
   of the layout; an h3 inside a block; running text at 16/24, over the page library's line of 1.625 for a paragraph, and with
   help at most 70 characters a line */
.prose h1 {{ font-size: 24px; line-height: 32px; font-weight: 600; margin: 0; color: {SURFACE}; }}
.prose h2 {{ font-size: 20px; line-height: 28px; font-weight: 600; margin: 16px 0 0; }}
.prose h3 {{ font-size: 16px; line-height: 24px; font-weight: 600; margin: 8px 0 0; }}
.prose strong {{ font-weight: 600; }}
.prose p {{ line-height: 24px; }}
.prose p, .prose li, .info-text {{ max-width: 70ch; }}

/* fields lie flat on the ground, each with its own edge as its only box: the page library puts neighbouring fields in one box
   (.form) whose gaps it paints in the line colour, which made the grey bar behind the language switch */
.form {{ background: none; border: 0; border-radius: 0; box-shadow: none; gap: 16px; overflow: visible; }}
.form > .block {{ background: none; padding: 0; }}

/* a dropdown has the edge of a field to type in, in the control colour, with its hover and its focus: the page library draws the box
   of a dropdown (the one that holds its inner row, .wrap-inner) in the line colour, which is never the only edge of a control */
.wrap:has(> .wrap-inner) {{ border-color: var(--input-border-color); }}
.wrap:has(> .wrap-inner):hover {{ border-color: var(--input-border-color-hover); }}
.wrap:has(> .wrap-inner):focus-within {{ border-color: var(--input-border-color-focus); }}

/* the head band: the tool's name in white on the left, the language switch on the right as a compact pair on a white pill, in boxes
   as wide as the pair (the switch's scale 0 keeps the page library from letting the outer one grow); the band reaches across the
   whole window and over the gap down to the page's tabs, which lie on it too, by a border image that paints outside the box
   without clipping anything, so that the "..." menu in which the page library folds the tabs of a narrow window opens over the page */
#page-head {{ align-items: center; padding: 4px 0 0; border-image: linear-gradient({BAND}, {BAND}) fill 0 // 100vmax 100vmax 16px; }}
#page-head > .form {{ flex: 0 0 auto; width: auto; min-width: 0; margin-left: auto; }}
.block.language-switch {{ flex: 0 0 auto; }}
.block.language-switch .wrap {{ flex-wrap: nowrap; gap: 4px; padding: 4px; background: {SURFACE}; border-radius: 999px; }}
.block.language-switch label {{ padding: 4px 10px; font-size: 14px; line-height: 20px; border-radius: 999px; }}

/* the tabs: the chosen one in ink and 600, the others muted; the sections of Instellingen under a bar in the accent, which shows at
   once, one step under the page's tabs; the page's own tabs on the head band as pills, the chosen one white with ink words, the
   others light, a step lighter in hover, the focus ring white on the band; its "..." press light on the band, its list as it was */
[role="tab"] {{ font-size: 16px; font-weight: 400; color: {MUTED}; }}
[role="tab"][aria-selected="true"] {{ color: {INK}; font-weight: 600; }}
[role="tab"][aria-selected="true"]::after {{ animation: none; }}
#settings-sections [role="tab"] {{ font-size: 15px; }}
.page-tabs > .tab-wrapper {{ height: auto; margin-top: -8px; padding: 0 0 8px; border-image: linear-gradient({BAND}, {BAND}) fill 0 // 0 100vmax; }}
.page-tabs > .tab-wrapper > .tab-container {{ height: auto; gap: 4px; overflow: visible; }}
.page-tabs > .tab-wrapper > .tab-container::after {{ display: none; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"] {{ height: 36px; padding: 0 16px; border-radius: 999px; color: {LINE_STRONG}; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"]:hover:not([aria-selected="true"]) {{ background: {INK_HOVER}; color: {SURFACE}; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"][aria-selected="true"] {{ background: {SURFACE}; color: {INK}; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"][aria-selected="true"]::after {{ display: none; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"]:focus-visible, .page-tabs > .tab-wrapper > .overflow-menu > button:focus-visible {{ outline-color: {SURFACE}; }}
.page-tabs > .tab-wrapper > .overflow-menu > button {{ min-width: 44px; min-height: 36px; color: {SURFACE}; background: none; border-color: {LINE_STRONG}; border-radius: 999px; }}
.page-tabs > .tab-wrapper > .overflow-menu > button:hover {{ background: {INK_HOVER}; }}

/* cards: the strip, the fold, the decisions of a case, its documents and comparison, the list of In Exact invoeren, the parts of
   Prijslijst and Bestanden and each section of Instellingen lie on white with a soft edge and shadow; the h2 of a card starts at
   the card's own padding */
#review-strip .strip, #review-all, #review-case, #review-evidence, .form:has(> #hand-rows), #pricelist-body, .column:has(> #files-title), #settings-sections > .tabitem {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; box-shadow: {CARD_SHADOW}; }}
#review-case, #review-evidence, #pricelist-body, .column:has(> #files-title) {{ padding: 24px; }}
#settings-sections > .tabitem {{ padding: 8px 24px 24px; }}
.form:has(> #hand-rows) {{ padding: 12px; }}
#review-case .prose h2, #review-evidence > .block .prose h2, #files-title .prose h2 {{ margin-top: 0; }}
#review-record {{ border-top: 1px solid {LINE}; padding-top: 8px; margin-top: 8px; }}

/* hover: a step of one tone (the theme), in 150 ms; the way back takes the theme's 120 ms */
button:hover, label:hover {{ transition-duration: 150ms; }}

/* tables: cells at 14/20 in the page's face, heads in 600 and muted on the tint (with !important, as the page library's own rule
   of a head cell sets its ground so), a rule under each row and none between the columns, drawn as
   the inset shadow with which the page library also rings a chosen cell (a chosen cell keeps its ring); a head's words whole, as the
   page library sizes a table's columns to hold them in a page at least 1280 px wide, and in a narrower page (a zoom of 150 or 200
   percent) a head word that cannot fit its column broken, since the library's head cell cuts off what overflows it */
.table-container {{ --input-text-size: 14px; --line-md: 20px; font-variant-numeric: tabular-nums; }}
.table-container .header-cell {{ font-weight: 600; color: {MUTED}; background-color: {TINT} !important; }}
.table-container .header-cell:not(.focus) {{ box-shadow: inset 0 -1px 0 {LINE_STRONG}; }}
.table-container .body-cell:not(.cell-selected) {{ box-shadow: inset 0 -1px 0 {LINE}; }}
.table-container .cell-wrap {{ padding: 10px; min-height: 40px; }}
.table-container .header-cell, .table-container .header-cell * {{ word-break: normal; overflow-wrap: normal; hyphens: none; }}
@media (max-width: 1279.98px) {{
  .table-container .header-cell, .table-container .header-cell * {{ overflow-wrap: break-word; }}
}}
.block.decision-what, .block.hand-rows, .chip, #review-all-table {{ font-variant-numeric: tabular-nums; }}

/* the two documents: each page on the second surface, the name above it as a plain label without the page library's chip and icon;
   each page at the full width of its column with its own height, not in the library's square cell, the pages one under the other
   in the gallery's own height, which scrolls (a click on a page still opens the library's larger view, which these rules leave) */
.block.doc-view .grid-container {{ grid-template-columns: 1fr; grid-template-rows: none; grid-auto-rows: auto; }}
.block.doc-view .thumbnail-lg {{ aspect-ratio: auto; height: auto; }}
.block.doc-view .thumbnail-lg > img {{ height: auto; object-fit: contain; }}
.block.doc-view .grid-wrap, .block.doc-view .gallery-container {{ background: {SURFACE_2}; }}
.block.doc-view label[data-testid="block-label"] {{ position: static; background: none; border: 0; box-shadow: none; padding: 0 0 8px;
  font-size: 14px; line-height: 20px; font-weight: 600; color: {INK}; }}
.block.doc-view label[data-testid="block-label"] svg {{ display: none; }}

/* notices: one look for every standing message (and the notice of Prijslijst), the tint with a bar of 4 px on the left; one that
   asks the buyer to act in the tint of attention */
.block.notice, #pricelist-notice {{ background: {TINT}; border: 0; border-left: 4px solid {CONTROL}; border-radius: 6px; padding: 12px 16px; }}
.block.notice.attention {{ background: {_ATTENTION.tint}; border-left-color: {_ATTENTION.mark}; }}
.block.notice .prose {{ font-size: 16px; line-height: 24px; }}

/* answer lines at 16/24 right under their buttons; the note of a decision row at 14 px in 600, in the colour of done, and a refusal
   there (the class refused) in the colour of a problem */
.block.answer .prose {{ font-size: 16px; line-height: 24px; }}
.block.row-note .prose, .block.row-note .prose * {{ font-size: 14px; line-height: 20px; font-weight: 600; color: {_DONE.text}; }}
.block.row-note.refused .prose, .block.row-note.refused .prose * {{ color: {_PROBLEM.text}; }}

/* the one sentence in the place of an empty list or table ("Geen afwijkingen in deze zaak.", "Nog geen bestanden verwerkt. ..."),
   muted, so that it reads as the absence of rows and not as a message */
.block.empty-line .prose, .block.empty-line .prose * {{ color: {MUTED}; }}

/* the strip of Beoordelen and its chips: a chip of 24 px with a dot of 6 px before its word, in the tint of its state, so that a
   colour never says something alone; under it the fold's table, ruled */
#review-strip .html-container {{ padding: 0; }}
#review-strip .strip {{ display: flex; flex-direction: column; gap: 8px; padding: 12px 16px; }}
#review-strip .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
#review-strip .meta {{ font-size: 13px; line-height: 18px; color: {MUTED}; }}
.chip {{ display: inline-flex; align-items: center; gap: 6px; min-height: 24px; padding: 0 8px; border-radius: 4px; font-size: 13px;
  line-height: 18px; font-weight: 600; }}
.chip::before {{ content: ""; flex: none; width: 6px; height: 6px; border-radius: 3px; }}
{_CHIPS}
#review-all-table table {{ width: 100%; margin: 0; border: 0; border-collapse: collapse; font-size: 14px; line-height: 20px; }}
#review-all-table tr {{ border: 0; }}
#review-all-table th, #review-all-table td {{ text-align: left; vertical-align: top; padding: 10px; border: 0; border-bottom: 1px solid {LINE};
  background: none; }}
#review-all-table th {{ font-weight: 600; color: {MUTED}; border-bottom-color: {LINE_STRONG}; background: {TINT}; }}
#review-all-table td:first-child {{ white-space: nowrap; }}
#review-all-table .empty {{ color: {MUTED}; }}

/* the decision rows: no box, a rule under each but the last, which the card's edge closes; the field and the line, then the
   values, then where each was read as meta */
.row.decision-row {{ border-bottom: 1px solid {LINE}; padding: 12px 0; }}
#review-case .row.decision-row:last-child {{ border-bottom: 0; padding-bottom: 0; }}
.block.decision-what .prose p {{ margin: 0 0 4px; }}
.block.decision-what .prose p:nth-of-type(n+3) {{ font-size: 13px; line-height: 18px; color: {MUTED}; }}

/* the main action of a case, "Oudste zaak openen" beside the line that asks for a case, the two pairs of In Exact invoeren and the
   presses under a form at their natural width, at least 200 px, those under a form at the start of the line: no bar across the
   page, and no words broken over two lines; a filled button that cannot be pressed yet calm grey with muted words */
#review-close > button, #review-close > .row > button, #review-oldest, #hand-actions .row > button, .form:has(> #hand-rows) + .row > button {{ flex: 0 0 auto; width: auto; min-width: 200px; }}
#files-give, #pricelist-file-prices, #pricelist-view, #pricelist-carry-out, #settings-sections button.primary {{ flex: 0 0 auto; width: auto; min-width: 200px; align-self: flex-start; }}
/* "Lijst vernieuwen" beside the text of In Exact invoeren and of Bestanden: its column starts from the button's natural width, and
   the button keeps its words on one line at the right end of its column, also at a width of 683 px */
.column:has(> #hand-intro) + .column, .column:has(> #files-done) + .column {{ flex-basis: auto; }}
.column:has(> #hand-intro) + .column > button, #files-refresh {{ width: auto; min-width: 160px; white-space: nowrap; align-self: flex-end; }}
button.primary:disabled {{ background: {LINE}; border-color: {LINE}; color: {MUTED}; opacity: 1; }}

/* the drop zone of a file: a dashed edge in the control colour on white */
#files-upload, #pricelist-export, #pricelist-list {{ border: 1px dashed {CONTROL}; border-radius: 8px; background: {SURFACE}; }}

/* a button beside a field ("Lijst vernieuwen" beside "Zaak", "Alles hiervan is ingevoerd" beside its dropdown, the small press beside
   the supplier on Prijslijst) stands level with the field and not with its label and help line, which the page library's row puts
   at the top; the library lays out such a row as the fields in a form block of their own, then the buttons */
.row > .form ~ button {{ align-self: flex-end; }}

/* In Exact invoeren: the top pair of buttons and their answer stay at the top of the window while the list scrolls, and a box that
   the keyboard reaches never hides under them */
#hand-actions {{ position: sticky; top: 0; z-index: 5; background: {GROUND}; padding: 8px 0; }}
.block.hand-rows input, .hand-copy button {{ scroll-margin-top: 148px; }}

/* In Exact invoeren: behind each row its copy buttons, which the page's script draws (PAGE_JS) and which name the text they copy:
   the row in the first column and its buttons in the second, on one line, once the script has marked the list (hand-copies), so
   that a page without the script keeps the list as it was. Both columns give way in a narrow window, and a long code breaks inside
   its button, so that no button lies over a row at a zoom of 200 percent; the list keeps 4 px on the right for the focus ring of
   a row's last button. The buttons have the look of a small secondary button, 32 px high. A button just pressed shows the word
   "Gekopieerd" or "Niet gekopieerd" for a moment, in the colours of done or of a problem: the word says it, the colour only goes
   with it. The line of what the press copied stands in the column of the two buttons, which stays in view, one line high also
   while it is empty, so that a press moves nothing, in the colour of done, or of a problem when the browser gave no clipboard */
.block.hand-rows .wrap.hand-copies {{ display: grid; grid-template-columns: minmax(0, max-content) minmax(0, max-content); justify-content: start; align-items: center; gap: 8px 12px; padding-right: 4px; }}
.hand-copy {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.hand-copy button {{ min-height: 32px; max-width: 100%; padding: 4px 12px; font-size: 14px; line-height: 20px; font-weight: 600; color: {INK}; background: {SURFACE}; border: 1px solid {CONTROL}; border-radius: 4px; cursor: pointer; text-align: left; overflow-wrap: break-word; }}
.hand-copy button:hover {{ background: {SURFACE_2}; border-color: {MUTED}; }}
.hand-copy button.copied {{ color: {_DONE.text}; background: {_DONE.tint}; border-color: {_DONE.mark}; }}
.hand-copy button.refused {{ color: {_PROBLEM.text}; background: {_PROBLEM.tint}; border-color: {_PROBLEM.mark}; }}
#hand-copy, #hand-copy .html-container {{ margin: 0; padding: 0; }}
#hand-copy-said {{ min-height: 20px; margin: 0; font-size: 14px; line-height: 20px; font-weight: 600; color: {_DONE.text}; }}
#hand-copy-said.refused {{ color: {_PROBLEM.text}; }}

/* Prijslijst: the small press that asks to take a supplier's prices away has the width of its words, on one line (its scale 0 gives
   it no share of the row); its question, with "Ja, weghalen" and "Niet weghalen", is one standing message in the look of a notice,
   its words and its two presses centred on one line */
#pricelist-remove {{ flex: 0 0 auto; width: auto; }}
#pricelist-remove-confirm {{ align-items: center; background: {TINT}; border-left: 4px solid {CONTROL}; border-radius: 6px; padding: 12px 16px; }}

/* Prijslijst: the five fields of the columns of a price list keep their words clear of the dropdown's arrow, and end a long title
   with "..." */
#pricelist-column-item_code input, #pricelist-column-price input, #pricelist-column-basis input, #pricelist-column-valid_from input, #pricelist-column-valid_to input {{ padding-right: 28px; text-overflow: ellipsis; }}

/* short fields (a percentage, an amount, a number of days, a time) at most 16 rem wide */
.block.short-field textarea, .block.short-field input {{ max-width: 16rem; }}

/* no motion where the PC asks for less of it */
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ transition: none !important; animation: none !important; }}
}}
"""

# Run once by gradio when the page has loaded. It names the page's language on <html> (nl, and en while English is chosen; gradio's
# own page says en), so that a screen reader and the browser read the page in its language; and it marks each answer line
# (.block.answer, and the note of a decision row, .block.row-note) once as a polite live region (role status), so that a screen
# reader reads an answer as it comes, without moving the focus. It watches the page for lines that appear later. It holds no
# address. The status line of "Beoordelen" and every note that answers a press on a tab have the class answer (aid/review/page.py).
#
# It also draws the copy buttons of the tab "In Exact invoeren" (copies). The tab gives them in a hidden element (hand-copy-data:
# _copy_data of aid/review/page.py), by the value of each row's tick box, which the page library writes as the box's name. After
# each tick box the script puts a holder with the row's buttons, as the next element and never inside the box's label, so that the
# label stays the name of the box alone; it does so again whenever the rows or the element change (the rows of a list read again,
# another language), and leaves a list that is right as it is, so that its own change does not call it again without end. A press
# copies the button's text through the browser's clipboard, or where the browser gives a page none, through a field of the page
# for a moment, after which the focus is back on the button; it never ticks the row; it says what it copied in the line in the
# column of the two buttons (hand-copy-said), or that copying did not work; and the pressed button itself reads "Gekopieerd" or
# "Niet gekopieerd" for a moment, at the width it had, with its name for a screen reader unchanged. A text of a document is set
# as text, never read as markup, and never as a pattern of a replacement.
PAGE_JS = """
() => {
  const language = () => {
    const chosen = document.querySelector('.language-switch input:checked');
    document.documentElement.lang = chosen && chosen.value === 'en' ? 'en' : 'nl';
  };
  const answers = () => document.querySelectorAll('.block.answer, .block.row-note').forEach((line) => {
    if (!line.hasAttribute('role')) {
      line.setAttribute('role', 'status');
      line.setAttribute('aria-live', 'polite');
    }
  });
  const copies = () => {
    const data = document.getElementById('hand-copy-data');
    const list = document.querySelector('#hand-rows [data-testid="checkbox-group"]');
    if (!data || !list) return;
    let rows = {};
    try { rows = JSON.parse(data.dataset.rows || '{}'); } catch (error) { rows = {}; }
    const boxes = Array.from(list.children).filter((child) => child.tagName === 'LABEL');
    const wanted = boxes.map((box) => JSON.stringify(rows[(box.querySelector('input') || {}).name] || []));
    const holders = Array.from(list.children).filter((child) => child.classList.contains('hand-copy'));
    if (holders.length === boxes.length && boxes.every((box, at) => box.nextElementSibling === holders[at] && holders[at].dataset.made === wanted[at])) return;
    holders.forEach((holder) => holder.remove());
    boxes.forEach((box, at) => {
      const holder = document.createElement('span');
      holder.className = 'hand-copy';
      holder.dataset.made = wanted[at];
      JSON.parse(wanted[at]).forEach(([text, words, name]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = words;
        button.setAttribute('aria-label', name);
        button.dataset.copy = text;
        holder.appendChild(button);
      });
      box.after(holder);
    });
    list.classList.add('hand-copies');
  };
  const copied = (button, done) => {
    const data = document.getElementById('hand-copy-data');
    const line = document.getElementById('hand-copy-said');
    const words = (data && (done ? data.dataset.copied : data.dataset.failed)) || '';
    if (line) {
      line.textContent = words.split('{text}').join(button.dataset.copy || '');
      line.classList.toggle('refused', !done);
    }
    const word = (data && (done ? data.dataset.done : data.dataset.notDone)) || '';
    clearTimeout(button.copiedTimer);
    if (!button.dataset.words) {
      button.dataset.words = button.textContent;
      button.style.minWidth = button.offsetWidth + 'px';
    }
    button.classList.toggle('copied', done);
    button.classList.toggle('refused', !done);
    if (word) button.textContent = word;
    button.copiedTimer = setTimeout(() => {
      button.classList.remove('copied', 'refused');
      button.textContent = button.dataset.words;
      button.style.minWidth = '';
      delete button.dataset.words;
    }, 1500);
  };
  const throughAField = (text, button) => {
    const field = document.createElement('textarea');
    field.value = text;
    field.setAttribute('readonly', '');
    field.setAttribute('aria-hidden', 'true');
    field.style.position = 'fixed';
    field.style.opacity = '0';
    document.body.appendChild(field);
    field.select();
    let done = false;
    try { done = document.execCommand('copy'); } catch (error) { done = false; }
    field.remove();
    button.focus({ preventScroll: true });
    return done;
  };
  language();
  answers();
  copies();
  new MutationObserver(() => { language(); answers(); copies(); })
    .observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['name', 'data-rows'] });
  document.addEventListener('change', language, true);
  document.addEventListener('click', (event) => {
    const button = event.target instanceof Element ? event.target.closest('.hand-copy button') : null;
    if (!button) return;
    event.preventDefault();
    const text = button.dataset.copy || '';
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(() => copied(button, true), () => copied(button, throughAField(text, button)));
    } else {
      copied(button, throughAField(text, button));
    }
  });
}
"""
