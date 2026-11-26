"""The look of the review page: one set of tokens for the whole page, each pair checked against the contrast formula of WCAG 2.x;
the gradio theme made of them (page_theme); the style rules the theme cannot set (PAGE_CSS); and the page's load script (PAGE_JS).
Text needs 4.5:1 against its ground; the edge of a control, a focus ring and a state mark need 3:1. The ratios beside the tokens
are those of that formula.

The page stays neutral and calm: a daylight grey ground on which workspace sections lie as 12 px white cards, a unified light
top bar (BAND, white chrome with a hairline rule below) holding the tool's name, a segmented language switch and calm pill tabs,
and one blue accent for the active tab, links, focus and the one primary action of a view. The four tones (TONES) appear only on
chips and lines that carry their word, so that a colour never says something alone: "attention" for what waits for the buyer
("Ter beoordeling", the approval banner, the notice of a failed case), "problem" for what went wrong ("Mislukt", the problems of
the assistant), "done" for what is finished ("Vanzelf afgesloten", "Door u afgesloten") and "waiting" for what waits for someone
else ("Wacht op leverancier", "Wacht op Copilot"). Each tone has the colour of its words, the tint of its ground and the colour of
its mark (a dot, a bar or an edge).

The type is the PC's own face (Segoe UI on Windows), at 13, 14, 15, 16, 19 and 20 px; corners are 6 px on compact copy buttons,
8 px on buttons, fields and notices, 12 px on workspace cards and full pills on status chips and tabs; a soft layered shadow
(CARD_SHADOW) lifts cards from the ground; and the only motion is a brief colour or shadow transition, 150 ms in and 120 ms out,
none at all where the PC asks for reduced motion. The theme copies each of its dark values from its light twin, so that a PC in
dark mode shows the same checked page.

The review command gives the theme, the style rules and the script to launch() (launch_options of aid/review/__main__.py), never
to gr.Blocks(), which warns about them in gradio 6.31; the Colab notebook launches the page with the page library's own look.
Nothing here names an address outside the PC: the theme is built in code and never named (gradio asks the Hub for a theme it does
not know by name), its fonts are names of fonts the PC has, so that no font file is fetched or served, and the style rules and the
script hold no address. The style rules name some class names of gradio 6.31.0 itself (aid/requirements.txt pins that version):
where a later version renames one, that part keeps the page library's own look.
"""
from typing import NamedTuple

GROUND = "#F5F5F7"           # daylight canvas on which workspace cards rest; INK 16.29:1, MUTED 6.55:1, CONTROL 4.44:1
SURFACE = "#FFFFFF"          # cards, top chrome, fields, tables, document frames; INK 17.74:1
SURFACE_2 = "#EFEFF2"        # segmented control track, document backdrop, secondary hover; INK 15.86:1
TINT = "#FAFAFC"             # recessed disclosure bar, notices, table headers; INK 17.07:1, MUTED 6.86:1, CONTROL 4.64:1
INK = "#111827"              # primary text; 17.74:1 on SURFACE, 16.29:1 on GROUND
INK_HOVER = "#E8E8ED"        # tab hover pill on the light top bar; INK on it 14.77:1
MUTED = "#525866"            # help under a field, meta, column heads, inactive tabs; 7.13:1 on SURFACE, 6.55:1 on GROUND, 5.68:1 on LINE
CONTROL = "#6B7280"          # control edges and placeholders; 4.83:1 on SURFACE, 4.64:1 on TINT
LINE = "#E5E5EA"             # hairline dividers and card edges (decorative)
LINE_STRONG = "#D1D1D6"      # stronger rule under table heads (decorative)
ACCENT = "#0062D6"           # primary button with white text (5.65:1), active tab text, links, focus ring; 5.65:1 on SURFACE, 5.15:1 on ACCENT_TINT
ACCENT_HOVER = "#004EB0"     # primary button hover; white on it 7.73:1
ACCENT_TINT = "#EEF5FF"      # active tab pill and chosen option fill; INK 16.43:1, ACCENT 5.15:1
BAND = SURFACE               # unified light desktop top chrome bar; INK 17.74:1, MUTED 7.13:1, ACCENT 5.65:1
CARD_SHADOW = "0 1px 2px rgba(17, 24, 39, 0.04), 0 4px 12px rgba(17, 24, 39, 0.03)"     # soft layered shadow: decorative only


