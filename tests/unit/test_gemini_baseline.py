"""The Gemini baseline against a fake client: upload, reuse, generation, conversion, output."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from google import genai
from google.genai import errors, types

from lecture_evals.baselines.gemini import GeminiNotes, Options, main, run_baseline
from lecture_evals.pricing import Price

# Every JSON Schema keyword the Gemini API accepts in response_json_schema (SDK 2.25 docs).
SUPPORTED_SCHEMA_KEYWORDS = {
    "$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum",
    "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf", "oneOf",
    "properties", "additionalProperties", "required", "propertyOrdering",
}  # fmt: skip

MODEL_OUTPUT = {
    "tldr": "Dynamic programming solves problems by reusing answers to overlapping subproblems.",
    "chapters": [
        {"title": "Recap", "start": "00:00", "end": "05:00", "summary": "Recursion recap."},
        {"title": "Memoisation", "start": "05:00", "end": "10:00", "summary": "Caching."},
    ],
    "concepts": [
        {"term": "Memoisation", "definition": "Caching results.", "timestamp": "[06:10]"},
    ],
    "formulas": [
        {"latex": "F_n = F_{n-1} + F_{n-2}", "meaning": "Fibonacci.", "timestamp": "12:30"},
    ],
    "quiz": [
        {"question": "Why memoise?", "answer": "To reuse work.", "timestamp": "about 7 min"},
    ],
}


class FakeFiles:
    def __init__(self) -> None:
        self.uploads = 0
        self.files: dict[str, types.File] = {}

    def upload(self, *, file: Path, config: types.UploadFileConfig) -> types.File:
        self.uploads += 1
        name = f"files/{self.uploads}"
        uploaded = types.File(
            name=name,
            display_name=config.display_name,
            mime_type="video/mp4",
            uri=f"https://files.example/{name}",
            state=types.FileState.PROCESSING,
        )
        self.files[name] = uploaded.model_copy(
            update={"state": types.FileState.ACTIVE, "video_metadata": {"videoDuration": "600s"}}
        )
        return uploaded

    def get(self, *, name: str) -> types.File:
        if name not in self.files:
            raise errors.ClientError(404, {"error": {"message": "File not found"}})
        return self.files[name]


class FakeModels:
    def __init__(self, output: dict[str, Any]) -> None:
        self.text = json.dumps(output)
        self.calls: list[dict[str, Any]] = []

    def generate_content_stream(
        self, *, model: str, contents: list[Any], config: types.GenerateContentConfig
    ) -> Iterator[types.GenerateContentResponse]:
        self.calls.append({"model": model, "contents": contents, "config": config})
        half = len(self.text) // 2
        yield _chunk(self.text[:half])
        yield _chunk(
            self.text[half:],
            finish_reason=types.FinishReason.STOP,
            usage=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=60_000,
                prompt_tokens_details=[
                    types.ModalityTokenCount(
                        modality=types.MediaModality.VIDEO, token_count=40_000
                    ),
                    types.ModalityTokenCount(
                        modality=types.MediaModality.AUDIO, token_count=19_200
                    ),
                    types.ModalityTokenCount(modality=types.MediaModality.TEXT, token_count=800),
                ],
                candidates_token_count=3_000,
                thoughts_token_count=1_000,
            ),
        )


def _chunk(
    text: str,
    finish_reason: types.FinishReason | None = None,
    usage: types.GenerateContentResponseUsageMetadata | None = None,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=finish_reason,
            )
        ],
        usage_metadata=usage,
        model_version="gemini-3.8-flash-fake",
    )


@pytest.fixture
def fake() -> SimpleNamespace:
    return SimpleNamespace(files=FakeFiles(), models=FakeModels(MODEL_OUTPUT))


@pytest.fixture
def options(tmp_path: Path) -> Options:
    video = tmp_path / "lecture-15.mp4"
    video.write_bytes(b"not really a video")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Write study notes.")
    return Options(
        video=video,
        title="Dynamic programming",
        prompt=prompt,
        out_root=tmp_path / "out",
        price_override=Price(input=0.75, output=3.75),
        poll_s=0,
    )


def test_schema_uses_only_keywords_gemini_supports() -> None:
    def keywords(schema: Any) -> set[str]:
        if isinstance(schema, list):
            return set().union(*(keywords(s) for s in schema))
        if not isinstance(schema, dict):
            return set()
        found = set(schema)
        for key, value in schema.items():
            if key in {"properties", "$defs"}:
                found |= keywords(list(value.values()))
            elif key in {"items", "anyOf", "oneOf", "additionalProperties"}:
                found |= keywords(value)
        return found

    assert keywords(GeminiNotes.model_json_schema()) <= SUPPORTED_SCHEMA_KEYWORDS


def test_run_writes_notes_usage_cost_and_checks(fake: SimpleNamespace, options: Options) -> None:
    run_dir, result = run_baseline(cast(genai.Client, fake), options)

    assert {p.name for p in run_dir.iterdir()} == {"result.json", "notes.md", "response.json"}
    saved = json.loads((run_dir / "result.json").read_text())
    assert saved["run"]["video_sha256"] == result.run.video_sha256

    run = result.run
    assert run.duration_s == 600  # from Gemini's file metadata; ffprobe can't read fake bytes
    assert run.usage.prompt_by_modality == {"VIDEO": 40_000, "AUDIO": 19_200, "TEXT": 800}
    assert run.cost_usd == pytest.approx((60_000 * 0.75 + 4_000 * 3.75) / 1_000_000)
    assert run.finish_reason == "STOP"

    notes = result.notes
    assert [c.start_s for c in notes.chapters] == [0, 300]
    assert notes.concepts[0].at_s == 370
    assert notes.quiz == []  # its timestamp was unreadable
    assert result.dropped == ["quiz[0]: unreadable timestamp 'about 7 min'"]
    assert result.checks.out_of_range == 1  # the formula at 12:30 in a 10-minute video
    assert result.checks.chapter_coverage == pytest.approx(1.0)
    assert "[06:10]" in (run_dir / "notes.md").read_text()


def test_request_sends_video_first_with_static_processing(
    fake: SimpleNamespace, options: Options
) -> None:
    run_baseline(cast(genai.Client, fake), options)

    call = fake.models.calls[0]
    video, prompt = call["contents"]
    assert video.media_processing == types.MediaProcessing.STATIC
    assert video.video_metadata.fps == 1.0
    assert prompt.endswith("Lecture title: Dynamic programming\nVideo length: 10:00")
    assert call["config"].response_json_schema == GeminiNotes.model_json_schema()
    assert call["config"].media_resolution == types.MediaResolution.MEDIA_RESOLUTION_LOW


def test_agentic_processing_sends_no_frame_rate(fake: SimpleNamespace, options: Options) -> None:
    run_baseline(cast(genai.Client, fake), replace(options, processing="agentic"))

    video = fake.models.calls[0]["contents"][0]
    assert video.media_processing == types.MediaProcessing.AGENTIC
    assert video.video_metadata is None


def test_second_run_reuses_the_upload(fake: SimpleNamespace, options: Options) -> None:
    run_baseline(cast(genai.Client, fake), options)
    _, second = run_baseline(cast(genai.Client, fake), options)

    assert fake.files.uploads == 1
    assert second.run.file_reused


def test_expired_upload_is_replaced(fake: SimpleNamespace, options: Options) -> None:
    run_baseline(cast(genai.Client, fake), options)
    fake.files.files.clear()  # Gemini deleted it after 48 hours

    _, second = run_baseline(cast(genai.Client, fake), options)

    assert fake.files.uploads == 2
    assert not second.run.file_reused


def test_truncated_response_fails_but_keeps_raw_output(
    fake: SimpleNamespace, options: Options
) -> None:
    fake.models.text = fake.models.text[:50]
    with pytest.raises(RuntimeError, match="doesn't match the schema"):
        run_baseline(cast(genai.Client, fake), options)

    (raw,) = options.out_root.glob("*/*/response.json")
    assert raw.read_text() == fake.models.text


def test_cli_needs_an_api_key(
    options: Options, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(options.video.parent)  # away from any real .env
    assert main([str(options.video)]) == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_real_sdk_accepts_the_request(fake: SimpleNamespace, options: Options) -> None:
    """Runs the real SDK's generation path against a mock HTTP transport. This catches the SDK
    rejecting or renaming a field for the Gemini API after an upgrade."""
    requests: list[dict[str, Any]] = []
    reply = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": json.dumps(MODEL_OUTPUT)}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 60_000, "candidatesTokenCount": 3_000},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = f"data: {json.dumps(reply)}\r\n\r\n".encode()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    sdk = genai.Client(
        api_key="test-key",
        http_options=types.HttpOptions(
            httpx_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    client = SimpleNamespace(files=fake.files, models=sdk.models)

    _, result = run_baseline(cast(genai.Client, client), options)

    (request,) = requests
    video_part = request["contents"][0]["parts"][0]
    assert video_part["mediaProcessing"] == "STATIC"
    assert video_part["videoMetadata"] == {"fps": 1.0}
    assert request["generationConfig"]["responseMimeType"] == "application/json"
    assert request["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert len(result.notes.chapters) == 2
