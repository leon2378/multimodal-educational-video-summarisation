from pathlib import Path

from lecture_api.openapi import schema

WEB_SCHEMA = Path(__file__).resolve().parents[2] / "apps" / "web" / "openapi.json"


def test_the_web_client_schema_is_current() -> None:
    """The web app's typed client is generated from this file. If this fails, run
    `make openapi` and commit both apps/web/openapi.json and the regenerated client."""
    assert WEB_SCHEMA.read_text(encoding="utf-8") == schema()
