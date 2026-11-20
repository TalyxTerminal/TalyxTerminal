"""Enforce every production dependency boundary."""

from pathlib import Path

from scripts.checks.check_boundaries import check

ROOT = Path(__file__).resolve().parents[2]


def test_production_boundaries_are_clean():
    report = check(ROOT)
    assert report["total"] == 0, report["violations"]
