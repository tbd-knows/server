import os
import json

from .user.api import create_app
from .user.auth import Auth
from .user.store import Store
from .messaging.service import Messaging
from .messaging.runtime import configure_logging

configure_logging()
cards_enabled = os.environ.get("COMMS_CONTACT_CARDS_ENABLED", "false")
if cards_enabled not in ("true", "false"):
    raise ValueError("COMMS_CONTACT_CARDS_ENABLED must be true or false")
store = Store(os.environ["DATABASE_URL"])
messaging = Messaging(
    store,
    os.environ["COMMS_API_KEY"],
    os.environ["COMMS_WEBHOOK_SECRET"],
    os.environ["COMMS_LINE_E164"],
    os.environ["MESSAGING_ACTIVATED_AT"],
    pilot_conversations=json.loads(os.environ.get("COMMS_PILOT_CONVERSATIONS", "{}")),
    cards_enabled=cards_enabled == "true",
)
print(json.dumps({"database_connection_verified": store.verify_connection()}), flush=True)

app = create_app(
    store,
    Auth(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"], os.environ["JOIN_SITE_URL"]),
    os.environ["PROVISIONING_TOKEN"],
    os.environ["INTERNAL_SERVICE_TOKEN"],
    [origin.strip() for origin in os.environ["ALLOWED_WEB_ORIGINS"].split(",") if origin.strip()],
    messaging=messaging,
)
