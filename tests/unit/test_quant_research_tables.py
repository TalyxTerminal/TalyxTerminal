
import pytest

from modules.quant.native.research_tables import input_path


def test_fixed_material_overrides_editable_path_without_resolving_current_source(tmp_path):
    fixed = tmp_path / "fixed.csv"
    invocation = {"source": str(tmp_path), "materials": [{"role": "prices", "path": str(fixed), "digest": "fixed-version"}]}
    assert input_path(invocation, {"prices": "later.csv"}, "prices") == fixed
    with pytest.raises(ValueError, match="Choose a losses"):
        input_path(invocation, {}, "losses")

