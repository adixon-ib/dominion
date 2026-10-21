"""Run.summary() writes results.json alongside junit.xml."""

import json
import os

import pytest

from dominion import Run


class FakeTarget:
    hostname = "vm-01"

    def __str__(self):
        return "vm-01 (1.2.3.4)"


def test_results_json(tmp_path):
    run = Run(output_dir=str(tmp_path), script="t.py")
    run.add(FakeTarget())
    run.test("ok", lambda: None)
    run.test("bad", lambda: 1 / 0)
    run.skip("sk", "why")
    with pytest.raises(SystemExit) as e:
        run.summary(no_ib_log=True)
    assert e.value.code == 1

    assert os.path.exists(os.path.join(run.output_dir, "junit.xml"))
    data = json.load(open(os.path.join(run.output_dir, "results.json")))
    assert data["script"] == "t.py"
    assert data["targets"] == ["vm-01 (1.2.3.4)"]
    assert (data["passed"], data["failed"], data["skipped"]) == (1, 1, 1)
    assert [r["name"] for r in data["results"]] == ["ok", "bad", "sk"]
    assert data["start"] <= data["end"]
