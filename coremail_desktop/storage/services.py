"""Local persistence is initialized on use, independently of mailbox login."""
from pathlib import Path
from coremail_desktop.drafts.store import DraftStore
from coremail_desktop.outbox.journal import SendJournal
from coremail_desktop.outbox.service import OutboxService
from coremail_desktop.errors import AppError


def local_services(adapter, directory, prepare_encrypted=None):
    directory=Path(directory)
    store=None
    journal=None
    outbox=None

    def drafts():
        nonlocal store
        if store is None: store=DraftStore(directory/'drafts')
        return store

    def sending():
        nonlocal journal,outbox
        if outbox is None:
            candidate=SendJournal(directory/'outbox')
            service=OutboxService(adapter,candidate,prepare_encrypted)
            journal,outbox=candidate,service
        return outbox

    def save_draft(draft):
        drafts().save(draft)
        return draft.revision

    def send(draft):
        local_id=sending().request_send(draft)
        return journal.load(local_id)

    def guarded(function):
        def call(**params):
            try: return function(**params)
            except PermissionError:
                raise AppError('LOCAL_STORAGE_UNAVAILABLE') from None
        return call

    return {name:guarded(function) for name,function in {
        'save_draft':save_draft,
        'list_drafts':lambda:drafts().list_summaries(),
        'restore_draft':lambda local_id:drafts().load(local_id),
        'delete_draft':lambda local_id:drafts().delete(local_id),
        'request_send':send,
        'recover_pending':lambda:sending().recover_pending(),
        'pending_for_review':lambda:sending().recover_pending(),
        'reconcile':lambda local_id:sending().reconcile(local_id),
        'review_jobs':lambda:sending().review_jobs(),
        'dismiss_job':lambda local_id:sending().dismiss(local_id),
    }.items()}
