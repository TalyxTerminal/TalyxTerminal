"""Common pagination and table encoding for the Quant public reads."""

import asyncio
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import ai_views


def offset(args):
    value = args.get("cursor")
    if value is None:
        return 0
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ContractError(
            "invalid_input", t("cursor must be a continuation position returned by the previous page.")
        )
    return int(value)


def page(items, args, **extra):
    return ai_views.page(
        items,
        offset(args),
        lambda rows, more, total: {
            **extra,
            "items": rows,
            "nextCursor": str(more) if more is not None else None,
            "total": total,
        },
        limit=args.get("limit", 25),
    )


async def table(ctx, records, args, *, columns=None, units=None):
    names = (
        columns if columns is not None else list(dict.fromkeys(k for row in records for k in row))
    )
    layout = [{"name": n, "unit": (units or {}).get(n)} for n in names]
    if args.get("format") == "file":
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = Path(ctx.handoff) / f"{uuid4().hex}.parquet"

        def write():
            pq.write_table(pa.table({n: [row.get(n) for row in records] for n in names}), path)

        try:
            await asyncio.to_thread(write)
            file = await ctx.hand_over(path, "values.parquet", move=True)
        finally:
            path.unlink(missing_ok=True)
        return {"columns": layout, "file": file, "total": len(records)}
    return ai_views.page(
        records,
        offset(args),
        lambda kept, more, total: {
            "columns": layout,
            "rows": [[r.get(n) for n in names] for r in kept],
            "total": total,
            "nextCursor": str(more) if more is not None else None,
        },
        limit=args.get("limit", 25),
    )


async def complete_json(ctx, value, name):
    """Return the full structured result inline or as a JSON file when it exceeds the AI response budget."""
    import orjson

    content = await asyncio.to_thread(orjson.dumps, value)
    if len(content) <= ai_views.BUDGET:
        return value
    path = Path(ctx.handoff) / f"{uuid4().hex}.json"
    try:
        await asyncio.to_thread(path.write_bytes, content)
        return {"file": await ctx.hand_over(path, name, move=True)}
    finally:
        path.unlink(missing_ok=True)
