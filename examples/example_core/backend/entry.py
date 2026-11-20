"""Example core: one public action. Its contract (public/contract.json) and clients are generated from this file."""
from typing import Annotated

from pydantic import Field
from talyx_sdk.declare import Model, Module
from talyx_sdk.process import Call

module = Module("example_core")


class TextInput(Model):
    text: Annotated[str, Field(max_length=200_000)]
    """The text to count."""


class TextStats(Model):
    characters: int
    words: int


@module.action(effects=["read"])
async def text_stats(input: TextInput, call: Call) -> TextStats:
    """How many characters and words a text has. Example: {"text": "Hello world"}."""
    return TextStats(characters=len(input.text), words=len(input.text.split()))
