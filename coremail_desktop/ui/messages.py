"""Fixed, non-echoing user messages for error codes.

Only the code selects text from this table. Exception text, server responses,
URLs, addresses and message content never reach the interface, so a failure can
never display a value the user did not just type.
"""

MESSAGES = {
    'LOCAL_STORAGE_UNAVAILABLE': '无法访问本机草稿或发送记录。请关闭程序，从文件资源管理器重新启动鸿雁；本机保存和发送需要该目录的访问权限。',
    'UNSUPPORTED_LOGIN': '服务器未返回受支持的登录表单，请确认服务器地址，或稍后重试。',
    'ATTACHMENT_UNAVAILABLE': '无法读取所选附件，请检查文件和权限。',
    'INVALID_ATTACHMENT': '附件名称或类型无效，未上传。',
    'ATTACHMENT_TOO_LARGE': '最多 20 个附件，单个不超过 10 MiB，总计不超过 20 MiB。',
    'ATTACHMENT_UPLOAD_FAILED': '附件上传未完整确认，邮件未提交。请检查后重试。',
    'INVALID_SEARCH': '请输入 1 至 1000 个字符的搜索条件。',
    'INVALID_MAIL_ACTION': '邮件操作参数无效，未提交修改。',
    'MAIL_UPDATE_UNCONFIRMED': '邮件状态更新结果未确认，请刷新核对后再操作。',
    'REPLY_SOURCE_REQUIRED': '这份回复缺少原邮件来源，请从原邮件重新发起回复。',
    'REPLY_SOURCE_MISMATCH': '原邮件与草稿引用不一致，请从原邮件重新发起回复。',
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
    'ENCRYPTED_SUBMISSION_UNVERIFIED': '加密邮件不能走明文发送通道，已阻止发送。',
    'WRITE_DENIED': '没有写入该位置的权限（可能被本机安全策略阻止），请换一个文件夹，例如鸿雁数据目录旁的文件夹或 D 盘。',
    'OWN_CERT_MISSING': '公司通讯录中没有你自己的加密证书，已发送的加密邮件将无法再打开，已阻止发送。',
    'ENCRYPT_FAILED': '无法生成加密载荷，已阻止发送。',
    'SIGN_FAILED': '无法生成数字签名，已阻止发送，不会改为不签名发送。',
    'CERT_SIGN_USAGE': '个人证书的用途不允许数字签名，请关闭“签名”后发送。',
    'INVALID_SIGNED_MESSAGE': '邮件签名结构无法识别，无法验证签名。',
    'SIGN_CERT_ADDRESS_MISMATCH': '个人证书上的邮箱与发件地址不一致，请关闭“签名”或改用证书上的地址发送。',
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
    'INVALID_SCHEDULE': '定时发送时间需在 1 分钟之后、一年以内，请重新选择。',
    'SCHEDULE_ENCRYPTED_UNSUPPORTED': '服务器不接受加密邮件的定时发送。可以立即加密发送，或取消定时；鸿雁不会改成明文发送。',
    'SCHEDULE_ZONE_UNSUPPORTED': '无法确定邮箱账号设置的时区，为避免发错时间，已停止定时发送。',
    'SCHEDULE_UNCONFIRMED': '服务器没有确认已取消定时发送，请刷新草稿箱核对。',
    'NOT_SCHEDULED': '这封邮件不是定时邮件，或已经发出。',
    'RECALL_UNSUPPORTED': '服务器不支持撤回这封邮件。',
    'RECALL_EXPIRED': '邮件已超过可撤回的期限。',
    'RECALL_NOT_FOUND': '服务器上找不到这封邮件，无法撤回。',
    'RECALL_UNCONFIRMED': '服务器没有返回撤回结果，请稍后在投递状态中核对，不要当作已撤回。',
    'RECALL_LATER': '撤回需要等待更长时间，请稍后再试。',
    'DELIVERY_STATUS_EXPIRED': '投递记录只保留一段时间，这封邮件的记录已过期。',
}

GENERIC = '操作未完成。请检查连接或稍后重试，本应用不会自动重发邮件。'


def user_message(code) -> str:
    if isinstance(code, str):
        return MESSAGES.get(code, GENERIC)
    return GENERIC


def login_message(code, category='') -> str:
    if code == 'INTERNAL_ERROR':
        stages = {'initialize':'本机初始化', 'login':'登录连接', 'operation':'登录处理'}
        parts = category.split(':') if isinstance(category,str) else []
        kinds = {'PermissionError','OSError','TypeError','ValueError','AttributeError','ImportError','RuntimeError','Exception'}
        if len(parts)==2 and parts[0] in stages and parts[1] in kinds:
            return f'{stages[parts[0]]}失败，请重试。诊断码：{parts[0]}/{parts[1]}（不含密码）。'
        return '登录未完成，请重试。诊断码：INTERNAL_ERROR。'
    if code in MESSAGES: return MESSAGES[code]
    return '登录未完成，服务器返回了尚未识别的错误。请稍后重试。'
