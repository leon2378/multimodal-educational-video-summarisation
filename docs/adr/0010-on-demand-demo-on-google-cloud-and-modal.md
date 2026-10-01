# 0010: The demo on an on-demand Google Cloud VM, GPU work on Modal

- Status: Accepted
- Date: 2026-10-01
- Code: `infra/terraform/`, `infra/compose.cloud.yaml`, `infra/modal/`,
  `apps/api/src/lecture_api/demo.py`, `.github/workflows/release.yml` and `deploy.yml`

## Context

Phase 6c puts the app online. The blueprint (section 12) has the web app on Vercel; the API,
worker, Temporal, Postgres and Qdrant on one small VM made by Terraform; GPU stages on Modal;
media in S3 or R2 behind a CDN; and a deploy on each tag. Since then:

- The demo should cost close to nothing while nobody uses it. The write-up (6d) shows
  screenshots, not a live link, so the site doesn't have to stay up.
- It serves the pre-processed MIT lectures to anyone, and signed-in users can upload their own
  within the quotas (ADR 0009).
- The stack needs about 4 to 6 GB of memory. The always-free VMs (Google Cloud's e2-micro,
  Azure's B1s) have 1 GB; Oracle's free ARM VM is big enough, but free capacity is often
  unavailable and every image would need an ARM build.
- There is no domain, so sign-in stays on Clerk's development instance.
- Measured so far (ADRs 0005 and 0008, the eval gate): on a CPU the embedding server took 218 s
  to embed Lecture 10, and returned wrong vectors when requests overlapped; a question embeds in
  about 0.4 s. Speech recognition took 1,189 s on the CI runner's four cores, against about
  40 s on a GPU. The reranker took 77 s for 30 passages on a CPU.

## Options

- **Where the stack runs**: an always-on VM (about $25 to 60 a month on Google Cloud or Azure,
  €7 on Hetzner); Oracle's free ARM VM; the developer's PC through a tunnel (free, with its GPU,
  but only up while the PC is on, and nothing deployed to show); or a VM that Terraform creates
  for a session and destroys afterwards (cents a session).
- **Which cloud**: Google Cloud (the account that already pays for Gemini), Azure, or Hetzner
  (cheapest).
- **Storage**: Cloudflare R2 (10 GB free, downloads free), Amazon S3 (another account, pennies),
  or Google Cloud Storage (the same account, downloads about $0.12/GB).
- **The web app**: Vercel, as planned, or the same VM.
- **GPU stages**: Modal, or the VM's CPU.
- **Search**: hybrid, or reranked with the reranker on Modal.

## Decision

- **A VM on Google Cloud, created on demand**: Terraform makes one e2-standard-2 VM (2 vCPUs,
  8 GB; the machine type and region are variables) for a session and deletes it afterwards. It
  runs the stack with Docker Compose (`infra/compose.cloud.yaml`) from the images CI pushes to
  GHCR. Temporal stays the dev server, and Postgres and Qdrant keep their data on the VM's disk.
- **Two Terraform parts**, with their state in a Cloud Storage bucket: `base`, applied once,
  holds what outlasts a session (the bucket, the VM's service account and secrets, a network
  open only on 80 and 443 and to SSH through Google's IAP proxy, and the access GitHub Actions
  deploys with); `demo` is the VM alone, so deleting it leaves the rest.
- **The web app on the same VM**, behind Caddy at one HTTPS origin: `/v1/*` goes to the API,
  everything else to the web app, which is built with an empty API URL so it calls its own
  origin. One URL per session, no CORS, and no Vercel site pointing at a server that is usually
  off. With no domain, the hostname is the VM's IP under sslip.io (wildcard DNS that needs no
  setup), which is enough for Caddy to get a Let's Encrypt certificate; `AUTH_AUTHORIZED_PARTIES`
  follows it.
- **Media and the stage cache in Google Cloud Storage**, through its S3-compatible API with an
  HMAC key, so the presigned uploads and playback work unchanged: the same account and bill as
  the VM and Gemini, and transfers to the VM in its region are free. The bucket outlives the VM.
