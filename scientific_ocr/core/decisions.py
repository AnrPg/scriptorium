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
