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


# --- #45 quick filter / search ---


def test_render_serve_index_includes_search_input(tmp_path):
    """A non-empty index ships a text input for filtering by project name."""
    projects = [
        _make_minimal_bundle(tmp_path, "alpha"),
        _make_minimal_bundle(tmp_path, "beta"),
    ]
    out = _render_serve_index(tmp_path, projects)
    assert 'id="proj-search"' in out
    # Standard search-input semantics — type, role, accessible label
    assert 'type="search"' in out
    assert 'placeholder=' in out


def test_render_serve_index_each_card_carries_searchable_data(tmp_path):
    """Cards expose a `data-search` attribute pre-lowercased so the JS
    filter can match without per-keystroke string allocation."""
    projects = [
        _make_minimal_bundle(tmp_path, "Foo Bar"),
    ]
    out = _render_serve_index(tmp_path, projects)
    # Lowercased name appears in the data-search attribute.
    assert 'data-search="' in out
    # The bundle stem is searchable in lower case (case-insensitive matching)
    assert "foo bar" in out


def test_render_serve_index_includes_match_counter(tmp_path):
    """A counter element shows 'Showing N of M' so the user has feedback
    while filtering. Lives inside the page where the count line already is."""
    projects = [
        _make_minimal_bundle(tmp_path, "alpha"),
        _make_minimal_bundle(tmp_path, "beta"),
    ]
    out = _render_serve_index(tmp_path, projects)
    # The counter element exists with a stable id the JS can target.
    assert 'id="proj-count"' in out


def test_render_serve_index_omits_search_when_empty(tmp_path):
    """An empty directory has nothing to filter — no input, no counter."""
    out = _render_serve_index(tmp_path, [])
    assert 'id="proj-search"' not in out
    assert 'id="proj-count"' not in out


def test_render_serve_index_filter_script_persists_query(tmp_path):
    """Filter query persists across reloads via localStorage, matching the
    theme + tab persistence convention already in the dashboard."""
    projects = [_make_minimal_bundle(tmp_path, "alpha")]
    out = _render_serve_index(tmp_path, projects)
    assert "lpxtool-search" in out
    assert "localStorage" in out


def test_render_serve_index_search_html_escapes_project_names(tmp_path):
    """Project names with HTML metacharacters must not break the
    data-search attribute or inject markup."""
    projects = [_make_minimal_bundle(tmp_path, "weird<name>")]
    out = _render_serve_index(tmp_path, projects)
    # The raw '<name>' must be escaped wherever it appears (including
    # inside data-search). The page must remain a valid HTML document.
    assert "<name>" not in out
    assert "&lt;name&gt;" in out


# --- #46 metadata chips on proj-card ---


def _meta(**overrides):
    """Build a synthetic index-metadata entry with sane defaults."""
    base = {
        "mtime": 1.0,
        "name": "song",
        "key": "C", "gender": "major",
        "bpm": 120.0,
        "track_count": 8,
        "bundle_size_bytes": 12 * 1024 * 1024,  # 12 MB
        "created_at": "2024-01-15T10:00:00",
        "modified_at": "2026-04-25T10:00:00",
    }
    base.update(overrides)
    return base


def test_render_serve_index_renders_chip_row_when_metadata_provided(tmp_path):
    """When the handler supplies a metadata map, each card gets a chip
    row carrying key+gender, BPM, track count, size, and modified-time."""
    project = _make_minimal_bundle(tmp_path, "song")
    metadata = {str(project): _meta()}
    out = _render_serve_index(tmp_path, [project], metadata=metadata)
    assert 'class="proj-chips"' in out
    assert "C major" in out          # key + gender chip
    assert "120" in out              # BPM
    assert "8" in out                # track count
    assert "MB" in out               # size formatter ran


def test_render_serve_index_chip_row_omitted_when_no_metadata(tmp_path):
    """Backwards compat: callers that don't pass metadata get the original
    name + path layout, not a row of empty/placeholder chips."""
    project = _make_minimal_bundle(tmp_path, "song")
    out = _render_serve_index(tmp_path, [project])
    assert 'class="proj-chips"' not in out


