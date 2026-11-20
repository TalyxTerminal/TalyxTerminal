"""Done criterion 9: price precision from Market's increment, else from the data's decimals."""
from modules.chart.backend.bars import price_format


def test_increment_gives_the_decimals():
    assert price_format("0.00001", ["1.2", "1.23456"]) == {"precision": 5, "minMove": 0.00001}
    assert price_format("0.01", ["356.27"])["precision"] == 2
    assert price_format("0.01", ["356.2700", "355.1234"]) == {"precision": 2, "minMove": 0.01}  # AAPL adjusted


def test_without_increment_the_data_decides():
    assert price_format(None, ["1.2345", "1.23"]) == {"precision": 4, "minMove": 0.0001}
    assert price_format(None, ["100", "101.5"])["precision"] == 1
