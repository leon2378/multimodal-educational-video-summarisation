"""Checking the issuer's session tokens: signature, expiry, issuer, audience and origin."""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from lecture_api.auth import TokenVerifier
from lecture_core.settings import Settings

ISSUER = "https://clerk.issuer.test"
WEB_APP = "http://localhost:3000"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class PublishedKey:
    """The issuer's JWKS, without fetching it: always the public half of KEY."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        return jwt.PyJWK(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True))


def token(key: rsa.RSAPrivateKey = KEY, **claims: Any) -> str:
    now = int(time.time())
    payload = {"iss": ISSUER, "sub": "user_1", "iat": now, "exp": now + 60, "azp": WEB_APP}
    return jwt.encode(payload | claims, key, algorithm="RS256")


def verifier(**settings: Any) -> TokenVerifier:
    return TokenVerifier(Settings(auth_issuer=ISSUER, **settings), keys=PublishedKey())


def test_a_valid_token_names_its_user() -> None:
    claims = verifier(auth_authorized_parties=[WEB_APP]).claims(token(email="a@example.org"))

    assert (claims["sub"], claims["email"]) == ("user_1", "a@example.org")


@pytest.mark.parametrize(
    ("made", "why"),
    [
        (lambda: token(exp=int(time.time()) - 60), jwt.ExpiredSignatureError),
        (lambda: token(key=OTHER_KEY), jwt.InvalidSignatureError),
        (lambda: token(iss="https://someone.else"), jwt.InvalidIssuerError),
        (lambda: token(azp="https://someone.else"), jwt.InvalidTokenError),
        (lambda: token(sub=None), jwt.InvalidTokenError),
    ],
    ids=["expired", "forged", "another issuer", "another origin", "no subject"],
)
def test_bad_tokens_are_refused(made: Any, why: type[Exception]) -> None:
    with pytest.raises(why):
        verifier(auth_authorized_parties=[WEB_APP]).claims(made())


def test_the_origin_is_checked_only_when_configured() -> None:
    assert verifier().claims(token(azp="https://anywhere.test"))["sub"] == "user_1"


def test_an_audience_is_required_once_configured() -> None:
    checker = verifier(auth_audience="lecture-api")

    assert checker.claims(token(aud="lecture-api"))["sub"] == "user_1"
    with pytest.raises(jwt.InvalidAudienceError):
        checker.claims(token(aud="another-api"))
    with pytest.raises(jwt.MissingRequiredClaimError):
        checker.claims(token())
