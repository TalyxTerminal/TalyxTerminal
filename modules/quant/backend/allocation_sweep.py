"""What a sweep's grid shows beyond each cell's own numbers: how far the target weights move from one cell to its neighbours."""
from __future__ import annotations


def distance(first: dict, second: dict) -> float | None:
    """The average one-way change in target weights between two runs (the share of the portfolio that differs), over the periods both hold, in points a period."""
    shared = sorted(set(first) & set(second))
    if not shared:
        return None
    total = 0.0
    for date in shared:
        names = set(first[date]) | set(second[date])
        total += sum(abs(first[date].get(name, 0.0) - second[date].get(name, 0.0)) for name in names) / 2
    return total / len(shared) * 100


def weights_move(cells: dict[tuple[int, int], dict]) -> dict[tuple[int, int], float | None]:
    """`cells` maps (row, column) to {period date: {asset: weight}} for the cells that finished; each cell's number is the average distance to the cells next to it
    (one step along a row or a column). A cell with no finished neighbour has none."""
    out = {}
    for (row, column), weights in cells.items():
        near = [distance(weights, cells[other]) for other in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)) if other in cells]
        near = [value for value in near if value is not None]
        out[(row, column)] = sum(near) / len(near) if near else None
    return out
