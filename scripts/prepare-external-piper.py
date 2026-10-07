#!/usr/bin/env python3
"""Write an equivalent ONNX model with external weights, then verify them."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import onnx
from onnx import numpy_helper

parser = argparse.ArgumentParser()
parser.add_argument('source')
parser.add_argument('target')
args = parser.parse_args()
source, target = Path(args.source), Path(args.target)
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists() or target.with_suffix('.weights').exists():
    raise SystemExit('Target exists; choose a fresh output path')
started = time.monotonic()
model = onnx.load(source)
before = {tensor.name: hashlib.sha256(numpy_helper.to_array(tensor).tobytes()).hexdigest()
          for tensor in model.graph.initializer}
onnx.save_model(model, target, save_as_external_data=True,
                all_tensors_to_one_file=True, location=target.with_suffix('.weights').name,
                size_threshold=1024, convert_attribute=False)
del model
restored = onnx.load(target)
after = {tensor.name: hashlib.sha256(numpy_helper.to_array(tensor).tobytes()).hexdigest()
         for tensor in restored.graph.initializer}
if before != after:  # explicit: an assert would vanish under python -O
    raise SystemExit('Converted tensor values differ; not writing a report')
shutil.copyfile(str(source)+'.json', str(target)+'.json')
report = dict(source=str(source), target=str(target),
              verified_initializers=len(before), nodes=len(restored.graph.node),
              preparation_seconds=round(time.monotonic()-started,3))
target.with_suffix('.verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
