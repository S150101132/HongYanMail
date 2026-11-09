"""会话: mail whose subjects match once reply / forward prefixes are gone, from every folder
but trash, spam and drafts. The one place that says which mail is one conversation (K2):
the list's conversation view, its counts, 关联任务 and 在等别人 all ask it. The page groups
its list by the same rule (threadKey in app.js; a test keeps the two equal)."""
import re

SUBJECT_PREFIX = re.compile(r'^\s*(?:re|fwd?|fw|回复|转发|答复)\s*(?:\[\d+\])?\s*[:：]\s*', re.I)
THREAD_SKIP = ('trash', 'spam', 'virus', 'drafts')  # Folders a conversation does not reach into.
THREAD_MIN = 6  # Characters: a shorter subject ('OK', '请假') would join unrelated mail.


def thread_subject(subject):
    text, prev = str(subject or '').strip(), None
    while prev != text:
        prev, text = text, SUBJECT_PREFIX.sub('', text).strip()
    return text


def thread_key(subject):
    return ' '.join(thread_subject(subject).split()).lower()


def conversation_key(subject):
    """The conversation a subject belongs to; '' when too short to tell, so the mail is a
    conversation of its own."""
    key = thread_key(subject)
    return key if len(key) >= THREAD_MIN else ''


def skip_folders(folders):
    """Ids of the folders a conversation does not reach into."""
    return {f['id'] for f in folders or () if f.get('role') in THREAD_SKIP}
