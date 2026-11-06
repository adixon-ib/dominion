"""IpcTicketTest: TicketTest defaults for IPc (token) tickets.

The box state is the usage mode, home is a factory reset, and ipc:bundle calls
and bundle uploads are one call each.
"""

from __future__ import annotations

from dominion.actions.appliance_reset_all import reset_all
from dominion.actions.token_enable import (
    BUNDLE_OBJECT,
    enable_token_mode_wapi,
    upload_bundle_token,
)
from dominion.src.constants import (
    TOKEN_BUNDLE_APPLY_TIMEOUT,
    TOKEN_BUNDLE_WAPI_VERSION,
)
from dominion.src.ticket_test import TicketTest

LICENSES, IPC = "Licenses", "IPc"


class IpcTicketTest(TicketTest):
    home_state = LICENSES
    deploy_groups = {"pyabs"}  # noqa: RUF012
    initial_state = {"tokens": []}  # noqa: RUF012
    bundle_path: str | None = None  # token_bundle.tar for upload_bundle_token

    def __init__(self, host, **kw):
        kw.setdefault("wapi_version", TOKEN_BUNDLE_WAPI_VERSION)
        super().__init__(host, **kw)

    def probe(self):
        return self.app.usage_mode()

    def home(self):
        reset_all(self.app)

    def _bundle_path(self, bundle: str | None) -> str:
        path = bundle or self.bundle_path
        if not path:
            raise ValueError("no bundle: pass one or set bundle_path")
        return path

    def _keep_token(self, token: str) -> None:
        vars(self.state).setdefault("tokens", []).append(token)

    def upload_bundle_token(self, bundle: str | None = None) -> str:
        """Fileop upload of the bundle patched with this grid's lpc_uid. The token
        is also appended to self.state.tokens (created if missing), for the
        oplog_contract secrets."""
        token = upload_bundle_token(self.app, self._bundle_path(bundle))
        self._keep_token(token)
        return token

    def enable_ipc(self, bundle: str | None = None) -> dict:
        """enable_token_mode_wapi with the bundle (default bundle_path): upload,
        enable, poll until done. The upload token is appended to
        self.state.tokens, only when the enable succeeds: if it raises, the token
        is not kept. Returns the action's result dict."""
        result = enable_token_mode_wapi(self.app, self._bundle_path(bundle))
        self._keep_token(result["token"])
        return result

    def bundle(self, func: str, data: dict, timeout: int | None = None):
        """POST ipc:bundle?_function=func: (status, body), no raise on HTTP
        errors. apply gets TOKEN_BUNDLE_APPLY_TIMEOUT unless timeout is given."""
        if timeout is None and func == "apply":
            timeout = TOKEN_BUNDLE_APPLY_TIMEOUT
        return self.app.wapi.request(
            "POST", BUNDLE_OBJECT, data=data, params=f"_function={func}",
            check=False, timeout=timeout,
        )
