"""Supabase SDK Auth operations with isolated, bounded HTTP clients."""

from contextlib import contextmanager
from uuid import UUID

import httpx
from supabase import ClientOptions, create_client
from supabase_auth.errors import AuthApiError, AuthError

from .models import normalize_email
from .store import Problem


class Auth:
    def __init__(self, url, secret, join_site_url, *, transport=None):
        self.url, self.secret = url, secret
        self.join_site_url = join_site_url.rstrip("/")
        self.transport = transport

    @contextmanager
    def client(self):
        # Admin state is never shared with a client that verifies/recovers a session.
        with httpx.Client(timeout=10, transport=self.transport) as http:
            yield create_client(
                self.url,
                self.secret,
                options=ClientOptions(
                    auto_refresh_token=False,
                    persist_session=False,
                    httpx_client=http,
                ),
            ).auth

    def verified_identity(self, token):
        try:
            with self.client() as client:
                response = client.get_user(token)
                user = response.user if response else None
        except AuthApiError as exc:
            if exc.status in (400, 401, 403, 404):
                raise Problem(401, "Invalid or expired session") from None
            raise Problem(503, "Auth unavailable; retry") from None
        except (httpx.HTTPError, AuthError, ValueError):
            raise Problem(503, "Auth unavailable; retry") from None
        if not user or not user.email or not user.email_confirmed_at:
            raise Problem(403, "Confirmed email required")
        return UUID(user.id), normalize_email(user.email)

    @staticmethod
    def find_email(admin, email):
        page = 1
        while True:
            users = admin.list_users(page=page, per_page=100)
            for user in users:
                if user.email and normalize_email(user.email) == email:
                    return user
            if len(users) < 100:
                return None
            page += 1

    def send(self, store, invitation_id):
        entry = store.invitation(invitation_id)
        if entry["joined_user_id"]:
            return {"id": invitation_id, "outcome": "already joined"}
        try:
            with self.client() as client:
                # Reconcile an Auth response lost during any previous attempt.
                user = self.find_email(client.admin, entry["email"])
                if user:
                    store.bind_auth(invitation_id, UUID(user.id))
                if user and user.email_confirmed_at:
                    with self.client() as recovery:
                        recovery.sign_in_with_otp(
                            {
                                "email": entry["email"],
                                "options": {
                                    "should_create_user": False,
                                    "email_redirect_to": self.join_site_url + "/join",
                                },
                            }
                        )
                else:
                    response = client.admin.invite_user_by_email(
                        entry["email"],
                        options={
                            "redirect_to": self.join_site_url + "/join",
                        },
                    )
                    if normalize_email(response.user.email) != entry["email"]:
                        raise Problem(503, "Auth response mismatch; reconcile before resend")
                    store.bind_auth(invitation_id, UUID(response.user.id))
        except Problem:
            raise
        except (httpx.HTTPError, AuthError, ValueError):
            raise Problem(503, "Delivery uncertain; founder can resend this invitation ID") from None
        return {"id": invitation_id, "outcome": "sent"}
