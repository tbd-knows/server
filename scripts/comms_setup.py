"""Register/test the temporary capture endpoint using a setup-only scoped key.

Use uv run --env-file /private/path/comms.env python scripts/comms_setup.py ...
Never put keys in arguments. Never run this from automated tests.
"""

import argparse
import json
import os
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("register", "test"))
    parser.add_argument("url", help="Controlled HTTPS capture URL")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        parser.error("HTTPS required")
    root = Path(os.environ["COMMS_CAPTURE_DIR"]).resolve()
    repo = Path(__file__).resolve().parents[1]
    if root == repo or repo in root.parents:
        parser.error("Capture evidence and secrets must be outside the repository")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with httpx.Client(
        base_url="https://osis.co/api/v1/comms/",
        follow_redirects=False,
        timeout=10,
        headers={"Authorization": "Bearer " + os.environ["COMMS_SETUP_KEY"]},
    ) as client:
        if args.operation == "register":
            existing = client.get("webhooks")
            existing.raise_for_status()
            # Avoid creating duplicate registrations after an uncertain response.
            hooks = existing.json()["webhooks"]
            metadata_file = root / "registration.private.json"
            if metadata_file.exists():
                previous = json.loads(metadata_file.read_text())
                if previous["url"] != args.url and any(
                    h.get("id") == previous["id"] and h.get("active", True) for h in hooks
                ):
                    raise SystemExit("Disable the old capture webhook in Comms before replacing its URL")
            webhook = next((h for h in hooks if h.get("url") == args.url), None)
            if webhook is None:
                response = client.post(
                    "webhooks", json={"url": args.url, "events": ["comms.message.received", "comms.ping"]}
                )
                response.raise_for_status()
                webhook = response.json()["webhook"]
            elif not webhook.get("active", True):
                raise SystemExit("Existing capture webhook is disabled; re-enable it before requesting a ping")
            # Listing returns the signing secret, so an uncertain setup can recover
            # without creating another subscription. Replace the secret atomically.
            import tempfile

            with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as file:
                os.chmod(file.name, 0o600)
                file.write(webhook["secret"])
                temporary = Path(file.name)
            temporary.replace(root / "webhook-secret")
            metadata = {"id": webhook["id"], "url": args.url}
            (root / "registration.private.json").write_text(json.dumps(metadata))
            os.chmod(root / "registration.private.json", 0o600)
            print("Registered; signing secret saved privately")
        else:
            metadata = json.loads((root / "registration.private.json").read_text())
            if metadata["url"] != args.url:
                raise SystemExit("Registration URL mismatch")
            response = client.post("webhooks/" + metadata["id"] + "/test")
            response.raise_for_status()
            print("Signed ping requested; inspect private capture outcome")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, KeyError, ValueError, OSError):
        raise SystemExit("Setup failed or outcome uncertain; reconcile registration privately") from None