- **Secrets in Secret Manager**: one secret with the storage settings, written by Terraform,
  and one with the app's own (Gemini, Clerk, Modal), stored from `infra/cloud.env`. The VM's
  startup script writes them into the stack's `.env`; nothing secret is in the VM's metadata.
- **The demo lectures come from the stage cache**: `lecture-demo export` writes the local
  stack's public, processed lectures (their videos and a manifest) and the stage cache, which
  are copied to the bucket once. On a VM's first boot, `lecture-demo load` makes the lectures
  through the API, the bucket copying each video into place, and processes them; every stage
  comes from the cache, as in the eval gate: no GPU time and no Gemini calls. The API runs with
  sign-in off for this step, so the lectures are public (ADR 0009), then restarts with it on.
- **GPU work on Modal**:
  - Speech recognition is a Modal function on an L4: faster-whisper large-v3-turbo at
    int8_float16, the model kept in a Modal Volume. It sits behind the `Transcriber` interface
    as a `ModalTranscriber` chosen by a setting (`TRANSCRIBER=modal`). The worker sends it the
    audio (16 kHz FLAC, about 40 MB for an hour) and the settings, and gets progress back
    while it runs, so the activity's heartbeats go on, then the transcript. Modal needs no
    access to storage. The function runs the local GPU worker's code with its model and
    settings, and the cache key holds the compute type but not the device, so transcripts
    made locally are cache hits in the cloud.
  - Indexing embeddings run on the embedding server's GPU image as a Modal web endpoint, behind
    Modal's proxy auth. The vectors are the same as on the CPU (cosine 1.0000, ADR 0008).
  - Questions embed on the VM's CPU embedding server, one call at a time, so its
    overlapping-requests bug can't occur.
  - The VM's one worker serves the cpu, llm and gpu queues, as in the eval gate.
- **Hybrid search, without the reranker**: on Lecture 10, Recall@5 was 0.97 against the
  reranker's 1.00, without a GPU call on every question.
- **Deploying**: a version tag builds the images, scans them with Grype (high or critical
  vulnerabilities with a fix fail it) and pushes them to GHCR. `make deploy` and
  `make destroy` run Terraform locally, and a manually started GitHub Actions workflow does the
  same, signing in to Google Cloud through GitHub's OIDC (Workload Identity Federation), so no
  service account key exists.

## Consequences

- Cost, from list prices (check the providers' calculators):
  - The VM: about $0.07 an hour, plus its disk and IP, while it exists; nothing once deleted.
  - Storage: about $0.02 a GB a month (the demo set is 0.5 GB), and $0.12 a GB a viewer
    downloads; the state bucket and Secret Manager are pennies or free.
  - Modal: a few cents per uploaded lecture, within its $30 monthly credit.
  - Gemini: capped by the $2 daily budget (ADR 0009).
  - A budget alert on the Google Cloud billing account covers all of it but Modal.
- The demo exists only while deployed. Uploads and conversations go with the VM, and
  `make destroy` deletes the uploaded videos too; dumping Postgres to the bucket before and
  restoring it after would keep them, if that turns out to matter.
- Each deploy gets a new IP and so a new hostname and certificate. A reserved IP would keep one
  hostname, but it costs about $7 a month while the VM is down. That Clerk's development
  instance works on an sslip.io origin is to be confirmed on the first deploy; if not, a domain
  and Clerk's production instance follow.
- A deploy takes about ten minutes before the demo answers: Docker, the images and the
  embedding model download on the VM, then the demo lectures load. An upload waits 30 to 60 s
  for Modal to start when it hasn't run recently. Nothing GPU-related costs anything while idle.
- The bucket's stage cache must match the pipeline. After a stage changes, the demo lectures
  are processed again locally and exported again; otherwise the VM's first boot recomputes
  the changed stages, which costs Gemini and Modal calls.
- The GHCR images must be public for the VM to pull them without a token; they hold nothing
  secret (the Clerk publishable key is public by design).
- This departs from the blueprint in two places: no Vercel, and no permanent demo. The
  scale-out path (Kubernetes, Temporal Cloud, autoscaled GPU workers) stays a separate ADR.
