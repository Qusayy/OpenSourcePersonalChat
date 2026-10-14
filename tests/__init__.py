"""The tests run with the gate off.

The application is closed to everyone unless ``AURORA_GATE`` says ``off``, and
nearly every test here asks it for something. This is set before any of them
imports the application, because the settings are read once, at import. The
gate's own tests switch it back on for themselves.
"""

import os

os.environ["AURORA_GATE"] = "off"
