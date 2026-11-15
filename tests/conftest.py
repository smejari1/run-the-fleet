"""Small validated scenarios, independent for each test."""

from copy import deepcopy

import pytest

from fleet.scenario import scenario_from_dict


@pytest.fixture
def scenario_data():
    return {
        "horizon": 30,
        "depot": {"x": 0, "y": 0},
        "fields": [{"id": "F1", "name": "Meadow", "x": 5, "y": 0, "acres": 10}],
        "edges": [{"a": "DEPOT", "b": "F1", "length": 5}],
        "tractors": [{"id": "T1", "name": "One", "implements": ["mower"],
                      "speed": 2, "work_rate": 2, "burn_travel": 0.5, "burn_work": 1}],
        "jobs": [{"id": "J1", "type": "mow", "field": "F1", "requires_implement": "mower",
                  "priority": 1, "deadline": 25, "submitted_at": 0, "acres_total": 4}],
    }


@pytest.fixture
def make_state(scenario_data):
    def make(**overrides):
        data = deepcopy(scenario_data)
        data.update(overrides)
        return scenario_from_dict(data)
    return make
