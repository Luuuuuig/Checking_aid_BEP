"""Tests of the Colab notebook for the made-up set (aid/colab/Run_Aid_Colab.ipynb)."""
import ast
import json
import re


def test_notebook_has_no_secrets_and_parses():
    with open("aid/colab/Run_Aid_Colab.ipynb", encoding="utf-8") as f:     # closed again: an open file fails the test under -W error
        nb = json.load(f)
    src = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert not re.search(r"sk-ant-|ghp_[A-Za-z0-9]{20,}", src) and "userdata.get" in src and "STOP" in src
    assert not re.search(r"ExactApiWriter|SmtpMailer|AID_MAIL_PASSWORD", src)     # made-up set only: no Exact writer, no SMTP mailer, no mailbox password
    for c in nb["cells"]:
        if c["cell_type"] == "code": ast.parse("".join(l for l in c["source"] if not l.lstrip().startswith(("!", "%"))))


# The test below covers what the rulings of the task add to the one above.

def test_notebook_has_five_code_cells_without_outputs_after_a_note_on_the_made_up_set():
    with open("aid/colab/Run_Aid_Colab.ipynb", encoding="utf-8") as f:
        nb = json.load(f)
    assert nb["nbformat"] == 4
    first, *code = nb["cells"]
    assert first["cell_type"] == "markdown" and "made-up set only" in "".join(first["source"])
    assert [c["cell_type"] for c in code] == ["code"] * 5
    assert all(c["outputs"] == [] and c["execution_count"] is None for c in code)
    sources = ["".join(c["source"]) for c in code]
    assert "drive.mount" in sources[0]
    assert 'userdata.get("GITHUB_TOKEN")' in sources[1] and "aid/requirements.txt" in sources[1]
    assert 'userdata.get("ANTHROPIC_API_KEY")' in sources[2] and "PreparedWriter" in sources[2] and "FileMailer" in sources[2]
    assert "run_loop(deps" in sources[3] and "STOP" in sources[3]
    assert "build_app(deps).launch(share=True)" in sources[4]


def test_the_review_cell_refuses_to_share_company_settings():
    """Cell 5 asks refuse_share before it opens the public link, and stops with the reason when the settings hold company values."""
    with open("aid/colab/Run_Aid_Colab.ipynb", encoding="utf-8") as f:
        nb = json.load(f)
    cell = "".join(nb["cells"][-1]["source"])
    assert "refuse_share(settings)" in cell and "raise SystemExit(" in cell
    assert cell.index("refuse_share(settings)") < cell.index("raise SystemExit(") < cell.index("launch(share=True)")
