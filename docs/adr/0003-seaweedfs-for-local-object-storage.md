# 0003: SeaweedFS as the local S3-compatible store

- Status: Accepted
- Date: 2026-09-27

## Context

Videos and derived media live in object storage, and clients upload directly to it through
presigned URLs. The cloud target is S3 or Cloudflare R2, so local development needs an
S3-compatible server. MinIO's open-source edition, the usual choice, was archived in April 2026.

## Options

- **SeaweedFS** (Apache-2.0): actively maintained; `weed mini` runs the whole thing in one process
  for development and can pre-create buckets.
- **Garage**: lightweight, but AGPL-3.0 and aimed at geo-distributed self-hosting.
- **LocalStack S3**: good emulation, but part of a much larger tool.
- **R2 even in development**: no local server, but needs network access and credentials for every
  test run.

## Decision

Use SeaweedFS 4.47 in `mini` mode, both in `infra/compose.yaml` and in the integration tests
(testcontainers), with a dev-only identity in `infra/seaweedfs/s3.json` and the `lectures` bucket
created at startup. Application code talks to storage only through boto3 in
`lecture_core.storage`, so switching to S3 or R2 is a configuration change.

## Consequences

- The integration tests use the same image as local development, including a real presigned PUT.
- Presigned URLs sign the host name. When the API runs inside Compose, it reaches storage at
  `http://seaweedfs:8333`, but clients need `http://localhost:8333`, hence
  `S3_PUBLIC_ENDPOINT_URL`.
- boto3 1.36+ adds CRC checksums to requests by default. The client sets checksums to
  `when_required`, so behaviour is the same on SeaweedFS, R2 and S3.
- Browser uploads (Phase 2) need CORS on the S3 gateway (`-s3.allowedOrigins`, "*" by default in
  development).