def test_render_serve_index_chips_omitted_for_unparseable_bundle(tmp_path):
    """A bundle that failed to parse (missing from metadata map) falls
    back to name + path only — chips can't be invented from nothing."""
    p1 = _make_minimal_bundle(tmp_path, "good")
    p2 = _make_minimal_bundle(tmp_path, "broken")
    metadata = {str(p1): _meta()}  # p2 omitted
    out = _render_serve_index(tmp_path, [p1, p2], metadata=metadata)
    # Both project cards exist
    assert 'href="/project/0"' in out
    assert 'href="/project/1"' in out
    # Only one chip row gets rendered (the parseable one).
    assert out.count('class="proj-chips"') == 1


def test_render_serve_index_inlines_lucide_svg_icons(tmp_path):
    """No external requests — icons are inline 24x24 SVG. Lucide stroke
    style is `stroke="currentColor"` so light/dark mode flips with the
    palette via CSS."""
    project = _make_minimal_bundle(tmp_path, "song")
    metadata = {str(project): _meta()}
    out = _render_serve_index(tmp_path, [project], metadata=metadata)
    assert "<svg" in out
    assert 'stroke="currentColor"' in out
    # Each chip carries one icon, so we expect at least 5 SVGs (key, BPM,
    # tracks, size, modified) on a single-card index.
    assert out.count("<svg") >= 5


def test_render_serve_index_relative_time_appears_in_chip(tmp_path):
    """The modified chip uses the dense relative-time formatter, not an
    absolute timestamp. The absolute date lives in the title attribute."""
    project = _make_minimal_bundle(tmp_path, "song")
    # Modified ~3 weeks ago from any wall clock — too old for hours/days.
    from datetime import datetime, timedelta
    mod = (datetime.now() - timedelta(days=22)).isoformat()
    created = (datetime.now() - timedelta(days=400)).isoformat()
    metadata = {str(project): _meta(modified_at=mod, created_at=created)}
    out = _render_serve_index(tmp_path, [project], metadata=metadata)
    # The card should carry "3w" (or similar) in the chip…
    panel = out[out.find("proj-chips"):]
    # 22 days = 3w under our ladder
    assert "3w" in panel
    # …and the absolute created date should be reachable via the title attr.
    assert "title=" in panel


def test_render_serve_index_renders_em_dash_for_zero_size(tmp_path):
    """Defensive: an unknown bundle size renders as `—` rather than `0 B`
    — keeps the chip honest when metadata is partial."""
    project = _make_minimal_bundle(tmp_path, "song")
    metadata = {str(project): _meta(bundle_size_bytes=0)}
    out = _render_serve_index(tmp_path, [project], metadata=metadata)
    panel = out[out.find("proj-chips"):]
    assert "&mdash;" in panel or "—" in panel


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


def test_serve_api_project_json_includes_track_list(live_server):
    """Regression: the serve handler must read ProjectData and pass it as
    `raw=` so `track_list` is populated. Without that, the dashboard's
    Tracks tab shows the empty state for every served project even when
    the project genuinely has tracks."""
    status, _, body = _get(live_server, "/api/projects/0")
    assert status == 200
    data = json.loads(body)
    # `track_list` is the registry-derived inventory — must be a list, not
    # null / missing. (Empty for the minimal fixture, but the key must be
    # present so the test fixture is *upgradable* — a real project with
    # tracks would populate it.)
    assert "track_list" in data
    assert isinstance(data["track_list"], list)


def test_serve_api_project_json_includes_phantom_plugins_field(live_server):
    """Same regression class: phantom_plugins requires `all_aus` (which
    requires reading ProjectData). The key must be present in the payload."""
    status, _, body = _get(live_server, "/api/projects/0")
    data = json.loads(body)
    assert "phantom_plugins" in data
    assert isinstance(data["phantom_plugins"], list)


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


