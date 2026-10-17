"""Recall and delivery-status codes -> fixed Chinese text and a display class.

The codes and wording follow the deployment's own webmail tables (mail_rcl_state_*,
mail_sent_state_*). An unknown code is shown as unknown, never as success.
"""

# mbox:recallMessage2 per-recipient result, and rclResult in the delivery history.
RECALL_STATES = {
    0: ('不支持撤回', 'bad'), 1: ('正在撤回', 'wait'), 2: ('已撤回', 'ok'), 3: ('已撤回（重复操作）', 'ok'),
    4: ('撤回失败：邮件不存在', 'bad'), 5: ('撤回失败：对方已阅读', 'bad'), 6: ('撤回失败：对方已用 POP 收取', 'bad'),
    7: ('撤回失败：邮件已被对方转发出去', 'bad'), 8: ('撤回失败：邮件已过期', 'bad'), 9: ('撤回失败：系统不允许', 'bad'),
    14: ('撤回失败：收件人不存在', 'bad'), 15: ('撤回出错', 'bad'), 16: ('不支持撤回', 'bad'),
}
RECALLED = {2, 3}

# user:listDeliveryHistory 'result' per recipient.
DELIVERY_STATES = {
    0: ('已成功处理', 'ok'), 1: ('重新投递中', 'wait'), 2: ('发送失败', 'bad'), 3: ('对方邮箱拒收', 'bad'),
    4: ('投递不成功', 'bad'), 5: ('邮件被丢弃', 'bad'), 6: ('对方已接收，但被判为病毒邮件', 'bad'),
    7: ('已投递到对方邮箱', 'ok'), 8: ('已被自动转发', 'ok'), 9: ('对方已阅读', 'read'),
    12: ('投递已取消', 'bad'), 13: ('已投递到隔离区', 'bad'), 15: ('网关审核中', 'wait'), 16: ('被网关拦截', 'bad'),
    100: ('已投递到对方邮箱', 'ok'), 101: ('投递出错，重新投递中', 'wait'), 110: ('重复投递', 'ok'),
    200: ('已到达对方服务器', 'ok'), 201: ('对方服务器未响应，重新投递中', 'wait'), 204: ('被对方服务器退回', 'bad'),
    412: ('审核邮件已被撤回', 'bad'), 600: ('已通过审核', 'ok'), 601: ('待审核', 'wait'),
    602: ('未通过审核', 'bad'), 603: ('未通过审核', 'bad'),
    700: ('已撤回', 'ok'), 709: ('撤回失败：对方已阅读', 'bad'), 800: ('已撤回', 'ok'), 809: ('撤回失败：对方已阅读', 'bad'),
}


def recall_state(code):
    text, kind = RECALL_STATES.get(code, ('撤回结果未知', 'wait')) if type(code) is int else ('撤回结果未知', 'wait')
    return {'code': code if type(code) is int else None, 'text': text, 'kind': kind, 'recalled': code in RECALLED}


def delivery_state(result, recall=None):
    """A recall result, when present, describes the recipient's copy better than the delivery result."""
    if type(recall) is int and recall:
        state = recall_state(recall)
        return {'text': state['text'], 'kind': state['kind']}
    if type(result) is not int:
        return {'text': '投递状态未知', 'kind': 'wait'}
    text, kind = DELIVERY_STATES.get(result) or DELIVERY_STATES.get(result % 100, ('投递状态未知（代码 %d）' % result, 'wait'))
    return {'text': text, 'kind': kind}
