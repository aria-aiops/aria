"""Unit tests for the ARIA_OPERATING_MODE scaffold (P1.5 S2, #47).

'inform' is the only implemented mode; 'hitm' and 'autonomous' must fail
loudly *before* the pipeline starts, with messages naming the phase that
ships them. Unknown values are rejected outright.
"""

from unittest.mock import MagicMock

import pytest

from core.orchestrator.pipeline import ARIAPipeline


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    """Ignore any local conf.yaml so the mode comes only from the env var (#87)."""
    monkeypatch.setattr("core.config._raw", lambda: {})


@pytest.fixture
def pipeline() -> ARIAPipeline:
    """Pipeline with mock agents — the mode guard fires before any agent runs."""
    return ARIAPipeline(MagicMock(), MagicMock(), MagicMock(), MagicMock())


def test_default_mode_is_inform(monkeypatch, pipeline):
    """Unset env var → inform → guard passes without raising."""
    monkeypatch.delenv("ARIA_OPERATING_MODE", raising=False)
    pipeline._check_operating_mode()  # must not raise


def test_inform_mode_passes(monkeypatch, pipeline):
    monkeypatch.setenv("ARIA_OPERATING_MODE", "inform")
    pipeline._check_operating_mode()  # must not raise


def test_inform_mode_is_case_insensitive(monkeypatch, pipeline):
    monkeypatch.setenv("ARIA_OPERATING_MODE", "INFORM")
    pipeline._check_operating_mode()  # must not raise


def test_hitm_mode_raises_not_implemented(monkeypatch, pipeline):
    """hitm fails before the run starts, pointing at Phase 2."""
    monkeypatch.setenv("ARIA_OPERATING_MODE", "hitm")
    with pytest.raises(NotImplementedError, match="Phase 2"):
        pipeline.run("INC0000001")
    pipeline._agent1.run.assert_not_called()  # guard fired before any agent


def test_autonomous_mode_raises_not_implemented(monkeypatch, pipeline):
    """autonomous fails before the run starts, pointing at Phase 3."""
    monkeypatch.setenv("ARIA_OPERATING_MODE", "autonomous")
    with pytest.raises(NotImplementedError, match="Phase 3"):
        pipeline.run("INC0000001")
    pipeline._agent1.run.assert_not_called()


def test_unknown_mode_raises_value_error(monkeypatch, pipeline):
    monkeypatch.setenv("ARIA_OPERATING_MODE", "yolo")
    with pytest.raises(ValueError, match="yolo"):
        pipeline.run("INC0000001")
