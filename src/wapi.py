from __future__ import annotations

import json
import os
import shlex
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from typing import TYPE_CHECKING, Any

from .constants import NIOS_CONSOLE_PASSWORD, NIOS_CONSOLE_USER, WAPI_VERSION
from .log import create_logger

if TYPE_CHECKING:
    from .transport import _BaseTransport
    from .log import NiosLogger

log = create_logger(name=__name__)

_STATUS_MARKER = "HTTP_STATUS:"
_WRITE_OUT = f"\\n{_STATUS_MARKER}%{{http_code}}"


class WapiError(Exception):
    def __init__(self, method: str, path: str, status: int, body: str):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"WAPI {method} {path} failed (HTTP {status}): {body}")


class WapiClient:
    """Thin wrapper around curl-based WAPI calls over a transport.

    All requests run inside the NIOS container (curl to localhost),
    so the transport must target a container with WAPI running.
    """

    def __init__(
        self,
        transport: _BaseTransport,
        version: str = WAPI_VERSION,
        user: str = NIOS_CONSOLE_USER,
        password: str = NIOS_CONSOLE_PASSWORD,
        logger: NiosLogger | None = None,
    ):
        self._transport = transport
        self._version = version
        self._user = user
        self._password = password
        self._logger = logger or log
        self._base_url = f"https://localhost/wapi/{version}"

    def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        params: str = "",
        check: bool = True,
    ) -> tuple[int, Any]:
        url = f"{self._base_url}/{path}"
        if params:
            url = f"{url}?{params}"

        cmd = f'curl -sk -w "{_WRITE_OUT}" -u {self._user}:{self._password}'
        if method != "GET":
            cmd += f" -X {method}"
        if data is not None:
            cmd += f" -H 'Content-Type: application/json' -d '{json.dumps(data)}'"
        cmd += f" '{url}'"

        _, out, _ = self._transport.run(cmd, check=False)
        body, _, status_str = out.rpartition(_STATUS_MARKER)
        body = body.strip()
        status = int(status_str.strip()) if status_str.strip().isdigit() else 0

        parsed = json.loads(body) if body else None

        if check and (status < 200 or status >= 300):
            raise WapiError(method, path, status, body)
        if check and isinstance(parsed, dict) and "Error" in parsed:
            raise WapiError(method, path, status, body)

        return status, parsed

    def get(self, path: str, params: str = "", check: bool = True) -> Any:
        _, parsed = self._request("GET", path, params=params, check=check)
        return parsed

    def post(self, path: str, data: dict | None = None, check: bool = True) -> Any:
        _, parsed = self._request("POST", path, data=data, check=check)
        return parsed

    def put(self, path: str, data: dict | None = None, check: bool = True) -> Any:
        _, parsed = self._request("PUT", path, data=data, check=check)
        return parsed

    def delete(self, path: str, check: bool = True) -> Any:
        _, parsed = self._request("DELETE", path, check=check)
        return parsed

    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        params: str = "",
        check: bool = True,
    ) -> tuple[int, Any]:
        return self._request(method, path, data=data, params=params, check=check)

    def upload_file(self, local_path: str) -> str:
        """Upload a local file via fileop uploadinit and return its token.

        The file is copied into the container, then curl'd multipart to the
        init URL (host rewritten to localhost, matching _base_url).

        Raises:
            WapiError: If uploadinit or the upload POST fails.
        """
        init = self.post("fileop?_function=uploadinit")
        token, url = init["token"], init["url"]
        parts = urlsplit(url)
        url = urlunsplit(parts._replace(netloc="localhost"))

        name = os.path.basename(local_path)
        remote = f"/tmp/dominion_upload_{uuid.uuid4().hex}"
        try:
            self._transport.upload(local_path, remote)
            cmd = (
                f'curl -sk -w "{_WRITE_OUT}" -u {self._user}:{self._password}'
                f" -F {shlex.quote(f'import_file=@{remote};filename={name}')} {shlex.quote(url)}"
            )
            _, out, _ = self._transport.run(cmd, check=False)
        finally:
            self._transport.run(f"rm -f {shlex.quote(remote)}", check=False)
        body, _, status_str = out.rpartition(_STATUS_MARKER)
        status = int(status_str.strip()) if status_str.strip().isdigit() else 0
        if status < 200 or status >= 300:
            raise WapiError("POST", urlsplit(url).path, status, body.strip())
        return token

    def wait_until_ready(self, timeout: int = 300, interval: int = 10) -> None:
        """Poll WAPI until it responds to a grid request.

        Args:
            timeout: Maximum seconds to wait before raising.
            interval: Seconds between poll attempts.

        Raises:
            RuntimeError: If WAPI does not respond within timeout.
        """
        for i in range(timeout // interval):
            try:
                if self.get("grid"):
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError(f"WAPI not ready after {timeout}s")

    def __repr__(self) -> str:
        return f"WapiClient(version={self._version!r})"