class Tone(NamedTuple):
    """A tone of a state: the colour of its words, the tint of its ground and the colour of its mark (a dot, a bar or an edge)."""
    text: str
    tint: str
    mark: str


TONES = {
    "attention": Tone("#92400E", "#FFFBEB", "#B45309"),    # the words 6.84:1 on the tint, the mark 4.84:1
    "problem": Tone("#991B1B", "#FEF2F2", "#B91C1C"),      # the words 7.60:1, the mark 5.91:1 on the tint and 6.47:1 on SURFACE
    "done": Tone("#166534", "#F0FDF4", "#15803D"),         # the words 6.81:1 on the tint and 7.13:1 on SURFACE, the mark 4.79:1
    "waiting": Tone("#374151", SURFACE_2, CONTROL),        # the words 9.10:1, the mark 4.21:1
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
        radius_size=sizes.Size(name="radius_aid", xxs="2px", xs="2px", sm="4px", md="6px", lg="8px", xl="8px", xxl="12px"),
        font=font, font_mono=font,
    ).set(
        body_background_fill=GROUND, background_fill_primary=SURFACE, background_fill_secondary=SURFACE_2,
        body_text_color=INK, body_text_color_subdued=MUTED, body_text_size="16px", body_text_weight="400",
        color_accent=ACCENT, color_accent_soft=ACCENT_TINT, border_color_accent=ACCENT, border_color_accent_subdued=ACCENT,
        border_color_primary=LINE,
        link_text_color=ACCENT, link_text_color_hover=ACCENT, link_text_color_visited=ACCENT, link_text_color_active=ACCENT,
        block_background_fill=SURFACE, block_border_color=LINE, block_border_width="1px", block_radius="12px", block_shadow="none",
        block_padding="12px 16px", block_title_text_color=INK, block_title_text_size="14px", block_title_text_weight="600",
        block_label_text_color=INK, block_label_text_size="14px", block_label_text_weight="600",
        block_label_background_fill=SURFACE, block_label_border_width="0px", block_label_shadow="none",
        block_info_text_color=MUTED, block_info_text_size="14px", block_info_text_weight="400",
        container_radius="12px", layout_gap="16px", form_gap_width="0px", panel_border_width="0px",
        section_header_text_size="16px", section_header_text_weight="600", prose_text_size="16px", prose_header_text_weight="600",
        input_background_fill=SURFACE, input_border_color=CONTROL, input_border_color_hover=MUTED, input_border_color_focus=ACCENT,
        input_border_width="1px", input_placeholder_color=CONTROL, input_radius="8px", input_shadow="none",
        input_shadow_focus=f"0 0 0 1px {ACCENT}", input_text_size="16px", input_padding="10px 12px",
        checkbox_border_color=CONTROL, checkbox_border_color_hover=MUTED, checkbox_border_color_focus=ACCENT,
        checkbox_border_color_selected=INK, checkbox_background_color_selected=INK, checkbox_border_radius="4px",
        checkbox_label_background_fill=SURFACE, checkbox_label_background_fill_hover=SURFACE_2,
        checkbox_label_background_fill_selected=ACCENT_TINT, checkbox_label_border_color=LINE_STRONG,
        checkbox_label_border_color_hover=CONTROL, checkbox_label_border_color_selected=ACCENT,
        checkbox_label_text_color=INK, checkbox_label_text_color_selected=INK, checkbox_label_text_size="16px",
        checkbox_label_padding="8px 12px", checkbox_label_gap="8px", checkbox_shadow="none",
        table_border_color=LINE, table_even_background_fill=SURFACE, table_odd_background_fill=SURFACE, table_radius="12px",
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
        button_large_padding="10px 20px", button_large_radius="8px", button_large_text_size="15px", button_large_text_weight="600",
        button_medium_padding="8px 16px", button_medium_radius="8px", button_medium_text_size="15px", button_medium_text_weight="600",
        button_small_padding="6px 12px", button_small_radius="8px", button_small_text_size="14px", button_small_text_weight="600",
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
.gradio-container {{ -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }}

/* focus: a ring of 2 px in the accent, 2 px off the control, at once; a field to type in and a dropdown show it by their own edge,
   2 px in the accent (the theme), since the ring of the field inside a dropdown would stand inside its box; a cell of a table keeps
   the page library's ring inside the cell, which the table's scrolling box would cut off outside it */
:focus-visible {{ outline: 2px solid {ACCENT}; outline-offset: 2px; transition: none; }}
textarea:focus-visible, input[type="text"]:focus-visible, input[type="number"]:focus-visible, [role="combobox"]:focus-visible {{ outline: none; }}
.table-container .body-cell:focus-visible {{ outline-offset: -2px; }}

/* headings: one h1, the tool's name in crisp ink on the light desktop top bar; an h2 for each section; an h3 inside a section;
   running text at 16/24 with help lines capped at 70 characters */
.prose h1 {{ font-size: 20px; line-height: 28px; font-weight: 600; margin: 0; color: {INK}; letter-spacing: -0.025em; }}
.prose h2 {{ font-size: 19px; line-height: 26px; font-weight: 600; margin: 16px 0 0; letter-spacing: -0.015em; }}
.prose h3 {{ font-size: 15px; line-height: 22px; font-weight: 600; margin: 8px 0 0; letter-spacing: -0.01em; }}
.prose strong {{ font-weight: 600; }}
.prose p {{ line-height: 24px; }}
.prose p, .prose li, .info-text {{ max-width: 70ch; }}

/* fields lie flat on the ground, each with its own edge as its only box: the page library puts neighbouring fields in one box
   (.form) whose gaps it paints in the line colour */
.form {{ background: none; border: 0; border-radius: 0; box-shadow: none; gap: 16px; overflow: visible; }}
.form > .block {{ background: none; padding: 0; }}

/* a dropdown has the edge of a field to type in, in the control colour, with its hover and its focus: the page library draws the box
   of a dropdown (the one that holds its inner row, .wrap-inner) in the line colour, which is never the only edge of a control */
.wrap:has(> .wrap-inner) {{ border-color: var(--input-border-color); }}
.wrap:has(> .wrap-inner):hover {{ border-color: var(--input-border-color-hover); }}
.wrap:has(> .wrap-inner):focus-within {{ border-color: var(--input-border-color-focus); }}
input[type="text"], input[type="number"], textarea, .wrap:has(> .wrap-inner) {{ box-shadow: 0 1px 2px rgba(17, 24, 39, 0.04); transition: border-color 120ms {EASE}, box-shadow 120ms {EASE}; }}
input[type="text"]:focus, input[type="number"]:focus, textarea:focus, .wrap:has(> .wrap-inner):focus-within {{ box-shadow: 0 0 0 3px {ACCENT_TINT}; }}

/* the unified light desktop top chrome: the tool's name in crisp ink on the left, a segmented language switch on SURFACE_2 on the
   right, and the page's navigation tabs integrated directly beneath with a full-width hairline divider */
#page-head {{ align-items: center; padding: 10px 0 4px; border-image: linear-gradient({BAND}, {BAND}) fill 0 // 100vmax 100vmax 16px; }}
#page-head > .form {{ flex: 0 0 auto; width: auto; min-width: 0; margin-left: auto; }}
.block.language-switch {{ flex: 0 0 auto; }}
.block.language-switch .wrap {{ flex-wrap: nowrap; gap: 2px; padding: 3px; background: {SURFACE_2}; border-radius: 999px; }}
.block.language-switch label {{ position: relative; gap: 0; padding: 4px 14px; font-size: 13px; line-height: 20px; font-weight: 500; color: {MUTED}; background: transparent; border: 0; border-radius: 999px; }}
.block.language-switch label:hover {{ color: {INK}; background: {INK_HOVER}; }}
.block.language-switch label.selected {{ background: {SURFACE}; color: {INK}; font-weight: 600; box-shadow: 0 1px 2px rgba(17, 24, 39, 0.10); }}
.block.language-switch input[type="radio"] {{ position: absolute; opacity: 0; width: 0; height: 0; margin: 0; padding: 0; pointer-events: none; }}
.block.language-switch label:has(input:focus-visible) {{ outline: 2px solid {ACCENT}; outline-offset: 2px; }}

/* the tabs: calm desktop pills on the light chrome bar; active tab in soft accent tint; settings sub-tabs as a clean segmented bar */
[role="tab"] {{ font-size: 15px; font-weight: 500; color: {MUTED}; }}
[role="tab"][aria-selected="true"] {{ color: {INK}; font-weight: 600; }}
[role="tab"][aria-selected="true"]::after {{ animation: none; }}
#settings-sections [role="tab"] {{ font-size: 14px; }}
.page-tabs > .tab-wrapper {{ height: auto; margin-top: -8px; padding: 4px 0 12px; border-bottom: 1px solid {LINE}; border-image: linear-gradient({BAND}, {BAND}) fill 0 // 0 100vmax; }}
.page-tabs > .tab-wrapper {{ box-shadow: -50vmax 1px 0 0 {LINE}, 50vmax 1px 0 0 {LINE}, 0 1px 0 0 {LINE}; }}
.page-tabs > .tab-wrapper > .tab-container {{ height: auto; gap: 4px; overflow: visible; }}
.page-tabs > .tab-wrapper > .tab-container::after {{ display: none; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"] {{ height: 34px; padding: 0 14px; border-radius: 999px; font-size: 14px; color: {MUTED}; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"]:hover:not([aria-selected="true"]) {{ background: {INK_HOVER}; color: {INK}; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"][aria-selected="true"] {{ background: {ACCENT_TINT}; color: {ACCENT}; font-weight: 600; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"][aria-selected="true"]::after {{ display: none; }}
.page-tabs > .tab-wrapper > .tab-container [role="tab"]:focus-visible, .page-tabs > .tab-wrapper > .overflow-menu > button:focus-visible {{ outline-color: {ACCENT}; }}
.page-tabs > .tab-wrapper > .overflow-menu > button {{ min-width: 44px; min-height: 34px; color: {INK}; background: {SURFACE}; border-color: {CONTROL}; border-radius: 999px; }}
.page-tabs > .tab-wrapper > .overflow-menu > button:hover {{ background: {SURFACE_2}; }}
.page-tabs > .tabitem {{ padding-top: 24px; }}

/* cards: workspace sections rest as crisp 12 px white sheets with soft layered shadows on the daylight canvas */
#review-strip-row, #review-case, #review-evidence, .form:has(> #hand-rows), #pricelist-body, .column:has(> #files-title), #settings-sections > .tabitem {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; box-shadow: {CARD_SHADOW}; }}
#review-case, #review-evidence, #pricelist-body, .column:has(> #files-title) {{ padding: 28px 32px; }}
#settings-sections > .tabitem {{ padding: 12px 28px 28px; }}
.form:has(> #hand-rows) {{ padding: 14px 16px; }}
#review-case .prose h2, #review-evidence > .block .prose h2, #files-title .prose h2 {{ margin-top: 0; }}
#review-record {{ border-top: 1px solid {LINE}; padding-top: 16px; margin-top: 16px; }}

/* hover: a step of one tone (the theme), in 150 ms; the way back takes the theme's 120 ms */
button:hover, label:hover {{ transition-duration: 150ms; }}
button.primary:not(:disabled) {{ box-shadow: 0 1px 2px rgba(0, 98, 214, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.16); letter-spacing: -0.005em; }}
button.primary:hover:not(:disabled) {{ box-shadow: 0 2px 8px rgba(0, 98, 214, 0.30); }}
button.secondary:not(:disabled) {{ box-shadow: 0 1px 2px rgba(17, 24, 39, 0.05); letter-spacing: -0.005em; }}
button.secondary:hover:not(:disabled) {{ box-shadow: 0 1px 3px rgba(17, 24, 39, 0.10); }}

/* tables: cells at 14/20 in the page's face, heads in 600 and muted on the tint (with !important, as the page library's own rule
   of a head cell sets its ground so), a rule under each row and none between the columns, drawn as
   the inset shadow with which the page library also rings a chosen cell (a chosen cell keeps its ring); a head's words whole, as the
   page library sizes a table's columns to hold them in a page at least 1280 px wide, and in a narrower page (a zoom of 150 or 200
   percent) a head word that cannot fit its column broken, since the library's head cell cuts off what overflows it */
.table-container {{ --input-text-size: 14px; --line-md: 20px; font-variant-numeric: tabular-nums; }}
.table-container .header-cell {{ font-weight: 600; color: {MUTED}; background-color: {TINT} !important; }}
.table-container .header-cell:not(.focus) {{ box-shadow: inset 0 -1px 0 {LINE_STRONG}; }}
.table-container .body-cell:not(.cell-selected) {{ box-shadow: inset 0 -1px 0 {LINE}; }}
.table-container .cell-wrap {{ padding: 10px 12px; min-height: 40px; }}
.table-container .header-cell, .table-container .header-cell * {{ word-break: normal; overflow-wrap: normal; hyphens: none; }}
@media (max-width: 1279.98px) {{
  .table-container .header-cell, .table-container .header-cell * {{ overflow-wrap: break-word; }}
}}
.block.decision-what, .block.hand-rows, .chip, #review-all-table, #review-next-preview {{ font-variant-numeric: tabular-nums; }}

/* the two documents: each page on the second surface, the name above it as a plain label without the page library's chip and icon;
   each page at the full width of its column with its own height, not in the library's square cell, the pages one under the other
   in the gallery's own height, which scrolls (a click on a page still opens the library's larger view, which these rules leave) */
.block.doc-view .grid-container {{ grid-template-columns: 1fr; grid-template-rows: none; grid-auto-rows: auto; }}
.block.doc-view .thumbnail-lg {{ aspect-ratio: auto; height: auto; }}
.block.doc-view .thumbnail-lg > img {{ height: auto; object-fit: contain; }}
.block.doc-view .grid-wrap, .block.doc-view .gallery-container {{ background: {SURFACE_2}; border-radius: 8px; }}
.block.doc-view label[data-testid="block-label"] {{ position: static; background: none; border: 0; box-shadow: none; padding: 0 0 8px;
  font-size: 14px; line-height: 20px; font-weight: 600; color: {INK}; }}
.block.doc-view label[data-testid="block-label"] svg {{ display: none; }}

/* notices: calm recessed callouts on TINT; attention callouts in amber tint */
.block.notice, #pricelist-notice {{ background: {TINT}; border: 0; border-left: 4px solid {CONTROL}; border-radius: 6px; padding: 12px 16px; }}
.block.notice.attention {{ background: {_ATTENTION.tint}; border-left-color: {_ATTENTION.mark}; }}
.block.notice .prose {{ font-size: 15px; line-height: 22px; }}

/* answer lines at 16/24 right under their buttons; the note of a decision row at 14 px in 600, in the colour of done, and a refusal
   there (the class refused) in the colour of a problem */
.block.answer .prose {{ font-size: 16px; line-height: 24px; }}
.block.row-note .prose, .block.row-note .prose * {{ font-size: 14px; line-height: 20px; font-weight: 600; color: {_DONE.text}; }}
.block.row-note.refused .prose, .block.row-note.refused .prose * {{ color: {_PROBLEM.text}; }}

/* the one sentence in the place of an empty list or table, muted, so that it reads as the absence of rows and not as a message */
.block.empty-line .prose, .block.empty-line .prose * {{ color: {MUTED}; }}

/* Beoordelen: one unified status bar card holding both the status pills and the assistant action button, a quiet recessed
   disclosure row for the 7-day history, a clean case-picker command row, a centered idle card, and a centered post-close
   completion stage that replaces the duplicate pick card while the next waiting case is offered */
#review-strip-row {{ flex-wrap: nowrap; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 20px; }}
#review-strip {{ flex: 1 1 0; width: auto; min-width: 0; }}
@media (max-width: 760px) {{
  #review-strip-row {{ flex-wrap: wrap; }}
  #review-strip {{ flex: 1 1 100%; }}
}}
#review-strip .html-container {{ padding: 0; }}
#review-strip .strip {{ display: flex; flex-direction: column; gap: 8px; padding: 0; background: none; border: 0; box-shadow: none; }}
#review-strip .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
#review-strip .meta {{ font-size: 13px; line-height: 18px; color: {MUTED}; }}
#review-strip-action {{ flex: 0 0 auto; width: auto; align-self: center; white-space: nowrap; min-height: 36px; padding: 6px 16px; font-size: 13px; }}
.chip {{ display: inline-flex; align-items: center; gap: 6px; min-height: 24px; padding: 2px 10px; border-radius: 999px; font-size: 13px;
  line-height: 18px; font-weight: 600; letter-spacing: -0.005em; }}
.chip::before {{ content: ""; flex: none; width: 6px; height: 6px; border-radius: 3px; }}
{_CHIPS}
#review-all {{ background: {TINT}; border: 1px solid {LINE}; border-radius: 10px; box-shadow: none; transition: border-color 120ms {EASE}, background-color 120ms {EASE}; }}
#review-all:hover {{ border-color: {LINE_STRONG}; background: {SURFACE}; }}
#review-all > button {{ font-size: 14px; font-weight: 600; color: {MUTED}; }}
#review-all > button:hover {{ color: {INK}; }}
#review-all-table table {{ width: 100%; margin: 0; border: 0; border-collapse: collapse; font-size: 14px; line-height: 20px; }}
#review-all-table tr {{ border: 0; }}
#review-all-table th, #review-all-table td {{ text-align: left; vertical-align: top; padding: 10px 12px; border: 0; border-bottom: 1px solid {LINE};
  background: none; }}
#review-all-table th {{ font-weight: 600; color: {MUTED}; border-bottom-color: {LINE_STRONG}; background: {TINT}; }}
#review-all-table td:first-child {{ white-space: nowrap; }}
#review-all-table .empty {{ color: {MUTED}; }}
#review-picker-row {{ align-items: flex-end; gap: 12px; }}
#review-picker-row > button {{ flex: 0 0 auto; width: auto; min-width: 156px; white-space: nowrap; min-height: 44px; }}
#review-pick-row {{ flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 16px;
  background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; box-shadow: {CARD_SHADOW}; padding: 40px 32px; }}
#review-pick-row > #review-pick {{ background: none; border: 0; border-left: 0; border-radius: 0; padding: 0; max-width: 520px; }}
#review-pick-row > #review-pick .prose p {{ margin: 0; font-size: 16px; line-height: 24px; color: {MUTED}; }}
#review-pick-row:has(~ #review-close #review-next:not(.hidden)) {{ display: none; }}
#review-answer-row:has(.prose p) {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; box-shadow: {CARD_SHADOW}; padding: 16px 20px; }}
#review-answer-row:has(#review-next:not(.hidden)) {{ flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 18px;
  padding: 36px 32px; box-shadow: 0 1px 2px rgba(17, 24, 39, 0.05), 0 8px 24px rgba(17, 24, 39, 0.06); }}
