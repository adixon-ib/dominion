"""pytest fixtures/markers for nios_test integration tests.

Replaces the old test()/skip()/summary() harness. Fixtures wrap the same
config factories and up/down checks helpers.py always had — nothing about
*how* an Appliance/NiosContainer gets built has changed, only how tests
declare "I need this" (a fixture argument) and "skip me if it's not up"
(pytest.skip() inside the fixture, once per session, instead of a repeated
if/elif/else in every file).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

SCRIPTS_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import helpers
from helpers import (
    APPLIANCE_DOWN_REASON,
    NCC_DOWN_REASON,
    appliance_config,
    container_config,
    get_ncc_ip,
    is_appliance_up,
    is_ncc_up,
    local_appliance_config,
    local_container_config,
)
from nios_test import Appliance, LocalAppliance, LocalNiosContainer, NiosContainer

_RESULTS_BASE = os.environ.get(
    "NIOS_TEST_OUTPUT_DIR", "/blox/adixon1/scripts/nios_test/results",
)


# -- CLI options ------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--target",
        action="store",
        default=os.environ.get("NIOS_TEST_TARGET", "all"),
        choices=("appliance", "ncc", "all"),
        help="Restrict to one target. Equivalent to -m appliance / -m ncc.",
    )


# -- Markers + timestamped run dir / junit-xml path -------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "appliance: requires vm-04-89 reachable over SSH",
    )
    config.addinivalue_line(
        "markers", "ncc: requires the balerion/drogon NCC container running",
    )
    config.addinivalue_line(
        "markers", "destructive: deploys real files/binaries or reboots a target",
    )

    run_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = os.path.join(_RESULTS_BASE, run_id)
    os.makedirs(run_dir, exist_ok=True)
    helpers.OUTPUT_DIR = run_dir

    if not config.option.xmlpath:
        config.option.xmlpath = os.path.join(run_dir, "junit.xml")


def pytest_collection_modifyitems(config, items):
    target = config.getoption("--target")
    if target == "all":
        return
    other = "ncc" if target == "appliance" else "appliance"
    skip_other = pytest.mark.skip(
        reason=f"--target={target} excludes '{other}'-marked tests",
    )
    for item in items:
        if other in item.keywords:
            item.add_marker(skip_other)


@pytest.fixture(scope="session", autouse=True)
def _record_run_properties(record_testsuite_property, request):
    record_testsuite_property("target", request.config.getoption("--target"))
    invocation = " ".join(str(a) for a in request.config.invocation_params.args)
    record_testsuite_property("invocation", invocation or "pytest")


# -- Appliance / container fixtures (session-scoped: built once per run) ---------

@pytest.fixture(scope="session")
def ssh_appliance():
    if not is_appliance_up():
        pytest.skip(APPLIANCE_DOWN_REASON)
    return Appliance.from_config(appliance_config())


@pytest.fixture(scope="session")
def ssh_transport(ssh_appliance):
    return ssh_appliance.container._transport


@pytest.fixture(scope="session")
def ssh_container():
    if not is_appliance_up():
        pytest.skip(APPLIANCE_DOWN_REASON)
    return NiosContainer.from_config(container_config(log_level="verbose"))


@pytest.fixture(scope="session")
def local_ncc_container():
    if not is_ncc_up():
        pytest.skip(NCC_DOWN_REASON)
    return LocalNiosContainer.from_config(local_container_config())


@pytest.fixture(scope="session")
def local_transport(local_ncc_container):
    return local_ncc_container._transport


@pytest.fixture(scope="session")
def local_appliance():
    if not is_ncc_up():
        pytest.skip(NCC_DOWN_REASON)
    return LocalAppliance.from_config(local_appliance_config(ip=get_ncc_ip()))


@pytest.fixture(scope="session")
def local_networked_appliance():
    """Heavier than `local_appliance`: pre-checks WAPI via get_ncc_ip(), falls
    through to set_network() (~5-7 min) if the network isn't configured yet."""
    if not is_ncc_up():
        pytest.skip(NCC_DOWN_REASON)
    ncc_ip = get_ncc_ip()
    return LocalAppliance.from_config(local_appliance_config(ip=ncc_ip))
