"""The frame detector (RF-DETR Nano, Phase 5b) before and after: PyTorch, ONNX Runtime and TensorRT,
at fp32, fp16 and int8.

Each variant is timed on the network alone (a preprocessed frame in, boxes and logits out) and
scored on the test lecture's frames: COCO mAP against their automatic labels, so a faster format
that loses accuracy shows it. Every variant shares RF-DETR's own preprocessing and decoding
(rfdetr.export._runtime, private in rfdetr 1.11), so they differ only in how the network runs.

- ONNX: RF-DETR's exporter (fp32). fp16 from ONNX Runtime's float16 converter. int8 two ways,
  from ONNX Runtime's quantizers: dynamic (weights stored in int8, activations quantized as they
  arrive; ONNX Runtime only) and calibrated (a fixed int8 scale for each convolution's and
  matrix multiply's inputs, measured on training frames, as QuantizeLinear/DequantizeLinear
  pairs). TensorRT 11 takes the calibrated one: it has no fp16 or int8 switches, and takes
  precision from the model.
- TensorRT: engines built from the fp32, fp16 and calibrated int8 files, run with PyTorch's
  CUDA buffers.
"""

import json
import statistics
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel

RESOLUTION = 384
# (boxes [1, queries, 4] as normalised cxcywh, logits [1, queries, classes + 1])
Runner = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]


class DetectorResult(BaseModel):
    variant: str
    device: str
    ms_p50: float
    ms_mean: float
    map_50_95: float
    map_50: float
    ap_by_class: dict[str, float]


@dataclass(frozen=True)
class Frame:
    image_id: int
    image: Image.Image


def test_frames(split: Path) -> tuple[list[Frame], dict[str, Any]]:
    coco: dict[str, Any] = json.loads((split / "_annotations.coco.json").read_text("utf-8"))
    frames = [
        Frame(image["id"], Image.open(split / image["file_name"]).convert("RGB"))
        for image in coco["images"]
    ]
    return frames, coco


def preprocess(image: Image.Image) -> np.ndarray:
    from rfdetr.export._runtime.preprocess import preprocess_to_nchw

    array: np.ndarray = preprocess_to_nchw(image, RESOLUTION, RESOLUTION, 3)
    return array.astype(np.float32)


# Runners


