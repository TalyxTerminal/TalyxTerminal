"""Exact counts for rectangular CSV/TSV input supplied in a user message or text attachment."""
import csv
import io


def table_facts(text):
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        delimiter = "\t" if "\t" in line else ","
        if delimiter not in line:
            continue
        # A prose preamble may precede the header. Only describe a complete rectangular table.
        reader = csv.reader(io.StringIO("".join(lines[index:]), newline=""), delimiter=delimiter, strict=True)
        try:
            header = next(reader)
            if len(header) < 2 or not all(header) or len(set(header)) != len(header):
                return None
            count = 0
            frequencies = {}
            for row in reader:
                if not row:
                    continue
                if len(row) != len(header):
                    return None
                if count == 0:
                    first = row[0]
                last = row[0]
                count += 1
                frequencies[last] = frequencies.get(last, 0) + 1
        except csv.Error:
            return None
        if count:
            return {"headerLine": index + 1, "delimiter": delimiter, "dataRows": count,
                    "firstColumn": header[0], "firstValue": first, "rowsWithFirstValue": frequencies[first],
                    "lastValue": last, "rowsWithLastValue": frequencies[last]}
        return None
    return None
