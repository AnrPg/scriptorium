#!/usr/bin/env python3
"""Prefetch UniMERNet, MolScribe and RxnScribe model files. No inference is performed."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

from huggingface_hub import hf_hub_download, snapshot_download


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def record(manifest, key, action):
    try:
        result = Path(action())
        files = [result] if result.is_file() else [p for p in result.rglob('*') if p.is_file() and not p.name.startswith('.git')]
        # Don't read every huge file twice unless it is directly a model checkpoint.
        weights = [p for p in files if p.suffix in ('.pth', '.ckpt', '.bin', '.safetensors')]
        manifest[key] = {'status': 'DOWNLOADED_NOT_TESTED', 'path': str(result),
                         'checkpoints': [{'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in weights]}
        print(f'{key}: downloaded; {len(weights)} checkpoint files')
    except Exception as exc:
        manifest[key] = {'status': 'DOWNLOAD_FAILED', 'error': str(exc)}
        print(f'{key}: FAILED: {exc}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.model_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'models': {}}
    m = manifest['models']

    record(m, 'unimernet_base', lambda: snapshot_download(repo_id='wanderkid/unimernet_base', local_dir=str(root / 'unimernet_base')))
    record(m, 'molscribe', lambda: hf_hub_download(repo_id='yujieq/MolScribe', filename='swin_base_char_aux_1m.pth', local_dir=str(root / 'molscribe')))
    record(m, 'rxnscribe', lambda: hf_hub_download(repo_id='yujieq/RxnScribe', filename='pix2seq_reaction_full.ckpt', local_dir=str(root / 'rxnscribe')))

    dest = root / 'specialized_weights_manifest.json'
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Model weight report: {dest}')
    # Partial downloads are reported in manifest; do not interpret as ready to infer.
    if any(v['status'] == 'DOWNLOAD_FAILED' for v in m.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
