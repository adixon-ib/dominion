#!/usr/bin/env python3
#
# Sanity test for NIOS token mode (IPc) on appliance.
#
# Usage:
#   GM_HOSTNAME=vm-04-89 GM_IP=10.35.211.4 MEMBER_HOSTNAME=vm-09-89 MEMBER_IP=10.35.211.9 IMAGE_FILE=/path/to/nios.ova python3 sanity.py
#   SKIP_DEPLOY=1 GM_HOSTNAME=vm-04-89 GM_IP=10.35.211.4 MEMBER_HOSTNAME=vm-09-89 MEMBER_IP=10.35.211.9 python3 sanity.py

import os
import subprocess
import glob
import atexit
import json
import sys
import threading
import time


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"Error: {name} environment variable is required.", file=sys.stderr)
        sys.exit(1)
    return val


GRID1_HOSTNAME = _require_env("GM_HOSTNAME")
GRID1_IP = _require_env("GM_IP")
GRID2_HOSTNAME = _require_env("MEMBER_HOSTNAME")
GRID2_IP = _require_env("MEMBER_IP")
SKIP_DEPLOY = os.environ.get("SKIP_DEPLOY") == "1"
IMAGE_FILE = os.environ.get("IMAGE_FILE", "")

if not SKIP_DEPLOY and not IMAGE_FILE:
    print("Error: IMAGE_FILE environment variable is required for fresh deploy. Set SKIP_DEPLOY=1 to skip.", file=sys.stderr)
    sys.exit(1)

GRID_NAME = "Infoblox"
GRID_SHARED_SECRET = "test"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UPDATE_TOKEN_BUNDLE_SCRIPT = os.path.join(REPO_ROOT, "tools", "update_token_bundle.sh")
DUMP_TOKEN_STATS_SCRIPT = os.path.join(REPO_ROOT, "tools", "dump_token_stats")

from dominion import (
    Appliance,
    ApplianceConfig,
    Grid,
    GridConfig,
    NIOS_CONSOLE_PASSWORD,
    NIOS_CONSOLE_USER,
    Run,
    WAPI_VERSION,
)


def sanity_verify_usage_mode_enabled_gm(joining_member):
    with joining_member.console_session() as console:
        output = console.run("show nios_usage_mode")
        assert "NIOS usage mode: IPc" in output, f"Expected 'NIOS usage mode: IPc' but got: {output}"


def sanity_verify_usage_mode_enabled_mem(joining_member):
    with joining_member.console_session() as console:
        output = console.run("show nios_usage_mode")
        assert "ERROR: Only GM can configure IPc mode settings." in output, f"Expected 'ERROR: Only GM can configure IPc mode settings.' but got: {output}"


def sanity_check_token_aggregator(app):
    with app.shell_session() as shell:
        output = shell.run("pgrep -f token_aggregator")
        assert output.strip(), f"token_aggregator service is not running. pgrep output: {output}"


def sanity_enable_token_mode(app):
    # update_token_bundle.sh rebuilds token_bundle.tar with app's lpc_uid,
    # scp's it to /storage/tmp/token_bundle.tar, and touches airgapped_skip
    # -- required before "set nios_usage_mode IPc" will succeed on an
    # air-gapped grid. It prompts for both steps; answer "y" to each.
    subprocess.run(
        [UPDATE_TOKEN_BUNDLE_SCRIPT, app.ip],
        input="y\ny\n",
        text=True,
        check=True,
    )

    with app.console_session() as console:
        # "set nios_usage_mode IPc" asks "Do you want to proceed?" once before enabling.
        console.sendline("set nios_usage_mode IPc")
        console.expect(r"Do you want to proceed\? \(y or n\)")
        console.sendline("y")
        output = console.expect("IPc mode enabled successfully")
        assert "IPc mode enabled successfully" in output, f"Failed to enable IPc mode. Console output: {output}"


def sanity_verify_usage_mode_disabled(app):
    with app.console_session() as console:
        output = console.run("show nios_usage_mode")
        assert "NIOS usage mode: IPc" not in output, f"nios_usage_mode should not be enabled. Got: {output}"


