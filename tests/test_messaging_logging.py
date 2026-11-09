"""Import the real app under Uvicorn logging, without any live DB/provider I/O."""

import json
import os
import subprocess
import sys


def test_startup_emits_one_structured_messaging_event_to_stderr():
    script = """
import logging
import uvicorn
from tbd_server.user.store import Store
Store.verify_connection = lambda self: {"role": "fake-restricted-role"}
config = uvicorn.Config("tbd_server.main:app", host="0.0.0.0", port=8000, workers=1, access_log=False)
root = logging.getLogger()
root_before = (root.level, tuple(root.handlers))
config.load()
from tbd_server.messaging.runtime import configure_logging, emit
configure_logging()  # Reinitialization must not duplicate output.
assert (root.level, tuple(root.handlers)) == root_before
assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)
emit("logging_smoke", request_id="fake-request", state="pending")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": "src",
            "DATABASE_URL": "postgresql://fake@localhost/fake",
            "SUPABASE_URL": "https://auth.example.test",
            "SUPABASE_SECRET_KEY": "fake-auth-key",
            "JOIN_SITE_URL": "https://join.example.test",
            "PROVISIONING_TOKEN": "fake-provision",
            "INTERNAL_SERVICE_TOKEN": "fake-internal",
            "ALLOWED_WEB_ORIGINS": "https://join.example.test",
            "COMMS_API_KEY": "fake-runtime-key",
            "COMMS_WEBHOOK_SECRET": "fake-webhook-secret",
            "COMMS_LINE_E164": "+12125550100",
            "MESSAGING_ACTIVATED_AT": "2026-11-08T00:00:00Z",
        },
    )
    expected = {"event": "logging_smoke", "request_id": "fake-request", "state": "pending"}
    assert [json.loads(line) for line in result.stderr.splitlines() if '"event"' in line] == [expected]
    assert '"logging_smoke"' not in result.stdout
    assert "fake-runtime-key" not in result.stderr and "+12125550100" not in result.stderr
