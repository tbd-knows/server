"""Founder commands print IDs/outcomes only, never input or credentials."""

import argparse
import json
import os
import sys

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["invite", "resend", "seed-business"])
    parser.add_argument("file", help='JSON file, or - for stdin; resend expects {"id":"UUID"}')
    args = parser.parse_args()
    try:
        if args.file == "-":
            data = json.load(sys.stdin)
        else:
            with open(args.file) as source:
                data = json.load(source)
        if args.command == "resend":
            from uuid import UUID

            path = f"/internal/invitations/{UUID(data['id'])}/send"
            data = None
        else:
            path = "/internal/invitations" if args.command == "invite" else "/internal/users/seed-business"
        response = httpx.post(
            os.environ["TBD_API_URL"].rstrip("/") + path,
            json=data,
            headers={"Authorization": "Bearer " + os.environ["PROVISIONING_TOKEN"]},
            timeout=30,
        )
        result = response.json()
        print(json.dumps({"status": response.status_code, "id": result.get("id"), "outcome": result.get("outcome")}))
        return 0 if response.is_success else 1
    except Exception:
        print("Request failed or outcome uncertain; reconcile before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
