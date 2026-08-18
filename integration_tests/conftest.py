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
from datetime import datetime

import pytest

from helpers import (
    appliance_config,
    disable_ssh_rate_limit,
    get_ncc_ip,
    is_appliance_up,
    is_ncc_up,
    local_appliance_config,
    local_container_config,
)
from nios_test import (
    DEFAULT_RESULTS_DIR,
    Appliance,
    Defaults,
    LocalAppliance,
    LocalNiosContainer,
)

_defaults = Defaults.from_json()
_RESULTS_BASE = os.environ.get("NIOS_TEST_OUTPUT_DIR", DEFAULT_RESULTS_DIR)

NCC_DOWN_REASON = f"{_defaults.container} slot is not running"
APPLIANCE_DOWN_REASON = f"{_defaults.hostname} is not reachable via SSH"


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
    config.nios_test_output_dir = run_dir

    if not config.option.xmlpath:
        config.option.xmlpath = os.path.join(run_dir, "junit.xml")


@pytest.fixture(scope="session")
def output_dir(request) -> str:
    return request.config.nios_test_output_dir


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
def _ssh_rate_limit_cleared():
    """Each test opens its own fresh SSH connection — a full appliance run
    can trip the SSH_RATE_LIMIT firewall rule, so clear it once up front.
    Session-scoped so it only runs once even if both ssh_appliance and
    ssh_container are used in the same run."""
    disable_ssh_rate_limit()


@pytest.fixture(scope="session")
def ssh_appliance(_ssh_rate_limit_cleared, output_dir):
    if not is_appliance_up():
        pytest.skip(APPLIANCE_DOWN_REASON)
    return Appliance.from_config(appliance_config(output_dir=output_dir))


@pytest.fixture(scope="session")
def ssh_transport(ssh_appliance):
    return ssh_appliance.container._transport


@pytest.fixture(scope="session")
def ssh_container(ssh_appliance):
    return ssh_appliance.container


@pytest.fixture(scope="session")
def local_ncc_container(output_dir):
    if not is_ncc_up():
        pytest.skip(NCC_DOWN_REASON)
    return LocalNiosContainer.from_config(local_container_config(output_dir=output_dir))


@pytest.fixture(scope="session")
def local_transport(local_ncc_container):
    return local_ncc_container._transport


@pytest.fixture(scope="session")
def local_appliance(output_dir):
    if not is_ncc_up():
        pytest.skip(NCC_DOWN_REASON)
    config = local_appliance_config(ip=get_ncc_ip(), output_dir=output_dir)
    return LocalAppliance.from_config(config)


@pytest.fixture(scope="session")
def local_networked_appliance(output_dir):
    """Heavier than `local_appliance`: pre-checks WAPI via get_ncc_ip(), falls
    through to set_network() (~5-7 min) if the network isn't configured yet."""
    if not is_ncc_up():
        pytest.skip(NCC_DOWN_REASON)
    ncc_ip = get_ncc_ip()
    config = local_appliance_config(ip=ncc_ip, output_dir=output_dir)
    return LocalAppliance.from_config(config)
