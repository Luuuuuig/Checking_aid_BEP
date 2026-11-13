"""The colours of the review page: one set of tokens for the whole page, each pair checked against the contrast formula of WCAG 2.x.
Text needs 4.5:1 against its ground; the edge of a control, a focus ring and a state mark need 3:1. The ratios beside the tokens
are those of that formula.

The page stays neutral for the most part: grey grounds, ink for the text and for the one filled button of a view, and one blue
accent for links, the focus and the chosen option. The four tones (TONES) appear only on chips and lines that carry their word,
so that a colour never says something alone: "attention" for what waits for the buyer ("Ter beoordeling", the approval banner,
the notice of a failed case), "problem" for what went wrong ("Mislukt", the problems of the assistant), "done" for what is
finished ("Vanzelf afgesloten", "Door u afgesloten") and "waiting" for what waits for someone else ("Wacht op leverancier",
"Wacht op Copilot"). Each tone has the colour of its words, the tint of its ground and the colour of its mark (a dot, a bar or
an edge).

For now the strip of the tab "Beoordelen" and the fold under it take these tokens in their own scoped style rules (the
css_template of their gr.HTML in aid/review/page.py). Nothing here is given to gr.Blocks() or to launch(), and nothing here names
an address outside the PC.
"""
from typing import NamedTuple

GROUND = "#F8FAFC"           # the page
SURFACE = "#FFFFFF"          # fields, tables, document frames, folds, the strip
SURFACE_2 = "#F1F5F9"        # the ground of a notice and of a document, the hover of a secondary button; INK on it 16.30:1
INK = "#0F172A"              # all text, and the one filled button of a view with white text; 17.85:1 on SURFACE, 17.06:1 on GROUND
INK_HOVER = "#1E293B"        # the hover of the filled button; white on it 14.63:1
MUTED = "#475569"            # help under a field, meta, column heads, tabs not chosen; 7.58:1 on SURFACE, 7.24:1 on GROUND, 6.92:1
                             # on SURFACE_2
CONTROL = "#64748B"          # the edge of fields, boxes, radios and secondary buttons, and placeholders; 4.76:1 on SURFACE, 4.55:1 on
                             # GROUND
LINE = "#E2E8F0"             # rules between rows and the edges of blocks: 1.23:1, so never the only edge of a control
LINE_STRONG = "#CBD5E1"      # the rule under the head of a table: 1.48:1, decorative too
ACCENT = "#1D4ED8"           # links, the focus ring, the edge of the chosen option, the bar under the chosen tab; 6.70:1 on SURFACE,
                             # 6.41:1 on GROUND, 6.12:1 on SURFACE_2, and 2.66:1 against the filled button, so the ring stands 2 px off
ACCENT_TINT = "#EFF6FF"      # the ground of the chosen option; INK on it 16.40:1, ACCENT 6.16:1


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