def sanity_verify_aggregator_not_running(app):
    with app.shell_session() as shell:
        output = shell.run("pgrep -f token_aggregator")
        assert not output.strip(), f"token_aggregator service should not be running. pgrep output: {output}"


def test_install_grids():
    grids = {}

    def create_grid1():
        grids['grid1'] = Grid.from_config(GridConfig(
            topology="standalone",
            member=GRID1_HOSTNAME,
            file=IMAGE_FILE,
            output_dir=run.output_dir,
        ))

    def create_grid2():
        grids['grid2'] = Grid.from_config(GridConfig(
            topology="standalone",
            member=GRID2_HOSTNAME,
            file=IMAGE_FILE,
            output_dir=run.output_dir,
        ))

    t1 = threading.Thread(target=create_grid1)
    t2 = threading.Thread(target=create_grid2)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    return grids['grid1'], grids['grid2']


def sanity_verify_independent_gms(grid1, grid2):
    raise NotImplementedError


def sanity_dump_stats_file(app):
    result = subprocess.run(
        [DUMP_TOKEN_STATS_SCRIPT, app.ip],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip(), f"dump_token_stats returned no output. stdout: {result.stdout}, stderr: {result.stderr}"
    return result.stdout


_WAPI_HTTP_STATUS_MARKER = "HTTP_STATUS:"
_WAPI_WRITE_OUT = "\\n" + _WAPI_HTTP_STATUS_MARKER + "%{http_code}"


def _wapi_curl(transport, method, path, data=None):
    """Run a WAPI call against an appliance's own localhost WAPI via its
    transport. Raises RuntimeError on a non-2xx response or a WAPI Error
    payload -- curl itself exits 0 for HTTP error responses, so a bare
    truthiness/exit-code check silently treats WAPI failures as success.
    """
    cmd = f'curl -sk -w "{_WAPI_WRITE_OUT}" -u {NIOS_CONSOLE_USER}:{NIOS_CONSOLE_PASSWORD}'
    if method == "POST":
        cmd += f" -X POST -H 'Content-Type: application/json' -d '{json.dumps(data)}'"
    elif method == "DELETE":
        cmd += " -X DELETE"
    cmd += f" https://localhost/wapi/{WAPI_VERSION}/{path}"

    _, out, _ = transport.run(cmd)
    body, _, status = out.rpartition(_WAPI_HTTP_STATUS_MARKER)
    body = body.strip()
    status = status.strip()

    parsed = json.loads(body) if body else None

    if status and not status.startswith("2"):
        raise RuntimeError(f"WAPI {method} {path} failed (HTTP {status}): {body}")
    if isinstance(parsed, dict) and "Error" in parsed:
        raise RuntimeError(f"WAPI {method} {path} failed: {parsed}")

    return parsed


def sanity_join_member(master, member):
    """Join `member` to `master`'s grid as a member.

    `master` and `member` are Appliance objects (each currently its own
    standalone grid master). The master must pre-register the incoming
    member's identity via WAPI before the member's CLI join will succeed --
    there is no CLI equivalent for this step on the master, only WAPI/GUI.
    See sakV.define_member in the lab tooling for the reference
    implementation.
    """
    master_transport = master.container._transport
    member_transport = member.container._transport

    self_records = _wapi_curl(
        member_transport, "GET",
        "member?_return_fields=host_name,vip_setting,ipv6_setting,config_addr_type,platform",
    )
    assert self_records, f"Member's own WAPI member record is empty. Response: {self_records}"
    self_record = self_records[0]

    # A prior join attempt may have left a stale member record on `master`
    # for this same VIP (e.g. `member` was later reset back to standalone
    # without the leftover master-side record being cleaned up) -- WAPI
    # rejects re-registering the same VIP under a "new" member, so clear
    # any existing match first.
    member_vip = self_record["vip_setting"]["address"]
    existing_members = _wapi_curl(
        master_transport, "GET", "member?_return_fields=vip_setting",
    )
    for existing in existing_members:
        if existing.get("vip_setting", {}).get("address") == member_vip:
            _wapi_curl(master_transport, "DELETE", existing["_ref"])

    # A fresh standalone appliance always self-reports the generic default
    # grid hostname ("master.mygrid.infoblox.com") -- reusing it verbatim
    # when registering on a *different* grid collides with that grid's own
    # naming. Use a name derived from the appliance's real hostname instead.
    new_member = {
        "host_name": f"{member.hostname}.mygrid.infoblox.com",
        "config_addr_type": self_record["config_addr_type"],
        "vip_setting": self_record["vip_setting"],
        "platform": self_record["platform"],
    }
    if self_record.get("ipv6_setting"):
        new_member["ipv6_setting"] = self_record["ipv6_setting"]

    master_ref = _wapi_curl(
        master_transport, "POST", "member", new_member,
    )
    assert master_ref, f"WAPI POST to master returned no member _ref. Response: {master_ref}"

    with member.console_session() as console:
        console.sendline("set membership")
        console.expect("Master VIP")
        console.sendline(master.ip)
        console.expect("Grid Name")
        console.sendline(GRID_NAME)
        console.expect("Shared Secret")
        console.sendline(GRID_SHARED_SECRET)
        console.expect("Is this correct")
        console.sendline("y")
        console.expect("Are you sure")
        console.sendline("y")


def generate_html_report():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.join(REPO_ROOT, "results")
    junit_files = sorted(glob.glob(f"{results_dir}/*/junit.xml"))
    if not junit_files:
        return
    latest_junit = junit_files[-1]
    html_file = os.path.join(script_dir, "results.html")
    try:
        subprocess.run(["junit2html", latest_junit, html_file], check=True)
        print(f"\nHTML report: {html_file}")
    except Exception as e:
        print(f"\nFailed to generate HTML: {e}")


if __name__ == "__main__":
    atexit.register(generate_html_report)

    run = Run()
    gm = None
    try:
        # Multi-grid token mode test
        if SKIP_DEPLOY:
            run.skip("install grids")
            grid1 = grid2 = None
            gm = Appliance.from_config(ApplianceConfig(hostname=GRID1_HOSTNAME, ip=GRID1_IP, output_dir=run.output_dir))
            joining_member = Appliance.from_config(ApplianceConfig(hostname=GRID2_HOSTNAME, ip=GRID2_IP, output_dir=run.output_dir))
        else:
            grid1, grid2 = run.test("install grids", test_install_grids)
            gm, joining_member = grid1.gm, grid2.gm

        run.test("verify nios_usage_mode disabled (gm)", lambda: sanity_verify_usage_mode_disabled(gm))
        run.test("verify nios_usage_mode disabled (joining_member)", lambda: sanity_verify_usage_mode_disabled(joining_member))
        run.test("verify token_aggregator not running (gm)", lambda: sanity_verify_aggregator_not_running(gm))
        run.test("verify token_aggregator not running (joining_member)", lambda: sanity_verify_aggregator_not_running(joining_member))
        # run.test("verify independent grid managers", lambda: sanity_verify_independent_gms(grid1, grid2))

        run.test("enable token mode (gm)", lambda: sanity_enable_token_mode(gm))
        run.test("verify nios_usage_mode enabled (gm)", lambda: sanity_verify_usage_mode_enabled_gm(gm))
        time.sleep(120)
        run.test("check token_aggregator running (gm)", lambda: sanity_check_token_aggregator(gm))
        run.test("dump stats file (gm)", lambda: sanity_dump_stats_file(gm))

        run.test("join joining_member to gm", lambda: sanity_join_member(gm, joining_member))
        time.sleep(1800 * 1.5)  # 45 minutes for post-join grid sync to complete

        run.test("verify nios_usage_mode enabled (joining_member)", lambda: sanity_verify_usage_mode_enabled_mem(joining_member))
        run.test("verify token_aggregator not running (joining_member)", lambda: sanity_verify_aggregator_not_running(joining_member))
        run.test("verify token_aggregator still running (gm)", lambda: sanity_check_token_aggregator(gm))
    finally:
        run.summary(gm)
