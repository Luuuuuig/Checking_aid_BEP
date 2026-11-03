"""The routing challenge set: one made-up case per row of the routing table (R01 to R11), run through the whole pipeline with the
parser as the reader, and the rule the case takes end to end. The frozen aid is checked against every row before the pilot.

Each row states the rule ids of all the actions of the case, as a set, and the route of its decisive action (the action of the
row's rule), so that a rule that fires beside the expected ones fails the row. The sets follow the routing table of the design
specification (section 5): R01's attach and confirm comes after an AUTO action (R02, R04, R10) and with no other action, and not
after an ASK or a REVIEW; R11 is the one REVIEW action of a case whose supplier is new.

R03, R10 and R11 run on the documents of other rows with a settings override: S14 a lead time of 2 days (R03); an authorization
limit that MU08's total exceeds (R10), worked out from the PO's stored total so that no amount is typed; and first_cases_review=5
on a fresh cases root (R11), where the supplier has no closed case yet."""
import pytest

from aid.pipeline import run_case
from aid.sources.upload import open_upload_case
from aid.tests.conftest import SETTINGS, deps
from aid.tests.helpers import po_number_of

HALF_THE_PO_TOTAL = "half the total of the case's PO"     # stands for the authorization limit of the R10 row, worked out in the test

# (case, rule, settings override, the rule ids of all the case's actions, the route of the action of the row's rule)
CHALLENGES = [("MU01", "R01", {}, {"R01"}, "AUTO"),
              ("MU14", "R02", {}, {"R02", "R01"}, "AUTO"),
              ("MU14", "R03", {"supplier_lead_times": {"S14": 2}}, {"R03"}, "REVIEW"),
              ("MU08", "R04", {}, {"R04", "R01"}, "AUTO"),
              ("MU07", "R05", {}, {"R05"}, "REVIEW"),
              ("MU11", "R06", {}, {"R06"}, "ASK"),
              ("MU17", "R07", {}, {"R07"}, "ASK"),
              ("MU20", "R08", {}, {"R08"}, "ASK"),
              ("MU06", "R09", {}, {"R09"}, "REVIEW"),
              ("MU08", "R10", {"authorization_limit": HALF_THE_PO_TOTAL}, {"R10", "R01"}, "AUTO"),
              ("MU01", "R11", {"first_cases_review": 5}, {"R11"}, "REVIEW")]


@pytest.mark.parametrize("case,rule,override,rules,decisive", CHALLENGES, ids=[row[1] for row in CHALLENGES])
def test_routing_challenge(tmp_path, madeup_out, case, rule, override, rules, decisive):
    if override.get("authorization_limit") == HALF_THE_PO_TOTAL:
        total = deps(tmp_path / "probe", madeup_out).store.versions(po_number_of(case))[0].total
        override = override | {"authorization_limit": total / 2}
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update=override))
    c = open_upload_case(d.settings.cases_root, [madeup_out / f"{case}_conf.pdf"], po_number_of(case), "confirmation", d.clock())
    actions = run_case(c, d).actions
    taken = [(a.rule_id, a.route) for a in actions]
    assert {a.rule_id for a in actions} == rules, taken
    assert [a.route for a in actions if a.rule_id == rule] == [decisive], taken
