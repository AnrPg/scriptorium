"""Use case: CONTEXTUAL RETRIEVAL for the RAG chunks (Anthropic, "Contextual Retrieval", 2024) with the LOCAL model
— optional, off by default.

For each chunk (pipeline.rag.chunks) the local model writes ONE short sentence situating the chunk in its chapter
(what it is about, what it continues). Stored apart from the book's text in <book>/cache/rag_context.json, keyed by
chunk id (content-addressed: a changed chunk gets a new sentence, an unchanged one is never asked again). The
export adds it as the chunk's separate 'context' field — never mixed into 'text', never presented as the book's
words. Evidence is mixed (35–49% fewer retrieval failures in Anthropic's own benchmark; marginal in independent
tests, Merola & Singh 2025): the heading path that every chunk already starts with is the cheap part of the gain.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..adapters import local_llm
from ..adapters.storage import atomic_write, read_json
from ..pipeline import rag
from .exports import rag_settings

FILE = Path('cache') / 'rag_context.json'
MAX_SECTION_CHARS = 6000           # the chunk's section (neighbouring chunks) given as context
SYSTEM = ('You situate an excerpt of a textbook within its chapter, for search retrieval. Answer with ONE short '
          'sentence (max 40 words) in the language of the excerpt, saying what the excerpt is about within the '
          'chapter. Do not add facts that are not in the text.')
SCHEMA = {'type': 'object', 'properties': {'context': {'type': 'string'}}, 'required': ['context']}


def run(out: Path, interpreter=None, log=print, limit: int | None = None) -> int:
    model = interpreter or local_llm.interpreter()
    if model is None:
        log('no local model installed (see config.NOTE_MODELS): nothing done')
        return 2
    c = read_json(out / 'canonical.json')
    if not c:
        log('process the book first')
        return 2
    title = (read_json(out / 'book.json', {}) or {}).get('title', '')
    chunks = rag.chunks(c, rag_settings(c), title)
    store = read_json(out / FILE, {}) or {}
    done = store.get('context') or {}
    by_path: dict[tuple, list[dict]] = {}
    for ch in chunks:
        by_path.setdefault(tuple(ch['breadcrumb']), []).append(ch)
    todo = [ch for ch in chunks if ch['id'] not in done][:limit]
    t0 = time.perf_counter()
    for i, ch in enumerate(todo, 1):
        section = '\n\n'.join(x['text'] for x in by_path[tuple(ch['breadcrumb'])] if x['id'] != ch['id'])[:MAX_SECTION_CHARS]
        user = f'<book>{title}</book>\n<section>\n{section}\n</section>\n<excerpt>\n{ch["text"]}\n</excerpt>'
        try:
            res = model.complete_json(SYSTEM, user, SCHEMA, max_tokens=120, temperature=0.0, seed=0)
            sentence = ' '.join(str(res['json'].get('context') or '').split())[:300]
        except Exception as exc:                      # a model failure is recorded, never fatal
            log(f'chunk {ch["id"]}: FAILED {exc}')
            continue
        if sentence:
            done[ch['id']] = sentence
        if i % 20 == 0 or i == len(todo):
            atomic_write(out / FILE, json.dumps({'model': getattr(model, 'name', ''), 'context': done}, ensure_ascii=False))
            log(f'context: {i}/{len(todo)} chunks ({round(time.perf_counter() - t0)} s)')
    live = {ch['id'] for ch in chunks}
    atomic_write(out / FILE, json.dumps({'model': getattr(model, 'name', ''),
                                         'context': {k: v for k, v in done.items() if k in live}}, ensure_ascii=False))
    return 0
