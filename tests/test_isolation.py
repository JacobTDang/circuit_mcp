"""The isolation itself, since everything else trusts it.

Four tests once passed here and failed the first time CI ran them. One of them
stubbed a method its route had stopped calling, so the real Showman answered
and the test only passed on a machine where Showman starts. These check that
the same thing cannot happen quietly again.
"""
from __future__ import annotations

import pytest

from circuit_mcp import ocr_client, paths, showman
from tests.conftest import PROJECT_STORE


def test_every_location_points_away_from_the_developers_own_store():
    for location in (paths.data_dir(), paths.showman_data_dir(),
                     paths.runtime_dir(), paths.workspace_config()):
        assert PROJECT_STORE not in location.parents and location != PROJECT_STORE, \
            f"{location} is inside the real command centre"


def test_starting_the_real_showman_is_an_error():
    with pytest.raises(AssertionError, match="Stub the method"):
        showman.SHOWMAN.start()


def test_starting_the_real_recogniser_is_an_error():
    with pytest.raises(AssertionError, match="810 MB"):
        ocr_client.OCRWorker()._start()


@pytest.mark.spawns_workers
def test_a_marked_test_may_still_spawn_one():
    """The marker is an opt-in, so a test that needs a real worker still can."""
    assert showman.SHOWMAN.start.__name__ != "no_showman"
