"""Adapter: local vision-language model served by llama.cpp's llama-server (implements ports.NoteInterpreter).

* Offline: weights are local files; the server binds 127.0.0.1 on a free port; no remote calls.
* Lazy and frugal: started on first use, stopped after `idle_seconds` without requests, and at exit.
* Schema-constrained: the request carries the JSON schema (response_format json_schema), so llama.cpp's
  grammar-constrained decoding can only emit JSON matching it. Domain validation still happens afterwards.
"""
from __future__ import annotations

import atexit
import base64
import json
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .. import config


class LocalVLM:
    def __init__(self, model: Path, mmproj: Path | None, name: str, idle_seconds: int = 600, ctx: int = 8192):
        self.model, self.mmproj, self.name = model, mmproj, name
        self.idle, self.ctx = idle_seconds, ctx
        self._proc: subprocess.Popen | None = None
        self._port = 0
        self._last = 0.0
        self._lock = threading.Lock()
        atexit.register(self.stop)

    # ---------------------------------------------------------------- lifecycle
    def available(self) -> bool:
        return self.model.exists() and Path(config.LLAMA_SERVER).exists()

    def _start(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self._port = s.getsockname()[1]
        cmd = [str(config.LLAMA_SERVER), '-m', str(self.model), '--host', '127.0.0.1', '--port', str(self._port),
               '-c', str(self.ctx), '-ngl', '99', '--jinja', '--no-webui']
        if self.mmproj and self.mmproj.exists():
            cmd += ['--mmproj', str(self.mmproj)]
        log = open(config.MODEL_HOME / 'llm' / 'llama-server.log', 'a')
        self._proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 180
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f'llama-server exited ({self._proc.returncode}); see {log.name}')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self._port}/health', timeout=2) as r:
                    if r.status == 200:
                        threading.Thread(target=self._reaper, daemon=True).start()
                        return
            except OSError:
                time.sleep(1)
        self.stop()
        raise RuntimeError('llama-server did not become healthy in 180 s')

    def _reaper(self):
        while self._proc and self._proc.poll() is None:
            time.sleep(30)
            if time.time() - self._last > self.idle:
                with self._lock:
                    self.stop()

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    # ---------------------------------------------------------------- inference
    def complete_json(self, system: str, user: str, schema: dict, image_png: bytes | None = None,
                      max_tokens: int = 1200, temperature: float = 0.0, seed: int | None = None) -> dict:
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                self._start()
            self._last = time.time()
        content = [{'type': 'text', 'text': user}]
        if image_png and self.mmproj and self.mmproj.exists():
            content.insert(0, {'type': 'image_url',
                               'image_url': {'url': f'data:{_image_mime(image_png)};base64,' + base64.b64encode(image_png).decode()}})
        body = {'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
                'temperature': temperature, 'max_tokens': max_tokens, 'cache_prompt': True,
                **({'seed': seed} if seed is not None else {}),
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'proposal', 'schema': schema,
                                                                           'strict': True}},
                'chat_template_kwargs': {'enable_thinking': False}}
        req = urllib.request.Request(f'http://127.0.0.1:{self._port}/v1/chat/completions', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
        self._last = time.time()
        text = data['choices'][0]['message']['content']
        return {'json': json.loads(text), 'raw': text, 'model': self.name, 'seconds': round(time.time() - t0, 1),
                'usage': data.get('usage')}


_INSTANCE: LocalVLM | None = None


def interpreter() -> LocalVLM | None:
    """The configured note interpreter, or None if its weights are not installed."""
    global _INSTANCE
    if _INSTANCE is None:
        m = config.NOTE_MODEL
        _INSTANCE = LocalVLM(m['model'], m.get('mmproj'), m['name'])
    return _INSTANCE if _INSTANCE.available() else None


def _image_mime(data: bytes) -> str:
    """The real type of the image bytes (callers pass PNG or JPEG crops)."""
    return 'image/jpeg' if data[:3] == b'\xff\xd8\xff' else 'image/png'
