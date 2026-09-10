"""CLI tests for argument handling."""

import sys

import pytest

from specfill.app import main


def test_main_reports_an_unreadable_seed_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["specfill", str(tmp_path / "missing.md")])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    assert "cannot read" in capsys.readouterr().err
