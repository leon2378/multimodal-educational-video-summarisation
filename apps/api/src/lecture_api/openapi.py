"""Print the API's OpenAPI schema. The web app generates its typed client from it:

uv run python -m lecture_api.openapi > apps/web/openapi.json   (or: make openapi)
"""

import json

from lecture_api.main import create_app
from lecture_core.settings import Settings


def schema() -> str:
    return json.dumps(create_app(Settings()).openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(schema(), end="")
