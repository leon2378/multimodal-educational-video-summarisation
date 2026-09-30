"""The caller: whether they're signed in, and what their quotas still allow today.

GET /v1/me
"""

from fastapi import APIRouter, Request

from lecture_api import quotas
from lecture_api.auth import ViewerDep
from lecture_api.deps import SessionDep, SettingsDep
from lecture_api.schemas import MeOut, UserOut

router = APIRouter(tags=["me"])


@router.get("/me")
async def me(
    request: Request, session: SessionDep, settings: SettingsDep, viewer: ViewerDep
) -> MeOut:
    """With sign-in off (`auth: false`), the caller is the one local user: signed in, an admin,
    no quotas. Otherwise an anonymous caller gets `signed_in: false`."""
    return MeOut(
        auth=request.app.state.verifier is not None,
        signed_in=viewer.signed_in,
        user=UserOut.model_validate(viewer.user) if viewer.user is not None else None,
        admin=viewer.admin,
        quotas=await quotas.usage(session, viewer, settings),
    )
