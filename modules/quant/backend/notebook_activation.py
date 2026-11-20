"""An AI's request to run a notebook cell: the cell's text identity, which the run records.

The platform no longer approves code an AI runs (AI and people use the same operations); whether to ask the user is the
AI client's own decision, from the action's declared effects."""
from __future__ import annotations

import hashlib


def identity(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class CellActivation:
    def __init__(self, ctx):
        self.ctx = ctx

    async def approve_ai(self, object_id, cell_id, code):
        """The identity of the cell's code an AI asked to run."""
        code_identity = identity(code)
        return code_identity
