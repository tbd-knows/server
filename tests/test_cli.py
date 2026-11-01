import json
import sys

import httpx
import pytest

from tbd_server.cli import main


@pytest.mark.parametrize(
    "command,path,payload",
    [
        ("invite", "/internal/invitations", {"name": "Fake Private Input"}),
        (
            "resend",
            "/internal/invitations/11111111-1111-4111-8111-111111111111/send",
            {"id": "11111111-1111-4111-8111-111111111111"},
        ),
        ("seed-business", "/internal/users/seed-business", {"seed_key": "example", "name": "Fake Private Input"}),
    ],
)
def test_cli_commands_do_not_print_private_payloads(command, path, payload, tmp_path, monkeypatch, capsys):
    source = tmp_path / "fake.json"
    source.write_text(json.dumps(payload))
    monkeypatch.setenv("TBD_API_URL", "https://api.example.test")
    monkeypatch.setenv("PROVISIONING_TOKEN", "fake-provisioning-secret")
    monkeypatch.setattr(sys, "argv", ["tbd-user", command, str(source)])

    def post(url, **kwargs):
        assert url == "https://api.example.test" + path
        assert kwargs["headers"]["Authorization"] == "Bearer fake-provisioning-secret"
        assert kwargs["json"] == (None if command == "resend" else payload)
        return httpx.Response(
            200, json={"id": "11111111-1111-4111-8111-111111111111"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)
    assert main() == 0
    output = capsys.readouterr().out
    assert "Fake Private Input" not in output and "fake-provisioning-secret" not in output
    assert json.loads(output)["outcome"] == "completed"


def test_cli_uncertain_delivery_preserves_id(tmp_path, monkeypatch, capsys):
    source = tmp_path / "fake.json"
    source.write_text("{}")
    monkeypatch.setattr(sys, "argv", ["tbd-user", "invite", str(source)])
    monkeypatch.setenv("TBD_API_URL", "https://api.example.test")
    monkeypatch.setenv("PROVISIONING_TOKEN", "fake-secret")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            503,
            json={"id": "entry-id", "outcome": "uncertain", "detail": "must-not-echo"},
            request=httpx.Request("POST", "https://api.example.test"),
        ),
    )
    assert main() == 1
    output = capsys.readouterr().out
    assert json.loads(output)["id"] == "entry-id" and "must-not-echo" not in output
