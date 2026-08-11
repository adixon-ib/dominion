from __future__ import annotations

import json
import re

from .constants import NIOS_REBOOT_TIMEOUT, NIOS_WAPI_READY_TIMEOUT


class LocalNetworkMixin:
    """NCC-only network bootstrap: docker network introspection, WAPI
    readiness polling, and host routing, layered on top of
    NiosContainer.set_network via super() (the shared console wizard).

    Mixed into LocalNiosContainer only — real appliances already have
    their network configured via SSH before a container is constructed,
    and call NiosContainer.set_network(ip, netmask, gateway) with explicit
    values instead of relying on discovery.
    """

    def _get_container_network(self) -> tuple[str, int]:
        _, out, _ = self._transport.run_on_host(
            f"docker inspect {self.container_name}",
        )
        data = json.loads(out)
        networks = data[0]["NetworkSettings"]["Networks"]
        if "ncc_lan1" not in networks:
            raise RuntimeError(
                f"No ncc_lan1 network on {self.container_name} — "
                "is the container running with NCC networking?"
            )
        net = networks["ncc_lan1"]
        ip = net["IPAddress"]
        prefix = int(net["IPPrefixLen"])
        if not ip:
            raise RuntimeError(f"ncc_lan1 has no IP on {self.container_name}")
        return ip, prefix

    def get_container_ip(self) -> str:
        ip, _ = self._get_container_network()
        return ip

    def _find_forwarding_bridge(self) -> str | None:
        _, out, _ = self._transport.run(
            'for eth in /sys/class/net/eth*/iflink; do cat "$eth" 2>/dev/null; done',
            check=False,
        )
        indices = out.strip().split()
        if not indices:
            return None

        _, links_out, _ = self._transport.run_on_host("ip -o link show", check=False)

        for idx in indices:
            veth = None
            for line in links_out.splitlines():
                parts = line.split()
                if parts and parts[0] == f"{idx}:":
                    veth = parts[1].split("@")[0]
                    break
            if not veth:
                continue

            _, bridge_out, _ = self._transport.run_on_host(
                f"bridge link show dev {veth}", check=False
            )
            if "state forwarding" not in bridge_out:
                continue

            _, link_out, _ = self._transport.run_on_host(
                f"ip -o link show dev {veth}", check=False
            )
            m = re.search(r"master (\S+)", link_out)
            if m:
                return m.group(1)

        return None

    def set_network(
        self,
        ip: str | None = None,
        netmask: str | None = None,
        gateway: str | None = None,
        reboot_timeout: int = NIOS_REBOOT_TIMEOUT,
        wapi_timeout: int = NIOS_WAPI_READY_TIMEOUT,
    ) -> str:
        """Auto-discovers ip/netmask/gateway from the NCC docker network
        when not given explicitly, runs the console wizard via
        super().set_network(...), then additionally polls WAPI and adds a
        host route — both needed only for local docker networking."""
        if ip is None:
            ip, prefix = self._get_container_network()
            netmask = {16: "255.255.0.0", 24: "255.255.255.0"}.get(prefix, "255.255.0.0")
            gateway = ip.rsplit(".", 1)[0] + ".1"

        super().set_network(ip, netmask, gateway, reboot_timeout)

        self._logger.info("Polling WAPI (up to %ds)", wapi_timeout)
        self._wait_for_wapi(timeout=wapi_timeout)
        self._logger.info("NIOS is back up at %s", ip)

        bridge = self._find_forwarding_bridge()
        if bridge:
            rc, _, err = self._transport.run_on_host(
                f"sudo ip route replace {ip}/32 dev {bridge}", check=False
            )
            if rc == 0:
                self._logger.info("Route: %s/32 → %s", ip, bridge)
            else:
                self._logger.warning("Could not add host route: %s", err.strip())
        else:
            self._logger.warning("No forwarding bridge found — host route not added")

        return ip
