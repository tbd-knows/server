"""User HTTP contract. Credentials and resolved context are never tool arguments."""

from hmac import compare_digest
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import AddressDelete, AddressWrite, DefaultWrite, Invitation, ProfilePatch, Resolve, Seed
from .store import Problem


def create_app(store, auth, provisioning_token: str, internal_token: str, origins: list[str]):
    if not provisioning_token or not internal_token or provisioning_token == internal_token:
        raise ValueError("Distinct nonempty provisioning/internal credentials are required")
    app = FastAPI(
        title="TBD User service",
        version="0.1.0",
        responses={
            code: {"description": description}
            for code, description in {
                401: "Missing or invalid credential",
                403: "Operation not authorized",
                404: "Own resource not found",
                409: "Identity or revision conflict",
                422: "Invalid input",
                503: "Infrastructure failure; retry or reconcile",
            }.items()
        },
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["POST"], allow_headers=["Authorization", "Content-Type"]
    )

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.exception_handler(psycopg.OperationalError)
    async def database_handler(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable; retry"})

    def bearer(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer credential required")
        return authorization[7:]

    def provisioning(token=Depends(bearer)):
        if not compare_digest(token, provisioning_token):
            raise HTTPException(401, "Invalid provisioning credential")

    def internal(token=Depends(bearer)):
        if not compare_digest(token, internal_token):
            raise HTTPException(401, "Invalid internal credential")

    def own(user_id: UUID, x_tbd_user_id: Annotated[str | None, Header()] = None, caller=Depends(internal)):
        if x_tbd_user_id != str(user_id):
            raise HTTPException(403, "Resolved User context required")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/internal/invitations", dependencies=[Depends(provisioning)])
    def invite(data: Invitation):
        entry = store.approve(data)
        try:
            return auth.send(store, entry["id"])
        except Problem as exc:
            if exc.status == 503:
                return JSONResponse(
                    status_code=503, content={"id": str(entry["id"]), "outcome": "uncertain", "detail": exc.detail}
                )
            raise

    @app.post("/internal/invitations/{invitation_id}/send", dependencies=[Depends(provisioning)])
    def resend(invitation_id: UUID):
        return auth.send(store, invitation_id)

    @app.post("/v1/join")
    def join(token=Depends(bearer)):
        identity = auth.verified_identity(token)
        return store.join(*identity)

    @app.post("/internal/users/seed-business", dependencies=[Depends(provisioning)])
    def seed(data: Seed):
        return store.seed(data)

    @app.post("/internal/users/resolve", dependencies=[Depends(internal)])
    def resolve(data: Resolve):
        return store.resolve(data.phone)

    @app.get("/internal/users/{user_id}/profile", dependencies=[Depends(own)])
    def profile(user_id: UUID):
        return store.read(user_id)

    @app.patch("/internal/users/{user_id}/profile", dependencies=[Depends(own)])
    def patch(user_id: UUID, data: ProfilePatch):
        return store.patch(user_id, data)

    @app.put("/internal/users/{user_id}/addresses/{address_id}", dependencies=[Depends(own)])
    def save(user_id: UUID, address_id: UUID, data: AddressWrite):
        return store.save_address(user_id, address_id, data)

    @app.delete("/internal/users/{user_id}/addresses/{address_id}", dependencies=[Depends(own)])
    def delete(user_id: UUID, address_id: UUID, data: AddressDelete):
        return store.delete_address(user_id, address_id, data)

    @app.put("/internal/users/{user_id}/default-address", dependencies=[Depends(own)])
    def default(user_id: UUID, data: DefaultWrite):
        return store.set_default(user_id, data)

    return app
