"""Development supports other modules provide at ``developer.development_support``: which apply to a document, and
asking one of them (the Python a file is analysed against)."""
from __future__ import annotations

import fnmatch

from talyx_sdk import ContractError


class DevelopmentSupports:
    def __init__(self, developer, documents, projects):
        self.developer, self.documents, self.projects = developer, documents, projects

    def applicable(self, document_id: str) -> list[dict]:
        """The supports whose ``appliesTo`` matches this document."""
        document = self.documents.record(document_id)
        project_id = document["context"]["projectId"]
        project = self.projects.get(project_id) if project_id else None
        result = []
        for item in self.developer.implementations("development_support"):
            applies = item["descriptor"].get("appliesTo", {})
            source = document["source"]
            if applies.get("sourceKinds") and source["kind"] not in applies["sourceKinds"]:
                continue
            if applies.get("filePatterns") and not any(fnmatch.fnmatchcase(document["name"].lower(), pattern.lower())
                                                       for pattern in applies["filePatterns"]):
                continue
            if applies.get("providerIds") and source.get("providerId") not in applies["providerIds"]:
                continue
            if project and applies.get("projectKinds") and project["kind"] not in applies["projectKinds"]:
                continue
            result.append(item)
        return result

    async def ask(self, item: dict, capability: str, command: str, document_id: str) -> dict:
        if capability not in item["descriptor"].get("capabilities", []):
            raise ContractError("invalid_input", f"{item['moduleId']}:{item['id']} does not provide {capability}.")
        document = self.documents.snapshot(self.documents.record(document_id))
        project_id = document["context"]["projectId"]
        payload = {"capability": capability, "command": command, "target": None, "document": document,
                   **({"project": self.projects.get(project_id)} if project_id else {})}
        return await self.developer.extension("development_support", item["moduleId"], item["id"], "invoke", payload)
