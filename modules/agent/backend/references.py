"""Bind answer links to web sources and panel targets observed in this conversation."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .providers.projection import safe_source

ADDRESS = r"talyx://[a-zA-Z0-9_%:./@~+-]*[a-zA-Z0-9_%:/@~+-]"


class References:
    def __init__(self, store):
        self.store = store

    async def answer(self, turn, text, sources):
        read = self.read_sources(turn["conversationId"], sources)
        bound = self.bind(self.observed_panels(turn["conversationId"]), turn["workspaceId"], text, read)
        with self.store.transaction():
            self.store.db.executemany("INSERT INTO references_data VALUES (?,?,?,?)",
                ((ref["id"], turn["conversationId"], turn["id"], json.dumps(ref)) for ref in bound["references"]))
        return bound

    def bind(self, panels, workspace, text, read_sources):
        references = []
        ids = (re.escape(key) for key in panels if not key.startswith("talyx://") and key in text)
        address = "|".join([ADDRESS, *ids])
        pattern = rf'\[([^\]\n]+)\]\(((?:https?|talyx)://[^\s)]+)\)|`({address})`|(?<![\w-])({address})(?![\w-])'

        def link(match):
            label, href = match[1], match[2] or match[3] or match[4]
            if href.startswith(("http://", "https://")):
                if href not in read_sources or not safe_source(href) or len(label) > 200:
                    return label
                kind, target = "source", {"url": href}
            else:
                key = href
                if href.startswith("talyx://"):
                    parts = href.removeprefix("talyx://").split("/")
                    key = "talyx://" + "/".join(quote(unquote(part), safe="") for part in parts)
                if key not in panels:
                    return label or match[0]
                kind, target, title = panels[key]
                if not label or label == href or label == target.get("panelId"):
                    label = title
            ref_id = uuid4().hex
            references.append({"id": ref_id, "kind": kind, "workspaceId": workspace, "target": target, "label": label, "href": href})
            return "[[ref:" + ref_id + "]]"

        return {"text": re.sub(pattern, link, text, flags=re.ASCII), "references": references}

    @staticmethod
    def panel_url(target):
        return "talyx://" + "/".join(quote(target[key], safe="") for key in ("module", "panelType", "objectId"))

    def observed_panels(self, conversation):
        panels = {}
        for target, title in self.panel_targets(conversation):
            panels[self.panel_url(target)] = ("object", target, title)
        rows = self.store.db.execute(
            "SELECT value FROM calls WHERE conversation=? AND json_extract(value,'$.state')='completed' AND "
            "(json_extract(value,'$.action') IN ('platform.panels.open','platform.panels.open_target') OR "
            "json_type(value,'$.result.openTarget')='object') ORDER BY rowid", (conversation,))
        for row in rows:
            call = json.loads(row[0])
            args, result = call["input"], call["result"]
            if call["action"] == "platform.panels.open":
                module, panel = args["type"].split(".", 1)
                entry = ("panel", {"type": args["type"], "panelId": result["panelId"]}, module.capitalize())
                panels[result["panelId"]] = panels[f"talyx://{module}/{panel}"] = entry
            else:
                target = args if call["action"] == "platform.panels.open_target" else result["openTarget"]
                if not all(isinstance(target.get(key), str) for key in ("module", "panelType", "objectId")):
                    continue
                url = self.panel_url(target)
                if url not in panels:
                    panels[url] = ("object", target, target["module"].capitalize())
                if call["action"] == "platform.panels.open_target":
                    panels[result["panelId"]] = panels[url]
        return panels

    def panel_targets(self, conversation):
        for row in self.store.db.execute("SELECT value FROM cards WHERE conversation=? AND json_extract(value,'$.type') "
                                         "IN ('summary','changes','task','result','answer')", (conversation,)):
            card = json.loads(row[0])
            if card["type"] == "answer":
                view = card.get("view")
                candidates = view.get("openTargets", []) if isinstance(view, dict) and card.get("role") == "user" else []
            elif card["type"] == "changes":
                candidates = [item.get("openTarget") for item in card["items"]]
            else:
                candidates = [card.get("openTarget")]
            for target in candidates:
                keys = ("module", "panelType", "objectId")
                if isinstance(target, dict) and all(isinstance(target.get(key), str) and target[key] for key in keys):
                    yield {key: target[key] for key in keys}, card.get("title") or target["module"].capitalize()

    def save(self, turn, kind, target, label):
        value = {"id": uuid4().hex, "kind": kind, "workspaceId": turn["workspaceId"], "target": target, "label": label}
        with self.store.transaction():
            self.store.db.execute("INSERT INTO references_data VALUES (?,?,?,?)",
                (value["id"], turn["conversationId"], turn["id"], json.dumps(value)))
        return value

    def read_sources(self, conversation, sources):
        urls = {item["url"] for item in sources}
        for row in self.store.db.execute("SELECT value FROM calls WHERE conversation=? AND json_extract(value,'$.action')="
                                         "'agent.search_web' AND json_extract(value,'$.state')='completed'", (conversation,)):
            call = json.loads(row[0])
            result = call["result"]
            urls |= ({result["url"]} if "url" in result else set()) if call["input"].get("mode") == "page" \
                else {item["url"] for item in result["sources"]}
        for row in self.store.db.execute("SELECT value FROM cards WHERE conversation=? AND json_extract(value,'$.type')='answer' "
                                         "AND json_extract(value,'$.role')='assistant'", (conversation,)):
            urls |= {item["url"] for item in json.loads(row[0])["sources"]}
        return urls

    def read(self, reference_id, workspace):
        row = self.store.db.execute("SELECT conversation,value FROM references_data WHERE id=?", (reference_id,)).fetchone()
        if row is None:
            raise ContractError("target_gone", t("This reference is unavailable"))
        self.store.conversation(row["conversation"], workspace)
        return json.loads(row["value"])
