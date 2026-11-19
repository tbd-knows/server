"""Private operator command. Uses the same role and routing config as the server."""

import argparse
import json
import os
from uuid import UUID

from tbd_server.agent.store import transition
from tbd_server.user.store import Problem, Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id", type=UUID)
    parser.add_argument("mode", choices=("agent", "fallback", "fixed"))
    parser.add_argument("--conversation")
    args = parser.parse_args()
    try:
        store = Store(os.environ["DATABASE_URL"])
        store.verify_connection()
        with store.transaction() as cur:
            transition(
                cur,
                args.user_id,
                args.mode,
                os.environ["COMMS_LINE_E164"],
                json.loads(os.environ.get("COMMS_PILOT_CONVERSATIONS", "{}")),
                args.conversation,
            )
    except Problem as exc:
        parser.exit(1, exc.detail + "\n")
    except Exception:
        parser.exit(1, "Transition unavailable or outcome uncertain. Check stored mode before retrying.\n")
    print(json.dumps({"user_id": str(args.user_id), "mode": args.mode}))


if __name__ == "__main__":
    main()
