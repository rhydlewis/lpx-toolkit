"""Shared test fixtures."""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def auval_sample() -> str:
    """Captured `auval -l` output covering tricky 4CC cases.

    Use this everywhere you'd otherwise shell out to auval. The fixture is
    deliberately small — it includes one example of each documented format
    quirk (trailing-space subtype, leading-space manufacturer, hyphenated
    plugin name) without trying to mirror a full plug-in installation.
    """
    return (FIXTURES / "auval_sample.txt").read_text()