#review-answer-text {{ flex: 0 1 auto; width: 100%; min-width: 0; gap: 12px; }}
#review-answer-row:has(#review-next:not(.hidden)) #review-answer-text {{ align-items: center; max-width: 680px; }}
#review-answer-text > .block:first-child .prose p {{ margin: 0; font-weight: 600; color: {INK}; }}
#review-answer-row:has(#review-next:not(.hidden)) #review-answer-text > .block:first-child {{ width: auto; flex: 0 0 auto; }}
#review-answer-row:has(#review-next:not(.hidden)) #review-answer-text > .block:first-child .prose p {{ display: inline-flex; align-items: center; gap: 8px;
  padding: 6px 14px; border-radius: 999px; background: {_DONE.tint}; color: {_DONE.text}; font-size: 15px; line-height: 20px; }}
#review-answer-row:has(#review-next:not(.hidden)) #review-answer-text > .block:first-child .prose p::before {{ content: ""; flex: none; width: 8px; height: 8px; border-radius: 999px; background: {_DONE.mark}; }}
#review-next-preview {{ width: 100%; max-width: 640px; margin: 0; }}
#review-next-preview .prose p {{ margin: 0; padding: 12px 20px; background: {TINT}; border: 1px solid {LINE}; border-radius: 10px;
  font-size: 14px; line-height: 20px; color: {MUTED}; }}

