"""Explicit selected-User welcome command. Uses backend SQL; never sends HTTP."""

import argparse
import json
import os
from uuid import UUID

from tbd_server.user.store import Store
from .core import pilot_welcomes


def main():
    parser = argparse.ArgumentParser(description="Ensure one welcome for selected joined Users")
    parser.add_argument("user_ids", type=UUID, nargs="+")
    args = parser.parse_args()
    store = Store(os.environ["DATABASE_URL"])
    store.verify_connection()
    with store.transaction() as cur:
        rows = pilot_welcomes(cur, args.user_ids, os.environ["TEXTING_BLUE_PHONE_NUMBER"])
    print(json.dumps({"welcome_message_ids": [str(row["id"]) for row in rows]}))


if __name__ == "__main__":
    main()
