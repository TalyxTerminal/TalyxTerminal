"""Table cursors and UTF-8-safe log segments."""

import base64
import codecs
import json

from talyx_sdk import ContractError
from talyx_sdk.i18n import t


def token(identity, offset):
    return base64.urlsafe_b64encode(json.dumps({"identity": identity, "offset": offset}).encode()).decode()


def offset(args, identity):
    if not args.get("cursor"):
        return 0
    try:
        value = json.loads(base64.b64decode(args["cursor"], altchars=b"-_", validate=True))
        if value["identity"] != identity:
            raise ContractError("conflict", t("This cursor belongs to another result or query; restart the read."))
        position = value["offset"]
        if type(position) is not int or position < 0:
            raise ValueError(t("Invalid offset"))
        return position
    except (ValueError, KeyError, TypeError, UnicodeError) as error:
        raise ContractError("invalid_input", t("Invalid result cursor")) from error


def log_segment(path, position, limit=12000, *, final=True):
    if not path.exists():
        return {"text": "", "nextOffset": position, "eof": True}
    with path.open("rb") as stream:
        stream.seek(position)
        raw = stream.read(limit)
        size = path.stat().st_size
        while True:
            # Logs may contain arbitrary subprocess bytes. Keep byte offsets in
            # the original file; replacement characters must not advance them.
            decoder = codecs.getincrementaldecoder("utf8")("replace")
            text = decoder.decode(raw, final=final and position + len(raw) >= size)
            if len(json.dumps(text, ensure_ascii=False).encode()) <= 15000:
                break
            raw = raw[:len(raw) // 2]
        end = position + len(raw) - len(decoder.getstate()[0])
        return {"text": text, "nextOffset": end, "eof": end >= path.stat().st_size}
