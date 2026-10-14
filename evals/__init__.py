"""Measuring what the agent does, as opposed to how fast it does it.

The benchmark answers "how many tokens a second". This package is for the
other question: given a message, did the right thing happen. It holds a
stand-in for the model that plays back prepared output, which is what lets
every rule the server enforces around the model be tested without one.
"""
