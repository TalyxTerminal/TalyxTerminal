"""Explicit full-permission owner for isolated MCP transport fixtures."""


def full_permissions():
    async def policy(value, call):
        return {"conversationId": "fixture-permissions", "permission": "full", "revision": 1,
                "readOnly": False, "rules": []}

    async def unexpected_approval(value, call):
        raise AssertionError("This transport fixture must not request user approval")

    return {"policy": policy, "request": unexpected_approval, "read": unexpected_approval,
            "finish": unexpected_approval}
