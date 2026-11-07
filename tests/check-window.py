"""Offscreen checks of the window that need no display.

The progress page must offer a second attempt after a failure, and the storage
step must open on no array.

Run from the arch/ directory: python tests/check-window.py
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from installer.gui.app import Installer  # noqa: E402

failures = []


def check(condition, message):
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


app = Installer(simulate=True, dry_run=True)
app.root.geometry("1024x768+-4000+-4000")
app.root.update_idletasks()

# -- the storage step opens on no array --------------------------------------
app.goto("storage")
app.root.update_idletasks()
storage = app.pages["storage"]
check(storage.raid_label.get() == "No array",
      "the storage step opens on %r" % storage.raid_label.get())
storage.collect()
check(app.form["raid"] == "none", "the level stays no array (%r)" % app.form["raid"])

# -- a failed attempt can be started over ------------------------------------
progress = app.pages["progress"]
check(hasattr(progress, "retry_button"), "the progress page has a Start over button")
check(progress.retry_button.winfo_manager() == "",
      "Start over stays hidden until something fails")
progress.retry_button.pack(fill="x")
check(progress.retry_button.winfo_manager() == "pack",
      "Start over can be shown (%r)" % progress.retry_button.winfo_manager())

app.session = types.SimpleNamespace(running=False, error="boom")
app.start_over()
app.root.update_idletasks()
check(app.session is None, "starting over forgets the failed session")
check(app._current == "summary", "starting over returns to %r" % app._current)
check(progress.retry_button.winfo_manager() == "",
      "starting over hides Start over again")

app.root.destroy()
print("\n%s: %d failure(s)"
      % ("WINDOW CHECK PASS" if not failures else "WINDOW CHECK FAIL", len(failures)))
sys.exit(1 if failures else 0)
