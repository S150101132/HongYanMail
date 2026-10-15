"""Fixed schema: freeform URLs, payloads, identities and exception text never pass."""
import re

OPERATIONS = frozenset({'login','list_folders','list_messages','fetch_mime','save_draft','request_send','reconcile','decrypt','encrypt','startup','shutdown'})
def sanitize_event(event):
    result = {}
    if event.get('operation') in OPERATIONS: result['operation'] = event['operation']
    for name in ('operation_id','error_code','capability'):
        value = event.get(name)
        if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value): result[name] = value
    elapsed = event.get('elapsed_ms')
    if type(elapsed) in (int,float) and 0 <= elapsed <= 86400000: result['elapsed_ms'] = elapsed
    return result
