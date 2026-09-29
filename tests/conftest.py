"""Fixtures communes aux tests."""

import pytest

from betaflight.commands import BetaflightCommands


@pytest.fixture(autouse=True)
def _disarmed_fc(request, monkeypatch):
    """
    Hors tests marqués `arming_guard`, simule un FC désarmé : ces tests portent sur
    d'autres comportements et leurs séquences de trames n'incluent pas MSP_STATUS_EX.
    La garde elle-même est testée avec de vraies trames dans test_arming_guard.py.
    """
    if request.node.get_closest_marker("arming_guard"):
        return
    monkeypatch.setattr(BetaflightCommands, "_require_disarmed", lambda self: None)
