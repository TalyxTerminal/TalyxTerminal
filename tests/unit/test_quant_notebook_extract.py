from modules.quant.backend import notebook_extract as extract


def cell(identifier, source, count=None, kind="code"):
    return {"id": identifier, "type": kind, "source": source, "executionCount": count}


ORDER = [cell("a", 'rev = q.library.read("Reversal 1M", version=2)', 1), cell("b", 'ic = q.ic(rev.values)', 2), cell("c", 'earn = q.library.read("Earnings windows")\nex = rev.values.mask(earn.x)', 4),
         cell("d", 'x = q.data(universe="S&P 500", start="2016-01-04", end="2026-11-03")', 5), cell("n", "# notes", None, "markdown")]


def test_code_as_written_with_cell_headers():
    chosen = [ORDER[1], ORDER[2]]
    assert extract.code_as_written(chosen, ORDER) == '# cell [2]\nic = q.ic(rev.values)\n\n# cell [4]\nearn = q.library.read("Earnings windows")\nex = rev.values.mask(earn.x)\n'


def test_data_settings_from_cells():
    found = extract.data_settings([ORDER[0], ORDER[2], ORDER[3]], held_out_from="2025-06-30")
    assert found["libraryInputs"] == [{"name": "Reversal 1M", "version": 2}, {"name": "Earnings windows", "version": None}]
    assert (found["universe"], found["since"], found["until"]) == ("S&P 500", "2016-01-04", "2026-11-03") and found["found"]
    assert found["heldOutStudied"]


def test_data_settings_none_found():
    found = extract.data_settings([cell("z", "y = x * 2")])
    assert not found["found"] and found["runs"] == [] and found["libraryInputs"] == [] and not found["heldOutStudied"]


def test_extract_marks_held_out_studied():
    before = [cell("z", 'x = q.data(universe="S&P 500", end="2025-01-01")')]
    assert not extract.data_settings(before, "2025-06-30")["heldOutStudied"]
    assert extract.data_settings([cell("z", 'x = q.data(universe="S&P 500")')], "2025-06-30")["heldOutStudied"]


def test_first_source_wins_and_conflicts_are_listed():
    found = extract.data_settings([cell("z", 'a = q.data(universe="S&P 500")\nb = q.data(universe="Nasdaq 100")')])
    assert found["universe"] == "S&P 500" and found["conflicts"] == ["universe: Nasdaq 100 (kept S&P 500)"]


def test_run_calls_are_read_without_running():
    found = extract.data_settings([cell("z", 'r = q.run("Short-term reversal", 21)\nn = q.run(name)')])
    assert found["runs"] == [{"object": "Short-term reversal", "number": 21}]


def test_extract_names_missing_definitions():
    assert extract.not_taken([ORDER[2]], ORDER)[0] == "Cell [1] defines rev that these cells use"
    both = [cell("a", "rev = 1", 2), cell("b", "ex = 2", 4), cell("c", "use(rev, ex)", 6)]
    assert extract.not_taken([both[2]], both) == ["Cells [2] and [4] define rev and ex that these cells use"]
    assert extract.not_taken([ORDER[0], ORDER[1]], ORDER) == []


def test_extract_each_kind():
    for kind in extract.KINDS:
        assert extract.preview(["a", "b"], ORDER, kind)["code"].startswith("# cell [1]")


def test_a_backtest_of_a_named_strategy_is_listed_with_the_data_it_reads():
    from modules.quant.backend.notebook_extract import data_settings
    got = data_settings([{"id": "a", "source": 'd = q.data(["akshare:600642.XSHG"], timeframe="1d", start="2024-01-02", end="2025-06-30")\nbt = q.backtest("One name on a signal", data=d)'}])
    assert got["runs"] == [{"object": "One name on a signal"}] and got["symbols"] == ["akshare:600642.XSHG"] and got["found"]

def test_extract_current_index_list_and_keyword_run_id():
    observed = extract.data_settings([cell("read", 'r = q.run(run_id="run-456")\na = q.data(index={"symbol":"^GSPC","at":"2026-01-01"}, end="2026-01-01")')], "2026-01-01")
    assert observed["runs"] == [{"object": "run-456"}]
    assert observed["index"] == {"symbol": "^GSPC", "at": "2026-01-01"}
    assert observed["heldOutStudied"]
    assert extract.data_settings([cell("read", 'q.data(list="My stocks")')])["list"] == "My stocks"
