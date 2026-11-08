import os
import json

from .user.api import create_app
from .user.auth import Auth
from .user.store import Store
from .messaging.service import Messaging

store = Store(os.environ["DATABASE_URL"], messaging_account=os.environ["TEXTING_BLUE_PHONE_NUMBER"])
messaging = Messaging(
    store,
    os.environ["TEXTING_BLUE_API_KEY"],
    os.environ["TEXTING_BLUE_WEBHOOK_SECRET"],
    os.environ["TEXTING_BLUE_PHONE_NUMBER"],
    os.environ["MESSAGING_ACTIVATED_AT"],
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
