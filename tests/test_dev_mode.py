import os

import pytest

from utils.config import is_dev_mode


def test_is_dev_mode_false_by_default(monkeypatch):
    monkeypatch.delenv("CONDOR_DEV", raising=False)
    assert is_dev_mode() is False


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_is_dev_mode_true_when_set(monkeypatch, value):
    monkeypatch.setenv("CONDOR_DEV", value)
    assert is_dev_mode() is True
