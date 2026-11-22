#!/usr/bin/env python3
"""UniMERNet adapter. Runs INSIDE .venvs/unimernet (subprocess). JSON in -> JSON out.

Usage: python unimernet_infer.py --model-dir DIR --input jobs.json --output results.json
jobs.json: [{"id": "...", "image": "/abs/crop.png"}, ...]
Result per job: {"id", "status": "OK"|"FAILED", "latex", "error", "seconds"}
UniMERNet is a third transcription, never proof of fidelity.
"""
import argparse
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path


def build(model_dir: Path, device: str):
    import torch  # noqa: F401
    import unimernet.tasks as tasks
    from unimernet.common.config import Config
    from unimernet.processors import load_processor

    cfg_text = f"""
model:
  arch: unimernet
  model_type: unimernet
  model_config:
    model_name: {model_dir}
    max_seq_len: 1536
  load_pretrained: True
  pretrained: {model_dir / 'pytorch_model.pth'}
  tokenizer_config:
    path: {model_dir}
datasets:
  formula_rec_eval:
    vis_processor:
      eval:
        name: "formula_image_eval"
        image_size: [192, 672]
run:
  runner: runner_iter
  task: unimernet_train
  device: "{device}"
  distributed: False
  evaluate: True
  generate_cfg:
    temperature: 0.0
    do_sample: False
    top_p: 1.0
"""
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write(cfg_text)
        cfg_path = f.name
    cfg = Config(argparse.Namespace(cfg_path=cfg_path, options=None))
    task = tasks.setup_task(cfg)
    model = task.build_model(cfg).to(device).eval()
    vis = load_processor('formula_image_eval', cfg.config.datasets.formula_rec_eval.vis_processor.eval)
    return model, vis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model-dir', type=Path, required=True)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--device', default='cpu', help='cpu is the verified path; mps is untested upstream')
    a = ap.parse_args()
    jobs = json.loads(a.input.read_text())
    results = []
    try:
        import torch
        from PIL import Image
        model, vis = build(a.model_dir, a.device)
    except Exception as exc:
        err = ''.join(traceback.format_exception(exc))[-4000:]
        results = [{'id': j['id'], 'status': 'FAILED', 'latex': None, 'error': 'MODEL_LOAD: ' + err} for j in jobs]
        a.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(err, file=sys.stderr)
        return 1
    for j in jobs:
        t0 = time.time()
        try:
            img = Image.open(j['image']).convert('RGB')
            x = vis(img).unsqueeze(0).to(a.device)
            with torch.no_grad():
                out = model.generate({'image': x})
            results.append({'id': j['id'], 'status': 'OK', 'latex': out['pred_str'][0], 'error': None,
                            'seconds': round(time.time() - t0, 2)})
        except Exception as exc:
            results.append({'id': j['id'], 'status': 'FAILED', 'latex': None,
                            'error': ''.join(traceback.format_exception(exc))[-4000:],
                            'seconds': round(time.time() - t0, 2)})
    a.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
