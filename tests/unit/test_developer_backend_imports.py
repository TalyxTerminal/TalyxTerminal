import importlib


def test_phase1_backend_modules_are_importable() -> None:
    for name in (
        "modules.developer.backend",
    ):
        importlib.import_module(name)
