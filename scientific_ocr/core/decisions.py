"""Vocabulary of human input (event sourcing): which events can be undone and which decisions set or confirm
a segment's content. Domain policy, shared by the event store, the command overlay and the services."""

UNDOABLE = {'decision', 'rule_add', 'rule_remove', 'region_add', 'region_remove', 'block_hide', 'block_show',
            'page_profile', 'merge_add', 'merge_remove', 'group_add', 'group_remove', 'suggestion_dismiss',
            'class_accept', 'entity_confirm', 'entity_assign', 'entity_remove', 'page_remove', 'page_restore'}
# Decisions about a segment's CONTENT. "Value" actions set the content; `approve` only confirms the current
# version; `revert` returns the segment to the machine result.
VALUE_ACTIONS = {'approve_candidate', 'replace_text', 'replace_latex', 'mark_image_only', 'mark_unreadable', 'revert'}
CONTENT_ACTIONS = VALUE_ACTIONS | {'approve'}

VALUE_DECISIONS = VALUE_ACTIONS - {'revert'}          # decisions whose value IS the content while in force

# Events whose effect needs new OCR, done in the rebuild (reviewer regions are OCR'd there): when rebuilds are
# banked, the reviewer is told the new reading appears only after the book is rebuilt.
NEEDS_OCR = {'region_add'}

# Required fields per event type (validated when an event is appended; extra fields are allowed).
EVENT_FIELDS = {
    'decision': {'block_id', 'action'}, 'rule_add': {'rule'}, 'rule_remove': {'rule_id'},
    'region_add': {'region'}, 'region_remove': {'region_id'}, 'block_hide': {'block_id'}, 'block_show': {'block_id'},
    'page_profile': {'page', 'profile'}, 'merge_add': {'merge'}, 'merge_remove': {'merge_id'},
    'group_add': {'group'}, 'group_remove': {'group_id'}, 'suggestion_dismiss': {'key'},
    'class_accept': {'reason', 'n', 'sample'}, 'entity_confirm': {'key', 'entity_type'},
    'entity_assign': {'key', 'members', 'entity_type'}, 'entity_remove': {'key'},
    'page_remove': {'pages'}, 'page_restore': {'pages'},
    'undo': {'targets'}, 'redo': {'targets'},
}
# Required fields of a decision per action (one of a tuple-set suffices).
DECISION_FIELDS = {
    'replace_text': {'value'}, 'replace_latex': {'value'}, 'approve_candidate': {'engine'},
    'move_after_block': ({'after'}, {'before'}), 'associate_caption': {'figure_id'}, 'link_reference': {'n'},
    'set_caption_role': {'role'}, 'set_heading_level': {'level'}, 'set_output': {'include'},
    'set_flow': {'flow'}, 'set_continuation': {'continues'},
}


def event_errors(ev: dict) -> list[str]:
    """Schema problems of one event (empty = valid)."""
    t = ev.get('type')
    if t not in EVENT_FIELDS:
        return [f'unknown event type {t!r}']
    errs = [f'{t}: missing {f}' for f in sorted(EVENT_FIELDS[t] - set(ev))]
    if t == 'decision' and not errs:
        need = DECISION_FIELDS.get(ev['action'])
        if isinstance(need, tuple):
            if not any(n <= set(ev) for n in need):
                errs.append(f"decision {ev['action']}: needs one of {[sorted(n) for n in need]}")
        elif need:
            errs += [f"decision {ev['action']}: missing {f}" for f in sorted(need - set(ev))]
    return errs


# ---------------------------------------------------------------- scope: page or book
# A PAGE-LOCAL action changes only what the page-local stages compute (composition, specialised models, decisions
# on its segments, reading order, captions on the same page, regions, merges, notation): its page can be rebuilt
# alone, at once. Anything else is BOOK-WIDE (hierarchy, groups, entities, furniture, rules, references, bulk
# acceptance, page removal…) and waits for a full rebuild. Unknown kinds are book-wide (the safe default).
PAGE_LOCAL_ACTIONS = {'approve', 'approve_candidate', 'replace_text', 'replace_latex', 'mark_image_only',
                      'mark_unreadable', 'revert', 'report_wrong', 'set_output', 'move_after_block',
                      'associate_caption', 'detach_caption', 'set_caption_role'}
PAGE_LOCAL_EVENTS = {'region_add', 'region_remove', 'block_hide', 'block_show', 'merge_add', 'merge_remove'}


def page_of(block_id: str | None) -> int | None:
    """Page of a segment id (core.model.block_id: 'p<page>_<hash>')."""
    if not block_id or not block_id.startswith('p') or '_' not in block_id:
        return None
    head = block_id[1:block_id.index('_')]
    return int(head) if head.isdigit() else None


def event_pages(ev: dict, by_seq: dict[int, dict], regions: dict[str, int], merges: dict[str, int]) -> set[int] | None:
    """Pages a page-local event affects, or None when it is book-wide. `regions`/`merges`: id -> page (to place
    the removal of a region or merge). Undo/redo take the scope of their targets."""
    t = ev.get('type')
    if t in ('undo', 'redo'):
        out: set[int] = set()
        for s in ev.get('targets') or []:
            sub = event_pages(by_seq[s], by_seq, regions, merges) if s in by_seq else None
            if sub is None:
                return None
            out |= sub
        return out
    if t == 'decision':
        p = page_of(ev.get('block_id'))
        return {p} if ev.get('action') in PAGE_LOCAL_ACTIONS and p else None
    if t not in PAGE_LOCAL_EVENTS:
        return None
    if t == 'region_add':
        return {int(ev['region']['page'])}
    if t == 'region_remove':
        p = regions.get(ev['region_id'])
        return {p} if p else None
    if t == 'merge_add':
        return {int(ev['merge']['page'])}
    if t == 'merge_remove':
        p = merges.get(ev['merge_id'])
        return {p} if p else None
    p = page_of(ev.get('block_id'))                    # block_hide / block_show
    return {p} if p else None
