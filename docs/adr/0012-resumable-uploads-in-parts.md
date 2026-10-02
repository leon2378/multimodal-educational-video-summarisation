# 0012: Resumable uploads in parts, each straight to storage

- Status: Accepted
- Date: 2026-10-02
- Code: `lecture_core.storage` (`PartPlan` and the upload methods), `POST /v1/lectures/{id}/upload-parts`
  and `complete-upload`, the bucket's lifecycle rule in `infra/terraform/base/main.tf`

## Context

The blueprint (section 1) has lectures uploaded "resumable, straight to storage", through Uppy
(section 7). Until now an upload was one presigned PUT of up to 5 GiB. When the connection
dropped, it started again from the first byte, as a new lecture, which used up another of the
day's three uploads (ADR 0009). Lecture recordings run to gigabytes, and laptops sleep. The API
never handles video bytes (ADR 0003), and storage is SeaweedFS in development and Cloud Storage
for the demo (ADR 0010), both through their S3 APIs.

## Options

1. **tus**, the resumable upload protocol, with a tusd server: mature clients, but the bytes go
   through a server of ours instead of straight to storage, and it's one more service to run.
2. **Cloud Storage's resumable uploads**, a session URL that takes the file in byte ranges: one
   URL for the whole file, but only on Cloud Storage. SeaweedFS and S3 don't have them.
3. **S3 multipart uploads, with a presigned URL for each part**: the file in parts of a fixed
   size, sent straight to storage in any order, and joined there. S3, R2, Cloud Storage's XML API
   and SeaweedFS all have them.

## Decision

Option 3.

- **`POST /v1/lectures/{id}/upload-parts`**, given the file's size, starts the upload or resumes
  it: it says which parts storage has and returns a presigned URL for each of the others. The
  client asks again after an interruption, or when the URLs expire after an hour.
  `complete-upload` joins the parts once storage has every one, and until then answers 409
  saying how many are missing.
- **Parts of 16 MiB** (`UPLOAD_PART_BYTES`). Storage wants at least 5 MiB for each but the last,
  and allows 10,000, so parts grow for files past 156 GiB. A failed part costs 16 MiB again, not
  the whole file.
- **The API asks storage which parts it has** (ListParts), rather than trusting a list of parts
  and ETags from the client. Resuming after a reload then needs nothing saved in the browser but
  the file, and the browser never reads storage's response headers, so the bucket's CORS doesn't
  have to expose `ETag`.
- **The size is fixed when the upload starts**:
  - A file over the uploader's limit is refused before any of it is sent. `POST /v1/lectures`
    takes the size too, so such a file doesn't use up one of the day's uploads either.
  - Each part's URL is signed for that part's length, so storage refuses a part of any other
    size, and an upload can't grow past what was declared.
- **The single presigned PUT stays**, for clients that don't give a size: scripts, the eval
  gate's preparation and the README's curl walkthrough. The demo loader copies its videos inside
  the bucket and needs neither.
- **Unfinished uploads**: their parts are billed, but they don't show in listings.
  - Deleting the lecture aborts its upload.
  - A lifecycle rule on the Cloud Storage bucket aborts any upload left for 7 days. Resuming one
    after that starts again from the first part.
  - SeaweedFS in development has no such rule, and its volume goes with `make reset`.
- **The web app** can send the parts itself, a few at a time with retries. Uppy's S3 plugin also
  works with these endpoints, but it reads each part's `ETag`, which the bucket's CORS would then
  have to expose.

## Consequences

- An interrupted upload continues from the parts that arrived. That works after a reload too,
  once the same file is chosen again, since browsers don't reopen files on their own. Resuming
  doesn't use up another upload.
- More requests: one per part, plus a few to the API, so 64 part uploads for a 1 GiB file. Cloud
  Storage bills them as Class A operations, a fraction of a cent.
- The lecture keeps the upload's id (`lectures.upload_id`) and the declared size until the parts
  are joined.
- SeaweedFS refuses a part of the wrong length (tested in `tests/integration/test_uploads.py`).
  Cloud Storage checks signed headers the same way; the next demo deploy confirms it there.
- `MAX_UPLOAD_BYTES` is no longer tied to the 5 GiB a single PUT can take, though it stays at
  5 GiB.
