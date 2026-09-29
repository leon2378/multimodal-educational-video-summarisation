"""The app's own metrics (blueprint section 6: stage durations, cache hits, tokens and cost, Q&A
time to first token, feedback). Recorded through OpenTelemetry's global meter, so they cost
nothing until lecture_core.telemetry installs a provider, and go wherever it sends them. In
Prometheus the dots become underscores, with the unit and _total for counters appended:
lecture.stage.duration is lecture_stage_duration_seconds.
"""

from opentelemetry import metrics

_meter = metrics.get_meter("lecture-summariser")

# Histogram buckets. The SDK's default ones end at 10,000: too coarse for stages in seconds, too
# short for answers that can take 20 s.
_SECONDS = [0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100, 250, 500, 1000, 2500]
_MILLISECONDS = [250, 500, 1000, 1500, 2000, 3000, 4000, 5000, 7500, 10_000, 15_000, 20_000, 30_000]

stage_duration = _meter.create_histogram(
    "lecture.stage.duration",
    unit="s",
    description="Time a pipeline stage took, by stage and whether the cache answered",
    explicit_bucket_boundaries_advisory=_SECONDS,
)
stage_runs = _meter.create_counter(
    "lecture.stage.runs", description="Pipeline stages run, by stage and whether the cache answered"
)
llm_tokens = _meter.create_counter(
    "lecture.llm.tokens",
    unit="{token}",
    description="LLM tokens, by model, purpose (a pipeline stage, or qa) and direction",
)
llm_cost = _meter.create_counter(
    "lecture.llm.cost.usd",
    unit="{USD}",
    description="What the LLM tokens cost at paid-tier prices, by model and purpose",
)
qa_first_token = _meter.create_histogram(
    "lecture.qa.first_token",
    unit="ms",
    description="From receiving a question to the first words of its answer",
    explicit_bucket_boundaries_advisory=_MILLISECONDS,
)
qa_duration = _meter.create_histogram(
    "lecture.qa.duration",
    unit="ms",
    description="From receiving a question to the saved answer",
    explicit_bucket_boundaries_advisory=_MILLISECONDS,
)
qa_answers = _meter.create_counter(
    "lecture.qa.answers", description="Answers, by scope (lecture or course) and outcome"
)
feedback = _meter.create_counter("lecture.feedback", description="Answer ratings, by rating")
