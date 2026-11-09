"""发后跟进 (F1): what follows once the server has taken a reply or forward, on every path
(sent now, after the undo delay, from the offline outbox). The draft remembers which mail it
answers (source_mid) and the assistant plan that drafted it (assist_plan); the page only
shows the result. Never before the server takes it: a queued mail waits for the outbox."""
import logging

TASKS_SHOWN = 3


def after_send(draft, answered, tasks_for, progress):
    """-> {'how', 'answered', 'tasks', 'plan'} for a draft the server took.
    answered(mid, how) -> ids of 要我做 cards marked 已回复 / 已转发 (none while 理解层 is off);
    tasks_for(mid) -> open tasks linked to mid or its conversation;
    progress(plan) -> the assistant's progress line to confirm, or None (offered once).
    A failing step leaves only its part empty: the mail went out either way."""
    mid = draft.source_mid
    how = ('forward' if draft.mode == 'forward' else 'reply') if mid else ''
    after = {'how': how, 'answered': None, 'tasks': [], 'plan': None}
    if mid:
        if _quiet(answered, mid, how):
            after['answered'] = {'mid': mid, 'how': how}
        after['tasks'] = [{'id': t['id'], 'content': t['content']} for t in (_quiet(tasks_for, mid) or [])[:TASKS_SHOWN]]
    if draft.assist_plan:
        after['plan'] = _quiet(progress, draft.assist_plan)
    return after


def _quiet(step, *args):
    try:
        return step(*args)
    except Exception:  # An expired plan, a task list not set up, a bug: the send itself succeeded.
        logging.getLogger('hongyan.aftersend').warning('after-send step failed', exc_info=True)
        return None
