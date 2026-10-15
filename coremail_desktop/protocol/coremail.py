"""Typed production boundary around the independently verified HTTPS transport."""
from email import policy
from email.parser import BytesParser
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request
from probe.coremail_transport import CoremailSession, CoremailError, _origin, decode_extended_json
from coremail_desktop.models import Folder, MessageSummary
from coremail_desktop.errors import AppError
from coremail_desktop.compose.recipients import normalize_recipients

class DesktopSession(CoremailSession):
    """Retain the verified login/redirect parser, classify production failures."""
    def _fetch(self,request):
        if _origin(request.full_url) != self._origin: raise AppError('UNSAFE_ORIGIN')
        code = 'NETWORK_ERROR'
        try:
            with self._opener.open(request,timeout=30) as response:
                final_url = response.geturl()
                if _origin(final_url) != self._origin: raise AppError('UNSAFE_ORIGIN')
                raw = response.read(64 * 1024 * 1024 + 1)
                if len(raw) > 64 * 1024 * 1024: raise AppError('RESPONSE_TOO_LARGE')
                return raw,final_url
        except AppError:
            raise
        except HTTPError as error:
            code = 'SESSION_EXPIRED' if error.code in (401,403) else 'NETWORK_ERROR'
        except URLError as error:
            code = 'TLS_ERROR' if isinstance(error.reason,ssl.SSLError) else 'NETWORK_ERROR'
        except ssl.SSLError:
            code = 'TLS_ERROR'
        except CoremailError as error:
            code = error.code
        except (OSError,ValueError):
            pass
        raise AppError(code)
    def call(self,func,payload):
        if func not in self.ALLOWED: raise AppError('RPC_NOT_ALLOWED')
        request = Request(self._rpc_url(func),data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers={'Accept':'text/x-json','Content-Type':'text/x-json; tz="Asia/Riyadh"'})
        raw,_ = self._fetch(request)
        try: result = decode_extended_json(raw.decode('utf-8-sig'))
        except (UnicodeError,CoremailError): raise AppError('INVALID_RESPONSE') from None
        if not isinstance(result,dict): raise AppError('INVALID_RESPONSE')
        code = result.get('code')
        if code == 'FA_UNAUTHORIZED': raise AppError('SESSION_EXPIRED')
        if code in ('FA_NEED_SECOND_AUTH','FA_NEED_VERIFY_CODE','FA_NEED_BIND_DEVICE'): raise AppError('AUTH_CHALLENGE')
        if code != 'S_OK': raise AppError('RPC_REJECTED')
        return result

class CoremailAdapter:
    supports_reply_headers = False
    supports_encrypted_submission = False
    def __init__(self, session_factory=DesktopSession):
        self._session_factory = session_factory
        self._session = None
        self._identities = set()
    def login(self, account, password):
        self.close()
        self._session = self._session_factory(account.origin)
        try:
            self._session.login(account.login_uid, password)
            self.list_folders()  # A sid alone is not authenticated evidence.
        except CoremailError as error:
            self.close()
            code = {'AUTH_INTERACTION_REQUIRED':'AUTH_CHALLENGE','NOT_AUTHENTICATED':'AUTH_REJECTED','TRANSPORT_ERROR':'NETWORK_ERROR'}.get(error.code,error.code)
            raise AppError(code) from None
        except Exception:
            self.close()
            raise
    def _call(self, operation, params, read=False):
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        for attempt in range(3 if read else 1):
            try: return self._session.call(operation, params)
            except (CoremailError,AppError) as error:
                if error.code in ('TRANSPORT_ERROR','NETWORK_ERROR') and read and attempt < 2: continue
                raise AppError(error.code) from None
    def list_folders(self):
        value = self._call('mbox:getAllFolders', {}, True).get('var')
        if not isinstance(value, list): raise AppError('INVALID_RESPONSE')
        result = []
        for item in value:
            if not isinstance(item,dict) or 'id' not in item: raise AppError('INVALID_RESPONSE')
            role = item.get('role') or item.get('type')
            role = role if role in ('inbox','sent','drafts','trash','spam') else None
            result.append(Folder(str(item['id']),str(item.get('name','')),role, item.get('stats',{})))
        return result
    def list_messages(self, folder_id, offset, limit=50):
        if offset < 0 or limit < 1 or limit > 100: raise AppError('INVALID_PAGE')
        value = self._call('mbox:listMessages', {'fid':int(folder_id) if str(folder_id).isdigit() else folder_id, 'start':offset,'limit':limit,'mode':'count','order':'date','desc':True,'returnTotal':True,'returnTag':True,'summaryWindowSize':0},True).get('var')
        if isinstance(value,dict): value = value.get('messages')
        if not isinstance(value,list): raise AppError('INVALID_RESPONSE')
        result = []
        for item in value:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str): raise AppError('INVALID_RESPONSE')
            sender = item.get('from','')
            if isinstance(sender,list): sender = ', '.join(str(x) for x in sender)
            result.append(MessageSummary(item['id'],str(folder_id),str(item.get('subject','')),str(sender),str(item.get('date',item.get('receivedDate',''))),item.get('flags',{})))
        return result
    def fetch_mime(self, message_id):
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        for attempt in range(3):
            try: raw = self._session.get_message(message_id); break
            except (CoremailError,AppError) as error:
                if error.code in ('TRANSPORT_ERROR','NETWORK_ERROR') and attempt < 2: continue
                raise AppError(error.code) from None
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not message.get('From') or not any(message.get(k) for k in ('To','Subject','Message-ID','Date')): raise AppError('MESSAGE_UNAVAILABLE')
        return raw
    def get_account_identities(self):
        # Identity attrs schema is not yet verified; never invent aliases from uid.
        return set(self._identities)
    def _attrs(self,draft):
        if draft.reference_headers or draft.mode in ('reply','reply_all'): raise AppError('REPLY_CONTRACT_UNVERIFIED')
        if draft.encryption_required: raise AppError('ENCRYPTED_SUBMISSION_UNVERIFIED')
        if '\r' in draft.subject or '\n' in draft.subject: raise AppError('INVALID_SUBJECT')
        return {'account':normalize_recipients((draft.from_identity,))[0], 'to':list(normalize_recipients(draft.to)), 'cc':list(normalize_recipients(draft.cc)), 'bcc':list(normalize_recipients(draft.bcc)), 'subject':draft.subject,'content':draft.body,'isHtml':False,'saveSentCopy':True,'attachments':[]}
    def create_compose(self, draft):
        value = self._call('%mbox/compose',{'action':'continue','returnInfo':True,'attrs':self._attrs(draft)}).get('var')
        value = value.get('id') if isinstance(value,dict) else value
        if not isinstance(value,str) or not value: raise AppError('INVALID_RESPONSE')
        return value
    def deliver(self, compose_id, draft):
        return self._call('%mbox/compose', {'action':'deliver','id':compose_id,'returnInfo':True,'autosaveHitCounter':True,'attrs':self._attrs(draft)})
    def submit_encrypted(self,payload,envelope_recipients):
        raise AppError('ENCRYPTED_SUBMISSION_UNVERIFIED')
    def find_sent_candidates(self,job,limit=50):
        results = []
        for folder in self.list_folders():
            if folder.role == 'sent': results.extend(self.list_messages(folder.remote_id,0,min(limit,50)))
        return results[:limit]
    def close(self):
        if self._session is not None: self._session.close()
        self._session = None
        self._identities.clear()
