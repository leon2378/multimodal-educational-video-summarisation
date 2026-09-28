"""Runtime configuration, read from environment variables (and `.env` in development)."""

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

    upload_url_ttl_s: int = 3600
    # A single presigned PUT tops out at 5 GiB on S3. Multipart uploads arrive with the web app.
    max_upload_bytes: int = 5 * 1024**3
