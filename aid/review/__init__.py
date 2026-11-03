"""The review page of the checking aid: app.py (the view of a case and the buyer's decisions), close.py (the close and the undo),
page.py (the gradio page), i18n.py (the strings of the page) and __main__.py (python3 -m aid.review).

close.py and page.py build on names of app.py, which imports them at its end to re-export them; app is imported here first, so that
each of the three modules can be imported on its own."""
from aid.review import app  # noqa: F401
