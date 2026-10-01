"""The embedding model on a GPU in Modal, for indexing lectures from a host without one
(ADR 0010).

Text Embeddings Inference's image for the L4 (compute capability 8.9) serves the model pinned in
infra/compose.yaml, so the vectors match the CPU server's (ADR 0008) and so does the stage cache
key, which comes from the model id and revision the server reports. Modal's proxy auth guards
it: the worker sends EMBEDDINGS_HEADERS={"Modal-Key": "...", "Modal-Secret": "..."}, from a
proxy auth token made in Modal's dashboard.

    make modal    # deploy this app and infra/modal/asr.py
"""

import subprocess

import modal

# As in infra/compose.yaml.
MODEL = [
    "--model-id",
    "Qwen/Qwen3-Embedding-0.6B",
    "--revision",
    "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
]
PORT = 8000
CACHE = "/data"

image = modal.Image.from_registry(
    "ghcr.io/huggingface/text-embeddings-inference:89-1.9.4", add_python="3.12"
).entrypoint([])

app = modal.App("lecture-embeddings")
# The model download (1.2 GB), kept between containers.
cache = modal.Volume.from_name("lecture-embeddings-cache", create_if_missing=True)


# Indexing sends one batch at a time; the container stays up five minutes after the last.
@app.function(image=image, gpu="L4", volumes={CACHE: cache}, scaledown_window=300)
@modal.concurrent(max_inputs=8)
@modal.web_server(PORT, startup_timeout=10 * 60, requires_proxy_auth=True)
def serve() -> None:
    subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - the image's TEI binary
            "text-embeddings-router",
            *MODEL,
            "--hostname",
            "0.0.0.0",  # noqa: S104 - inside the container, behind Modal's proxy
            "--port",
            str(PORT),
            "--huggingface-hub-cache",
            CACHE,
        ]
    )
