"""Use cases — library: books as aggregates (list, import, settings, trash) and processing jobs."""
from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from pathlib import Path

from ...adapters import pdf as pdfio
from ...core.decisions import VALUE_DECISIONS
from ...core.reasons import as_table
from ...core.vocabulary import GROUP_TYPES
from ...pipeline import engine_json, markdown, references
from ...pipeline.overlay import COMMANDS
from .. import exports
from ...adapters.storage import NotFound, natural_key, page_order
from .base import PROFILES, ServiceBase, ServiceError, _now  # noqa: F401
from .export import _entity_types


class LibraryService(ServiceBase):
    def library_root(self) -> Path:
        return self.lib.root

    def staging_file(self, name: str) -> Path:
        """A path in the upload staging area (the adapter streams the upload body into it)."""
        return self.lib.staging() / Path(name).name

    def jobs_list(self, limit: int = 50) -> list[dict]:
        return self.jobs.list()[:limit]

    def job_log(self, job_id: str, tail: int = 20000) -> bytes:
        job = next((j for j in self.jobs.list() if j['id'] == job_id), None)
        if not job:
            raise NotFound(job_id)
        p = self.jobs.log_path(job)
        return p.read_bytes()[-tail:] if p.exists() else b''

    FS_ROOT = Path.home()                # the local file picker never leaves the user's home folder

    # ---------------------------------------------------------------- queries
    def meta(self) -> dict:
        it = self.interpreter()
        return {'reasons': as_table(), 'labels': engine_json.label_catalog(), 'profiles': PROFILES,
                'note_model': getattr(it, 'name', None), 'group_types': GROUP_TYPES,
                'actions': sorted(COMMANDS), 'value_actions': sorted(VALUE_DECISIONS), 'exports': list(exports.EXPORTERS), 'styled_exports': sorted(exports.STYLED),
                'styles': list(markdown.STYLES), 'entity_types': _entity_types()}

    def books(self) -> list[dict]:
        return [{**b.summary(), 'job': self.jobs.busy(b.id)} for b in self.lib.books()]

    def book(self, book_id: str) -> dict:
        b = self._book(book_id)
        st = b.events.state()
        c = b.canonical() or {}
        return {**b.summary(), 'rev': b.revision(), 'pages': b.pages(), 'rules': st['rules'], 'job': self.jobs.busy(b.id),
                'progress': b.progress(), 'jobs': self.jobs.list(b.id)[:20],
                'can_undo': bool(st['undo_targets']), 'can_redo': bool(st['redo_targets']),
                'stale': (c.get('human') or {}).get('stale', []), 'sections': c.get('sections', []),
                'groups': c.get('groups', []), 'document_structure': c.get('document_structure') or {'fixes': [], 'issues': []},
                'ref_targets': [{'id': t['id'], 'label': t['label'], 'page': t['page']}
                                for t in references.targets(c.get('blocks', []))] if c else [],
                'critical': [f for f in c.get('findings', []) if f['severity'] == 'critical']}

    def history(self, book_id: str) -> list[dict]:
        st = self._book(book_id).events.state()
        return [{**e, 'active': e['seq'] in st['active']} for e in reversed(st['history'])]

    def revision(self, book_id: str) -> dict:
        b = self._book(book_id)
        """rev: changes with the events or the projection; projection: changes only when outputs were rebuilt;
        rebuilding: a re-projection is queued/running (the UI confirms an action only once it is reflected)."""
        active = getattr(self.jobs, 'active', None)
        return {'rev': b.revision(), 'busy': bool(self.jobs.busy(b.id)), 'projection': b.revision().split('-')[0],
                'rebuilding': bool(active(b.id, 'light')) if active else False}

    # ---------------------------------------------------------------- books
    def create_from_upload(self, tmp: Path, filename: str, title: str | None) -> dict:
        if Path(filename).suffix.lower() != '.pdf':
            raise ServiceError('expected a PDF')
        book, created = self.lib.create_from_pdf(tmp, filename, title)
        return {'book': book.summary(), 'created': created}

    def create_from_images(self, files: list[Path], title: str, names: list[str] | None = None,
                           order: str = 'name') -> dict:
        """order='name': pages follow the natural order of the original names (relative paths for a folder).
        Any other value means the files are ALREADY in the page order the reviewer chose and previewed (by name,
        by date, reversed, rearranged by hand); the value is recorded as provenance (source.page_order)."""
        if not files:
            raise ServiceError('no images')
        names = names or [f.name for f in files]
        if len(names) != len(files) or not all(isinstance(n, str) and n for n in names):
            raise ServiceError('one original name per image')
        if not isinstance(order, str) or not re.fullmatch(r'[\w:+-]{1,40}', order):
            raise ServiceError('order: a short label such as "name", "date", "manual"')
        idx = page_order(names) if order == 'name' else list(range(len(files)))
        try:
            book, created = self.lib.create_from_images([files[i] for i in idx], title, [names[i] for i in idx], order)
        except pdfio.UnsupportedImage as exc:         # nothing created; the staged copies are discarded
            for f in files:
                f.unlink(missing_ok=True)
            raise ServiceError(str(exc))
        return {'book': book.summary(), 'created': created}

    def browse(self, path: str | None) -> dict:
        """List folders and importable files (PDF, page images) under the user's home folder."""
        root = self.FS_ROOT.resolve()
        p = Path(path).expanduser().resolve() if path else root
        if p != root and root not in p.parents or not p.is_dir():
            raise ServiceError('folder outside the home directory or missing')
        dirs, files = [], []
        try:
            entries = sorted(p.iterdir(), key=lambda x: x.name.lower())
        except PermissionError:
            raise ServiceError('macOS denied access to this folder')
        entries.sort(key=lambda x: natural_key(x.name))
        for e in entries:
            if e.name.startswith('.'):
                continue
            try:
                if e.is_dir():
                    dirs.append({'name': e.name, 'path': str(e)})
                elif e.suffix.lower() == '.pdf' or e.suffix.lower() in pdfio.IMAGE_SUFFIXES:
                    st = e.stat()
                    files.append({'name': e.name, 'path': str(e), 'size': st.st_size, 'mtime': st.st_mtime,
                                  'type': 'pdf' if e.suffix.lower() == '.pdf' else 'image'})
            except OSError:
                continue
        crumbs = [{'name': '~', 'path': str(root)}] + [
            {'name': part.name, 'path': str(part)} for part in reversed(p.parents) if root in part.parents] + \
            ([{'name': p.name, 'path': str(p)}] if p != root else [])
        return {'path': str(p), 'crumbs': crumbs, 'dirs': dirs, 'files': files,
                'images': sum(1 for f in files if f['type'] == 'image'), 'pdfs': sum(1 for f in files if f['type'] == 'pdf')}

    def _home_file(self, raw: str, suffixes: set[str]) -> Path:
        root = self.FS_ROOT.resolve()
        p = Path(raw).expanduser().resolve()
        if root not in p.parents or not p.is_file() or p.suffix.lower() not in suffixes:
            raise ServiceError(f'not importable: {raw}')
        return p

    def fs_thumb(self, path: str, width: int) -> Path:
        """Preview of a page image on disk (the order dialog of the in-app file browser), cached by path+mtime."""
        p = self._home_file(path, pdfio.IMAGE_SUFFIXES)
        width = max(80, min(600, int(width)))
        st = p.stat()
        key = hashlib.sha256(f'{p}|{st.st_mtime_ns}|{st.st_size}|{width}'.encode()).hexdigest()[:24]
        try:
            return pdfio.image_thumbnail(p, width, self.lib.root / '.cache' / 'fs-thumbs' / f'{key}.jpg')
        except Exception as exc:                     # unreadable/unsupported image: no preview, not a server error
            raise ServiceError(f'no preview: {exc}')

    def import_images(self, paths: list[str], title: str, order: str = 'name', base: str | None = None) -> dict:
        """Page images from disk -> one book, in the order given (the reviewer previewed and chose it; `order`
        records how). Names are recorded relative to `base` (the folder) when given. Originals are copied."""
        files = [self._home_file(x, pdfio.IMAGE_SUFFIXES) for x in paths or []]
        if not files:
            raise ServiceError('no images')
        root = Path(base).expanduser().resolve() if base else None
        names = [str(f.relative_to(root)) if root and root in f.parents else f.name for f in files]
        staged = []
        for f in files:
            dst = self.lib.staging() / f'{uuid.uuid4().hex[:8]}-{f.name}'
            shutil.copy2(f, dst)
            staged.append(dst)
        return self.create_from_images(staged, title or (root.name if root else 'Σαρωμένο βιβλίο'), names, order)

    def import_paths(self, paths: list[str], title: str | None = None) -> list[dict]:
        """Import PDFs (one book each) and/or a folder: its PDFs become books, its page images one book.
        Source files are copied, never moved or modified."""
        out = self._import_paths(paths, title)
        if not out:
            raise ServiceError('no PDF or page images found')
        return out

    def _import_paths(self, paths: list[str], title: str | None = None) -> list[dict]:
        root = self.FS_ROOT.resolve()
        out = []
        for raw in paths:
            p = Path(raw).expanduser().resolve()
            if root not in p.parents or not p.exists():
                raise ServiceError(f'not importable: {raw}')
            if p.is_dir():
                kids = sorted(x for x in p.iterdir() if not x.name.startswith('.'))
                out += self._import_paths([str(x) for x in kids if x.suffix.lower() == '.pdf'])
                imgs = [x for x in kids if x.suffix.lower() in pdfio.IMAGE_SUFFIXES]
                if imgs:
                    staged = []
                    for x in imgs:
                        dst = self.lib.staging() / f'{uuid.uuid4().hex[:8]}-{x.name}'
                        shutil.copy2(x, dst)
                        staged.append(dst)
                    out.append(self.create_from_images(staged, title or p.name, [x.name for x in imgs]))
            elif p.suffix.lower() == '.pdf':
                dst = self.lib.staging() / f'{uuid.uuid4().hex[:8]}-{p.name}'
                shutil.copy2(p, dst)
                out.append(self.create_from_upload(dst, p.name, title if len(paths) == 1 else None))
        return out

    def update_settings(self, book_id: str, changes: dict) -> dict:
        b = self._book(book_id)
        out = b.update_settings(**changes)
        if 'calibrate_with' in changes:              # calibration priors feed the projection: show their effect now
            self._reproject(b, 'Βαθμονόμηση από άλλα βιβλία')
        return out

    def trash(self, book_id: str) -> dict:
        if self.jobs.busy(book_id):
            raise ServiceError('stop the running job first')
        return {'trash_name': self.lib.trash(book_id)}

    def restore(self, trash_name: str) -> dict:
        return self.lib.restore(trash_name).summary()

    # ---------------------------------------------------------------- pages
    def remove_pages(self, book_id: str, pages: list, actor='reviewer', restore: bool = False) -> dict:
        """Leave pages out of the book (or bring them back). An undoable event: the source PDF and raw OCR stay
        untouched, so a restored page reappears without re-processing."""
        b = self._book(book_id)
        n = b.page_count()
        try:
            want = sorted({int(p) for p in pages or []})
        except (TypeError, ValueError):
            raise ServiceError('pages: a list of page numbers')
        if not want or want[0] < 1 or want[-1] > n:
            raise ServiceError(f'pages must be within 1..{n}')
        removed = set(b.removed_pages())
        todo = [p for p in want if (p in removed) == restore]
        if not restore and len(removed | set(todo)) >= n:
            raise ServiceError('a book keeps at least one page; move the whole book to the trash instead')
        if not todo:
            return {'events': [], 'pages': []}
        out = b.events.append({'type': 'page_restore' if restore else 'page_remove', 'pages': todo}, actor=actor)
        label = ', '.join(map(str, todo)) if len(todo) <= 6 else f'{len(todo)} σελίδες'
        self._reproject(b, f'{"Επαναφορά" if restore else "Αφαίρεση"} σελ. {label}')
        return {'events': out, 'pages': todo}

    # ---------------------------------------------------------------- processing
    def process(self, book_id: str, pages: str | None) -> dict:
        b = self._book(book_id)
        spec = (pages or '').strip() or None
        try:
            want = pdfio.parse_pages(spec, b.page_count())
        except ValueError as exc:
            raise ServiceError(str(exc))
        removed = set(b.removed_pages())
        if removed & set(want):                       # removed pages are never processed: restore them first
            want = [p for p in want if p not in removed]
            if not want:
                raise ServiceError('all requested pages are removed from the book; restore them first')
            spec = ','.join(map(str, want))
        n = len(want)
        args = ['process', '--book', str(b.dir)] + (['--pages', spec] if spec else [])
        return self.jobs.submit(b.id, 'process', args, f'Επεξεργασία {n} σελίδων' if n != 1 else f'Επεξεργασία σελ. {spec}')

    def cancel(self, job_id: str) -> dict:
        return self.jobs.cancel(job_id)

    def remove_job(self, job_id: str) -> dict:
        """Delete a finished job from the task list (and its log). A running/queued job must be stopped first."""
        try:
            return self.jobs.remove(job_id)
        except ValueError as exc:
            raise ServiceError(str(exc))

    def clear_jobs(self, book_id: str | None = None) -> dict:
        """Delete every finished job (of one book, or all) from the task list."""
        return {'removed': self.jobs.clear(book_id)}

