# Results

What the measurements say, with the comparisons behind each choice: the pipeline against a
single Gemini call, three ways of reading slides, a trained frame detector against a rule, and
every self-hosted model run each way it could run. Most of it is on MIT 6.0001 Lecture 10 (51
minutes), the lecture with eval datasets; Lectures 11 and 12 are processed too, and the frame
detector uses Lectures 1 to 12. How the eval suites work is in the [README](../README.md#evals).

The latest release (v0.6.9), as the CI eval gate scored it: answers correct 1.00 and faithful
1.00, citations inside the passages 1.00 and near the answer 0.97, and 3 of 3 questions the
lecture doesn't cover declined; slide text word F1 0.86; hybrid search Recall@5 0.97; 9 of 10
checkable concepts cited within 10 s; a word error rate of 2.8%. The sections below are the
measurements made as each part was built, and what they decided.

## Study notes: the pipeline against one Gemini call

Scored the same way for both producers.

| | Gemini, one call | Pipeline |
|---|---|---|
| Notes | 8 chapters, 8 concepts, 3 formulas, 9 quiz | 5 chapters, 14 concepts, 7 formulas, 9 quiz |
| Concept citations within 10 s of where the captions say the term | 3 of 4, median 2.2 s | 9 of 10, median 0.5 s |
| Concepts whose term is never said as written (slide titles), so can't be checked | 4 | 4 |
| API cost | $0.21 (`gemini-3.8-flash`) | about $0.04 (`gemini-3.5-flash-lite`, paid-tier prices) |
| Time | 135 s | 119 s through the API and workers (speech and slides in parallel), 15 s when only the notes change |
| Speech recognition | | WER 3.0% against the human captions, 99.1% of the technical terms; 65 to 74 s on an RTX 3060 Laptop (6 GB), about 45× real time, 2.3 GB VRAM peak |

Scored by `lecture-eval --suites notes,asr`. Caveats: one lecture, two different Gemini models,
and a citation measure that can only check terms said as written.

### The Gemini baseline

`gemini-baseline` sends a whole lecture video to Gemini in one call and asks for the study notes
the pipeline will produce: a TL;DR, chapters, key concepts, formulas and a quiz, each with a
timestamp. It sets the bar the pipeline has to beat, and answers "why not just use Gemini?" with
numbers.

1. Get an API key at https://aistudio.google.com/apikey and add `GEMINI_API_KEY=...` to `.env`.
2. Put lecture videos in `data/lectures/` (git ignores `data/`). On the free tier Google may use
   what you send to improve its products, so stick to public, openly licensed lectures.
3. Optionally install ffmpeg (`sudo apt install -y ffmpeg`) so the script can read the video's
   length itself. Otherwise it uses the length Gemini reports.
4. Run it:

```bash
uv run gemini-baseline data/lectures/lecture-15.mp4 --title "Dynamic programming"
```

Each run writes a folder under `data/baselines/<video>/`:

- `notes.md`: the notes, for reading.
- `result.json`: the notes in the shared format (`lecture_core.notes.StudyNotes`, times in
  seconds), plus the model, prompt and video hashes, tokens by modality, cost, timings, and
  checks: timestamps outside the video, chapter gaps and overlaps, and how much of the video the
  chapters cover.
- `response.json`: the model's raw output.

| Option | Default | Notes |
|---|---|---|
| `--model` | `gemini-3.8-flash` | Prices for common models are built in ([pricing.py](../packages/llm/src/lecture_llm/pricing.py)). For others, pass `--input-price` and `--output-price` (USD per million tokens). |
| `--processing` | `static` | `static` puts every frame in context. `agentic` lets the model choose where to look, which uses fewer tokens. Worth running both. |
| `--fps` | `1` | Frames per second, `static` only. Slides change slowly, so try `0.5`. |
| `--media-resolution` | `low` | About 100 tokens per second of video. `high` is about 300 and reads small text better. |
| `--prompt` | `prompts/baseline-gemini/v1.md` | To try a change, copy it to `v2.md`. Each run records which prompt it used. |

At low resolution a one-hour lecture is roughly 360k input tokens, so a run on
`gemini-3.8-flash` costs about $0.30. That model's prices double on 1 January 2027. Uploads are
reused for 48 hours, so repeat runs on the same video skip the upload.

## Answers

The 36 golden questions asked through the API (`make eval`), answered by
`gemini-3.5-flash-lite` and graded by the same model. The judge isn't calibrated yet; checking
9 of the answers by hand agreed with it.

| Correct | Faithful to the passages | Citations inside the passages | Citing near the answer | Declined when not covered | First token, p50 / p95 |
|---|---|---|---|---|---|
| 0.98 | 1.00 | 0.99 | 0.97 | 3 of 3 | 2.1 s / 8.1 s |

The one answer marked partly right: asked how many operations the summing loop takes, it gave
the 3 per iteration but not the total, 3x + 2. First-token times vary with Gemini's free tier:
the p95 was 19 s on an earlier run. An answer costs about $0.0015 at paid-tier prices: around
4,500 input tokens, mostly the six passages, and 70 output tokens on average.

## Search

The 33 golden questions against the lecture's 47 segments (`make eval-retrieval`). A hit counts
when its segment overlaps the answer by at least 3 seconds. Latency is through the API, with the
reranker on the GPU.

| Mode | Recall@5 | MRR@10 | nDCG@10 | Median latency |
|---|---|---|---|---|
| dense | 0.97 | 0.84 | 0.82 | 0.4 s |
| BM25 | 0.91 | 0.76 | 0.74 | 8 ms |
| hybrid (RRF) | 0.97 | 0.84 | 0.84 | 0.4 s |
| hybrid + rerank | **1.00** | **0.89** | **0.86** | 0.8 s |

The reranker is the only step that clearly helps: it puts the answer first for 26 questions,
against 24 for hybrid. On the CPU it took 77 seconds a query, which is why it runs on the GPU.
With a single lecture there's little for hybrid to beat dense on (one question is 0.03 of
Recall@5); more lectures, and course-wide search, should separate them. Embedding a lecture
took 5 minutes on the CPU, so a fresh run took about 6 minutes, up from 2. Since Phase 5c the
embedding model runs on the GPU when there is one: Lecture 10 embeds in 1.4 s, and each search
that embeds the question is about 0.4 s faster than above ([speed](#speed)). More in
[ADR 0005](adr/0005-qdrant-for-hybrid-search.md).

## Slide reading

The tables above were measured with the vision LLM reading every slide. Since Phase 5a, OCR
reads every slide and only the slides OCR can't handle go to the vision LLM (`SLIDE_READER`).
Lecture 10 read all three ways, everything else the same; slide text is scored against the
lecture's slide PDF by word overlap (`lecture-eval --suites slides`), and the notes and answers
are regenerated from each reading:

| | Vision LLM, every slide | Routed (default) | OCR only |
|---|---|---|---|
| Slides the vision LLM reads | 24 | 11 | 0 |
| Slide text against the PDF, word F1 | 0.80 | 0.84 | 0.82 |
| Slides without a title | 9 | 0 | 0 |
| Formulas in the notes | 7 | 8 | 3 |
| Search with reranking, Recall@5 / MRR@10 | 1.00 / 0.89 | 1.00 / 0.92 | 1.00 / 0.94 |
| Answers correct / faithful / citing near the answer | 0.98 / 1.00 / 0.97 | 1.00 / 1.00 / 1.00 | 0.95 / 0.97 / 0.97 |
| Reading slides: LLM tokens in / out | 26,729 / 3,551 | 12,471 / 2,236 | none |
| Reading slides: cost at paid-tier prices | $0.017 | $0.009 | $0 |
| All of the lecture's LLM calls | $0.041 | $0.035 | $0.024 |

OCR (RapidOCR's PP-OCRv6 models on the CPU, 12 s for the 24 slides) reads plain text as well
as the vision LLM, tables better, and never skips a title: the vision LLM left 9 empty, a whole
batch of 8 among them. What OCR can't do is describe a plot or a diagram, write LaTeX, or keep
the lecturer's annotations apart from the slide text. With OCR alone the notes kept 3 of the
formulas, and one answer went wrong: asked for the three ways of measuring efficiency, it missed
"order of growth", which OCR had read but between the lines of an annotation written across the
slide. Routing sends slides with figures, angled text or doubtful OCR to the vision LLM, which
keeps those, and halves the cost of reading slides. The differences in search and answers are
within what one lecture and one judge can separate. The vision LLM's missing titles came back in
the CI eval gate's first rehearsal (a batch of 8 of the routed slides), so a routed slide read
without a title now keeps OCR's.

Later a table on the demo came out as one jumbled column: OCR reads a slide a line at a time,
across everything on it, so a table or code with notes beside it comes out interleaved. Slides
with text side by side on 3 or more rows now go to the vision LLM as well: 8 more slides in the
MIT lectures (in Lecture 10, 14 of 24 are routed), 5 of them code with notes beside it, and every
other slide OCR read had no such rows. With the vision LLM's prompt also asking for tables row by
row, Lecture 10's slide text scored word F1 0.87 against the PDF, and 0.86 in the CI gate.

## Frame detector

RF-DETR Nano ([ADR 0007](adr/0007-rf-detr-for-the-frame-detector.md)), fine-tuned on
frames from ten MIT 6.0001 lectures and scored on two it never saw, one from each half of the
course: Lectures 6 and 12. No box was drawn by hand (`make detector-data`,
`make detector-train`): each slide frame is matched to its page of the lecture's slide PDF and
aligned to it by OCR'd text lines (median error under 2 pixels); the vision LLM boxes each
page's figures and annotations once (427 boxes on 396 pages), and those boxes carry onto every
frame that shows the page; a COCO-trained RF-DETR finds people. Frames the labeller can't be
sure of (a slide playing a video, a screen of live coding) are left out.

| | Train (Lectures 1-5, 7-11) | Valid (their last fifth) | Test (Lectures 6, 12) |
|---|---|---|---|
| Frames | 1,409 | 371 | 387 |
| Boxes: slide / person / figure / annotation | 639 / 762 / 199 / 314 | 201 / 176 / 66 / 56 | 188 / 204 / 56 / 33 |

On the test lectures, against their automatic labels (mAP50:95 0.71, mAP50 0.81):

| Class | AP50:95 | Precision | Recall |
|---|---|---|---|
| slide | 1.00 | 1.00 | 1.00 |
| person | 0.99 | 1.00 | 0.99 |
| annotation | 0.36 | 0.38 | 0.61 |
| figure | 0.34 | 0.67 | 0.36 |

Routing, deciding which slides go to the vision LLM, on the test lectures' 188 slide frames (65
with a figure or annotation by the labels):

| | Slides routed | Precision | Recall | F1 | On Lecture 12 alone |
|---|---|---|---|---|---|
| The 5a rule: ink outside text, angled lines, OCR confidence | 74 | 0.80 | 0.91 | **0.85** | 0.78 |
| The detector, confidence 0.5 | 54 | 0.87 | 0.72 | 0.79 | 0.68 |
| Either of them | 77 | 0.77 | 0.91 | 0.83 | |

Ten training lectures instead of two lifted the detector's routing F1 on Lecture 12 from 0.59
to 0.68, and figure AP from 0.15 on Lecture 12 to 0.34 on the two test lectures (annotation
AP, 0.50 on Lecture 12's 8 annotations then, is 0.36 on the 33 now). But the rule still routes
better: the detector misses more than a quarter of the slides with a figure or an annotation,
almost half of Lecture 12's, and routing when either says so doesn't beat the rule alone. So
the rule keeps routing slides, and the detector stays out of the pipeline. Finding slides and
people is solved at this scale, and the detector also recognises a slide playing a video, which
the brightness test takes for a camera shot. Every score here is against labels a model made,
not checked by hand, and the LLM's boxes aren't consistent (highlighted code sometimes counts
as a figure).

## Speed

`lecture-bench` (`make bench`) runs the three models the stack hosts itself every way they
could run, on the same laptop (RTX 3060 Laptop GPU with 6 GB, Ryzen 7 5800H), and scores every
variant, so one that gets faster by getting worse shows it. What the numbers decided is
[ADR 0008](adr/0008-where-the-models-run.md); the raw results are saved in `data/bench/`.

**The embedding model** (Qwen3-Embedding-0.6B on Text Embeddings Inference): Lecture 10's 47
chunks (16,439 tokens), then each golden question on its own, searched by the dense vectors
alone.

| | Lecture 10 embedded | Tokens/s | A question, median | Recall@5 / MRR@10 / nDCG@10 | Cosine to the CPU's vectors | VRAM |
|---|---|---|---|---|---|---|
| CPU, fp32 (before) | 218 s | 75 | 437 ms | 0.97 / 0.87 / 0.85 | – | – |
| GPU, fp16 (now) | 1.4 s | 11,560 | 16 ms | 0.97 / 0.87 / 0.85 | 1.0000 | 1.5 GB |

The same vectors, 150 times faster, so Compose now runs the embedding model on the GPU when
there is one. It was the pipeline's slowest stage and the first step of every answer: through
the API, a reranked search now takes 0.43 s instead of 0.86, and a dense or hybrid one 30 to
40 ms instead of 0.43 s.

The benchmark also turned up a bug in TEI's CPU server: a text sent while other requests are in
flight sometimes comes back with the wrong vector. The benchmark's first run found the CPU's
chunk vectors at a mean cosine of 0.96 to the GPU's; rerun on an idle stack, 1.0000. Bursts of
15 overlapping requests (24 texts) went wrong in 3 of 12 on the CPU, with vectors at cosine
0.13 to 0.16 to the right ones, and in none of 60 on the GPU. Every vector in the index was
checked and is right, but on the CPU a question asked while a lecture is being embedded, or a
lecture embedded while questions are asked, can get wrong vectors.

**Speech recognition** (faster-whisper large-v3-turbo): Lecture 10 at each of CTranslate2's
compute types, scored against the human captions as the ASR eval scores it. The time is
recognition alone, after the model has loaded.

| Compute type | Load | Lecture 10 (51 min) | Real-time factor | VRAM added | WER | Technical terms |
|---|---|---|---|---|---|---|
| GPU, float16 | 14.3 s | 44 s | 0.014 | 3.3 GB | 2.8% | 99.6% |
| GPU, int8_float16 (the pipeline's) | 11.0 s | 40 s | 0.013 | 2.2 GB | 3.0% | 99.1% |
| GPU, int8 | 11.1 s | 42 s | 0.014 | 2.2 GB | 3.0% | 99.1% |
| CPU, int8 (4 threads) | 17.5 s | 938 s | 0.30 | – | 2.9% | 99.1% |

int8 weights take a third less VRAM than float16 and are no slower, for 0.2 points of WER. With
the reranker and the embedding model beside it the card peaks at 5.1 of its 6 GB, so float16,
1.1 GB more, wouldn't fit. Without a GPU a 51-minute lecture takes 16 minutes. In the pipeline
the stage takes 65 to 75 s: it also loads the model, and slide detection and OCR run beside it.

**The frame detector** (RF-DETR Nano at 384 px, [above](#frame-detector), as first trained on
Lectures 10 and 11): each of Lecture 12's 177 test frames through the network on its own, timed
without the preprocessing and decoding every variant shares, and scored against the frames'
automatic labels. Retraining on more lectures changes the weights, not the network, so the
timings stand.

| Runtime | Device | ms a frame, median | mAP50:95 | mAP50 | slide | person | figure | annotation |
|---|---|---|---|---|---|---|---|---|
| PyTorch, fp32 | CPU | 156.6 | 0.649 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| ONNX Runtime, fp32 | CPU | 122.4 | 0.649 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| ONNX Runtime, int8 dynamic | CPU | **68.9** | 0.645 | 0.720 | 0.99 | 0.99 | 0.14 | 0.46 |
| ONNX Runtime, int8 calibrated | CPU | 123.5 | 0.553 | 0.646 | 0.92 | 0.93 | 0.06 | 0.31 |
| PyTorch, fp32 | GPU | 17.6 | 0.649 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| PyTorch, fp16 | GPU | 22.3 | 0.650 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| TensorRT, fp32 | GPU | 6.9 | 0.649 | 0.720 | 1.00 | 0.99 | 0.14 | 0.47 |
| TensorRT, fp16 | GPU | **3.8** | 0.648 | 0.720 | 1.00 | 0.99 | 0.14 | 0.46 |
| TensorRT, int8 calibrated | GPU | 7.5 | 0.569 | 0.644 | 0.94 | 0.95 | 0.11 | 0.28 |

At a frame a second, a 51-minute lecture is about 3,060 frames: 12 s of network time in
TensorRT fp16, 54 s in PyTorch on the GPU, 3.5 minutes on the CPU in int8 and 6 in fp32. So
when the detector joins the pipeline it runs as a TensorRT fp16 engine, 4.6 times faster than
PyTorch at the same accuracy.

- **fp16 doesn't help PyTorch.** One frame at a time, the Nano model is too small to keep the
  GPU busy: PyTorch launches its operations one by one, and the launching takes longer than
  the arithmetic. TensorRT compiles the network into fewer, fused kernels.
- **int8 pays off only on the CPU, and only dynamic.** Dynamic int8 (weights stored in int8,
  each activation quantized as it arrives) is 1.8 times faster than fp32 for 0.004 of mAP.
  Calibrated int8 fixes each activation's scale in advance from training frames, which TensorRT
  requires; it loses 0.08 to 0.10 of mAP in both runtimes, probably because the vision
  transformer's activations have outliers one fixed scale per tensor can't cover, and in
  TensorRT it's slower than fp32: only the convolutions and matrix multiplies are int8, and
  converting in and out of them costs more than it saves. Making the rest of it fp16 as well,
  tried separately, broke its accuracy (mAP 0.10).
- The mAP here comes from the benchmark's own decoding, the same for every variant: 0.649 for
  the model trained on two lectures, against 0.66 from RF-DETR's own test pass on it.