def torch_runner(checkpoint: Path, device: str, half: bool) -> Runner:
    import torch
    from rfdetr import RFDETRNano

    net = RFDETRNano(pretrain_weights=str(checkpoint)).model.model
    if net is None:
        raise RuntimeError(f"no network in {checkpoint}")
    net.eval()
    net.export()
    net = net.to(device=device, dtype=torch.float16 if half else torch.float32)

    def run(batch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        tensor = torch.from_numpy(batch).to(device=device, dtype=net.query_feat.weight.dtype)
        with torch.inference_mode():
            boxes, logits = net.forward_export(tensor)[:2]
        if device == "cuda":
            torch.cuda.synchronize()
        return boxes.float().cpu().numpy(), logits.float().cpu().numpy()

    return run


def onnx_runner(path: Path) -> Runner:
    import onnxruntime as ort

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    outputs = [o.name for o in session.get_outputs()]
    boxes_at = next(i for i, n in enumerate(outputs) if "dets" in n)
    logits_at = next(i for i, n in enumerate(outputs) if "labels" in n)

    def run(batch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        result = session.run(None, {name: batch})
        return np.asarray(result[boxes_at]), np.asarray(result[logits_at])

    return run


def tensorrt_runner(path: Path) -> Runner:
    """Builds (or loads) a TensorRT engine for the ONNX model and runs it on PyTorch's CUDA
    tensors, so no other CUDA binding is needed."""
    import tensorrt_bindings as trt
    import torch

    logger = trt.Logger(trt.Logger.WARNING)
    plan = path.with_suffix(".plan")
    if not plan.exists():
        builder = trt.Builder(logger)
        network = builder.create_network(0)
        parser = trt.OnnxParser(network, logger)
        if not parser.parse(path.read_bytes()):
            errors = [str(parser.get_error(i)) for i in range(parser.num_errors)]
            raise RuntimeError(f"TensorRT couldn't parse {path.name}: {errors}")
        config = builder.create_builder_config()
        config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 30)
        serialized = builder.build_serialized_network(network, config)
        if serialized is None:
            raise RuntimeError(f"TensorRT couldn't build {path.name}")
        plan.write_bytes(bytes(serialized))
    engine = trt.Runtime(logger).deserialize_cuda_engine(plan.read_bytes())
    context = engine.create_execution_context()
    names = [engine.get_tensor_name(i) for i in range(engine.num_io_tensors)]
    dtypes = {trt.DataType.FLOAT: torch.float32, trt.DataType.HALF: torch.float16}
    buffers = {
        name: torch.empty(
            tuple(engine.get_tensor_shape(name)),
            dtype=dtypes[engine.get_tensor_dtype(name)],
            device="cuda",
        )
        for name in names
    }
    for name, buffer in buffers.items():
        context.set_tensor_address(name, buffer.data_ptr())
    inputs = [n for n in names if engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
    boxes_name = next(n for n in names if "dets" in n)
    logits_name = next(n for n in names if "labels" in n)

    def run(batch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        buffers[inputs[0]].copy_(torch.from_numpy(batch))
        stream = torch.cuda.current_stream()
        context.execute_async_v3(stream.cuda_stream)
        stream.synchronize()
        return (
            buffers[boxes_name].float().cpu().numpy(),
            buffers[logits_name].float().cpu().numpy(),
        )

    return run


# ONNX files


def export_onnx(checkpoint: Path, out: Path) -> Path:
    """RF-DETR's own exporter: fp32, batch 1, the training resolution."""
    from rfdetr import RFDETRNano

    target = out / "detector-fp32.onnx"
    if not target.exists():
        out.mkdir(parents=True, exist_ok=True)
        before = set(out.glob("*.onnx"))
        RFDETRNano(pretrain_weights=str(checkpoint)).export(
            output_dir=str(out), format="onnx", shape=(RESOLUTION, RESOLUTION), verbose=False
        )
        made = [p for p in out.glob("*.onnx") if p not in before]
        if len(made) != 1:
            raise RuntimeError(f"expected one exported ONNX file, found {made}")
        made[0].rename(target)
    return target


def to_fp16(fp32: Path) -> Path:
    import onnx
    from onnxruntime.transformers.float16 import convert_float_to_float16

    target = fp32.with_name("detector-fp16.onnx")
    if not target.exists():
        # fp16 inside, fp32 in and out, like the other variants.
        onnx.save(convert_float_to_float16(onnx.load(str(fp32)), keep_io_types=True), target)
    return target


def to_int8_dynamic(fp32: Path) -> Path:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    target = fp32.with_name("detector-int8-dynamic.onnx")
    if not target.exists():
        # The matrix multiplies, where a transformer does most of its work (the network has
        # 131 of them, and 9 convolutions).
        quantize_dynamic(
            str(fp32),
            str(target),
            op_types_to_quantize=["MatMul", "Gemm"],
            per_channel=True,
            weight_type=QuantType.QInt8,
        )
    return target


def to_int8_calibrated(fp32: Path, calibration: Sequence[Image.Image]) -> Path:
    import onnx
    from onnxruntime.quantization import (
        CalibrationDataReader,
        QuantFormat,
        QuantType,
        quantize_static,
    )

    target = fp32.with_name("detector-int8-calibrated.onnx")
    if target.exists():
        return target
    input_name = onnx.load(str(fp32)).graph.input[0].name

    class Frames(CalibrationDataReader):  # type: ignore[misc]
        def __init__(self) -> None:
            self._batches: Iterator[np.ndarray] = (preprocess(i) for i in calibration)

        def get_next(self) -> dict[str, np.ndarray] | None:
            batch = next(self._batches, None)
            return None if batch is None else {input_name: batch}

    quantize_static(
        str(fp32),
        str(target),
        Frames(),
        quant_format=QuantFormat.QDQ,
        # Only the layers TensorRT runs in int8; quantizing the rest (constants, layer norms)
        # makes a model TensorRT can't parse.
        op_types_to_quantize=["Conv", "MatMul", "Gemm"],
        activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8,
        per_channel=True,
        # TensorRT takes symmetric int8 only, and dequantizes 8-bit values only: biases stay
        # float rather than becoming int32.
        extra_options={
            "ActivationSymmetric": True,
            "WeightSymmetric": True,
            "QuantizeBias": False,
        },
    )
    return target


# Scoring


def evaluate(
    variant: str,
    device: str,
    run: Runner,
    frames: Sequence[Frame],
    coco: dict[str, Any],
    warmup: int = 10,
) -> DetectorResult:
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    from rfdetr.export._runtime.decode import decode_detections

    batches = [preprocess(frame.image) for frame in frames]
    for batch in batches[:warmup]:
        run(batch)
    detections: list[dict[str, Any]] = []
    times_ms = []
    for frame, batch in zip(frames, batches, strict=True):
        started = time.perf_counter()
        boxes, logits = run(batch)
        times_ms.append((time.perf_counter() - started) * 1000)
        decoded = decode_detections(boxes[0], logits[0], frame.image.size, threshold=0.001)
        for (x0, y0, x1, y1), score, label in zip(
            decoded.xyxy, decoded.confidence, decoded.class_id, strict=True
        ):
            detections.append(
                {
                    "image_id": frame.image_id,
                    "category_id": int(label) + 1,
                    "bbox": [float(x0), float(y0), float(x1 - x0), float(y1 - y0)],
                    "score": float(score),
                }
            )

    truth = COCO()
    truth.dataset = coco
    truth.createIndex()
    found = truth.loadRes(detections) if detections else COCO()
    evaluation = COCOeval(truth, found, "bbox")
    evaluation.evaluate()
    evaluation.accumulate()
    evaluation.summarize()
    precision = evaluation.eval["precision"]  # [iou, recall, class, area, max dets]
    by_class = {}
    for index, category in enumerate(coco["categories"]):
        values = precision[:, :, index, 0, -1]
        valid = values[values > -1]
        by_class[category["name"]] = float(valid.mean()) if valid.size else float("nan")
    return DetectorResult(
        variant=variant,
        device=device,
        ms_p50=statistics.median(times_ms),
        ms_mean=statistics.fmean(times_ms),
        map_50_95=float(evaluation.stats[0]),
        map_50=float(evaluation.stats[1]),
        ap_by_class=by_class,
    )
