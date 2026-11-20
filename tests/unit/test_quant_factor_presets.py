"""A factor's presets are the price styles written as formulas, from the one definition in native/styles.py."""
from modules.quant.native import formula, styles


def test_every_price_style_is_a_preset_with_a_formula_the_formula_language_accepts():
    presets = styles.preset_formulas()
    assert [item["style"] for item in presets] == [item["style"] for item in styles.definitions() if item["family"] == "price"]
    for item in presets:
        assert not formula.check(item["formula"]), (item["style"], formula.check(item["formula"]))


def test_the_windows_of_a_preset_are_the_styles_own():
    by_style = {item["style"]: item["formula"] for item in styles.preset_formulas()}
    assert f"Ref($close, {styles.REVERSAL})" in by_style["Reversal"]
    assert f"Ref($close, {styles.MOMENTUM})" in by_style["Momentum"] and f"Ref($close, {styles.SKIP})" in by_style["Momentum"]
    assert f"Mean($amount, {styles.LIQUIDITY})" in by_style["Liquidity"]


def test_new_factor_from_the_public_action_can_run_at_once(tmp_path, monkeypatch):
    """The Agent's way: create_object for a factor (a preset or not), then start_run with nothing else set: the data settings come from the backend."""
    import asyncio

    from modules.quant.backend.object_routes import handle_actions
    from tests.unit.test_quant_factor_runs import SP500, services, settled, with_materials

    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012, .013])
        with_materials(ctx, SP500)
        handlers = {}
        ctx.handlers = handlers
        handle_actions(ctx, shell)
        for args in ({"kind": "factor", "preset": "factor:Reversal"}, {"kind": "factor", "name": "Mine"}):
            made = await handlers["create_object"]({**args,**({} if args.get("preset") else {"formula":"-($close / Ref($close, 5) - 1)"})}, None)
            started = await handlers["start_run"]({"objectId":made["objectId"]},None)
            run = await settled(factors, started["runId"])
            assert run["state"] == "completed" and run["settings"]["data"]["index"]["instrument"] == SP500
            assert run["settings"]["data"]["timeframe"] == "1d" and run["settings"]["data"]["since"] == "2016-01-01"
    asyncio.run(scenario())