/* the decision rows: structured two-column review rows with crisp bold headings on the left and equal-width 2x2 decision cards
   on the right (collapsing cleanly to one column at 200 percent zoom) */
.row.decision-row {{ border-bottom: 1px solid {LINE}; padding: 20px 0; gap: 24px; align-items: start; }}
#review-case .row.decision-row:last-child {{ border-bottom: 0; padding-bottom: 0; }}
.block.decision-what .prose p {{ margin: 0 0 4px; }}
.block.decision-what .prose p:first-of-type {{ font-weight: 600; color: {INK}; letter-spacing: -0.01em; margin-bottom: 6px; }}
.block.decision-what .prose p:nth-of-type(2) {{ color: {INK}; margin-bottom: 8px; }}
.block.decision-what .prose p:nth-of-type(n+3) {{ font-size: 13px; line-height: 18px; color: {MUTED}; }}
.decision-row .wrap {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
.decision-row .wrap label {{ border-radius: 8px; padding: 10px 12px; font-size: 14px; line-height: 20px; font-weight: 500; }}
.row.decision-foot {{ align-items: center; gap: 12px; margin-top: 8px; }}
@media (max-width: 900px) {{
  .decision-row .wrap {{ grid-template-columns: 1fr; }}
}}

/* primary and action button sizing */
#review-close > button, #review-close > .row > button, #review-oldest, #review-next, #hand-actions .row > button, .form:has(> #hand-rows) + .row > button {{ flex: 0 0 auto; width: auto; min-width: 200px; }}
#files-give, #pricelist-file-prices, #pricelist-view, #pricelist-carry-out, #settings-sections button.primary, #settings-sections button.stop {{ flex: 0 0 auto; width: auto; min-width: 200px; align-self: flex-start; }}
/* "Lijst vernieuwen" beside the text of In Exact invoeren and of Bestanden: its column starts from the button's natural width, and
   the button keeps its words on one line at the right end of its column, also at a width of 683 px */
.column:has(> #hand-intro) + .column, .column:has(> #files-done) + .column {{ flex-basis: auto; }}
.column:has(> #hand-intro) + .column > button, #files-refresh {{ width: auto; min-width: 160px; white-space: nowrap; align-self: flex-end; }}
button.primary:disabled {{ background: {LINE}; border-color: {LINE}; color: {MUTED}; opacity: 1; }}
button.secondary:disabled {{ background: {SURFACE_2}; border-color: {LINE_STRONG}; color: {MUTED}; box-shadow: none; opacity: 1; cursor: not-allowed; }}

/* the drop zone of a file: a dashed edge in the control colour on white, softly tinted on hover */
#files-upload, #pricelist-export, #pricelist-list {{ border: 1px dashed {CONTROL}; border-radius: 12px; background: {SURFACE}; transition: border-color 120ms {EASE}, background-color 120ms {EASE}; }}
#files-upload:hover, #pricelist-export:hover, #pricelist-list:hover {{ background: {TINT}; border-color: {ACCENT}; }}

/* a button beside a field stands level with the field and not with its label and help line */
.row > .form ~ button {{ align-self: flex-end; }}

/* In Exact invoeren: sticky bar above the list so actions stay anchored without visual weight;
   collapse empty answer and copy-feedback slots so no blank band stands above the list */
#hand-actions {{ position: sticky; top: 0; z-index: 41; background: {GROUND}; gap: 0; padding: 8px 0; margin-bottom: -8px; }}
#hand-note:has(.prose p) {{ margin-top: 8px; }}
.block.hand-rows input, .hand-copy button {{ scroll-margin-top: 148px; }}

/* In Exact invoeren: behind each row its copy buttons, drawn by PAGE_JS */
.block.hand-rows .wrap label {{ font-size: 14px; line-height: 20px; }}
.block.hand-rows .wrap.hand-copies {{ display: grid; grid-template-columns: minmax(0, max-content) minmax(0, max-content); justify-content: start; align-items: center; gap: 8px 12px; padding-right: 4px; }}
.hand-copy {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.hand-copy button {{ min-height: 32px; max-width: 100%; padding: 4px 12px; font-size: 14px; line-height: 20px; font-weight: 600; color: {INK}; background: {SURFACE}; border: 1px solid {CONTROL}; border-radius: 4px; cursor: pointer; text-align: left; overflow-wrap: break-word; }}
.hand-copy button:hover {{ background: {SURFACE_2}; border-color: {MUTED}; }}
.hand-copy button.copied {{ color: {_DONE.text}; background: {_DONE.tint}; border-color: {_DONE.mark}; }}
.hand-copy button.refused {{ color: {_PROBLEM.text}; background: {_PROBLEM.tint}; border-color: {_PROBLEM.mark}; }}
#hand-copy, #hand-copy .html-container {{ margin: 0; padding: 0; }}
#hand-copy-said {{ margin: 0; font-size: 14px; line-height: 20px; font-weight: 600; color: {_DONE.text}; }}
#hand-copy-said:not(:empty) {{ margin-top: 8px; }}
#hand-copy-said.refused {{ color: {_PROBLEM.text}; }}

/* Prijslijst: removal press and standing confirmation bar */
#pricelist-remove {{ flex: 0 0 auto; width: auto; }}
#pricelist-remove-confirm {{ align-items: center; background: {TINT}; border-left: 4px solid {CONTROL}; border-radius: 6px; padding: 12px 16px; }}

/* Prijslijst: the five fields of the columns of a price list align level on one line at desktop width, wrap cleanly when zoomed,
   keep their words clear of the dropdown's arrow, and end a long title with "..." */
#pricelist-columns-row > .form {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); align-items: end; gap: 12px; width: 100%; }}
#pricelist-columns-row > .form > .block {{ min-width: 0; }}
#pricelist-column-item_code input, #pricelist-column-price input, #pricelist-column-basis input, #pricelist-column-valid_from input, #pricelist-column-valid_to input {{ padding-right: 28px; text-overflow: ellipsis; }}
@media (max-width: 1024px) {{
  #pricelist-columns-row > .form {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 560px) {{
  #pricelist-columns-row > .form {{ grid-template-columns: 1fr; }}
}}

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
# It sets autocomplete "new-password" on the input of each key box of the settings tab (settings-azure_openai-key and
# settings-foundry-key, _service_block of aid/review/page.py), so that the browser does not offer to remember a key: the page library
# renders a password box with an empty autocomplete and does not apply the one of html_attributes (the review's code-1). It does so
# at the load and again whenever the page renders anew.
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
  const keys = () => document.querySelectorAll('#settings-azure_openai-key input, #settings-foundry-key input').forEach((box) => {
    if (box.getAttribute('autocomplete') !== 'new-password') box.setAttribute('autocomplete', 'new-password');
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
  keys();
  new MutationObserver(() => { language(); answers(); copies(); keys(); })
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
