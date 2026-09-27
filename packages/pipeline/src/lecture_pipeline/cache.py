"""Content-addressed stage cache (docs/adr/0001-stage-cache.md).

A stage's output is stored under a key derived from everything that determines it: its
inputs, the stage name and version, the model, and the params. Change any of them and the
key changes, so only the affected stages re-run. Changing the summary prompt re-runs
summarisation and indexing, not ASR.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, JsonValue


@dataclass(frozen=True)
class StageSpec:
    """Identifies a stage run. Bump `version` whenever a code change can change the output,
    including a change to the output schema."""

    name: str
    version: str
    model: str | None = None
    params: Mapping[str, JsonValue] = field(default_factory=dict)


def cache_key(spec: StageSpec, inputs: Mapping[str, str]) -> str:
    """`inputs` maps each input's role (e.g. "audio") to a content hash or an upstream cache key."""
    payload = {
        "stage": spec.name,
        "version": spec.version,
        "model": spec.model,
        "params": dict(spec.params),
        "inputs": dict(inputs),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def artifact_path(stage: str, key: str) -> str:
    return f"artifacts/{stage}/{key}.json"


class ArtifactStore(Protocol):
    def get_bytes(self, key: str) -> bytes | None: ...

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...


class ArtifactEnvelope(BaseModel):
    """What gets stored: the output plus enough provenance to trace how it was produced."""

    key: str
    stage: str
    version: str
    model: str | None
    params: dict[str, JsonValue]
    inputs: dict[str, str]
    created_at: datetime
    output: JsonValue


@dataclass(frozen=True)
class StageResult[T: BaseModel]:
    key: str
    output: T
    cached: bool


class StageCache:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def get[T: BaseModel](
        self, spec: StageSpec, inputs: Mapping[str, str], output_type: type[T]
    ) -> StageResult[T] | None:
        return self._read(spec, cache_key(spec, inputs), output_type)

    def run[T: BaseModel](
        self,
        spec: StageSpec,
        inputs: Mapping[str, str],
        output_type: type[T],
        compute: Callable[[], T],
    ) -> StageResult[T]:
        """Return the cached output, or compute and store it.

        Two workers racing on the same key both compute and write the same artifact.
        That wastes work but is safe, since the output depends only on the key.
        """
        key = cache_key(spec, inputs)
        if (hit := self._read(spec, key, output_type)) is not None:
            return hit
        output = compute()
        envelope = ArtifactEnvelope(
            key=key,
            stage=spec.name,
            version=spec.version,
            model=spec.model,
            params=dict(spec.params),
            inputs=dict(inputs),
            created_at=datetime.now(UTC),
            output=output.model_dump(mode="json"),
        )
        self._store.put_bytes(
            artifact_path(spec.name, key), envelope.model_dump_json().encode(), "application/json"
        )
        return StageResult(key=key, output=output, cached=False)

    def _read[T: BaseModel](
        self, spec: StageSpec, key: str, output_type: type[T]
    ) -> StageResult[T] | None:
        raw = self._store.get_bytes(artifact_path(spec.name, key))
        if raw is None:
            return None
        envelope = ArtifactEnvelope.model_validate_json(raw)
        # A validation error here usually means the output schema changed without a version bump.
        return StageResult(key=key, output=output_type.model_validate(envelope.output), cached=True)
