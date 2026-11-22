"""Words follow the language setting: into module processes as it changes, and into processes they start later."""

from __future__ import annotations

from talyx_sdk import i18n

from tests.platform.harness import run, running, until


def test_a_module_answers_in_the_language_the_user_picks_without_restarting(tmp_path):
    async def scenario():
        async with running(tmp_path, ("echo",)) as platform:
            words = lambda: platform.call("echo.words")  # noqa: E731
            assert (await words())["text"] == "Hello, TALYX | en"
            pid = platform.process("echo").endpoint.pid
            await platform.call("platform.settings.write", {"module": "platform", "workspaceId": "global",
                                                            "values": {"language": "zh-CN"}})
            assert i18n.language() == "zh-CN"   # the Host's own words
            assert (await until(words, lambda value: value["text"] != "Hello, TALYX | en"))["text"] == "你好，TALYX | zh-CN"
            assert platform.process("echo").endpoint.pid == pid
        i18n.set_language("en")
    run(scenario())
