"""Runtime configuration, read from environment variables (and `.env` in development)."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Defaults match the local stack in infra/compose.yaml. Deployments set every value."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://lecture:lecture@localhost:5432/lecture"

    # None means AWS S3 itself; set it for SeaweedFS or Cloudflare R2.
    s3_endpoint_url: str | None = "http://localhost:8333"
    # The host clients use to reach storage, for presigned URLs. The signature covers the host,
    # so when the API runs in Docker this differs from s3_endpoint_url (http://seaweedfs:8333).
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "lecture-dev"
    s3_secret_access_key: SecretStr = SecretStr("lecture-dev-secret")
    s3_bucket: str = "lectures"

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"

    # Browser origins allowed to call the API (the web app). JSON list in the environment.
    cors_origins: list[str] = ["http://localhost:3000"]

    # Search: Qdrant, and Text Embeddings Inference servers for the embedding model and the
    # reranker (infra/compose.yaml).
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "segments"
    embeddings_url: str = "http://localhost:8081"
    # Headers for the embedding server, as JSON: Modal's proxy auth for the one on a GPU in
    # Modal (infra/modal/embeddings.py), {"Modal-Key": "...", "Modal-Secret": "..."}.
    embeddings_headers: dict[str, SecretStr] = {}
    reranker_url: str = "http://localhost:8082"
    # The search mode when a request doesn't name one. "rerank" ranks best but needs the
    # reranker on a GPU (Compose sets it); on a laptop CPU it takes over a minute a query.
    search_mode: Literal["dense", "bm25", "hybrid", "rerank"] = "hybrid"

    # Q&A: how many retrieved segments an answer draws on, and how many earlier exchanges in a
    # thread it sees.
    qa_passages: int = 6
    qa_history_turns: int = 3

    # Observability (lecture_core.telemetry). OpenTelemetry over HTTP, e.g. http://otel-lgtm:4318
    # in Compose; off when unset. Langfuse also gets the LLM calls when both keys are set.
    otel_endpoint: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    upload_url_ttl_s: int = 3600
    # A single presigned PUT tops out at 5 GiB on S3. Multipart uploads arrive with the web app.
    max_upload_bytes: int = 5 * 1024**3
    # Tests only: lets links to this machine through the API's check (lecture_core.links), for
    # the integration tests' web server. The worker checks every connection regardless.
    allow_private_links: bool = False

    # Sign-in (docs/adr/0009). With an issuer set, requests may carry its session token (Clerk's,
    # or any OIDC issuer's JWT) as a bearer token: signed-in users ask questions and upload,
    # within their quotas, and anonymous visitors read and search the public lectures. Unset,
    # every request is one local user with no limits: development, tests and the eval gate.
    auth_issuer: str | None = None
    # Where the issuer publishes its signing keys; defaults to {issuer}/.well-known/jwks.json.
    auth_jwks_url: str | None = None
    # The token's `aud`, for issuers that set one (Clerk's session tokens don't).
    auth_audience: str | None = None
    # The web app's origins, checked against the token's `azp` (Clerk); empty skips the check.
    auth_authorized_parties: list[str] = []
    # Users (the token's `sub`) who see every lecture, can make lectures public, and have no
    # quotas.
    admin_users: list[str] = []

    # Quotas for each signed-in user, per UTC day, and a ceiling on everyone's LLM spend: past
    # it, questions and processing wait for the next day.
    quota_questions_per_day: int = 30
    quota_questions_per_minute: int = 5
    quota_uploads_per_day: int = 3
    quota_upload_bytes: int = 1024**3
    daily_llm_budget_usd: float = 2.0
