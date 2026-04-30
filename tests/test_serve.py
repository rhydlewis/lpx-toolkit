"""Tests for the --serve mode (#37).

`lpxtool --serve [DIR]` starts a local HTTP server bound to 127.0.0.1
that lets the user browse every .logicx project in a directory through
the same HTML dashboard `--html` produces, plus JSON endpoints for
tooling.

Tests cover:
  - `_list_projects()` finds .logicx bundles in a directory
  - `_render_serve_index()` produces a styled HTML index with links
  - The HTTP handler routes GETs correctly (/, /project/<idx>,
    /api/projects, /api/projects/<idx>, /api/rollup)
  - 404 for unknown paths and out-of-range indexes
  - `start_serve()` returns a bound port (free port when port=0)
  - `--serve` flag wired up in the CLI parser
"""
import http.client
import json
import plistlib
import threading
from pathlib import Path

import pytest

from lpx_inspect import (
    _list_projects,
    _render_serve_index,
    build_parser,
    make_serve_handler,
    start_serve,
)


def _make_minimal_bundle(root: Path, name: str = "demo") -> Path:
    """Build a minimal valid .logicx bundle (mirrors the HTML test helper)."""
    bundle = root / f"{name}.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    md = {
        "SongKey": "C", "SongGenderKey": "major",
        "BeatsPerMinute": 120.0,
        "SongSignatureNumerator": 4, "SongSignatureDenominator": 4,
        "NumberOfTracks": 0, "SampleRate": 48000,
        "FrameRateIndex": 1, "AudioFiles": [],
        "ImpulsResponsesFiles": [],
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    return bundle


# --- _list_projects ---

def test_list_projects_finds_logicx_bundles(tmp_path):
    _make_minimal_bundle(tmp_path, "alpha")
    _make_minimal_bundle(tmp_path, "beta")
    found = _list_projects(tmp_path)
    assert [p.stem for p in found] == ["alpha", "beta"]


def test_list_projects_skips_non_logicx_directories(tmp_path):
    _make_minimal_bundle(tmp_path, "valid")
    (tmp_path / "not-a-project").mkdir()
    (tmp_path / "scratch.txt").write_text("noise")
    found = _list_projects(tmp_path)
    assert [p.stem for p in found] == ["valid"]


def test_list_projects_handles_missing_directory(tmp_path):
    assert _list_projects(tmp_path / "does-not-exist") == []


def test_list_projects_returns_sorted(tmp_path):
    for name in ["zulu", "alpha", "mike"]:
        _make_minimal_bundle(tmp_path, name)
    found = _list_projects(tmp_path)
    assert [p.stem for p in found] == ["alpha", "mike", "zulu"]


# --- _render_serve_index ---

def test_render_serve_index_includes_project_names(tmp_path):
    projects = [
        _make_minimal_bundle(tmp_path, "song-one"),
        _make_minimal_bundle(tmp_path, "song-two"),
    ]
    out = _render_serve_index(tmp_path, projects)
    assert "song-one" in out
    assert "song-two" in out


def test_render_serve_index_links_to_project_routes(tmp_path):
    projects = [
        _make_minimal_bundle(tmp_path, "alpha"),
        _make_minimal_bundle(tmp_path, "beta"),
    ]
    out = _render_serve_index(tmp_path, projects)
    assert 'href="/project/0"' in out
    assert 'href="/project/1"' in out


def test_render_serve_index_handles_empty_directory(tmp_path):
    out = _render_serve_index(tmp_path, [])
    assert "<!doctype html>" in out.lower()
    # Some kind of empty-state indicator — be lenient on exact wording
    assert "no" in out.lower() or "empty" in out.lower()


def test_render_serve_index_includes_theme_toggle(tmp_path):
    """Index page should ship the same light/dark toggle as the project view."""
    out = _render_serve_index(tmp_path, [])
    assert 'id="theme-toggle"' in out
    assert "lpxtool-theme" in out


# --- HTTP handler ---

@pytest.fixture
def live_server(tmp_path):
    """Spin up a real HTTP server on a free port for an integration test."""
    _make_minimal_bundle(tmp_path, "alpha")
    _make_minimal_bundle(tmp_path, "beta")
    httpd, port = start_serve(tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(port: int, path: str) -> tuple[int, str, str]:
    """Make a GET to the live server. Returns (status, content_type, body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        ctype = resp.getheader("Content-Type", "")
        return resp.status, ctype, body
    finally:
        conn.close()


def test_serve_root_returns_html_index(live_server):
    status, ctype, body = _get(live_server, "/")
    assert status == 200
    assert "text/html" in ctype
    assert "alpha" in body
    assert "beta" in body


def test_serve_api_projects_returns_json_list(live_server):
    status, ctype, body = _get(live_server, "/api/projects")
    assert status == 200
    assert "application/json" in ctype
    data = json.loads(body)
    assert isinstance(data, list)
    assert len(data) == 2
    assert {"index", "name", "path"} <= set(data[0].keys())
    assert data[0]["name"] == "alpha"


def test_serve_project_html_returns_dashboard(live_server):
    status, ctype, body = _get(live_server, "/project/0")
    assert status == 200
    assert "text/html" in ctype
    # render_project_html signature — should contain its hallmarks
    assert "<!doctype html>" in body.lower()
    assert "lpx" in body and "toolkit" in body
    assert "alpha" in body  # project name visible in title or header


def test_serve_api_project_json_returns_full_payload(live_server):
    status, ctype, body = _get(live_server, "/api/projects/0")
    assert status == 200
    assert "application/json" in ctype
    data = json.loads(body)
    assert "schema_version" in data
    assert "project" in data


def test_serve_project_404_for_unknown_index(live_server):
    status, _, _ = _get(live_server, "/project/999")
    assert status == 404


def test_serve_404_for_unknown_path(live_server):
    status, _, _ = _get(live_server, "/no-such-route")
    assert status == 404


def test_serve_api_rollup_returns_aggregate_json(live_server):
    status, ctype, body = _get(live_server, "/api/rollup")
    assert status == 200
    assert "application/json" in ctype
    data = json.loads(body)
    # rollup shape (from rollup_projects)
    assert "fingerprints" in data
    assert "vendors" in data


# --- start_serve port selection ---

def test_start_serve_picks_free_port_when_zero(tmp_path):
    httpd, port = start_serve(tmp_path, port=0, open_browser=False)
    try:
        assert port > 0
    finally:
        httpd.server_close()


# --- CLI flag ---

def test_cli_accepts_serve_flag():
    parser = build_parser()
    args = parser.parse_args(["--serve", "/tmp"])
    assert args.serve is True
    assert args.path == "/tmp"


def test_cli_accepts_serve_with_port():
    parser = build_parser()
    args = parser.parse_args(["--serve", "--port", "8765", "/tmp"])
    assert args.serve is True
    assert args.port == 8765
