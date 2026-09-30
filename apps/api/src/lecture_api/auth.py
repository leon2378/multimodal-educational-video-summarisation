"""Who is asking (docs/adr/0009-clerk-sign-in-and-quotas.md).

With AUTH_ISSUER set, a request may carry the issuer's session token (Clerk's; any OIDC issuer's
JWT works) as `Authorization: Bearer <token>`. Its signature is checked against the keys the
issuer publishes, with its expiry, issuer, audience (for issuers that set one) and the origin it
was issued for (Clerk's `azp`). Its subject names the user, who gets a `users` row on their first
request. A request without a token is anonymous; one with a bad token is refused, so a user whose
session has lapsed is told to sign in again rather than quietly shown less.

Without an issuer, every request is the one local user, who can see and change everything and has
no quotas: development, the tests and the eval gate.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from lecture_api.deps import SessionDep, SettingsDep
from lecture_core.models import User
from lecture_core.settings import Settings


@dataclass(frozen=True)
class Viewer:
    """The person behind a request."""

    user: User | None = None  # signed in through the issuer
    admin: bool = False
    local: bool = False  # sign-in is off: the one local user

    @property
    def signed_in(self) -> bool:
        return self.user is not None or self.local

    @property
    def user_id(self) -> uuid.UUID | None:
        return self.user.id if self.user is not None else None

    @property
    def limited(self) -> bool:
        """Quotas apply to signed-in users who aren't admins."""
        return self.user is not None and not self.admin


ANONYMOUS = Viewer()
LOCAL = Viewer(admin=True, local=True)


class SigningKeys(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class TokenVerifier:
    """Checks the issuer's session tokens. Its signing keys are fetched once and cached."""

    def __init__(self, settings: Settings, keys: SigningKeys | None = None) -> None:
        if settings.auth_issuer is None:
            raise ValueError("no AUTH_ISSUER to check tokens against")
        self.issuer = settings.auth_issuer
        self.audience = settings.auth_audience
        self.parties = set(settings.auth_authorized_parties)
        jwks_url = settings.auth_jwks_url or (
            settings.auth_issuer.rstrip("/") + "/.well-known/jwks.json"
        )
        self._keys = keys or jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)

    def claims(self, token: str) -> dict[str, Any]:
        """The token's claims; raises jwt.PyJWTError when it isn't valid. Blocking: it fetches
        the issuer's keys when it meets one it hasn't seen."""
        key = self._keys.get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=self.issuer,
            audience=self.audience,
            options={"require": ["exp", "iat", "sub"], "verify_aud": self.audience is not None},
            leeway=5,
        )
        if self.parties and claims.get("azp") not in self.parties:
            raise jwt.InvalidTokenError("the token was issued for another origin")
        return claims


def get_verifier(request: Request) -> TokenVerifier | None:
    verifier: TokenVerifier | None = request.app.state.verifier
    return verifier


async def get_viewer(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    verifier: Annotated[TokenVerifier | None, Depends(get_verifier)],
) -> Viewer:
    if verifier is None:
        return LOCAL
    header = request.headers.get("authorization")
    if header is None:
        return ANONYMOUS
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorised("Send the session token as `Authorization: Bearer <token>`.")
    try:
        claims = await run_in_threadpool(verifier.claims, token.strip())
    except jwt.PyJWKClientConnectionError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in can't be checked right now."
        ) from error
    except jwt.PyJWTError as error:
        raise _unauthorised("The session token isn't valid. Sign in again.") from error
    # Email and name are there when the issuer's session token carries them (a Clerk setting).
    added = insert(User).values(
        subject=claims["sub"], email=claims.get("email"), name=claims.get("name")
    )
    upsert = added.on_conflict_do_update(
        index_elements=[User.subject],
        set_={
            "last_seen_at": func.now(),
            "email": func.coalesce(added.excluded.email, User.email),
            "name": func.coalesce(added.excluded.name, User.name),
        },
    )
    user = await session.scalar(
        upsert.returning(User), execution_options={"populate_existing": True}
    )
    await session.commit()
    if user is None:
        raise RuntimeError("the user upsert returned no row")
    return Viewer(user=user, admin=claims["sub"] in settings.admin_users)


def require_signed_in(viewer: Annotated[Viewer, Depends(get_viewer)]) -> Viewer:
    if not viewer.signed_in:
        raise _unauthorised("Sign in to do this.")
    return viewer


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


ViewerDep = Annotated[Viewer, Depends(get_viewer)]
SignedInDep = Annotated[Viewer, Depends(require_signed_in)]
