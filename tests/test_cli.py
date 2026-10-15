from __future__ import annotations

from pathlib import Path

from ciforge.cli import main

EXAMPLE = str(Path(__file__).parent.parent / "examples" / "hello" / ".ciforge.yml")


def test_validate_and_graph(capsys):
    assert main(["validate", EXAMPLE]) == 0
    assert "chain: compile -> test -> lint -> package" in capsys.readouterr().out
    assert main(["graph", EXAMPLE]) == 0
    assert "compile -. artifacts .-> package" in capsys.readouterr().out


def test_run_returns_the_build_outcome(tmp_path, repo, capsys):
    repo({"a": "1"})
    rc = main(["run", EXAMPLE, "--source", str(repo.root), "-b", "test", "--data-dir", str(tmp_path / "d")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "compile          success" in out and "test             success" in out


def test_invalid_files_exit_with_2(tmp_path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text("version: 1\nproject: x\nbuilds: {}\n")
    assert main(["validate", str(bad)]) == 2
    assert "invalid pipeline" in capsys.readouterr().err
