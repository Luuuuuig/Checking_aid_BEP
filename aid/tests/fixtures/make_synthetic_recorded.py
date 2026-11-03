"""Write the synthetic recorded answers that the replay tests read until a real recording replaces them.

For each document the --record command of aid/readers/supplier_llm.py reads in aid/madeup/out (the 20 confirmations and
CART02.png), this writes the answer a perfect model would give, answer_from_spec of aid/tests/helpers.py, as a RecordedClient
file named by the digest of the request the reader sends for that document. Replaying them runs the reader's own path (the
request and its digest, the recorded file, the parsing, the normalisation and the source check); they measure no model.
Their model is "synthetic" and their token counts and seconds are 0. A synthetic answer the script no longer writes (after a
change of the prompt, the schema or a document) is deleted; an answer of a model is never touched. recorded/README.md says
how the real answers replace them.

    python3 -m aid.tests.fixtures.make_synthetic_recorded
"""
import json
from pathlib import Path

from aid.readers.llm_client import LlmResponse, RecordedClient
from aid.readers.supplier_llm import documents_to_record, reading_request
from aid.tests.helpers import answer_from_spec

REPO_ROOT = Path(__file__).resolve().parents[3]
SET = REPO_ROOT / "aid" / "madeup" / "out"
RECORDED = Path(__file__).resolve().parent / "recorded"


def write_synthetic_answers(set_dir: Path = SET, recorded: Path = RECORDED) -> list[Path]:
    """Write the synthetic answer for each document of `set_dir` into `recorded` and delete the synthetic answers it no
    longer writes; returns the files written."""
    recorder = RecordedClient(recorded)
    written = [recorder.record(reading_request(path, kind),
                               LlmResponse(text=answer_from_spec(path.stem.removesuffix("_conf"), kind), model="synthetic", input_tokens=0,
                                           output_tokens=0, seconds=0.0))
               for path, kind in documents_to_record(set_dir)]
    for stale in set(Path(recorded).glob("*.json")) - set(written):
        if json.loads(stale.read_text(encoding="utf-8"))["model"] == "synthetic":
            stale.unlink()
    return written


if __name__ == "__main__":
    files = write_synthetic_answers()
    print(f"{len(files)} synthetic answers in {RECORDED}")
