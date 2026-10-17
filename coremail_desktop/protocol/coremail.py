"""Typed production boundary around the independently verified HTTPS transport."""
from email import policy
from email.parser import BytesParser
import json
import ssl
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.parse import urljoin, urlencode
from probe.coremail_transport import CoremailSession, CoremailError, _origin, decode_extended_json
from coremail_desktop.models import Folder, MessageSummary
from coremail_desktop.errors import AppError
from coremail_desktop.compose.recipients import normalize_recipients

class DesktopSession(CoremailSession):
    """Retain the verified login/redirect parser, classify production failures."""
    ALLOWED=CoremailSession.ALLOWED|{'mbox:updateMessageInfos','upload:prepare','oab:autoMatch','pab:searchContacts','cert:listPublicKey','cert:get'}
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
        return self._decode_response(raw)
    def _decode_response(self,raw):
        try: result = decode_extended_json(raw.decode('utf-8-sig'))
        except (UnicodeError,CoremailError): raise AppError('INVALID_RESPONSE') from None
        if not isinstance(result,dict): raise AppError('INVALID_RESPONSE')
        code = result.get('code')
        if code == 'FA_UNAUTHORIZED': raise AppError('SESSION_EXPIRED')
        if code in ('FA_NEED_SECOND_AUTH','FA_NEED_VERIFY_CODE','FA_NEED_BIND_DEVICE'): raise AppError('AUTH_CHALLENGE')
        if code != 'S_OK': raise AppError('RPC_REJECTED')
        return result
    def prepare_reply(self,source_id,reply_all=False,is_html=False):
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        url=urljoin(self.base_url,'XT/jsp/compose.jsp')+'?'+urlencode({'sid':self.sid})
        data=urlencode({'ctype':'replyall' if reply_all else 'reply','mid':source_id,'isHtml':'true' if is_html else 'false'}).encode('utf-8')
        raw,_=self._fetch(Request(url,data=data,headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'text/x-json'}))
        value=self._decode_response(raw).get('var')
        if not isinstance(value,dict) or not isinstance(value.get('id'),str) or not value['id']: raise AppError('INVALID_RESPONSE')
        return value
    def account_identities(self):
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        url=urljoin(self.base_url,'XT/jsp/mail.jsp')+'?'+urlencode({'func':'getAllAccounts','sid':self.sid})
        raw,_=self._fetch(Request(url,data=b'{}',headers={'Content-Type':'text/x-json','Accept':'text/x-json'}))
        value=self._decode_response(raw).get('var')
        if not isinstance(value,list) or any(not isinstance(item,dict) or not isinstance(item.get('addr'),str) for item in value): raise AppError('INVALID_RESPONSE')
        return set(normalize_recipients(tuple(item['addr'] for item in value)))
    def search_messages(self,pattern,offset,limit):
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        url=urljoin(self.base_url,'XT/jsp/mail.jsp')+'?'+urlencode({'func':'searchMessages','sid':self.sid})
        data=urlencode({'pattern':pattern,'start':offset,'limit':limit,'summaryWindowSize':0}).encode('utf-8')
        raw,_=self._fetch(Request(url,data=data,headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'text/x-json'}))
        return self._decode_response(raw)
    def upload_attachment(self,compose_id,attachment):
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        from coremail_desktop.compose.attachments import validate_attachments
        validate_attachments((attachment,))
        size=len(attachment.payload)
        value=self.call('upload:prepare',{'composeId':compose_id,'attachmentId':-1,'fileName':attachment.name,'displayName':attachment.name,'contentType':attachment.media_type,'size':size}).get('var')
        if not isinstance(value,dict) or type(value.get('attachmentId')) is not int or value['attachmentId']<0: raise AppError('ATTACHMENT_UPLOAD_FAILED')
        attachment_id=value['attachmentId']; chunk_size=2*1024*1024
        for offset in range(0,max(1,size),chunk_size):
            chunk=attachment.payload[offset:offset+chunk_size]; boundary='Hongyan'+uuid.uuid4().hex
            data=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{attachment.name}"\r\nContent-Type: {attachment.media_type}\r\n\r\n'.encode('utf-8')+chunk+f'\r\n--{boundary}--\r\n'.encode('ascii'))
            url=urljoin(self.base_url,'XT/jsp/upload.jsp')+'?'+urlencode({'func':'directData','sid':self.sid,'composeId':compose_id,'attachmentId':attachment_id,'offset':offset})
            raw,_=self._fetch(Request(url,data=data,headers={'Content-Type':'multipart/form-data; boundary='+boundary,'Accept':'text/x-json'}))
            uploaded=self._decode_response(raw).get('var')
            if not isinstance(uploaded,dict) or uploaded.get('attachmentId')!=attachment_id or uploaded.get('size')!=size or uploaded.get('actualSize')!=offset+len(chunk): raise AppError('ATTACHMENT_UPLOAD_FAILED')
        return {'id':attachment_id,'type':'upload','name':attachment.name,'size':size}
    def deliver_preencoded(self,compose_id,fields,lettercontent):
        """Submit a fully built MIME message (webmail's lettercontent path).

        Verified on the deployment 2026-10-17 with an S/MIME send-to-self: the
        message arrives as built (top-level application/pkcs7-mime), whereas
        extraHeaders sent through mbox:compose are ignored."""
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        boundary='Hongyan'+uuid.uuid4().hex
        parts=[f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode('ascii')+str(value).encode('utf-8')+b'\r\n'
               for name,value in {**fields,'lettercontent':lettercontent}.items()]
        data=b''.join(parts)+f'--{boundary}--\r\n'.encode('ascii')
        query={'func':'mbox:composeF','uploadMode':'preEncodedData','sid':self.sid,'id':compose_id,'action':'deliver','returnInfo':'true','autosaveHitCounter':'true'}
        raw,_=self._fetch(Request(urljoin(self.base_url,'XT/jsp/mformdata.jsp')+'?'+urlencode(query),data=data,
                                  headers={'Content-Type':'multipart/form-data; boundary='+boundary,'Accept':'text/x-json'}))
        return self._decode_response(raw)

