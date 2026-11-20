"""Example add-on: it offers nothing of its own to call, so its contract is empty. Everything it does is in its frontend,
with example_core's public action and view."""
from talyx_sdk.declare import Module

module = Module("example_addon")
