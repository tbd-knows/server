"""Temporary signed provider evidence receiver; never part of the hosted app.

Run with COMMS_CAPTURE_DIR and COMMS_CAPTURE_SECRET_FILE pointing outside Git.
The secret file contains the webhook secret only, supplied after registration.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tbd_server.messaging.adapter import signed_event
from tbd_server.user.store import Problem

app = FastAPI(docs_url=None, openapi_url=None)


@app.post("/webhooks/comms")
async def capture(request: Request):
    secret_file = Path(os.environ["COMMS_CAPTURE_SECRET_FILE"])
    if not secret_file.exists():
        return JSONResponse({"received": False}, status_code=503)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 512 * 1024:
            return JSONResponse({"received": False}, status_code=413)
    try:
        event = signed_event(bytes(raw), request.headers.get("x-osis-signature", ""), secret_file.read_text().strip())
    except Problem as error:
        return JSONResponse({"received": False}, status_code=error.status)
    root = Path(os.environ["COMMS_CAPTURE_DIR"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Keep exact verified bytes private. Public fixtures must be sanitized separately.
    target = root / (str(uuid4()) + ".private.json")
    with target.open("xb") as output:
        os.chmod(target, 0o600)
        output.write(bytes(raw))
    print(
        json.dumps({"capture": "signature_verified", "event": event.get("event"), "capture_id": target.stem}),
        flush=True,
    )
    return {"received": True}
