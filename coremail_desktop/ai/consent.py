"""加密邮件与 AI (K3): whether a mail's body may go to the AI service now. The one rule the
backend, 问鸿雁's read_mail tool, 转为任务 and the 理解层 all use. The company sets no limit
(user, 2026-10-21): the user agrees for one mail, or once for all in 设置 → AI 助手."""


def access(state, allowed, auto_ok):
    """-> 'ok', 'locked' (the certificate is not unlocked: nothing to read) or 'ask' (encrypted:
    the user decides first). state: the mail's encryption_state ('none' / 'decrypted' /
    'locked'); allowed: the user agreed for this mail now; auto_ok(): allowed once for all
    and not excluded (asked only for decrypted mail)."""
    if state == 'locked':
        return 'locked'
    if state == 'decrypted' and not allowed and not auto_ok():
        return 'ask'
    return 'ok'
