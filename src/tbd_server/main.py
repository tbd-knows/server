import os

from .user.api import create_app
from .user.auth import Auth
from .user.store import Store

app = create_app(
    Store(os.environ["DATABASE_URL"]),
    Auth(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"], os.environ["JOIN_SITE_URL"]),
    os.environ["PROVISIONING_TOKEN"],
    os.environ["INTERNAL_SERVICE_TOKEN"],
    [origin.strip() for origin in os.environ["ALLOWED_WEB_ORIGINS"].split(",") if origin.strip()],
)
