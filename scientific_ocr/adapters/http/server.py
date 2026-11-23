"""HTTP adapter: a thin route table over application.services.App (stdlib only, localhost only).

Security for a local tool: binds 127.0.0.1; rejects foreign Host headers (DNS rebinding); every state-changing
request must carry the `X-OCR-Client` header, which other web origins cannot send without a CORS preflight that
this server never grants (CSRF). Static assets are served from ./static with no remote dependencies.
"""
from __future__ import annotations

import json
import mimetypes
import re
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from ...adapters import pdf as pdfio
from ...adapters.event_store import EventError
from ...adapters.storage import NotFound
from ...application.services import App, ServiceError
from ...core.rules import RuleError

STATIC = Path(__file__).parent / 'static'
MAX_UPLOAD = 4 << 30
ALLOWED_HOSTS = {'127.0.0.1', 'localhost', '[::1]'}
ROUTES: list[tuple[str, re.Pattern, str]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile('^' + pattern + '$'), fn.__name__))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = 'scientific-ocr'

    def log_message(self, *a):
        pass

    # ---------------------------------------------------------------- plumbing
    def _dispatch(self, method: str):
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0]
        if host not in ALLOWED_HOSTS:
            return self._json(403, {'error': 'forbidden host'})
        url = urlparse(self.path)
        self.query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if method != 'GET' and self.headers.get('X-OCR-Client') != '1':
            return self._json(403, {'error': 'missing client header'})
        for m, rx, name in ROUTES:
            mt = rx.match(url.path)
            if m == method and mt:
                try:
                    return getattr(self, name)(*[unquote(g) for g in mt.groups()])
                except (ServiceError, RuleError, EventError) as exc:              # invalid input: the caller's fault
                    return self._json(400, {'error': str(exc)})
                except NotFound as exc:                                            # an explicit lookup miss
                    return self._json(404, {'error': f'not found: {exc}'})
                except (ValueError, TypeError) as exc:     # usually bad input (int('x'), bad body keys), maybe a bug: log
                    traceback.print_exc()
                    return self._json(400, {'error': f'{type(exc).__name__}: {exc}'})
                except Exception as exc:                                           # a bug: answer, log, keep serving
                    traceback.print_exc()
                    return self._json(500, {'error': f'internal error: {type(exc).__name__}: {exc}'})
        return self._json(404, {'error': 'no route'})

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _export_options(self) -> dict:
        q = self.query
        split = lambda k: [x for x in (q.get(k) or '').split(',') if x]
        return {'exclude': split('exclude'), 'move': split('move'), 'images': q.get('fig_images') or 'keep',
                'unit_level': q.get('unit_level') or 1}

    def _body(self) -> dict:
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            body = json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f'request body is not valid JSON: {exc}') from None
        if not isinstance(body, dict):
            raise ValueError('request body must be a JSON object')
        return body

    def _json(self, code: int, obj):
        self._bytes(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), 'application/json')

    def _bytes(self, code: int, data: bytes, ctype: str, filename: str | None = None, cache: bool = False):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'max-age=3600' if cache else 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if filename:
            ascii_name = filename.encode('ascii', 'replace').decode().replace('?', '_')
            self.send_header('Content-Disposition',            # RFC 6266/5987: non-ASCII titles (e.g. Greek)
                             f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}')
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, cache=True):
        self._bytes(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                    cache=cache)

    def _receive(self, filename: str) -> Path:
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > MAX_UPLOAD:
            raise ServiceError('empty or too large upload')
        safe = re.sub(r'[^\w.-]+', '_', Path(filename).name)[-80:] or 'upload'
        dest = self.app.staging_file(f'{uuid.uuid4().hex[:12]}-{safe}')
        tmp = dest.with_name(dest.name + '.part')
        left = n
        with open(tmp, 'wb') as f:
            while left:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    tmp.unlink(missing_ok=True)
                    raise ServiceError('upload interrupted')
                f.write(chunk)
                left -= len(chunk)
        tmp.replace(dest)
        return dest

    def _actor(self) -> str:
        return (self.headers.get('X-OCR-Actor') or 'reviewer')[:40]

    # ---------------------------------------------------------------- static
    @route('GET', r'/')
    def index(self):
        self._file(STATIC / 'index.html', cache=False)

    @route('GET', r'/static/([\w./-]+)')
    def static(self, name):
        p = (STATIC / name).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            raise KeyError(name)
        self._file(p, cache='vendor/' in name)          # vendored libraries are immutable

    # ---------------------------------------------------------------- library
    @route('GET', r'/api/meta')
    def meta(self):
        self._json(200, self.app.meta())

    @route('GET', r'/api/books')
    def books(self):
        self._json(200, self.app.books())

    @route('POST', r'/api/books/upload')
    def upload_pdf(self):
        name = self.query.get('filename', 'book.pdf')
        self._json(200, self.app.create_from_upload(self._receive(name), name, self.query.get('title')))

    @route('POST', r'/api/uploads')
    def upload_file(self):
        p = self._receive(self.query.get('filename', 'page'))
        self._json(200, {'token': p.name})

    @route('POST', r'/api/books/from-images')
    def from_images(self):
        body = self._body()
        files = [self.app.staging_file(t) for t in body.get('tokens', [])]
        if any(not f.is_file() or f.suffix.lower() not in pdfio.IMAGE_SUFFIXES
               for f in files):
            raise ServiceError('unknown or unsupported upload')
        self._json(200, self.app.create_from_images(files, body.get('title') or 'Scanned book', body.get('names'),
                                                      body.get('order') or 'name'))

    @route('GET', r'/api/fs')
    def fs(self):
        self._json(200, self.app.browse(self.query.get('path')))

    @route('POST', r'/api/fs/pick')
    def fs_pick(self):
        self._json(200, self.app.pick_native(self._body().get('start')))

    @route('GET', r'/api/fs/thumb')
    def fs_thumb(self):
        self._file(self.app.fs_thumb(self.query.get('path', ''), int(self.query.get('w', 200))), cache=False)

    @route('POST', r'/api/books/import-images')
    def import_images(self):
        b = self._body()
        self._json(200, self.app.import_images(b.get('paths') or [], b.get('title') or '', b.get('order') or 'name',
                                               b.get('base')))

    @route('POST', r'/api/books/import')
    def import_paths(self):
        b = self._body()
        self._json(200, self.app.import_paths(b.get('paths') or [], b.get('title')))

    @route('POST', r'/api/trash/([\w.-]+)/restore')
    def restore(self, name):
        self._json(200, self.app.restore(name))

    # ---------------------------------------------------------------- book
    @route('GET', r'/api/books/([\w.-]+)')
    def book(self, bid):
        self._json(200, self.app.book(bid))

    @route('POST', r'/api/books/([\w.-]+)/settings')
    def settings(self, bid):
        self._json(200, self.app.update_settings(bid, self._body()))

    @route('POST', r'/api/books/([\w.-]+)/trash')
    def trash(self, bid):
        self._json(200, self.app.trash(bid))

    @route('POST', r'/api/books/([\w.-]+)/pages/remove')
    def pages_remove(self, bid):
        self._json(200, self.app.remove_pages(bid, self._body().get('pages'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/pages/restore')
    def pages_restore(self, bid):
        self._json(200, self.app.remove_pages(bid, self._body().get('pages'), self._actor(), restore=True))

    @route('POST', r'/api/books/([\w.-]+)/rebuild')
    def rebuild(self, bid):
        self._json(200, self.app.rebuild(bid) or {})

    @route('POST', r'/api/books/([\w.-]+)/process')
    def process(self, bid):
        self._json(200, self.app.process(bid, self._body().get('pages')))

    @route('GET', r'/api/books/([\w.-]+)/history')
    def history(self, bid):
        self._json(200, self.app.history(bid))

    @route('GET', r'/api/books/([\w.-]+)/queue')
    def queue(self, bid):
        self._json(200, self.app.queue(bid, self.query.get('level', 'required'), self.query.get('order', 'priority')))

    @route('GET', r'/api/books/([\w.-]+)/pages/(\d+)')
    def page(self, bid, n):
        self._json(200, self.app.page(bid, int(n)))

    @route('GET', r'/api/books/([\w.-]+)/pages/(\d+)/output')
    def page_output(self, bid, n):
        self._json(200, self.app.page_output(bid, int(n), self.query.get('style', 'tags')))

    @route('GET', r'/api/books/([\w.-]+)/pages/(\d+)/image')
    def page_image(self, bid, n):
        self._file(self.app.page_image(bid, int(n), int(self.query.get('w', 300))))

    @route('GET', r'/api/books/([\w.-]+)/(assets/.+)')
    def asset(self, bid, rel):
        self._file(self.app.asset(bid, rel), cache=False)

    @route('GET', r'/api/books/([\w.-]+)/export/([\w]+)')
    def export(self, bid, fmt):
        data, ctype, name = self.app.export(bid, fmt, self.query.get('style', 'tags'), self._export_options())
        self._bytes(200, data, ctype, filename=name)

    @route('GET', r'/api/books/([\w.-]+)/classes')
    def classes(self, bid):
        self._json(200, self.app.classes(bid))

    @route('GET', r'/api/books/([\w.-]+)/classes/(\w+)/items')
    def class_items(self, bid, reason):
        self._json(200, self.app.class_items(bid, reason, int(self.query.get('limit', 200))))

    @route('POST', r'/api/books/([\w.-]+)/bulk')
    def bulk(self, bid):
        b = self._body()
        self._json(200, self.app.bulk_decide(bid, b.get('blocks') or [], b.get('action', 'approve'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/classes/(\w+)/sample')
    def sample(self, bid, reason):
        self._json(200, self.app.sample(bid, reason, float(self._body().get('max_error', 0.05))))

    @route('POST', r'/api/books/([\w.-]+)/classes/(\w+)/accept')
    def accept_class(self, bid, reason):
        b = self._body()
        self._json(200, self.app.accept_class(bid, reason, b.get('results') or [], float(b.get('max_error', 0.05)),
                                              self._actor()))

    @route('GET', r'/api/books/([\w.-]+)/raw')
    def raw(self, bid):
        self._json(200, self.app.raw_fragment(bid, self.query.get('file', ''), self.query.get('pointer')))

    @route('GET', r'/api/books/([\w.-]+)/preview')
    def preview(self, bid):
        text = self.app.preview(bid, self.query.get('style', 'tags'), self.query.get('images', '1') == '1',
                                options=self._export_options())
        self._bytes(200, text.encode(), 'text/plain; charset=utf-8')

    # ---------------------------------------------------------------- review
    @route('POST', r'/api/books/([\w.-]+)/decide')
    def decide(self, bid):
        b = self._body()
        for reserved in ('actor', 'book_id'):              # never taken from the body
            b.pop(reserved, None)
        self._json(200, self.app.decide(bid, b.pop('block_id', ''), b.pop('action', ''), self._actor(), **b))

    @route('POST', r'/api/books/([\w.-]+)/undo')
    def undo(self, bid):
        self._json(200, self.app.undo(bid, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/redo')
    def redo(self, bid):
        self._json(200, self.app.redo(bid, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/rules/preview')
    def rule_preview(self, bid):
        self._json(200, self.app.preview_rule(bid, self._body()))

    @route('POST', r'/api/books/([\w.-]+)/rules')
    def rule_add(self, bid):
        self._json(200, self.app.add_rule(bid, self._body(), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/rules/([\w.-]+)/remove')
    def rule_remove(self, bid, rid):
        self._json(200, self.app.remove_rule(bid, rid, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/regions')
    def region_add(self, bid):
        b = self._body()
        self._json(200, self.app.add_region(bid, int(b['page']), b['bbox'], b.get('label') or {},
                                            b.get('replaces'), self._actor(), b.get('replaces_region')))

    @route('POST', r'/api/books/([\w.-]+)/merge')
    def merge(self, bid):
        b = self._body()
        self._json(200, self.app.merge(bid, b.get('blocks') or [], b.get('label') or {}, b.get('mode', 'join'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/groups')
    def group(self, bid):
        b = self._body()
        self._json(200, self.app.group(bid, b.get('blocks') or [], b.get('type', ''), b.get('name', ''), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/groups/([\w.-]+)/members')
    def group_members(self, bid, gid):
        self._json(200, self.app.update_group(bid, gid, self._body().get('blocks') or [], self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/groups/([\w.-]+)/remove')
    def ungroup(self, bid, gid):
        self._json(200, self.app.ungroup(bid, gid, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/merges/([\w.-]+)/remove')
    def unmerge(self, bid, mid):
        self._json(200, self.app.unmerge(bid, mid, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/regions/([\w.-]+)/remove')
    def region_remove(self, bid, rid):
        self._json(200, self.app.remove_region(bid, rid, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/segments/move')
    def move_segments(self, bid):
        b = self._body()
        self._json(200, self.app.move_segments(bid, b.get('blocks') or [], b.get('after'), b.get('before'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/segments/remove')
    def remove_segments(self, bid):
        self._json(200, self.app.remove_segments(bid, self._body().get('blocks') or [], self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/hide')
    def hide(self, bid, block):
        self._json(200, self.app.hide_block(bid, block, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/interpret')
    def interpret(self, bid, block):
        self._json(200, self.app.interpret_note(bid, block, self._body().get('note', '')))

    @route('POST', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/proposal/check')
    def proposal_check(self, bid, block):
        self._json(200, self.app.check_proposal(bid, block, self._body().get('proposal') or {}))

    @route('POST', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/proposal/apply')
    def proposal_apply(self, bid, block):
        b = self._body()
        self._json(200, self.app.apply_proposal(bid, block, b.get('proposal') or {}, b.get('note', ''),
                                                b.get('model', 'unknown'), self._actor()))

    @route('GET', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/history')
    def block_history(self, bid, block):
        self._json(200, self.app.block_history(bid, block))

    @route('POST', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/relabel')
    def relabel(self, bid, block):
        self._json(200, self.app.relabel(bid, block, self._body().get('label') or {}, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/show')
    def show(self, bid, block):
        self._json(200, self.app.show_block(bid, block, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/blocks/([\w.-]+)/wrong')
    def wrong(self, bid, block):
        self._json(200, self.app.report_wrong(bid, block, self._body().get('note', ''), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/pages/(\d+)/suggestions/(\w+)/accept')
    def suggestion(self, bid, n, key):
        self._json(200, self.app.accept_suggestion(bid, int(n), key, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/suggestions/accept-all')
    def accept_all(self, bid):
        p = self._body().get('page')
        self._json(200, self.app.accept_all(bid, int(p) if p else None, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/structure/apply')
    def structure_apply(self, bid):
        self._json(200, self.app.apply_structure(bid, self._body(), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/pages/(\d+)/structure/propose')
    def structure_propose(self, bid, n):
        self._json(200, self.app.propose_structure(bid, int(n)))

    @route('POST', r'/api/books/([\w.-]+)/auto-groups/(\w+)/keep')
    def keep_group(self, bid, key):
        self._json(200, self.app.keep_group(bid, key, self._body().get('type'), self._actor()))

    @route('GET', r'/api/books/([\w.-]+)/entities')
    def entities(self, bid):
        self._json(200, self.app.entities(bid))

    @route('POST', r'/api/books/([\w.-]+)/entities/confirm')
    def entities_confirm(self, bid):
        p = self._body()
        self._json(200, self.app.confirm_entities(bid, p.get('keys') or [], p.get('type'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/groups/([\w-]+)/end')
    def group_end(self, bid, gid):
        self._json(200, self.app.set_group_end(bid, gid, self._body().get('last'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/extent-review')
    def extent_review_start(self, bid):
        self._json(200, self.app.start_extent_review(bid))

    @route('GET', r'/api/books/([\w.-]+)/extent-review')
    def extent_review_list(self, bid):
        self._json(200, self.app.extent_proposals(bid))

    @route('POST', r'/api/books/([\w.-]+)/extent-review/(\w+)/accept')
    def extent_review_accept(self, bid, key):
        self._json(200, self.app.accept_extent_proposal(bid, key, self._actor()))

    @route('POST', r'/api/render-inline')
    def render_inline(self):
        self._json(200, self.app.render_inline(self._body().get('text')))

    @route('POST', r'/api/books/([\w.-]+)/output')
    def set_output(self, bid):
        p = self._body()
        self._json(200, self.app.set_output(bid, p.get('blocks') or [], bool(p.get('include')), p.get('group'), self._actor()))

    @route('GET', r'/api/books/([\w.-]+)/outline')
    def outline(self, bid):
        self._json(200, self.app.outline(bid))

    @route('GET', r'/api/books/([\w.-]+)/heading-review')
    def heading_review(self, bid):
        self._json(200, self.app.heading_review(bid))

    @route('POST', r'/api/books/([\w.-]+)/heading-review/(\w+)/accept')
    def heading_review_accept(self, bid, key):
        self._json(200, self.app.accept_heading_proposal(bid, key, self._body().get('members'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/entities/confirm-trusted')
    def entities_confirm_trusted(self, bid):
        self._json(200, self.app.confirm_trusted(bid, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/entities/assign')
    def entities_assign(self, bid):
        p = self._body()
        self._json(200, self.app.assign_entity(bid, p.get('blocks') or [], p.get('type'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/entities/(\w+)/remove')
    def entities_remove(self, bid, key):
        self._json(200, self.app.remove_entity(bid, key, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/entity-review')
    def entity_review_start(self, bid):
        self._json(200, self.app.start_entity_review(bid))

    @route('GET', r'/api/books/([\w.-]+)/entity-review')
    def entity_review_list(self, bid):
        self._json(200, self.app.entity_proposals(bid))

    @route('POST', r'/api/books/([\w.-]+)/entity-review/(\w+)/accept')
    def entity_review_accept(self, bid, key):
        self._json(200, self.app.accept_entity_proposal(bid, key, self._actor()))

    @route('GET', r'/api/books/([\w.-]+)/rev')
    def book_rev(self, bid):
        self._json(200, self.app.revision(bid))

    @route('POST', r'/api/books/([\w.-]+)/merge/preview')
    def merge_preview(self, bid):
        p = self._body()
        self._json(200, self.app.merge_preview(bid, p.get('blocks') or [], p.get('label')))

    @route('POST', r'/api/books/([\w.-]+)/structure-review')
    def structure_review_start(self, bid):
        self._json(200, self.app.start_structure_review(bid))

    @route('GET', r'/api/books/([\w.-]+)/structure-review')
    def structure_review_list(self, bid):
        self._json(200, self.app.structure_proposals(bid))

    @route('POST', r'/api/books/([\w.-]+)/structure-review/(\w+)/accept')
    def structure_review_accept(self, bid, key):
        self._json(200, self.app.accept_structure_proposal(bid, key, self._body().get('members'), self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/suggestions/(\w+)/dismiss')
    def dismiss(self, bid, key):
        self._json(200, self.app.dismiss_suggestion(bid, key, self._actor()))

    @route('POST', r'/api/books/([\w.-]+)/pages/(\d+)/profile')
    def profile(self, bid, n):
        self._json(200, self.app.set_profile(bid, int(n), self._body().get('profile', 'diligent'), self._actor()))

    # ---------------------------------------------------------------- jobs
    @route('GET', r'/api/jobs')
    def jobs(self):
        self._json(200, self.app.jobs_list())

    @route('POST', r'/api/jobs/(\w+)/cancel')
    def cancel(self, jid):
        self._json(200, self.app.cancel(jid))

    @route('POST', r'/api/jobs/(\w+)/remove')
    def job_remove(self, jid):
        self._json(200, self.app.remove_job(jid))

    @route('POST', r'/api/jobs/clear')
    def jobs_clear(self):
        self._json(200, self.app.clear_jobs(self._body().get('book')))

    @route('GET', r'/api/jobs/(\w+)/summary')
    def job_summary(self, jid):
        self._json(200, self.app.job_summary(jid))

    @route('GET', r'/api/jobs/(\w+)/log')
    def job_log(self, jid):
        self._bytes(200, self.app.job_log(jid), 'text/plain; charset=utf-8')


def serve(app: App, port: int = 8765):
    import signal

    def _graceful(*_):                 # SIGTERM -> normal shutdown, so atexit cleanups (model servers) run
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _graceful)
    Handler.app = app
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    srv.daemon_threads = True
    print(f'Scientific OCR: http://127.0.0.1:{port}  (library: {app.library_root()}; Ctrl-C to stop)', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
