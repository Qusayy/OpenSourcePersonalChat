"""The tests run with the gate off and the model in this process.

The application is closed to everyone unless ``AURORA_GATE`` says ``off``, and
nearly every test here asks it for something. This is set before any of them
imports the application, because the settings are read once, at import. The
gate's own tests switch it back on for themselves.

``AURORA_LLM_URL`` is emptied for the same reason: a model server named in a
developer's environment or ``.env`` would otherwise be what the tests talk to.
The tests of the server path give their engines a server of their own.

With the gate off the site answers only to this machine's own names and to
``AURORA_ALLOWED_HOSTS``; the test clients ask for it as ``testserver`` and
``aurora.test``.
"""

import os

os.environ["AURORA_GATE"] = "off"
os.environ["AURORA_LLM_URL"] = ""
os.environ["AURORA_ALLOWED_HOSTS"] = "testserver,aurora.test"
