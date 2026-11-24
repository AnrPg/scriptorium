"""Application facade. The HTTP adapter and the CLI call App; nothing here knows about HTTP.

Every human action becomes an event (adapters.event_store) followed by a coalesced re-projection job, so the
pipeline never waits for review and review can happen at any time after (or during) processing. The use cases
live in application.use_cases, one module per responsibility; App composes them into one entry point.
"""
from __future__ import annotations

from .use_cases.base import PROFILES, ServiceError  # noqa: F401  (public names of the application layer)
from .use_cases.entities import EntityService
from .use_cases.excursus import ExcursusService
from .use_cases.export import ExportService
from .use_cases.hierarchy import HierarchyService
from .use_cases.library import LibraryService
from .use_cases.review import ReviewService
from .use_cases.structure import StructureService
from .use_cases.triage import TriageService
from .use_cases.views import ViewsService


class App(LibraryService, ViewsService, ReviewService, TriageService, StructureService, HierarchyService, EntityService,
          ExcursusService, ExportService):
    """The application: library, read views, review, triage, structure, entity and export use cases."""
