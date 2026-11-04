"""Unit tests for WapiClient: curl command construction and response parsing."""

import json
import re

import pytest

from dominion import TransportError, WapiClient, WapiError
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

    def test_non_json_error_body_raises_wapi_error_with_excerpt(self):
        html = "<html>" + "x" * 500 + "</html>"
        t = FakeTransport(stdout=f"{html}\n{_STATUS_MARKER}502")
        client = WapiClient(transport=t)

        with pytest.raises(WapiError) as exc_info:
            client.get("grid")
        assert exc_info.value.status == 502
        assert exc_info.value.body.startswith("<html>")
        assert len(exc_info.value.body) == 200

    def test_non_json_error_body_check_false_returns_none(self):
        t = FakeTransport(stdout=f"<html>bad gateway</html>\n{_STATUS_MARKER}502")
        client = WapiClient(transport=t)

        assert client.request("GET", "grid", check=False) == (502, None)

    def test_non_json_success_body_still_raises_decode_error(self):
        t = FakeTransport(stdout=f"not json\n{_STATUS_MARKER}200")
        client = WapiClient(transport=t)

        with pytest.raises(json.JSONDecodeError):
            client.get("grid")

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


class TestUploadFile:

    def _client(self, upload_status=200):
        class T(FakeTransport):
            def __init__(self):
                super().__init__()
                self.uploads = []

            def upload(self, local, remote):
                self.uploads.append((local, remote))

            def run(self, cmd, check=True, quiet=False):
                self.calls.append(cmd)
                if "uploadinit" in cmd:
                    body = {"token": "tok1", "url": "https://10.1.1.1/http_direct_file_io/req1/import_file"}
                    return 0, _make_response(body), ""
                return 0, f"\n{_STATUS_MARKER}{upload_status}", ""

        t = T()
        return t, WapiClient(transport=t)

    def test_upload_file(self, tmp_path):
        f = tmp_path / "bundle.tar"
        f.write_bytes(b"x")
        t, client = self._client()

        assert client.upload_file(str(f)) == "tok1"

        assert "-X POST" in t.calls[0] and "fileop?_function=uploadinit" in t.calls[0]
        remote = t.uploads[0][1]
        assert t.uploads == [(str(f), remote)]
        assert re.fullmatch(r"/tmp/dominion_upload_[0-9a-f]{32}", remote)
        cmd = t.calls[1]
        assert f"-F 'import_file=@{remote};filename=bundle.tar'" in cmd
        assert t.calls[2] == f"rm -f {remote}"
        assert "https://localhost/http_direct_file_io/req1/import_file" in cmd
        assert "10.1.1.1" not in cmd

    def test_upload_failure_raises(self, tmp_path):
        f = tmp_path / "bundle.tar"
        f.write_bytes(b"x")
        t, client = self._client(upload_status=500)
        with pytest.raises(WapiError):
            client.upload_file(str(f))
        assert t.calls[-1].startswith("rm -f /tmp/dominion_upload_")

    def test_upload_error_still_cleans_up(self, tmp_path):
        f = tmp_path / "bundle.tar"
        f.write_bytes(b"x")
        t, client = self._client()

        def boom(local, remote):
            raise RuntimeError("upload failed")

        t.upload = boom
        with pytest.raises(RuntimeError):
            client.upload_file(str(f))
        assert t.calls[-1].startswith("rm -f /tmp/dominion_upload_")


class TestOnCall:

    def _client(self, stdout, records, **kw):
        client = WapiClient(transport=FakeTransport(stdout=stdout), **kw)
        client.on_call = records.append
        return client

    def test_record_has_path_query_status_and_full_body(self):
        records = []
        c = self._client(_make_response([{"a": 1}]), records, version="v2.15")
        c.get("ipc:overage", params="_return_fields=a")
        assert records == [{
            "kind": "wapi", "method": "GET",
            "path": "/wapi/v2.15/ipc:overage?_return_fields=a",
            "body": None, "status": 200, "response": '[{"a": 1}]',
        }]

    def test_request_body_recorded_and_no_credentials(self):
        records = []
        c = self._client(_make_response({}, 201), records, password="s3cret")
        c.post("record:a", {"name": "x"})
        assert records[0]["body"] == {"name": "x"}
        assert "s3cret" not in str(records)
        assert "admin" not in str(records)

    def test_recorded_before_wapi_error_with_untruncated_body(self):
        records = []
        html = "<html>" + "x" * 500
        c = self._client(f"{html}\n{_STATUS_MARKER}502", records)
        with pytest.raises(WapiError) as exc:
            c.get("grid")
        assert len(exc.value.body) == 200
        assert records[0]["status"] == 502
        assert records[0]["response"] == html

    def test_recorded_before_json_decode_error(self):
        records = []
        c = self._client(f"not json\n{_STATUS_MARKER}200", records)
        with pytest.raises(json.JSONDecodeError):
            c.get("grid")
        assert records[0]["response"] == "not json"

    def test_no_hook_changes_nothing(self):
        c = WapiClient(transport=FakeTransport(stdout=_make_response([1])))
        assert c.get("grid") == [1]

    def test_body_is_a_snapshot_not_the_callers_dict(self):
        records = []
        c = self._client(_make_response({}, 201), records)
        body = {"n": 1}
        c.post("record:a", body)
        body["n"] = 2
        c.post("record:a", body)
        assert [r["body"] for r in records] == [{"n": 1}, {"n": 2}]

    def test_hook_is_per_client_and_class_default_stays_none(self):
        records = []
        a = self._client(_make_response([1]), records)
        b = WapiClient(transport=FakeTransport(stdout=_make_response([1])))
        a.get("grid")
        b.get("grid")
        assert len(records) == 1
        assert b.on_call is None
        assert WapiClient.on_call is None

    def test_raising_hook_does_not_replace_result(self):
        c = WapiClient(transport=FakeTransport(stdout=_make_response([1])))

        def bad(record):
            raise ValueError("hook bug")

        c.on_call = bad
        assert c.get("grid") == [1]

    def test_hook_error_does_not_replace_wapi_error(self):
        c = WapiClient(transport=FakeTransport(stdout=f"x\n{_STATUS_MARKER}500"))
        c.on_call = lambda r: 1 / 0
        with pytest.raises(WapiError):
            c.get("grid")

    def test_transport_error_recorded_then_raised(self):
        records = []
        c = self._client("", records)

        def boom(cmd, check=True, quiet=False):
            raise TransportError("ssh timed out")

        c._transport.run = boom
        with pytest.raises(TransportError):
            c.get("grid")
        assert records[0]["status"] == 0
        assert records[0]["response"] == "ssh timed out"
        assert records[0]["path"] == "/wapi/v2.12/grid"
