"""Bot definitions, group messages, routines and queued wakes, owned by the workspace."""

import base64
import binascii
import io
import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

# A mention in a message: the bot's reference marker.
MENTION = re.compile(r"\[\[ref:([^\]]+)\]\]")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_entries(id TEXT PRIMARY KEY, workspace TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS bot_entries_workspace ON bot_entries(workspace);
CREATE TABLE IF NOT EXISTS bot_sections(workspace TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(workspace,name));
CREATE TABLE IF NOT EXISTS bot_lines(bot TEXT NOT NULL,line TEXT NOT NULL,conversation TEXT NOT NULL UNIQUE,PRIMARY KEY(bot,line));
CREATE TABLE IF NOT EXISTS bot_messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT NOT NULL,line TEXT NOT NULL,
    speaker TEXT,at REAL NOT NULL,text TEXT NOT NULL,mentions TEXT NOT NULL,reply TEXT,turn_id TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS bot_messages_line ON bot_messages(line,seq);
CREATE TABLE IF NOT EXISTS bot_routines(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,bot TEXT,conversation TEXT,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bot_wakes(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,bot TEXT,line TEXT NOT NULL,
    conversation TEXT NOT NULL,queued REAL NOT NULL,cause TEXT NOT NULL,instruction TEXT NOT NULL,turn_id TEXT,result TEXT,
    notice TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS bot_wakes_turn ON bot_wakes(turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS bot_wakes_workspace ON bot_wakes(workspace,bot,result);
CREATE TABLE IF NOT EXISTS bot_edits(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,bot TEXT NOT NULL,
    field TEXT NOT NULL,object_id TEXT NOT NULL,before TEXT NOT NULL,after TEXT NOT NULL);
"""


def identity():
    return uuid4().hex


def stamp(at):
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="minutes")


def text_value(value, required_message):
    if not isinstance(value, str) or not value.strip():
        raise ContractError("invalid_input", required_message)
    return value.strip()


def avatar(look):
    if (
        not isinstance(look, dict)
        or look.get("shape")
        not in {"circle", "blob", "square", "pill", "triangle", "hexagon", "cloud", "drop"}
        or look.get("tint")
        not in {"brown", "red", "yellow", "green", "teal", "blue", "purple", "pink", "grey"}
    ):
        raise ContractError("invalid_input", t("Choose a bot shape and colour"))
    result = {"shape": look["shape"], "tint": look["tint"]}
    if "photo" not in look:
        return result
    photo = look["photo"]
    try:
        data = base64.b64decode(photo["data"].split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP", "GIF", "BMP"]) as image:
            # Re-encode the first frame only; no EXIF, filenames, location or source metadata survives.
            image = ImageOps.exif_transpose(image).convert("RGBA")
            image.thumbnail((160, 160), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, "PNG")
        png = output.getvalue()
        result["photo"] = {
            "name": str(photo["name"]),
            "size": f"{max(1, round(len(png) / 1024))} KB",
            "data": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        }
    except (
        KeyError,
        IndexError,
        ValueError,
        TypeError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        binascii.Error,
    ) as exc:
        raise ContractError(
            "invalid_input", t("Choose a readable PNG, JPEG, WebP, GIF or BMP image")
        ) from exc
    return result


def recipients(members, mentions, reply_to, *, user):
    if "everyone" in mentions:
        return list(members)
    selected = set(mentions)
    if reply_to:
        selected.add(reply_to)
    return [bot for bot in members if bot in selected] if selected or not user else list(members)


def group_handoff(group, limit):
    """A human message resets both values. Reaching the limit sends exactly one notification."""
    if limit and group["rounds"] >= limit:
        notify = not group["limitNotified"]
        group["limitNotified"] = True
        return False, notify
    group["rounds"] += 1
    if limit and group["rounds"] == limit:
        group["limitNotified"] = True
        return True, True
    return True, False


def decode_rows(rows):
    return [json.loads(row[0]) for row in rows]
