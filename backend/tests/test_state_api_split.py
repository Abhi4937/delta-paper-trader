# backend/tests/test_state_api_split.py
# get_state must NOT embed history; each position carries series == [].
import inspect

from app.sim import service


def test_get_state_does_not_call_db_series() -> None:
    src = inspect.getsource(service.get_state)
    assert "_db_series" not in src  # history is no longer eager
    assert "build_position_series" not in src  # not eager either


def test_service_exposes_per_position_series_builder() -> None:
    assert hasattr(service, "build_position_series")
