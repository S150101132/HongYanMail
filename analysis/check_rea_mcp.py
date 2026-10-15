"""Smoke check the local REA MCP server, without opening or executing targets."""
import json, subprocess, threading, queue
from pathlib import Path

workspace = Path(__file__).resolve().parent.parent
launcher = workspace / '.npm-cache/_npx/af752b90495482d7/node_modules/rea-agents/scripts/rea.mjs'
messages = queue.Queue()
proc = subprocess.Popen(['node', str(launcher), 'mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', cwd=workspace)
def read_messages():
    for line in proc.stdout:
        try:
            messages.put(json.loads(line))
        except json.JSONDecodeError:
            continue
threading.Thread(target=read_messages, daemon=True).start()
def send(message):
    proc.stdin.write(json.dumps(message)+'\n')
    proc.stdin.flush()
def response(request_id):
    while True:
        message = messages.get(timeout=30)
        if message.get('id') == request_id:
            if 'error' in message:
                raise RuntimeError(message['error'])
            return message['result']
try:
    send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'local-rea-smoke-check','version':'1'}}})
    initialized = response(1)
    send({'jsonrpc':'2.0','method':'notifications/initialized'})
    send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
    listing = response(2)
    result = {'server':initialized.get('serverInfo'), 'protocol_version':initialized.get('protocolVersion'), 'tool_count':len(listing['tools']), 'tool_names':[t['name'] for t in listing['tools']], 'next_cursor':listing.get('nextCursor'), 'scope':'fresh standalone MCP connection; not the active Codex tool registry'}
    (workspace/'analysis/rea-mcp-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='tool_names'},ensure_ascii=False))
finally:
    proc.terminate()
    proc.wait(timeout=10)