def test_serve_rollup_html_returns_browsable_view(live_server):
    """`/rollup` returns an HTML rollup view, not raw JSON — clickable
    project list + top plugins + top manufacturers."""
    status, ctype, body = _get(live_server, "/rollup")
    assert status == 200
    assert "text/html" in ctype
    # Project names visible on the page
    assert "alpha" in body
    assert "beta" in body
    # Links into per-project dashboards
    assert 'href="/project/0"' in body or 'href="/project/1"' in body


def test_serve_index_links_to_rollup(live_server):
    """Library index advertises the /rollup view so users can find it
    when they entered through `--serve` rather than `--rollup`."""
    _, _, body = _get(live_server, "/")
    assert 'href="/rollup"' in body


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


# --- --rollup serves an HTML view by default ---

def test_expand_rollup_paths_expands_directories(tmp_path):
    """`lpxtool --rollup ~/Music/Logic` (a directory) should be treated
    as `lpxtool --rollup ~/Music/Logic/*.logicx` — auto-glob the children."""
    from lpx_inspect import _expand_rollup_paths
    _make_minimal_bundle(tmp_path, "alpha")
    _make_minimal_bundle(tmp_path, "beta")
    out = _expand_rollup_paths([str(tmp_path)])
    assert {p.stem for p in out} == {"alpha", "beta"}


def test_expand_rollup_paths_keeps_explicit_bundles(tmp_path):
    from lpx_inspect import _expand_rollup_paths
    a = _make_minimal_bundle(tmp_path, "alpha")
    b = _make_minimal_bundle(tmp_path, "beta")
    out = _expand_rollup_paths([str(a), str(b)])
    assert {p.stem for p in out} == {"alpha", "beta"}


def test_expand_rollup_paths_dedupes(tmp_path):
    """Mixing a directory and an explicit bundle inside it should not
    create duplicates."""
    from lpx_inspect import _expand_rollup_paths
    a = _make_minimal_bundle(tmp_path, "alpha")
    out = _expand_rollup_paths([str(tmp_path), str(a)])
    assert [p.stem for p in out] == ["alpha"]


def test_expand_rollup_paths_skips_missing(tmp_path):
    from lpx_inspect import _expand_rollup_paths
    out = _expand_rollup_paths([str(tmp_path / "does-not-exist")])
    assert out == []


# --- bad-bundle resilience in the live server ---

def test_parse_project_raises_helpful_error_on_invalid_bundle(tmp_path):
    """An empty / non-.logicx directory should raise a clean
    FileNotFoundError, not StopIteration."""
    from lpx_inspect import parse_project
    bad = tmp_path / "not-a-project"
    bad.mkdir()
    with pytest.raises(FileNotFoundError):
        parse_project(bad)


def test_serve_handles_invalid_bundle_gracefully(tmp_path):
    """When the project provider yields a non-bundle path (e.g. a stale
    glob entry), hitting /project/0 or /api/projects/0 must not crash
    the request handler."""
    from lpx_inspect import start_serve_for_projects
    bad = tmp_path / "not-a-project.logicx"  # has the suffix but no contents
    bad.mkdir()
    httpd, port = start_serve_for_projects([bad], port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = _get(port, "/project/0")
        assert 500 <= status < 600
        assert "lpxtool" in body.lower() or "could not" in body.lower() or "error" in body.lower()
        status, _, _ = _get(port, "/api/projects/0")
        assert 500 <= status < 600
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_start_serve_for_projects_accepts_explicit_paths(tmp_path):
    """`--rollup` needs the server to accept an explicit project list
    rather than scanning a directory. start_serve_for_projects spins up
    a server scoped to exactly the bundles passed in."""
    from lpx_inspect import start_serve_for_projects
    bundles = [
        _make_minimal_bundle(tmp_path, "one"),
        _make_minimal_bundle(tmp_path, "two"),
    ]
    httpd, port = start_serve_for_projects(bundles, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, ctype, body = _get(port, "/api/projects")
        assert status == 200
        data = json.loads(body)
        assert {p["name"] for p in data} == {"one", "two"}
        # Rollup HTML available for the same explicit set
        status, ctype, body = _get(port, "/rollup")
        assert status == 200
        assert "text/html" in ctype
    finally:
        httpd.shutdown()
        httpd.server_close()
