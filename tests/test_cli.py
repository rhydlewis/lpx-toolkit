"""Tests for CLI argument parsing.

The CLI used hand-rolled `sys.argv` inspection which crashed on `--help`,
`-h`, `--version`, `-v` (the path-positional captured the flag and tried to
open it as a project bundle). These tests pin the argparse contract.
"""
import pytest

from lpx_inspect import __version__, build_parser, cli


def test_version_constant_is_a_dotted_string():
    """`__version__` is a single source of truth for `--version`."""
    assert isinstance(__version__, str)
    parts = __version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts), f"non-numeric version segment in {__version__}"


def test_parser_help_does_not_crash(capsys):
    """`--help` exits cleanly with status 0 and writes a usage line."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_parser_short_help_does_not_crash(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-h"])
    assert exc.value.code == 0


def test_parser_version_prints_version_and_exits(capsys):
    """`--version` and `-v` print __version__ and exit 0."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_parser_short_version_prints_version_and_exits(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-v"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_parser_inspect_mode_with_path():
    """The default mode takes one project path."""
    parser = build_parser()
    args = parser.parse_args(["/some/project.logicx"])
    assert args.path == "/some/project.logicx"
    assert args.json is False
    assert args.bplists is False
    assert args.rollup is False


def test_parser_inspect_with_json_flag():
    parser = build_parser()
    args = parser.parse_args(["--json", "/some/project.logicx"])
    assert args.json is True
    assert args.path == "/some/project.logicx"


def test_parser_inspect_with_bplists_flag():
    parser = build_parser()
    args = parser.parse_args(["--bplists", "/some/project.logicx"])
    assert args.bplists is True


def test_parser_rollup_mode_with_multiple_paths():
    """`--rollup` followed by N paths: first goes to `path`, rest to
    `rollup_paths`. The cli() entry point recombines them."""
    parser = build_parser()
    args = parser.parse_args(["--rollup", "a.logicx", "b.logicx", "c.logicx"])
    assert args.rollup is True
    assert args.path == "a.logicx"
    assert args.rollup_paths == ["b.logicx", "c.logicx"]


def test_parser_unknown_flag_errors_with_nonzero_exit(capsys):
    """Unknown flags should produce an argparse-style error, not a crash
    deeper in the program."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--bogus-flag", "/some/project.logicx"])
    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert "unrecognized" in captured.err.lower() or "unrecognised" in captured.err.lower()


def test_cli_inspect_mode_requires_path(capsys):
    """Calling cli() with no args should error (path is required when not
    rolling up). The error happens in cli(), not the parser, since the
    parser permits empty path to support --rollup mode."""
    with pytest.raises(SystemExit) as exc:
        cli([])
    assert exc.value.code != 0


def test_cli_rollup_mode_requires_at_least_one_path(capsys):
    """`--rollup` alone (no paths) should error."""
    with pytest.raises(SystemExit) as exc:
        cli(["--rollup"])
    assert exc.value.code != 0


def test_cli_directory_with_logicx_children_suggests_rollup(tmp_path, capsys):
    """If the user points the inspect mode at a directory that *contains*
    .logicx bundles, suggest `--rollup` instead of crashing with a glob
    StopIteration."""
    import plistlib
    bundle = tmp_path / "demo.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    (alt / "MetaData.plist").write_bytes(plistlib.dumps({"BeatsPerMinute": 120.0}))
    (alt / "ProjectData").write_bytes(b"")
    with pytest.raises(SystemExit) as exc:
        cli([str(tmp_path)])
    assert exc.value.code != 0
    captured = capsys.readouterr()
    msg = captured.err.lower() + captured.out.lower()
    assert "--rollup" in msg


def test_cli_non_logicx_directory_explains_problem(tmp_path, capsys):
    """If the user points inspect mode at a plain directory (no .logicx
    children), say so cleanly — no traceback."""
    with pytest.raises(SystemExit) as exc:
        cli([str(tmp_path)])
    assert exc.value.code != 0
    captured = capsys.readouterr()
    msg = captured.err.lower() + captured.out.lower()
    assert ".logicx" in msg or "not a" in msg


def test_cli_missing_path_explains_problem(tmp_path, capsys):
    """A non-existent path should error cleanly, not crash."""
    with pytest.raises(SystemExit) as exc:
        cli([str(tmp_path / "does-not-exist.logicx")])
    assert exc.value.code != 0
    captured = capsys.readouterr()
    msg = captured.err.lower() + captured.out.lower()
    assert "not found" in msg or "no such" in msg or "does not exist" in msg
