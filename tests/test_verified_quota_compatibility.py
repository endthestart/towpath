"""Preserve explicitly verified pacing when integrating older local sync work."""

import pytest

from towpath.quota import PacingSettings


def test_verified_quota_configuration_remains_loadable():
    settings = PacingSettings.from_table({"verified_units_per_minute": 8000,
                                         "units_per_minute": 1800, "min_interval_seconds": 0.75})
    assert settings.units_per_minute == 1800 and settings.min_interval_seconds == 0.75


@pytest.mark.parametrize("values", [
    {"verified_units_per_minute": 3000, "units_per_minute": 901},
    {"verified_units_per_minute": 8000, "units_per_minute": 1801},
    {"units_per_minute": 1800},
    {"min_interval_seconds": 0.75},
    {"verified_units_per_minute": True},
])
def test_verified_quota_keeps_existing_ceiling_and_unverified_defaults(values):
    with pytest.raises(ValueError):
        PacingSettings.from_table(values)
