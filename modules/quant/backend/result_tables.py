"""Read fixed published tables, including an exact diagnostic observation."""
import json
import math
import re
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from talyx_sdk import ContractError


def read_result_table(path, args):
    if path.suffix != ".parquet":
        raise ContractError("invalid_input", "This result is not a table.")
    columns = args.get("columns")
    observation = args.get("observation")
    read_columns = list(dict.fromkeys(["date", *columns])) if columns and observation else columns
    dataset = ds.dataset(path, format="parquet")
    predicate = None
    selected_filter = args.get("filter")
    if selected_filter:
        column, value = selected_filter["column"], selected_filter["equals"]
        if column not in dataset.schema.names or not isinstance(value, (str, bool, int, float, type(None))) or isinstance(value, float) and not math.isfinite(value):
            raise ContractError("invalid_input", "Choose a real column and one finite scalar value for this table filter.")
        predicate = ds.field(column).is_null() if value is None else ds.field(column) == value
    for condition in args.get("filters", []):
        column, operator, value = condition["column"], condition["operator"], condition["value"]
        if column not in dataset.schema.names or operator not in {"eq", "gte", "lte", "in"}:
            raise ContractError("invalid_input", "Choose an available column and supported table filter.")
        values = value if operator == "in" and isinstance(value, list) else [value]
        if any(not isinstance(item, (str, bool, int, float, type(None))) or isinstance(item, float) and not math.isfinite(item) for item in values):
            raise ContractError("invalid_input", "Table filters need finite values.")
        field = ds.field(column)
        kind = dataset.schema.field(column).type
        try:
            if pa.types.is_timestamp(kind):
                values = [pa.scalar(pd.Timestamp(item), type=kind) if item is not None else None for item in values]
            elif pa.types.is_date(kind):
                values = [pa.scalar(pd.Timestamp(item).date(), type=kind) if item is not None else None for item in values]
            if operator == "in": selected = field.isin(values)
            elif operator == "eq": selected = field.is_null() if values[0] is None else field == values[0]
            elif operator == "gte": selected = field >= values[0]
            else: selected = field <= values[0]
        except (ValueError, TypeError, pa.ArrowException) as error:
            raise ContractError("invalid_input", "This filter does not match the column's data type.") from error
        predicate = selected if predicate is None else predicate & selected
    if observation:
        if "date" not in dataset.schema.names:
            raise ContractError("invalid_input", "This result has no observation timestamps.")
        try:
            selected = pd.Timestamp(observation)
            if selected.tzinfo is None:
                raise ValueError("An exact timestamp must include its UTC offset.")
            timestamp = pa.scalar(selected.tz_convert("UTC"), type=pa.timestamp("ns", tz="UTC"))
            date_predicate = ds.field("date").cast(pa.timestamp("ns", tz="UTC")) == timestamp
            predicate = date_predicate if predicate is None else predicate & date_predicate
        except ValueError as error:
            raise ContractError("invalid_input", str(error)) from error
    offset, limit = args.get("offset", 0), args.get("limit", 100)
    try:
        total = dataset.count_rows(filter=predicate)
        sorting = args.get("sort")
        if sorting:
            if sorting["column"] not in dataset.schema.names:
                raise ContractError("invalid_input", "Choose an available sort column.")
            selected_columns = list(dict.fromkeys([*read_columns, sorting["column"]])) if read_columns else None
            ordinal = "__quant_query_order"
            while ordinal in dataset.schema.names: ordinal += "_"
            keys = [(sorting["column"], "descending" if sorting.get("descending") else "ascending"), (ordinal, "ascending")]
            # Keep only the requested prefix while scanning, rather than
            # materializing and sorting a multi-million-row history per click.
            candidate, seen, prefix = None, 0, offset + limit
            for batch in dataset.scanner(columns=selected_columns, filter=predicate, batch_size=8192).to_batches():
                part = pa.Table.from_batches([batch]).append_column(ordinal, pa.array(range(seen, seen + batch.num_rows), type=pa.int64()))
                seen += batch.num_rows
                candidate = part if candidate is None else pa.concat_tables([candidate, part])
                if len(candidate) > prefix:
                    candidate = candidate.take(pc.select_k_unstable(candidate, k=prefix, sort_keys=keys))
            if candidate is None:
                names = selected_columns or dataset.schema.names
                candidate = pa.Table.from_batches([], schema=pa.schema([dataset.schema.field(name) for name in names])).append_column(ordinal, pa.array([],type=pa.int64()))
            frame = candidate.sort_by(keys).slice(offset, limit).drop([ordinal]).to_pandas()
        else:
            frame = None
        batches, skipped, remaining = [], 0, limit
        for batch in (() if sorting else dataset.scanner(columns=read_columns, filter=predicate, batch_size=4096).to_batches()):
            if skipped + batch.num_rows <= offset:
                skipped += batch.num_rows
                continue
            start = max(0, offset - skipped)
            page = batch.slice(start, remaining)
            batches.append(page)
            remaining -= page.num_rows
            skipped += batch.num_rows
            if not remaining:
                break
        schema = dataset.schema if read_columns is None else pa.schema([dataset.schema.field(name) for name in read_columns])
        if frame is None:
            frame = pa.Table.from_batches(batches, schema=schema).to_pandas()
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise ContractError("invalid_input", "This table selection cannot be read: " + str(error)) from error
    if columns:
        frame = frame[columns]
    rows = json.loads(frame.to_json(orient="records", date_format="iso", date_unit="ns"))
    # pandas' default JSON precision rounds small native sensitivities to zero.
    # Keep its existing time/null representation and restore original floats.
    for row, original in zip(rows, frame.to_dict(orient="records")):
        for column, value in original.items():
            if isinstance(value, pd.Timestamp):
                row[column] = value.isoformat().replace("+00:00", "Z")
            elif isinstance(value, Decimal):
                row[column] = str(value) if value.is_finite() else None
            elif isinstance(value, float):
                row[column] = None if not math.isfinite(value) else repr(value) if value.is_integer() and abs(value) > 2**53 - 1 else value
            elif isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
                row[column] = str(value)
    numeric_columns = []
    for column in frame.columns:
        if re.search(r"(^ts_|timestamp|(?:At|Time)Ns$|(?:^|_)id$|address|hash|date)", column, re.I):
            continue
        values = [row.get(column) for row in rows if row.get(column) is not None]
        if not values or not all(isinstance(value, str) and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?: [A-Z][A-Z0-9]{1,11})?", value) for value in values):
            continue
        target = column + "Numeric"
        if target in frame.columns:
            continue
        precision = max(max(0, -Decimal(value.split()[0]).as_tuple().exponent) for value in values)
        units = sorted({value.split()[1] for value in values if len(value.split()) == 2})
        for row in rows:
            text = row.get(column)
            amount = Decimal(text.split()[0]) if text is not None else None
            row[target] = (int(amount) if amount == amount.to_integral_value() else float(amount)) if amount is not None else None
        numeric_columns.append({"name": target, "source": column, "precision": precision,
                                "unit": units[0] if len(units) == 1 else None, "exactValueField": column})
    return {"columns": [*frame.columns, *(item["name"] for item in numeric_columns)], "rows": rows,
            "numericColumns": numeric_columns, "total": total, "offset": offset, "nextOffset": offset + limit if offset + limit < total else None}
