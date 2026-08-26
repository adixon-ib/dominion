"""Unit tests for WapiClient: curl command construction and response parsing."""

import json

import pytest

from dominion import WapiClient, WapiError
from dominion.src.wapi import _STATUS_MARKER


class FakeTransport:
    def __init__(self, stdout="", returncode=0):
        self.calls = []
        self._stdout = stdout
        self._returncode = returncode

    def run(self, cmd, check=True, quiet=False):
        self.calls.append(cmd)
        return self._returncode, self._stdout, ""


def _make_response(body, status=200):
    return f"{json.dumps(body)}\n{_STATUS_MARKER}{status}"


class TestWapiClient:

    def test_get_builds_correct_curl(self):
        t = FakeTransport(stdout=_make_response([{"_ref": "grid/b25lLm"}]))
        client = WapiClient(transport=t)

        result = client.get("grid")

        assert len(t.calls) == 1
        cmd = t.calls[0]
        assert "curl -sk" in cmd
        assert "-u admin:infoblox" in cmd
        assert "https://localhost/wapi/v2.12/grid" in cmd
        assert result == [{"_ref": "grid/b25lLm"}]

    def test_get_with_params(self):
        t = FakeTransport(stdout=_make_response([]))
        client = WapiClient(transport=t)

        client.get("record:a", params="_return_fields=name,ipv4addr")

        assert "?_return_fields=name,ipv4addr" in t.calls[0]

    def test_post_sends_json_data(self):
        t = FakeTransport(stdout=_make_response("record:a/new"))
        client = WapiClient(transport=t)

        result = client.post("record:a", data={"name": "test.example.com", "ipv4addr": "1.2.3.4"})

        cmd = t.calls[0]
        assert "-X POST" in cmd
        assert "Content-Type: application/json" in cmd
        assert "test.example.com" in cmd
        assert result == "record:a/new"

    def test_put_sends_json_data(self):
        t = FakeTransport(stdout=_make_response("record:a/updated"))
        client = WapiClient(transport=t)

        result = client.put("record:a/ZG5z", data={"ipv4addr": "5.6.7.8"})

        assert "-X PUT" in t.calls[0]
        assert result == "record:a/updated"

    def test_delete(self):
        t = FakeTransport(stdout=_make_response("record:a/deleted"))
        client = WapiClient(transport=t)

        result = client.delete("record:a/ZG5z")

        assert "-X DELETE" in t.calls[0]
        assert result == "record:a/deleted"

    def test_get_does_not_include_dash_x(self):
        t = FakeTransport(stdout=_make_response([]))
        client = WapiClient(transport=t)

        client.get("grid")

        assert "-X " not in t.calls[0]

    def test_raises_wapi_error_on_non_2xx(self):
        t = FakeTransport(stdout=_make_response({"Error": "not found"}, status=404))
        client = WapiClient(transport=t)

        with pytest.raises(WapiError) as exc_info:
            client.get("nonexistent:object")
        assert exc_info.value.status == 404
        assert exc_info.value.method == "GET"

    def test_raises_wapi_error_on_error_payload(self):
        t = FakeTransport(stdout=_make_response({"Error": "AdmConDataError", "text": "bad"}, status=200))
        client = WapiClient(transport=t)

        with pytest.raises(WapiError):
            client.get("grid")

    def test_check_false_returns_error_without_raising(self):
        t = FakeTransport(stdout=_make_response({"Error": "not found"}, status=404))
        client = WapiClient(transport=t)

        result = client.get("grid", check=False)

        assert isinstance(result, dict)
        assert "Error" in result

    def test_custom_version(self):
        t = FakeTransport(stdout=_make_response([]))
        client = WapiClient(transport=t, version="v2.15")

        client.get("grid")

        assert "wapi/v2.15" in t.calls[0]

    def test_custom_credentials(self):
        t = FakeTransport(stdout=_make_response([]))
        client = WapiClient(transport=t, user="testuser", password="testpass")

        client.get("grid")

        assert "-u testuser:testpass" in t.calls[0]

    def test_request_returns_status_and_parsed(self):
        t = FakeTransport(stdout=_make_response({"result": "ok"}, status=201))
        client = WapiClient(transport=t)

        status, parsed = client.request("POST", "record:a", data={"name": "test"})

        assert status == 201
        assert parsed == {"result": "ok"}

    def test_empty_body_returns_none(self):
        t = FakeTransport(stdout=f"\n{_STATUS_MARKER}204")
        client = WapiClient(transport=t)

        status, parsed = client.request("DELETE", "record:a/ref", check=False)

        assert status == 204
        assert parsed is None

    def test_repr(self):
        t = FakeTransport()
        client = WapiClient(transport=t, version="v2.12")
        assert repr(client) == "WapiClient(version='v2.12')"
