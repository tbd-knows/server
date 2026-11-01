"""Separate SDK clients for administration and session validation/recovery."""

from uuid import UUID

from .models import normalize_email
from .store import Problem


class Auth:
    def __init__(self, url, secret, join_site_url):
        from supabase import ClientOptions, create_client

        options = ClientOptions(auto_refresh_token=False, persist_session=False)
        self.admin = create_client(url, secret, options=options).auth.admin
        self.url, self.secret, self.join_site_url = url, secret, join_site_url

    def session_client(self):
        from supabase import ClientOptions, create_client

        return create_client(
            self.url,
            self.secret,
            options=ClientOptions(
                auto_refresh_token=False,
                persist_session=False,
            ),
        ).auth

    def verified_identity(self, token):
        from supabase_auth.errors import AuthApiError

        try:
            user = self.session_client().get_user(token).user
        except AuthApiError as exc:
            if exc.status in (400, 401, 403, 404):
                raise Problem(401, "Invalid or expired session") from None
            raise Problem(503, "Auth unavailable; retry") from None
        except Exception:
            raise Problem(503, "Auth unavailable; retry") from None
        if not user or not user.email or not user.email_confirmed_at:
            raise Problem(403, "Confirmed email required")
        return UUID(user.id), normalize_email(user.email)

    def find_email(self, email):
        page = 1
        while True:
            users = self.admin.list_users(page=page, per_page=100)
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
            # Always reconcile first: an earlier admin response may have been lost.
            user = self.find_email(entry["email"])
            if user:
                store.bind_auth(invitation_id, UUID(user.id))
            if user and user.email_confirmed_at:
                self.session_client().sign_in_with_otp(
                    {
                        "email": entry["email"],
                        "options": {"should_create_user": False, "email_redirect_to": self.join_site_url + "/join"},
                    }
                )
            else:
                response = self.admin.invite_user_by_email(
                    entry["email"],
                    options={
                        "redirect_to": self.join_site_url + "/join",
                    },
                )
                store.bind_auth(invitation_id, UUID(response.user.id))
        except Problem:
            raise
        except Exception:
            # Keep the approved entry, and reconcile on the next manual resend.
            raise Problem(503, "Delivery uncertain; founder can resend this invitation ID") from None
        return {"id": invitation_id, "outcome": "sent"}