def _summary(item,folder_id,sender):
    flags=item.get('flags',{}) if isinstance(item.get('flags',{}),dict) else {}
    size=item.get('size') if type(item.get('size')) is int else None
    return MessageSummary(item['id'],folder_id,str(item.get('subject','')),str(sender),str(item.get('date',item.get('receivedDate',''))),flags,
                          preview=str(item.get('summary','') or '')[:300],recipients=str(item.get('to','') or ''),size=size)

class CoremailAdapter:
    supports_reply_headers = True
    supports_encrypted_submission = True  # Pre-encoded S/MIME, verified by send-to-self 2026-10-17.
    # Folder roles: the deployment's mbox:getAllFolders returns only
    # {id, name, flags{system:bool}, keepPeriod} for every read-only parameter
    # variant, so no role is exposed and none is inferred from names. Folder id 1
    # is the inbox, which is independently confirmed by listing messages in it.
    INBOX_FOLDER_ID = '1'
    SUMMARY_WINDOW = 120  # Verified: adds a plain-text 'summary' preview per row.
    TRASH_FOLDER_ID = '4'  # Confirmed by deployment folder metadata and reversible self-test.
    # Multi-id mbox:updateMessageInfos verified (2026-10-17, read/unread on two inbox
    # messages, restored): each id comes back in originalMsgInfos and is applied.
    batch_updates = True

    def __init__(self, session_factory=DesktopSession):
        self._session_factory = session_factory
        self._session = None
        self._identities = set()
        self._compose_attachments = {}
        self.last_folder_total = None
        self.last_folder_id = None
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
        # stats:true is verified read-only on the deployment (2026-10-17): each
        # folder then carries messageCount/unreadMessageCount.
        value = self._call('mbox:getAllFolders', {'stats':True}, True).get('var')
        if not isinstance(value, list): raise AppError('INVALID_RESPONSE')
        result = []
        for item in value:
            if not isinstance(item,dict) or 'id' not in item: raise AppError('INVALID_RESPONSE')
            role = item.get('role') or item.get('type')
            if role not in ('inbox','sent','drafts','trash','spam'):
                # The server does not expose a role; only the inbox id is verified,
                # so everything else stays unclassified rather than guessed.
                role = 'inbox' if str(item['id']) == self.INBOX_FOLDER_ID else ('trash' if str(item['id']) == self.TRASH_FOLDER_ID and item.get('flags',{}).get('system') is True else None)
            counts = item.get('stats') if isinstance(item.get('stats'), dict) else {}
            result.append(Folder(str(item['id']),str(item.get('name','')),role, counts))
        return result
    def list_messages(self, folder_id, offset, limit=50):
        if offset < 0 or limit < 1 or limit > 100: raise AppError('INVALID_PAGE')
        response = self._call('mbox:listMessages', {'fid':int(folder_id) if str(folder_id).isdigit() else folder_id, 'start':offset,'limit':limit,'mode':'count','order':'date','desc':True,'returnTotal':True,'returnTag':True,'summaryWindowSize':self.SUMMARY_WINDOW},True)
        value = response.get('var')
        if isinstance(value,dict):
            total = value.get('total')
            value = value.get('messages')
        else:
            total = response.get('total')
        if isinstance(total,int) and total >= 0:
            # The server reports the folder total on the list response, so the
            # interface can show a real count without inventing one.
            self.last_folder_total = total
            self.last_folder_id = str(folder_id)
        if not isinstance(value,list): raise AppError('INVALID_RESPONSE')
        result = []
        for item in value:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str): raise AppError('INVALID_RESPONSE')
            sender = item.get('from','')
            if isinstance(sender,list): sender = ', '.join(str(x) for x in sender)
            result.append(_summary(item,str(folder_id),sender))
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
    def search_messages(self,pattern,offset,limit=50):
        if not isinstance(pattern,str) or not pattern.strip() or len(pattern)>1000: raise AppError('INVALID_SEARCH')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100: raise AppError('INVALID_PAGE')
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        response=self._session.search_messages(pattern.strip(),offset,limit)
        rows=response.get('var'); total=response.get('total')
        if not isinstance(rows,list) or type(total) is not int or total<0: raise AppError('INVALID_RESPONSE')
        messages=[]
        for item in rows:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or not isinstance(item.get('fid'),(int,str)) or not isinstance(item.get('flags',{}),dict): raise AppError('INVALID_RESPONSE')
            sender=item.get('from','')
            if isinstance(sender,list): sender=', '.join(str(x) for x in sender)
            messages.append(_summary(item,str(item['fid']),sender))
        return {'messages':messages,'total':total}
    def move_message(self,message_id,folder_id):
        if not isinstance(message_id,str) or not message_id or not isinstance(folder_id,str) or not folder_id: raise AppError('INVALID_MAIL_ACTION')
        if folder_id not in {folder.remote_id for folder in self.list_folders()}: raise AppError('INVALID_MAIL_ACTION')
        response=self._call('mbox:updateMessageInfos',{'ids':[message_id],'attrs':{'fid':int(folder_id) if folder_id.isdigit() else folder_id},'returnOriginalMsgInfos':True})
        affected=response.get('originalMsgInfos')
        if not isinstance(affected,list) or not any(isinstance(item,dict) and item.get('id')==message_id for item in affected): raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return {'message_id':message_id,'folder_id':folder_id}
    def get_account_identities(self):
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        getter=getattr(self._session,'account_identities',None)
        if getter: self._identities=set(getter())
        return set(self._identities)
    def set_message_flags(self,message_id,flags):
        if not isinstance(message_id,str) or not message_id or not isinstance(flags,dict) or not flags or set(flags)-{'read','flagged'} or any(type(v) is not bool for v in flags.values()):
            raise AppError('INVALID_MAIL_ACTION')
        response=self._call('mbox:updateMessageInfos',{'ids':[message_id],'attrs':{'flags':dict(flags)},'returnOriginalMsgInfos':True})
        affected=response.get('originalMsgInfos')
        if not isinstance(affected,list) or not any(isinstance(item,dict) and item.get('id')==message_id for item in affected): raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return {'message_id':message_id,'flags':dict(flags)}
    def _update_many(self,message_ids,attrs):
        """One mbox:updateMessageInfos for many ids; returns only the ids the server confirmed."""
        ids=list(dict.fromkeys(message_ids))
        if not ids or len(ids)>100 or any(not isinstance(i,str) or not i for i in ids): raise AppError('INVALID_MAIL_ACTION')
        response=self._call('mbox:updateMessageInfos',{'ids':ids,'attrs':attrs,'returnOriginalMsgInfos':True})
        affected=response.get('originalMsgInfos')
        if not isinstance(affected,list): raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return [i for i in ids if any(isinstance(item,dict) and item.get('id')==i for item in affected)]
    def set_flags_many(self,message_ids,flags):
        if not isinstance(flags,dict) or not flags or set(flags)-{'read','flagged'} or any(type(v) is not bool for v in flags.values()):
            raise AppError('INVALID_MAIL_ACTION')
        return self._update_many(message_ids,{'flags':dict(flags)})
    def move_many(self,message_ids,folder_id):
        if not isinstance(folder_id,str) or folder_id not in {folder.remote_id for folder in self.list_folders()}: raise AppError('INVALID_MAIL_ACTION')
        return self._update_many(message_ids,{'fid':int(folder_id) if folder_id.isdigit() else folder_id})
    def _attrs(self,draft,encrypted=False):
        from coremail_desktop.compose.attachments import validate_attachments
        validate_attachments(draft.attachments)
        if draft.mode in ('reply','reply_all'):
            if not isinstance(draft.reply_source_id,str) or not draft.reply_source_id: raise AppError('REPLY_SOURCE_REQUIRED')
        elif draft.reference_headers: raise AppError('REPLY_SOURCE_REQUIRED')
        # An encrypted draft must never reach the plaintext compose path.
        if draft.encryption_required and not encrypted: raise AppError('ENCRYPTED_SUBMISSION_UNVERIFIED')
        if '\r' in draft.subject or '\n' in draft.subject: raise AppError('INVALID_SUBJECT')
        return {'account':normalize_recipients((draft.from_identity,))[0], 'to':list(normalize_recipients(draft.to)), 'cc':list(normalize_recipients(draft.cc)), 'bcc':list(normalize_recipients(draft.bcc)), 'subject':draft.subject,'content':draft.body,'isHtml':bool(getattr(draft,'is_html',False)),'saveSentCopy':True,'attachments':[]}
    def create_compose(self, draft):
        self._attrs(draft)  # Validate before creating a server-side session.
        compose_id=self._compose_session(draft)
        if draft.attachments:
            attachments=[self._session.upload_attachment(compose_id,attachment) for attachment in draft.attachments]
            self._compose_attachments[compose_id]=(draft,attachments)
        return compose_id
    def _compose_session(self, draft, encrypted=False):
        if draft.mode in ('reply','reply_all'):
            # Restoring a draft under another session must not bind its reply to
            # an unrelated remote message. The parent Message-ID is the contract.
            from email.parser import BytesHeaderParser
            original=BytesHeaderParser(policy=policy.default).parsebytes(self.fetch_mime(draft.reply_source_id))
            expected=' '.join(str(item) for item in original.get_all('Message-ID',[]))
            if not expected or expected!=draft.reference_headers.get('In-Reply-To',''): raise AppError('REPLY_SOURCE_MISMATCH')
            value=self._session.prepare_reply(draft.reply_source_id,draft.mode=='reply_all',**({'is_html':True} if getattr(draft,'is_html',False) else {}))
            if not isinstance(value,dict) or not isinstance(value.get('id'),str) or not value['id']: raise AppError('INVALID_RESPONSE')
            compose_id=value['id']
        else:
            # The encrypted body never goes into the compose session in plaintext.
            attrs={**self._attrs(draft,encrypted),'content':''} if encrypted else self._attrs(draft)
            value = self._call('%mbox/compose',{'action':'continue','returnInfo':True,'attrs':attrs}).get('var')
            compose_id = value.get('id') if isinstance(value,dict) else value
            if not isinstance(compose_id,str) or not compose_id: raise AppError('INVALID_RESPONSE')
        return compose_id
    def deliver(self, compose_id, draft):
        attrs=self._attrs(draft)
        if draft.attachments:
            prepared=self._compose_attachments.get(compose_id)
            if prepared is None or prepared[0]!=draft: raise AppError('ATTACHMENT_UPLOAD_FAILED')
            attrs['attachments']=prepared[1]
        try: return self._call('%mbox/compose', {'action':'deliver','id':compose_id,'returnInfo':True,'autosaveHitCounter':True,'attrs':attrs})
        finally: self._compose_attachments.pop(compose_id,None)
    def match_contacts(self,keyword,limit=8):
        """Organization directory autocomplete; shape verified read-only 2026-10-17."""
        if not isinstance(keyword,str) or not keyword.strip() or len(keyword)>100: return []
        rows=self._call('oab:autoMatch',{'keyword':keyword.strip(),'limit':max(1,min(int(limit),20))},True).get('var')
        if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
        return [{'name':str(r.get('true_name') or ''),'email':r['email']} for r in rows if isinstance(r,dict) and isinstance(r.get('email'),str) and '@' in r['email']]
    def display_name(self):
        value=self._call('user:getAttrs',{'optionalAttrIds':['true_name']},True).get('var')
        return str(value.get('true_name') or '') if isinstance(value,dict) else ''
    def public_certificates(self,emails):
        """Recipients' S/MIME certificates from the company directory, as DER.

        cert:listPublicKey {userList} returns {email: {id, publicKey (base64 DER),
        publicKeyHash, isDynamic}} (verified read-only 2026-10-17). A certificate is
        accepted only if it names the requested address."""
        import base64
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding
        from cryptography.x509.oid import NameOID
        wanted=list(normalize_recipients(tuple(emails)))
        if not wanted: return {}
        value=self._call('cert:listPublicKey',{'userList':wanted},True).get('var')
        if not isinstance(value,dict): raise AppError('INVALID_RESPONSE')
        result={}
        for key,entry in value.items():
            if not isinstance(key,str) or not isinstance(entry,dict) or not isinstance(entry.get('publicKey'),str) or not entry['publicKey']: continue
            try: cert=x509.load_der_x509_certificate(base64.b64decode(entry['publicKey']))
            except ValueError: continue
            names={v.value.casefold() for v in cert.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)}
            try: names|={v.casefold() for v in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.RFC822Name)}
            except x509.ExtensionNotFound: pass
            if key.casefold() in names: result[key.casefold()]=cert.public_bytes(Encoding.DER)
        return result
    def submit_encrypted(self,payload,envelope_recipients,draft=None):
        """Open a compose session for the draft, then deliver the pre-built S/MIME message."""
        if draft is None or not draft.encryption_required: raise AppError('ENCRYPTION_REQUIRED')
        attrs=self._attrs(draft,encrypted=True)
        compose_id=self._compose_session(draft,encrypted=True)
        fields={'returnInfo':'true','account':attrs['account'],'to':','.join(attrs['to']),'cc':','.join(attrs['cc']),'bcc':','.join(attrs['bcc']),
                'subject':attrs['subject'],'isHtml':'false','saveSentCopy':'true'}
        return self._session.deliver_preencoded(compose_id,fields,payload.decode('ascii'))
    def find_sent_candidates(self,job,limit=50):
        results = []
        for folder in self.list_folders():
            if folder.role == 'sent': results.extend(self.list_messages(folder.remote_id,0,min(limit,50)))
        return results[:limit]
    def close(self):
        if self._session is not None: self._session.close()
        self._session = None
        self._identities.clear()
        self._compose_attachments.clear()
