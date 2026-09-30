# 0009: Clerk for sign-in, public demo lectures, and quotas counted in Postgres

- Status: Accepted
- Date: 2026-09-30
- Code: `apps/api/src/lecture_api/auth.py`, `access.py`, `quotas.py`

## Context

Phase 6 puts the app online (blueprint sections 9 and 12): "an OIDC provider issues a JWT that
FastAPI verifies. Each user has quotas and rate limits on processing and Q&A", and "the public
demo shows pre-processed OCW lectures with live Q&A. Uploads sit behind auth and quotas". Until
now anyone who could reach the API could upload, process and ask, and every question and
processed lecture costs LLM money: the Gemini key is on the paid tier since the eval gate ran
out of the free one.

## Options

- **Sign-in**: a hosted provider (Clerk, Auth0), or one run in Compose (Keycloak), each issuing
  JWTs the API checks against the provider's published keys; or GitHub sign-in through the web
  app (Auth.js), whose sessions aren't tokens the API can check on its own. Clerk has the
  quickest Next.js integration (ready-made sign-in pages) and a free tier ample for a demo;
  Auth0 takes more setup; Keycloak is a heavy service to run locally and in the cloud.
- **Counting quotas**: a counter store (Redis, not in the stack), or counting rows Postgres
  already has: each user's questions (`qa_messages`) and lectures, and each answer's and
  processing run's LLM cost.

## Decision

- **Clerk**, checked generically: the API verifies any issuer's RS256 JWTs against
  `{issuer}/.well-known/jwks.json` (signature, expiry, issuer, the `azp` origin Clerk sets, and an
  audience for issuers that use one) with PyJWT, and never holds a Clerk secret. The token's
  `sub` names the user, who gets a `users` row on their first request.
- **Who sees what**: lectures and courses are `public` or `private` and have an owner. Visitors
  without an account read and search the public ones (the demo); asking questions, uploading and
  making courses need sign-in. What a user uploads is private to them; admins (by Clerk user id,
  `ADMIN_USERS`) see everything and make lectures public. Conversations are private to their
  user. Something the caller may not read answers 404.
- **Quotas**, counted in Postgres per UTC day: 30 questions a user (5 a minute), 3 uploads of up
  to 1 GB, and a $2 ceiling on everyone's LLM spend at paid-tier prices, after which questions
  and processing wait for the next day. A limit answers 429 with `Retry-After`. All are
  settings; admins have none.
- **Off without an issuer**: with `AUTH_ISSUER` unset, every request is one local user who sees
  and changes everything, with no quotas, so development, the tests and the eval gate work as
  before.

## Consequences

- The web app signs in with Clerk and sends the session token as a bearer token on every call.
  The progress stream can't use `EventSource` (it can't send headers) and reads the stream with
  `fetch` instead, as the Q&A stream already does. `GET /v1/me` tells it whether sign-in is on,
  who the user is and what their quotas leave.
- Email and name reach the `users` table only if Clerk's session token carries them
  (customised in the Clerk dashboard); the user id is enough to work.
- The limits are soft: two requests at the same moment can both pass a check. A processing run's
  cost is known only when it finishes, and runs whose LLM stages all came from the cache count
  as free. That is enough for a demo; a counter store would make them exact.
- Lectures and courses made before this are public (the demo), and so are ones made with
  sign-in off. Threads from before have no user and only admins see them.
- Tests fake the issuer (a token names its user) for the access rules and quotas, and check real
  RS256 tokens against a locally made key.
