"""Fixed, non-echoing user messages for error codes.

Only the code selects text from this table. Exception text, server responses,
URLs, addresses and message content never reach the interface, so a failure can
never display a value the user did not just type.
"""

MESSAGES = {
    'NETWORK_ERROR': '网络连接失败，请检查网络后重试。',
    'TLS_ERROR': '服务器 TLS 证书验证失败，已停止连接。',
    'AUTH_REJECTED': '账号或密码被服务器拒绝。',
    'AUTH_CHALLENGE': '服务器要求额外身份验证，当前预览版暂不支持。',
    'NOT_AUTHENTICATED': '尚未登录，请先登录再执行此操作。',
    'SESSION_EXPIRED': '会话已失效，请重新登录。',
    'RESPONSE_INCOMPATIBLE': '服务器返回的结构与当前预览版不兼容，已停止处理。',
    'PARSE_FAILED': '邮件正文无法解析，已保留摘要。',
    'MESSAGE_TOO_LARGE': '邮件超过当前大小限制，已保留摘要并停止解析。',
    'DRAFT_SAVE_FAILED': '本机草稿保存失败：内容仍保留在窗口中，请勿关闭。',
    'STALE_REVISION': '草稿版本已变化，请保留当前内容并重新保存。',
    'SEND_REJECTED': '服务器明确拒绝了这封邮件。',
    'SEND_OUTCOME_UNKNOWN': '发送结果待确认：请先核对已发送，本应用不会自动重发。',
    'CERT_LOCKED': '此邮件需要证书：请先解锁本机证书。',
    'CERT_UNLOCK_FAILED': '证书密码不正确或文件已损坏。',
    'CERT_FILE_UNAVAILABLE': '无法读取所选证书文件。',
    'CERT_FORMAT_UNSUPPORTED': '证书格式不受支持，未做任何猜测处理。',
    'CERT_KEY_MISMATCH': '证书与私钥不匹配。',
    'CERT_PRIVATE_KEY_MISSING': '证书文件不包含私钥。',
    'CERT_ALGORITHM_UNSUPPORTED': '证书算法不在当前支持的范围内。',
    'DECRYPT_FAILED': '解密失败：私钥与该加密邮件不匹配或内容已损坏。',
    'UNSUPPORTED_ENCRYPTION_FORMAT': '该加密格式尚未验证支持，未按明文处理。',
    'ENCRYPTED_ALGORITHM_UNSUPPORTED': '这封邮件使用当前版本还不支持的加密算法（实测为 3DES），已如实报告，不会显示为空正文。',
    'RECIPIENT_CERT_MISSING': '缺少收件人证书，已阻止加密发送，不会降级为明文。',
    'RECIPIENT_CERT_EXPIRED': '收件人证书已过期，已阻止加密发送。',
    'RECIPIENT_CERT_USAGE': '收件人证书用途不允许加密，已阻止发送。',
    'RECIPIENT_CERT_UNTRUSTED': '收件人证书尚未由你明确信任，已阻止发送。',
    'ENCRYPTION_REQUIRED': '需要可用的收件人证书及已验证的加密提交接口，已阻止发送。',
    'ENCRYPTED_SUBMISSION_UNVERIFIED': '证书加密提交接口尚未完成互通验证，已阻止发送。',
    'ENCRYPT_FAILED': '无法生成加密载荷，已阻止发送。',
    'CERT_FILE_TOO_LARGE': '证书文件超过大小限制，已停止处理。',
    'INVALID_ENCRYPTED_MESSAGE': '加密邮件内容不完整或已损坏，无法解密。',
    'NESTED_CRYPTO_UNSUPPORTED': '该邮件包含嵌套加密，当前预览版不支持。',
    'INVALID_RECIPIENT': '请填写有效的完整邮箱地址。',
    'RECIPIENT_REQUIRED': '请填写至少一个有效收件人。',
    'INVALID_SUBJECT': '主题不能包含换行。',
    'INVALID_ENCRYPTED_PAYLOAD': '加密载荷为空或格式无效，已阻止发送。',
    'REPLY_CONTRACT_UNVERIFIED': '回复引用头协议尚未完成真实验证，已阻止提交。',
    'INVALID_RESPONSE': '服务器响应结构无法识别，已停止处理。',
    'RESPONSE_TOO_LARGE': '服务器响应超过大小限制，已停止处理。',
    'RPC_REJECTED': '服务器拒绝了该请求。',
    'RPC_NOT_ALLOWED': '当前预览版不允许调用该服务器接口。',
    'MESSAGE_UNAVAILABLE': '这封邮件的内容在服务器上不可用。',
    'INVALID_PAGE': '分页参数无效，已停止加载。',
    'UNSAFE_ORIGIN': '检测到跨来源跳转，已停止以保护凭据。',
    'INVALID_LOCAL_ID': '本机草稿标识无效。',
    'PROTECTION_FAILED': '本机加密保护失败，未保存明文。',
    'UNPROTECT_FAILED': '本机加密记录无法解开，已保留原记录。',
    'DPAPI_UNAVAILABLE': '当前 Windows 用户级保护不可用。',
    'SESSION_CLOSED': '会话已关闭。',
    'OPERATION_NOT_ALLOWED': '当前预览版不支持该操作。',
    'DEMO_WRITE_DISABLED': '演示模式不保存或发送邮件。',
}

GENERIC = '操作未完成。请检查连接或稍后重试，本应用不会自动重发邮件。'


def user_message(code) -> str:
    if isinstance(code, str):
        return MESSAGES.get(code, GENERIC)
    return GENERIC
