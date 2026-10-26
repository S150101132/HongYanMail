"""Typed production boundary around the independently verified HTTPS transport."""
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import json
import re
import ssl
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, HTTPCookieProcessor, build_opener
from urllib.parse import urljoin, urlencode, urlsplit, parse_qs
from probe.coremail_transport import CoremailSession, CoremailError, SameOriginRedirectHandler, _origin, decode_extended_json
from coremail_desktop.protocol.keepalive import KeepAliveHTTPSHandler
from coremail_desktop.models import Folder, MessageSummary
from coremail_desktop.errors import AppError
from coremail_desktop.compose.recipients import normalize_recipients

class DesktopSession(CoremailSession):
    """Retain the verified login/redirect parser, classify production failures."""
    ALLOWED=CoremailSession.ALLOWED|{'mbox:updateMessageInfos','upload:prepare','oab:autoMatch','pab:searchContacts','cert:listPublicKey','cert:get',
                                       'user:listDeliveryHistory','mbox:recallMessage2','mbox:cancelDeliver','mbox:getMessageInfos','mbox:listSharedAccessMbox',
                                       'oab:getDirectories','oab:listEx','oab:get','oab:getHeadImageData',
                                       'user:getMailRules','user:addMailRules','user:updateMailRules','user:removeMailRules','user:adjustMailRuleOrder',
                                       'user:setAttrs','mbox:listTags','mbox:manageTags','mbox:updateMessageTags','cal:respond',
                                       'mbox:createUserFolders','mbox:updateFolders','mbox:deleteFolders','mbox:emptyFolder','mbox:deleteMessages'}
    # Sending (and taking a send back) is never repeated: such requests get a fresh
    # connection and are not resent when a reused one turns out to be dropped.
    ONCE_ONLY=frozenset({'mbox:compose','mbox:composeF','mbox:cancelDeliver','mbox:recallMessage2'})
    def __init__(self,base_url='https://newmail.inspur.com/coremail/'):
        super().__init__(base_url)
        self._https=KeepAliveHTTPSHandler(self._repeatable)  # One TLS handshake per connection, not per request.
        self._opener=build_opener(SameOriginRedirectHandler(base_url),HTTPCookieProcessor(self._cookies),self._https)
    def _repeatable(self,request):
        parts=urlsplit(request.full_url)
        return not parts.path.endswith('/compose.jsp') and not set(parse_qs(parts.query).get('func',()))&self.ONCE_ONLY
    def close(self):
        super().close()
        if getattr(self,'_https',None) is not None: self._https.close_all()
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
    _last_code=None
    def call_code(self,func,payload):
        """Like call, but a refusal comes back as its fixed FA_* code instead of RPC_REJECTED."""
        try: return self.call(func,payload)
        except AppError as error:
            if error.code!='RPC_REJECTED' or not self._last_code: raise
            return {'code':self._last_code}
    def _decode_response(self,raw):
        try: result = decode_extended_json(raw.decode('utf-8-sig'))
        except (UnicodeError,CoremailError): raise AppError('INVALID_RESPONSE') from None
        if not isinstance(result,dict): raise AppError('INVALID_RESPONSE')
        code = result.get('code')
        self._last_code = code if isinstance(code,str) and re.fullmatch(r'F[AS]_[A-Z0-9_]{1,60}',code) else None
        if code == 'FA_UNAUTHORIZED': raise AppError('SESSION_EXPIRED')
        if code in ('FA_NEED_SECOND_AUTH','FA_NEED_VERIFY_CODE','FA_NEED_BIND_DEVICE'): raise AppError('AUTH_CHALLENGE')
        if code != 'S_OK': raise AppError('RPC_REJECTED')
        return result
    def get_raw(self,func,**parameters):
        """A read-only GET whose answer is bytes rather than JSON (head images)."""
        if func not in self.ALLOWED: raise AppError('RPC_NOT_ALLOWED')
        raw,_ = self._fetch(Request(self._rpc_url(func,**parameters),headers={'Accept':'image/*,text/x-json'}))
        return raw
    def push_credentials(self):
        """Everything the push channel needs, or None when push is off or signed out.

        Verified 2026-10-17 (analysis/push-shape-2026-10-17.json): the socket.io
        handshake is authenticated by the Coremail.sid cookie (the jar scopes it
        to /coremail, so the header is built here for /socket.io/), and the topics
        come from the webmail page's X.U settings. A push host on another origin
        is refused: push then stays off and polling carries on."""
        from coremail_desktop.protocol.push import read_push_config, topics
        if not self.sid: return None
        sid=self.sid
        scheme,host,port=self._origin
        origin=f'{scheme}://{host}'+('' if port==443 else f':{port}')
        config=read_push_config(self)
        if config.get('enablePushNotify') is not True: return None
        online=config.get('onlineHost')
        if online:
            try:
                if _origin(online)!=self._origin: return None
            except CoremailError: return None
        request=Request(origin+'/socket.io/1/')
        self._cookies.add_cookie_header(request)
        cookies=[c.strip() for c in (request.get_header('Cookie') or '').split(';') if c.strip()]
        if not any(c.startswith('Coremail.sid=') for c in cookies): cookies.append('Coremail.sid='+sid)
        return {'origin':origin,'cookie':'; '.join(cookies),'opener':self._opener,'sid':sid,
                'username':str(config.get('dnemail') or ''),'topics':topics(config),'alive':lambda: self.sid==sid}
    def prepare_reply(self,source_id,reply_all=False,is_html=False,mboxa=''):
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        url=urljoin(self.base_url,'XT/jsp/compose.jsp')+'?'+urlencode({'sid':self.sid})
        data=urlencode({'ctype':'replyall' if reply_all else 'reply','mid':source_id,'isHtml':'true' if is_html else 'false',**({'mboxa':mboxa} if mboxa else {})}).encode('utf-8')
        raw,_=self._fetch(Request(url,data=data,headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'text/x-json'}))
        value=self._decode_response(raw).get('var')
        if not isinstance(value,dict) or not isinstance(value.get('id'),str) or not value['id']: raise AppError('INVALID_RESPONSE')
        return value
    def open_draft(self,message_id):
        """Reopen a server draft as a compose session (webmail's ctype=draft).

        Verified 2026-10-17 on a scheduled send-to-self: var carries id, the
        message attrs and, for a scheduled draft, scheduleDate."""
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        url=urljoin(self.base_url,'XT/jsp/compose.jsp')+'?'+urlencode({'sid':self.sid})
        data=urlencode({'ctype':'draft','mid':message_id,'stringifyScheduleDate':'true'}).encode('utf-8')
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
    def search_messages(self,pattern,offset,limit,fields=None,summary=0):
        """fields: extra advanced-search form fields (see CoremailAdapter.search_fields).
        summary: characters of body text per row; 100 verified 2026-10-17
        (analysis/search-capability2-2026-10-17.json, B.form.summary100)."""
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        url=urljoin(self.base_url,'XT/jsp/mail.jsp')+'?'+urlencode({'func':'searchMessages','sid':self.sid})
        form={'pattern':pattern} if pattern else {}
        data=urlencode({**form,**(fields or {}),'start':offset,'limit':limit,'summaryWindowSize':summary}).encode('utf-8')
        raw,_=self._fetch(Request(url,data=data,headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'text/x-json'}))
        return self._decode_response(raw)
    def upload_attachment(self,compose_id,attachment,inlined=False):
        """inlined: an image shown in the body (webmail's inlined upload, X.03931.js:
        upload:prepare and directData both carry inlined)."""
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        from coremail_desktop.compose.attachments import validate_attachments
        validate_attachments((attachment,))
        size=len(attachment.payload)
        value=self.call('upload:prepare',{'composeId':compose_id,'attachmentId':-1,'fileName':attachment.name,'displayName':attachment.name,'contentType':attachment.media_type,'size':size,**({'inlined':True} if inlined else {})}).get('var')
        if not isinstance(value,dict) or type(value.get('attachmentId')) is not int or value['attachmentId']<0: raise AppError('ATTACHMENT_UPLOAD_FAILED')
        attachment_id=value['attachmentId']; chunk_size=2*1024*1024
        for offset in range(0,max(1,size),chunk_size):
            chunk=attachment.payload[offset:offset+chunk_size]; boundary='Hongyan'+uuid.uuid4().hex
            data=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{attachment.name}"\r\nContent-Type: {attachment.media_type}\r\n\r\n'.encode('utf-8')+chunk+f'\r\n--{boundary}--\r\n'.encode('ascii'))
            url=urljoin(self.base_url,'XT/jsp/upload.jsp')+'?'+urlencode({'func':'directData','sid':self.sid,'composeId':compose_id,'attachmentId':attachment_id,'offset':offset,**({'inlined':'1'} if inlined else {})})
            raw,_=self._fetch(Request(url,data=data,headers={'Content-Type':'multipart/form-data; boundary='+boundary,'Accept':'text/x-json'}))
            uploaded=self._decode_response(raw).get('var')
            if not isinstance(uploaded,dict) or uploaded.get('attachmentId')!=attachment_id or uploaded.get('size')!=size or uploaded.get('actualSize')!=offset+len(chunk): raise AppError('ATTACHMENT_UPLOAD_FAILED')
        return {'id':attachment_id,'type':'upload','name':attachment.name,'size':size,**({'inlined':True} if inlined else {})}
    def compose_data_url(self,compose_id,attachment_id):
        """Where webmail's editor shows an inlined upload (X.03931.js Zt: s/json
        func=mbox:getComposeData). On save/deliver the server turns this src into
        cid: and the image into a multipart/related part; no sid is left in the
        message (verified 2026-10-18, analysis/compose-v05-shape-2026-10-18.json)."""
        if not self.sid: raise AppError('NOT_AUTHENTICATED')
        return urljoin(self.base_url,'s/json')+'?'+urlencode({'sid':self.sid,'func':'mbox:getComposeData','composeId':compose_id,'attachId':attachment_id})
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

# Dates in responses are written in the zone named by the request's tz header
# (Asia/Riyadh, UTC+3, no DST): a scheduled draft's sentDate read this way equals
# the requested moment exactly (verified 2026-10-17).
RESPONSE_ZONE = timezone(timedelta(hours=3))
# scheduleDate, however, is read in the account's own time_zone attribute (Asia/Shanghai
# on the deployment; a UTC+3 string was refused as a past date). Only zones with a
# fixed offset are listed: without tz data a zone with daylight saving cannot be
# converted safely, so scheduling is refused there instead of guessed.
FIXED_ZONES = {'Asia/Shanghai':8,'Asia/Chongqing':8,'Asia/Harbin':8,'Asia/Urumqi':6,'Asia/Hong_Kong':8,'Asia/Macau':8,'Asia/Taipei':8,
               'Asia/Singapore':8,'Asia/Kuala_Lumpur':8,'Asia/Manila':8,'Asia/Tokyo':9,'Asia/Seoul':9,'Asia/Riyadh':3,'Asia/Dubai':4,
               'Asia/Karachi':5,'Asia/Kolkata':5.5,'Asia/Calcutta':5.5,'Asia/Dhaka':6,'Asia/Bangkok':7,'Asia/Jakarta':7,'Asia/Ho_Chi_Minh':7,
               'Europe/Moscow':3,'Africa/Nairobi':3,'UTC':0,'Etc/UTC':0,'GMT':0,'Etc/GMT':0}

def account_zone(name):
    if isinstance(name,str) and name in FIXED_ZONES: return timezone(timedelta(hours=FIXED_ZONES[name]))
    match=re.fullmatch(r'(?:GMT|UTC)?([+-])(\d{1,2})(?::?(\d\d))?',name.strip()) if isinstance(name,str) else None
    if match and int(match.group(2))<=14:
        hours=int(match.group(2))+int(match.group(3) or 0)/60
        return timezone(timedelta(hours=hours if match.group(1)=='+' else -hours))
    raise AppError('SCHEDULE_ZONE_UNSUPPORTED')

def _schedule_at(item,flags):
    if not flags.get('scheduleDelivery'): return None
    try: moment=datetime.strptime(str(item.get('sentDate','')),'%Y-%m-%d %H:%M:%S').replace(tzinfo=RESPONSE_ZONE)
    except ValueError: return None
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def _box(mboxa):
    """The mboxa parameter naming a shared mailbox ('@'+id), or nothing for the user's own.

    Webmail (analysis/web-login X.03931.js) passes mboxa in the JSON body of
    every mail call and in the query only for mbox-data URLs. The account used
    for the 2026-10-18 probe has no shared mailbox (mbox:listSharedAccessMbox
    answers an empty list), so the shared paths follow webmail unverified."""
    if not mboxa: return {}
    if not isinstance(mboxa,str) or not re.fullmatch(r'@[^\s/?&#]{1,200}',mboxa): raise AppError('INVALID_MAIL_ACTION')
    return {'mboxa':mboxa}

def _flag(value):
    return value is True or value == 1 or value in ('1','true')

def _summary(item,folder_id,sender):
    flags=item.get('flags',{}) if isinstance(item.get('flags',{}),dict) else {}
    size=item.get('size') if type(item.get('size')) is int else None
    return MessageSummary(item['id'],folder_id,str(item.get('subject','')),str(sender),str(item.get('date',item.get('receivedDate',''))),flags,
                          preview=str(item.get('summary','') or '')[:300],recipients=str(item.get('to','') or ''),size=size,
                          schedule_at=_schedule_at(item,flags),
                          tags=tuple(str(t) for t in item.get('tag') or () if isinstance(t,(str,int))) if isinstance(item.get('tag'),list) else ())

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
    DRAFTS_FOLDER_ID = '2'  # Scheduled mail waits here with flags.scheduleDelivery (verified 2026-10-17).
    # mbox:composeF refused action=schedule with FA_INVALID_DATE for every date
    # encoding tried (analysis/send-features-shape3/4-2026-10-17.json), and webmail
    # never schedules pre-encoded mail: encrypted mail can only be sent now.
    supports_scheduled_encrypted = False

    def __init__(self, session_factory=DesktopSession):
        self._session_factory = session_factory
        self._session = None
        self._identities = set()
        self._compose_attachments = {}
        self.last_folder_total = None
        self.last_folder_id = None
        self._zone = None
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
                if error.code in ('TRANSPORT_ERROR','NETWORK_ERROR','TLS_ERROR') and read and attempt < 2: continue
                raise AppError(error.code) from None
    def list_folders(self, mboxa=''):
        # stats:true is verified read-only on the deployment (2026-10-17): each
        # folder then carries messageCount/unreadMessageCount.
        value = self._call('mbox:getAllFolders', {'stats':True,**_box(mboxa)}, True).get('var')
        if not isinstance(value, list): raise AppError('INVALID_RESPONSE')
        result = []
        # A sub-folder is listed inside its parent's 'children' (verified 2026-10-19 with a
        # self-made test folder); there is no parent id field on the folder itself.
        stack = [(item, None, 0) for item in reversed(value)]
        while stack:
            item, parent, depth = stack.pop()
            if not isinstance(item,dict) or 'id' not in item or depth > 10: raise AppError('INVALID_RESPONSE')
            role = item.get('role') or item.get('type')
            if role not in ('inbox','sent','drafts','trash','spam'):
                # The server does not expose a role; only the inbox id is verified,
                # so everything else stays unclassified rather than guessed.
                role = 'inbox' if str(item['id']) == self.INBOX_FOLDER_ID else ('trash' if str(item['id']) == self.TRASH_FOLDER_ID and item.get('flags',{}).get('system') is True else None)
            counts = item.get('stats') if isinstance(item.get('stats'), dict) else {}
            system = isinstance(item.get('flags'),dict) and item['flags'].get('system') is True
            result.append(Folder(str(item['id']),str(item.get('name','')),role, counts, parent, system))
            children = item.get('children')
            if isinstance(children,list):
                stack.extend((child, str(item['id']), depth + 1) for child in reversed(children))
        return result
    def list_messages(self, folder_id, offset, limit=50, mboxa=''):
        if offset < 0 or limit < 1 or limit > 100: raise AppError('INVALID_PAGE')
        response = self._call('mbox:listMessages', {'fid':int(folder_id) if str(folder_id).isdigit() else folder_id, 'start':offset,'limit':limit,'mode':'count','order':'date','desc':True,'returnTotal':True,'returnTag':True,'summaryWindowSize':self.SUMMARY_WINDOW,**_box(mboxa)},True)
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
            self.last_folder_id = str(folder_id) + (mboxa or '')  # A shared folder reads as 'fid@id'.
        if not isinstance(value,list): raise AppError('INVALID_RESPONSE')
        result = []
        for item in value:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str): raise AppError('INVALID_RESPONSE')
            sender = item.get('from','')
            if isinstance(sender,list): sender = ', '.join(str(x) for x in sender)
            result.append(_summary(item,str(folder_id),sender))
        return result
    def fetch_mime(self, message_id, mboxa=''):
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        extra = _box(mboxa)
        for attempt in range(3):
            try: raw = self._session.get_message(message_id, **extra); break
            except (CoremailError,AppError) as error:
                if error.code in ('TRANSPORT_ERROR','NETWORK_ERROR','TLS_ERROR') and attempt < 2: continue
                raise AppError(error.code) from None
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not message.get('From') or not any(message.get(k) for k in ('To','Subject','Message-ID','Date')): raise AppError('MESSAGE_UNAVAILABLE')
        return raw
    # Webmail's advanced search form (X.03931.js: '!mail:searchMessages' picks pattern,
    # fid, subject, from, to, sentDate, attachType, flagType, read, dealings ...).
    # Verified read-only 2026-10-18 (analysis/contacts-search-shape-2026-10-18.json):
    # each field narrows the total, every returned row satisfies it, fields combine,
    # and start/limit still page. pattern is the full-text search, subject searches
    # subjects only; from/to match address substrings, dealings needs a whole address.
    SEARCH_FILTERS=frozenset({'from','to','with','scope','since','until','folder','attachment','unread','flagged'})
    @staticmethod
    def search_fields(pattern,filters):
        """Validated backend filters -> (pattern, form fields)."""
        fields={}
        for key,name in (('from','from'),('to','to'),('with','dealings')):
            if filters.get(key): fields[name]=filters[key]
        if pattern and filters.get('scope')=='subject': fields['subject'],pattern=pattern,''
        since,until=filters.get('since'),filters.get('until')
        # 'from:to 23:59:59' and 'from:' are the forms webmail sends (and were verified).
        if since or until: fields['sentDate']=f"{since or '1970-01-01'}:"+(f'{until} 23:59:59' if until else '')
        if filters.get('folder'): fields['fid']=int(filters['folder']) if str(filters['folder']).isdigit() else filters['folder']
        if filters.get('attachment'): fields['attachType']='attached'
        if filters.get('flagged'): fields['flagType']='flagged'
        if filters.get('unread'): fields['read']='false'
        return pattern,fields
    def search_messages(self,pattern,offset,limit=50,filters=None,snippet=False):
        """snippet: each row's preview carries the first 100 characters of the body."""
        filters=dict(filters or {})
        if not isinstance(pattern,str) or len(pattern)>1000 or set(filters)-self.SEARCH_FILTERS: raise AppError('INVALID_SEARCH')
        pattern,fields=self.search_fields(pattern.strip(),filters)
        if not pattern and not fields: raise AppError('INVALID_SEARCH')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100: raise AppError('INVALID_PAGE')
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        extra={'summary':100} if snippet else {}
        response=self._session.search_messages(pattern,offset,limit,fields,**extra) if fields else self._session.search_messages(pattern,offset,limit,**extra)
        rows=response.get('var'); total=response.get('total')
        if not isinstance(rows,list) or type(total) is not int or total<0: raise AppError('INVALID_RESPONSE')
        messages=[]
        for item in rows:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or not isinstance(item.get('fid'),(int,str)) or not isinstance(item.get('flags',{}),dict): raise AppError('INVALID_RESPONSE')
            sender=item.get('from','')
            if isinstance(sender,list): sender=', '.join(str(x) for x in sender)
            messages.append(_summary(item,str(item['fid']),sender))
        return {'messages':messages,'total':total}
    def move_message(self,message_id,folder_id,mboxa=''):
        if not isinstance(message_id,str) or not message_id or not isinstance(folder_id,str) or not folder_id: raise AppError('INVALID_MAIL_ACTION')
        if folder_id not in {folder.remote_id for folder in self.list_folders(mboxa)}: raise AppError('INVALID_MAIL_ACTION')
        response=self._call('mbox:updateMessageInfos',{'ids':[message_id],'attrs':{'fid':int(folder_id) if folder_id.isdigit() else folder_id},'returnOriginalMsgInfos':True,**_box(mboxa)})
        affected=response.get('originalMsgInfos')
        if not isinstance(affected,list) or not any(isinstance(item,dict) and item.get('id')==message_id for item in affected): raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return {'message_id':message_id,'folder_id':folder_id}
    def get_account_identities(self):
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        getter=getattr(self._session,'account_identities',None)
        if getter: self._identities=set(getter())
        return set(self._identities)
    def set_message_flags(self,message_id,flags,mboxa=''):
        if not isinstance(message_id,str) or not message_id or not isinstance(flags,dict) or not flags or set(flags)-{'read','flagged'} or any(type(v) is not bool for v in flags.values()):
            raise AppError('INVALID_MAIL_ACTION')
        response=self._call('mbox:updateMessageInfos',{'ids':[message_id],'attrs':{'flags':dict(flags)},'returnOriginalMsgInfos':True,**_box(mboxa)})
        affected=response.get('originalMsgInfos')
        if not isinstance(affected,list) or not any(isinstance(item,dict) and item.get('id')==message_id for item in affected): raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return {'message_id':message_id,'flags':dict(flags)}
    def _update_many(self,message_ids,attrs,mboxa=''):
        """One mbox:updateMessageInfos for many ids; returns only the ids the server confirmed."""
        ids=list(dict.fromkeys(message_ids))
        if not ids or len(ids)>100 or any(not isinstance(i,str) or not i for i in ids): raise AppError('INVALID_MAIL_ACTION')
        response=self._call('mbox:updateMessageInfos',{'ids':ids,'attrs':attrs,'returnOriginalMsgInfos':True,**_box(mboxa)})
        affected=response.get('originalMsgInfos')
        if not isinstance(affected,list): raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return [i for i in ids if any(isinstance(item,dict) and item.get('id')==i for item in affected)]
    def set_flags_many(self,message_ids,flags,mboxa=''):
        if not isinstance(flags,dict) or not flags or set(flags)-{'read','flagged'} or any(type(v) is not bool for v in flags.values()):
            raise AppError('INVALID_MAIL_ACTION')
        return self._update_many(message_ids,{'flags':dict(flags)},mboxa)
    def move_many(self,message_ids,folder_id,mboxa=''):
        if not isinstance(folder_id,str) or folder_id not in {folder.remote_id for folder in self.list_folders(mboxa)}: raise AppError('INVALID_MAIL_ACTION')
        return self._update_many(message_ids,{'fid':int(folder_id) if folder_id.isdigit() else folder_id},mboxa)
    # Folder management, as webmail does it (X.03931.js module 88812, S.03931.js
    # folder settings): mbox:createUserFolders {items:[{name, parent}]},
    # mbox:updateFolders {items:[{id, name}]}, mbox:deleteFolders {ids}. Names follow
    # webmail's trimName: at most 24 width units (CJK counts 2), none of FOLDER_FORBIDDEN.
    FOLDER_FORBIDDEN=frozenset('%\'"\\/:*?<>|=,^')
    MAX_FOLDER_DEPTH=5  # index.jsp maxFolderNestLevel, read 2026-10-19.
    @classmethod
    def folder_name(cls,name):
        name=name.strip() if isinstance(name,str) else ''
        if not name or sum(1 if ord(c)<128 else 2 for c in name)>24 or set(name)&cls.FOLDER_FORBIDDEN or any(ord(c)<32 for c in name):
            raise AppError('INVALID_FOLDER_NAME')
        return name
    @staticmethod
    def _fid(value):
        value=str(value)
        if not re.fullmatch(r'\d{1,12}',value): raise AppError('INVALID_FOLDER')
        return int(value)
    def _user_folder(self,folder_id,mboxa=''):
        folder=next((f for f in self.list_folders(mboxa) if f.remote_id==str(folder_id)),None)
        if folder is None: raise AppError('INVALID_FOLDER')
        if folder.system or folder.role: raise AppError('SYSTEM_FOLDER')
        return folder
    def create_folder(self,name,parent=None,mboxa=''):
        """A new folder (under parent, a folder id, or at the top); returns its id."""
        name=self.folder_name(name)
        parent_id=self._fid(parent) if parent is not None else 0
        before={f.remote_id for f in self.list_folders(mboxa)}
        response=self._call('mbox:createUserFolders',{'items':[{'name':name,'parent':parent_id}],**_box(mboxa)})
        made=[f for f in self.list_folders(mboxa) if f.remote_id not in before and f.name==name]
        value=response.get('var')
        ids=[str(v) for v in (value if isinstance(value,list) else [value]) if type(v) in (int,str)]
        found=next((f.remote_id for f in made if f.remote_id in ids),None) or (made[0].remote_id if len(made)==1 else None)
        if found is None: raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return found
    def rename_folder(self,folder_id,name,mboxa=''):
        folder=self._user_folder(folder_id,mboxa)
        self._call('mbox:updateFolders',{'items':[{'id':self._fid(folder.remote_id),'name':self.folder_name(name)}],**_box(mboxa)})
        return True
    def delete_folder(self,folder_id,mboxa=''):
        """Deletes an empty user folder. The backend moves its mail out first, so the
        server never takes mail with it (webmail empties a folder before deleting it)."""
        folder=self._user_folder(folder_id,mboxa)
        if dict(folder.counts).get('messageCount'): raise AppError('FOLDER_NOT_EMPTY')
        self._call('mbox:deleteFolders',{'ids':[self._fid(folder.remote_id)],**_box(mboxa)})
        return True
    # Permanent deletion (webmail "彻底删除" realDeleteMails, offered when X.M.enableRealDelete,
    # which index.jsp sets on this deployment): mbox:deleteMessages {ids} and
    # mbox:emptyFolder {id, permanently:true}. The backend allows them only in trash and spam.
    def delete_messages(self,message_ids,mboxa=''):
        ids=list(dict.fromkeys(message_ids))
        if not ids or len(ids)>100 or any(not isinstance(i,str) or not i for i in ids): raise AppError('INVALID_MAIL_ACTION')
        self._call('mbox:deleteMessages',{'ids':ids,**_box(mboxa)})
        return ids
    def empty_folder(self,folder_id,mboxa=''):
        self._call('mbox:emptyFolder',{'id':self._fid(folder_id),'permanently':True,**_box(mboxa)})
        return True
    # Spam (webmail 173.03931.js Ze.report / Ze.notJunk, offered when X.S.enableReportSpam):
    # the move itself carries reportType, so the server learns from it.
    SPAM_FOLDER_ID='5'
    def report_spam(self,message_ids,spam=True):
        ids=list(dict.fromkeys(message_ids))
        if not ids or len(ids)>100 or any(not isinstance(i,str) or not i for i in ids): raise AppError('INVALID_MAIL_ACTION')
        target=self.SPAM_FOLDER_ID if spam else self.INBOX_FOLDER_ID
        response=self._call('mbox:updateMessageInfos',{'ids':ids,'attrs':{'fid':int(target)},'reportType':'spam' if spam else 'notspam','returnOriginalMsgInfos':True})
        affected=response.get('originalMsgInfos')
        if not isinstance(affected,list): raise AppError('MAIL_UPDATE_UNCONFIRMED')
        return [i for i in ids if any(isinstance(item,dict) and item.get('id')==i for item in affected)]
    def unread_ids(self,folder_id,mboxa='',limit=5000):
        """Ids of the unread mail in a folder: mbox:listMessages filter {flags:{read:false}}
        (verified 2026-10-19: its total equals the folder's unreadMessageCount)."""
        ids=[]
        while len(ids)<limit:
            response=self._call('mbox:listMessages',{'fid':self._fid(folder_id),'start':len(ids),'limit':100,'mode':'count','order':'date','desc':True,
                                                     'returnTotal':True,'summaryWindowSize':0,'filter':{'flags':{'read':False}},**_box(mboxa)},True)
            rows=response.get('var')
            if isinstance(rows,dict): rows=rows.get('messages')
            if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
            page=[r['id'] for r in rows if isinstance(r,dict) and isinstance(r.get('id'),str)]
            ids+=[i for i in page if i not in ids]
            if len(rows)<100: break
        return ids[:limit]
    # Sender lists: the user attributes safelist (白名单) and refuselist (黑名单), comma
    # separated, read with user:getAttrs and written with user:setAttrs like webmail's
    # updateUser (verified read 2026-10-19). '*@domain' entries cover a whole domain.
    SENDER_LISTS=('safelist','refuselist')
    def sender_lists(self):
        value=self._call('user:getAttrs',{'optionalAttrIds':list(self.SENDER_LISTS)},True).get('var')
        if not isinstance(value,dict): raise AppError('INVALID_RESPONSE')
        return {k:[x for x in re.split(r',\s*',value.get(k) or '') if x] if isinstance(value.get(k) or '',str) else [] for k in self.SENDER_LISTS}
    def set_sender_lists(self,lists):
        if not isinstance(lists,dict) or not lists or set(lists)-set(self.SENDER_LISTS): raise AppError('INVALID_SENDER')
        self._call('user:setAttrs',{'attrs':{k:','.join(v) for k,v in lists.items()}})
        return True
    def list_shared_mailboxes(self):
        """Shared / department mailboxes this account may open, with the account's rights.

        mbox:listSharedAccessMbox {showHidden, v:2} as webmail calls it (X.03931.js
        module 88812); verified read-only 2026-10-18 only as far as an account
        without shared mailboxes allows: S_OK with an empty list
        (analysis/shared-mailbox-shape-2026-10-18.json). Row fields follow webmail's
        compose code: id (mboxa is '@'+id), email, remark, send, rw, recallable."""
        rows=self._call('mbox:listSharedAccessMbox',{'showHidden':True,'v':2},True).get('var')
        if rows is None: return []
        if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
        result=[]
        for row in rows:
            if not isinstance(row,dict) or type(row.get('id')) not in (int,str) or not str(row['id']) or not isinstance(row.get('email'),str) or '@' not in row['email']: continue
            try: _box('@'+str(row['id']))
            except AppError: continue
            result.append({'id':str(row['id']),'email':row['email'],'name':str(row.get('remark') or ''),
                           'send':_flag(row.get('send')),'rw':_flag(row.get('rw')),'recallable':_flag(row.get('recallable'))})
        return result
    def _attrs(self,draft,encrypted=False):
        from coremail_desktop.compose.attachments import validate_attachments
        validate_attachments(draft.attachments)
        if draft.mode in ('reply','reply_all'):
            if not isinstance(draft.reply_source_id,str) or not draft.reply_source_id: raise AppError('REPLY_SOURCE_REQUIRED')
        elif draft.reference_headers: raise AppError('REPLY_SOURCE_REQUIRED')
        # An encrypted draft must never reach the plaintext compose path.
        if (draft.encryption_required or draft.signature_required) and not encrypted: raise AppError('ENCRYPTED_SUBMISSION_UNVERIFIED')
        if '\r' in draft.subject or '\n' in draft.subject: raise AppError('INVALID_SUBJECT')
        return {'account':normalize_recipients((draft.from_identity,))[0], 'to':list(normalize_recipients(draft.to)), 'cc':list(normalize_recipients(draft.cc)), 'bcc':list(normalize_recipients(draft.bcc)), 'subject':draft.subject,'content':draft.body,'isHtml':bool(getattr(draft,'is_html',False)),'saveSentCopy':True,'attachments':[],
                # Verified 2026-10-17: given on continue and deliver, the message carries Disposition-Notification-To.
                **({'requestReadReceipt':True} if getattr(draft,'request_receipt',False) else {})}
    def _schedule_text(self,draft):
        """The draft's UTC moment as scheduleDate text in the account's zone."""
        if self._zone is None:
            value=self._call('user:getAttrs',{'optionalAttrIds':['time_zone']},True).get('var')
            self._zone=account_zone(value.get('time_zone') if isinstance(value,dict) else None)
        moment=datetime.strptime(draft.schedule_at,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        return moment.astimezone(self._zone).strftime('%Y-%m-%d %H:%M:%S')
    def create_compose(self, draft):
        from coremail_desktop.compose.inline import extract_inline, fill_inline
        self._attrs(draft)  # Validate before creating a server-side session.
        template,images=extract_inline(draft.body) if getattr(draft,'is_html',False) else (draft.body,[])
        compose_id=self._compose_session(draft)
        if draft.attachments or images:
            attachments=[self._session.upload_attachment(compose_id,attachment) for attachment in draft.attachments]
            inline=[self._session.upload_attachment(compose_id,image,inlined=True) for image in images]
            content=fill_inline(template,[self._session.compose_data_url(compose_id,item['id']) for item in inline])
            self._compose_attachments[compose_id]=(draft,attachments+inline,content)
        return compose_id
    def _retire_draft(self,draft,response):
        """After the edited copy of a server draft was saved or sent, move the draft it
        replaces to the trash (a move, so it can be restored). A fresh compose is used
        instead of reopening the draft (compose.jsp ctype=draft): a reopened session
        keeps the draft's own attachments, so re-uploading doubled them, and the
        pre-encoded deliver left the reopened draft in place (verified 2026-10-19,
        analysis/compose-v05-real-verification-2026-10-18.json)."""
        old=getattr(draft,'server_draft_id','')
        if old and isinstance(response,dict) and response.get('code')=='S_OK':
            try: self.move_message(old,self.TRASH_FOLDER_ID)
            except AppError: pass  # The edited copy is already safe; at worst the old one stays.
        return response
    def _compose_session(self, draft, encrypted=False):
        if draft.mode in ('reply','reply_all'):
            # Restoring a draft under another session must not bind its reply to
            # an unrelated remote message. The parent Message-ID is the contract.
            from email.parser import BytesHeaderParser
            mboxa=getattr(draft,'reply_mailbox','')
            original=BytesHeaderParser(policy=policy.default).parsebytes(self.fetch_mime(draft.reply_source_id,mboxa))
            expected=' '.join(str(item) for item in original.get_all('Message-ID',[]))
            if not expected or expected!=draft.reference_headers.get('In-Reply-To',''): raise AppError('REPLY_SOURCE_MISMATCH')
            value=self._session.prepare_reply(draft.reply_source_id,draft.mode=='reply_all',**({'is_html':True} if getattr(draft,'is_html',False) else {}),**_box(mboxa))
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
        attrs=self._prepared(compose_id,draft)
        action='deliver'
        if getattr(draft,'schedule_at',None):
            # Verified 2026-10-17: action 'schedule' keeps the message in the drafts
            # folder (flags.scheduleDelivery) and the server sends it at that time.
            action,attrs['scheduleDate']='schedule',self._schedule_text(draft)
        try: response=self._call('%mbox/compose', {'action':action,'id':compose_id,'returnInfo':True,'autosaveHitCounter':True,'attrs':attrs})
        finally: self._compose_attachments.pop(compose_id,None)
        # A scheduled save is confirmed by the stored copy the server reports back.
        if action=='schedule' and not isinstance(response.get('scheduledSent'),dict): raise AppError('INVALID_RESPONSE')
        return self._retire_draft(draft,response)
    def _prepared(self,compose_id,draft):
        """Compose attrs with the uploads create_compose made for this exact draft."""
        from coremail_desktop.compose.inline import has_inline
        attrs=self._attrs(draft)
        prepared=self._compose_attachments.get(compose_id)
        if draft.attachments or (getattr(draft,'is_html',False) and has_inline(draft.body)):
            if prepared is None or prepared[0]!=draft: raise AppError('ATTACHMENT_UPLOAD_FAILED')
            attrs['attachments'],attrs['content']=prepared[1],prepared[2]
        return attrs
    def save_server_draft(self,draft):
        """Write a plaintext draft to the server's 草稿箱 (mbox:compose action save).
        Returns the draft's new message id. Encrypted drafts never reach the server."""
        if draft.encryption_required or draft.signature_required: raise AppError('DRAFT_ENCRYPTED_LOCAL')
        compose_id=self.create_compose(draft)
        try:
            attrs=self._prepared(compose_id,draft)
            response=self._call('%mbox/compose',{'action':'save','id':compose_id,'returnInfo':True,'attrs':attrs})
        finally: self._compose_attachments.pop(compose_id,None)
        if not isinstance(response.get('draftId'),str) or not response['draftId']: raise AppError('DRAFT_SAVE_UNCONFIRMED')
        self._retire_draft(draft,response)
        return response['draftId']
    def match_contacts(self,keyword,limit=8):
        """Organization directory autocomplete; shape verified read-only 2026-10-17."""
        if not isinstance(keyword,str) or not keyword.strip() or len(keyword)>100: return []
        rows=self._call('oab:autoMatch',{'keyword':keyword.strip(),'limit':max(1,min(int(limit),20))},True).get('var')
        if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
        return [{'name':str(r.get('true_name') or ''),'email':r['email']} for r in rows if isinstance(r,dict) and isinstance(r.get('email'),str) and '@' in r['email']]
    # Directory (read-only). oab:getDirectories {} returns [{id, name, ou:[{id, name, ou}]}]
    # (one root, 169 departments in 3 levels, ids unique and slash-free). oab:listEx takes
    # the dn webmail builds for a department at any depth, '<root id>/<department id>';
    # the root is listed by its own id. start/limit page (limit 1000 accepted) and total
    # is the department's full count, up to several thousand people. '@location' has one
    # '/'-separated segment per level plus the root (verified 2026-10-18,
    # analysis/contacts-search-shape{,2}-2026-10-18.json).
    DIRECTORY_PAGE=1000
    DIRECTORY_CAP=2000
    _root_name=None
    def directory_tree(self):
        value=self._call('oab:getDirectories',{},True).get('var')
        if not isinstance(value,list): raise AppError('INVALID_RESPONSE')
        self._root_name=next((str(r.get('name') or '') for r in value if isinstance(r,dict)),None)
        def build(nodes,root,depth):
            return [{'id':f"{root}/{n['id']}",'name':str(n.get('name') or ''),'children':build(n.get('ou'),root,depth+1) if depth<10 else []}
                    for n in nodes or [] if isinstance(n,dict) and isinstance(n.get('id'),(str,int)) and str(n['id']) and '/' not in str(n['id'])]
        return [{'id':str(r['id']),'name':str(r.get('name') or ''),'children':build(r.get('ou'),str(r['id']),1)}
                for r in value if isinstance(r,dict) and isinstance(r.get('id'),(str,int)) and str(r['id'])]
    def directory_members(self,dn):
        """A department's own people (not its sub-departments'), as {rows, total}."""
        if not isinstance(dn,str) or not dn or len(dn)>500: raise AppError('INVALID_CONTACT')
        rows,total=[],None
        while len(rows)<self.DIRECTORY_CAP:
            response=self._call('oab:listEx',{'dn':dn,'returnAttrs':['@id','@type','true_name','email','duty','@location'],'start':len(rows),'limit':self.DIRECTORY_PAGE},True)
            page=response.get('var')
            if not isinstance(page,list): raise AppError('INVALID_RESPONSE')
            if type(response.get('total')) is int: total=response['total']
            rows.extend(r for r in page if isinstance(r,dict))
            if len(page)<self.DIRECTORY_PAGE or (total is not None and len(rows)>=total): break
        if self._root_name is None: self.directory_tree()
        people=[]
        for r in rows:
            if not isinstance(r.get('email'),str) or '@' not in r['email']: continue
            parts=[part for part in (r.get('@location') if isinstance(r.get('@location'),str) else '').split('/') if part]
            if parts and parts[-1]==self._root_name: parts.reverse()  # Shown root first, whichever way the server writes it.
            people.append({'name':str(r.get('true_name') or ''),'email':r['email'],'title':str(r.get('duty') or ''),'dept':' / '.join(parts)})
        return {'rows':people,'total':total if total is not None else len(people)}
    # A colleague's card: oab:get {list, returnAttrs, forEmail} answers one row per
    # address, matched exactly (webmail's card lookup, X.03931.js). Verified read-only
    # 2026-10-18 (analysis/contact-details-shape2-2026-10-18.json): any attribute the
    # deployment does not expose fails the whole call with FA_FORBIDDEN (employee
    # number, manager, office ... are not exposed); of the allowed ones only
    # true_name, duty, @location and mobile_number were filled in the sample, the
    # others are asked for in case a department fills them.
    PERSON_ATTRS={'name':('true_name',),'title':('duty',),'mobile':('mobile_number',),'phone':('company_phone',),
                  'fax':('fax_number',),'address':('address',)}
    def person_details(self,email):
        if not isinstance(email,str) or '@' not in email or len(email)>254: raise AppError('INVALID_CONTACT')
        attrs=['@id','email','@location']+[a for names in self.PERSON_ATTRS.values() for a in names]
        rows=self._call('oab:get',{'list':[email],'returnAttrs':attrs,'forEmail':True},True).get('var')
        if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
        row=next((r for r in rows if isinstance(r,dict) and isinstance(r.get('email'),str) and r['email'].casefold()==email.casefold()),None)
        if row is None: return None
        text=lambda v: ', '.join(str(x) for x in v if x) if isinstance(v,list) else str(v).strip() if isinstance(v,(str,int)) else ''
        result={field:next((text(row.get(a)) for a in names if text(row.get(a))),'') for field,names in self.PERSON_ATTRS.items()}
        if self._root_name is None: self.directory_tree()
        parts=[part for part in text(row.get('@location')).split('/') if part]
        if parts and parts[-1]==self._root_name: parts.reverse()
        return {**result,'email':row['email'],'dept':' / '.join(parts),'uid':text(row.get('@id'))}
    def head_image(self,uid):
        """Raw image bytes, or None when the directory has no head image for uid.
        oab:getHeadImageData?uid= answered empty for every sampled person (2026-10-18);
        webmail's avatar/ URL serves one generic picture, so it is not used."""
        if not isinstance(uid,str) or not uid or len(uid)>200: return None
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        raw=self._session.get_raw('oab:getHeadImageData',uid=uid)
        if raw.lstrip()[:1] in (b'{',b'<',b''): return None  # A JSON/HTML answer means no image.
        return raw
    def personal_contacts(self):
        """Personal address book. pab:searchContacts {} lists every contact as
        {id, FN, 'EMAIL;PREF', groups} (verified 2026-10-18; pab:getAllGroups is empty here)."""
        rows=self._call('pab:searchContacts',{},True).get('var')
        if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
        return [{'name':str(r.get('FN') or ''),'email':r['EMAIL;PREF']} for r in rows
                if isinstance(r,dict) and isinstance(r.get('EMAIL;PREF'),str) and '@' in r['EMAIL;PREF']]
    # Mail rules, auto-reply, tags (shapes: coremail_desktop/protocol/rules.py) ---------------
    def mail_rules(self):
        """The account's mail rules as the server stores them, in their order."""
        rows=self._call('user:getMailRules',{},True).get('var')
        if rows is None: return []
        if not isinstance(rows,list) or any(not isinstance(r,dict) or type(r.get('id')) not in (int,str) for r in rows): raise AppError('INVALID_RESPONSE')
        return rows
    def add_mail_rule(self,rule):
        # Webmail: user:addMailRules {items:[rule]} with disabled defaulting to false (X.03931.js module 88812).
        self._call('user:addMailRules',{'items':[{'disabled':False,**rule}]})
        return True
    def update_mail_rule(self,rule):
        if type(rule.get('id')) not in (int,str): raise AppError('INVALID_RULE')
        self._call('user:updateMailRules',{'items':[rule]})
        return True
    def remove_mail_rule(self,rule_id):
        if type(rule_id) not in (int,str): raise AppError('INVALID_RULE')
        self._call('user:removeMailRules',{'ids':[rule_id]})
        return True
    def move_mail_rule(self,rule_id,ref_id):
        """Swap a rule with its neighbour ref, as webmail's up/down buttons do:
        user:adjustMailRuleOrder {id, ref, value:0} (S.03931.js setting.filter.letter)."""
        if type(rule_id) not in (int,str) or type(ref_id) not in (int,str): raise AppError('INVALID_RULE')
        self._call('user:adjustMailRuleOrder',{'id':rule_id,'ref':ref_id,'value':0})
        return True
    def set_mail_rule_disabled(self,rule_id,disabled):
        # Webmail's on/off switch sends only {id, disabled} through user:updateMailRules.
        return self.update_mail_rule({'id':rule_id,'disabled':bool(disabled)})
    def rule_matches(self,rule_id,folder_id,offset=0,limit=100):
        """Mail already in a folder that a saved rule matches, newest first: webmail lists them after
        saving a rule with mbox:listMessages {fid, ruleConditions:'@<id>'} (S.03931.js setting.filter.letter)."""
        if type(rule_id) not in (int,str) or offset<0 or not 1<=limit<=100: raise AppError('INVALID_RULE')
        response=self._call('mbox:listMessages',{'fid':self._fid(folder_id),'ruleConditions':f'@{rule_id}','start':offset,'limit':limit,
                                                 'mode':'count','order':'date','desc':True,'returnTotal':True,'summaryWindowSize':0},True)
        value=response.get('var')
        total=value.get('total') if isinstance(value,dict) else response.get('total')
        rows=value.get('messages') if isinstance(value,dict) else value
        if not isinstance(rows,list) or not isinstance(total,int) or total<0: raise AppError('INVALID_RESPONSE')
        messages=[]
        for item in rows:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id']: raise AppError('INVALID_RESPONSE')
            sender=item.get('from','')
            if isinstance(sender,list): sender=', '.join(str(x) for x in sender)
            messages.append(_summary(item,str(item.get('fid',folder_id)),sender))
        return {'messages':messages,'total':total}
    def auto_reply(self):
        from coremail_desktop.protocol.rules import AUTOREPLY_ATTRS
        value=self._call('user:getAttrs',{'optionalAttrIds':AUTOREPLY_ATTRS+['time_zone']},True).get('var')
        if not isinstance(value,dict): raise AppError('INVALID_RESPONSE')
        return value
    def set_auto_reply(self,attrs):
        # Webmail's updateUser: user:setAttrs {attrs} (public-login.js).
        self._call('user:setAttrs',{'attrs':dict(attrs)})
        return True
    def account_now(self):
        """The current time in the account's zone, as the server writes times."""
        if self._zone is None:
            value=self._call('user:getAttrs',{'optionalAttrIds':['time_zone']},True).get('var')
            self._zone=account_zone(value.get('time_zone') if isinstance(value,dict) else None)
        return datetime.now(timezone.utc).astimezone(self._zone).strftime('%Y-%m-%d %H:%M:%S')
    def list_tags(self,mboxa=''):
        return self._call('mbox:listTags',{**_box(mboxa)},True).get('var') or []
    def manage_tags(self,op,payload):
        """op: add/update -> [[id, {name, color}]], delete -> [id] (webmail 173.03931.js module 91424)."""
        if op not in ('add','update','delete') or not isinstance(payload,list) or not payload: raise AppError('INVALID_TAG')
        self._call('mbox:manageTags',{op:payload})
        return True
    def update_message_tags(self,message_ids,add=(),remove=(),mboxa=''):
        """mbox:updateMessageTags {ids, add, delete} (webmail X.03931.js je())."""
        ids=list(dict.fromkeys(message_ids))
        if not ids or len(ids)>100 or any(not isinstance(i,str) or not i for i in ids) or not (add or remove): raise AppError('INVALID_MAIL_ACTION')
        self._call('mbox:updateMessageTags',{'ids':ids,**({'add':list(add)} if add else {}),**({'delete':list(remove)} if remove else {}),**_box(mboxa)})
        return ids
    def tagged_messages(self,tag_id,offset,limit=50):
        """A tag's mail across folders, as webmail lists a tag view (X.03931.js ke(): fids
        without the trash, skipLockedFolders, tags). Verified read-only 2026-10-18."""
        if offset<0 or not 1<=limit<=100: raise AppError('INVALID_PAGE')
        fids=[int(f.remote_id) if f.remote_id.isdigit() else f.remote_id for f in self.list_folders() if f.remote_id!=self.TRASH_FOLDER_ID]
        response=self._call('mbox:listMessages',{'fids':fids,'skipLockedFolders':True,'tags':[str(tag_id)],'start':offset,'limit':limit,'mode':'count',
                                                 'order':'date','desc':True,'returnTotal':True,'returnTag':True,'summaryWindowSize':self.SUMMARY_WINDOW},True)
        rows=response.get('var'); total=response.get('total')
        if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
        result=[]
        for item in rows:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not isinstance(item.get('fid'),(int,str)): raise AppError('INVALID_RESPONSE')
            sender=item.get('from','')
            if isinstance(sender,list): sender=', '.join(str(x) for x in sender)
            result.append(_summary(item,str(item['fid']),sender))
        return {'messages':result,'total':total if type(total) is int else None}
    # Meeting invitations ---------------------------------------------------------------
    def invitation_meta(self,message_id):
        """The server's calendar record for an invitation mail. mbox:readMessage {id} returns
        var.calendar {calFid, calUid, isAttendee, meetingRequestMethod, meetingRequestStatus, ...}
        (verified read-only 2026-10-18 on an invitation from an external calendar; webmail's
        XT/jsp/readMessage.jsp returns the same record under var.mail.calendar)."""
        var=self._call('mbox:readMessage',{'id':message_id},True).get('var')
        calendar=var.get('calendar') if isinstance(var,dict) else None
        return calendar if isinstance(calendar,dict) else None
    def respond_invitation(self,message_id,answer):
        """Answer an invitation as webmail does: cal:respond {fid: calFid, eventId: calUid,
        type: 2 tentative / 3 accept / 4 decline, inform: true} (the server mails the
        organizer), then mark the mail with partStat."""
        kind={'tentative':2,'accept':3,'decline':4}.get(answer)
        if kind is None: raise AppError('INVALID_MAIL_ACTION')
        meta=self.invitation_meta(message_id)
        if not meta or meta.get('calUid') in (None,''): raise AppError('INVITE_UNAVAILABLE')
        params={'eventId':meta['calUid'],'type':kind,'inform':True}
        if meta.get('calFid') not in (None,''): params['fid']=meta['calFid']
        exception=(meta.get('meetingRequest') or {}).get('exceptionDate') if isinstance(meta.get('meetingRequest'),dict) else None
        if exception: params['exceptionDate']=exception
        self._call('cal:respond',params)
        try: self._call('mbox:updateMessageInfos',{'ids':[message_id],'attrs':{'partStat':kind}})
        except AppError: pass  # Only the list marker; the answer itself went out.
        return {'answer':answer,'type':kind}
    def push_credentials(self):
        getter=getattr(self._session,'push_credentials',None)
        return getter() if getter else None
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
        if draft is None or not (draft.encryption_required or draft.signature_required): raise AppError('ENCRYPTION_REQUIRED')
        attrs=self._attrs(draft,encrypted=True)
        compose_id=self._compose_session(draft,encrypted=True)
        fields={'returnInfo':'true','account':attrs['account'],'to':','.join(attrs['to']),'cc':','.join(attrs['cc']),'bcc':','.join(attrs['bcc']),
                'subject':attrs['subject'],'isHtml':'false','saveSentCopy':'true'}
        return self._retire_draft(draft,self._session.deliver_preencoded(compose_id,fields,payload.decode('ascii')))
    def delivery_history(self,message_id,mboxa=''):
        """Per-recipient delivery records of a sent message.

        user:listDeliveryHistory {mid} verified on the deployment 2026-10-17
        (analysis/send-features-shape-2026-10-17.json): var is a list of
        {to, to_name, result, rclResult, recallable, modtime, ...}. A send-to-self
        went result 100 (delivered) -> 509 after the copy was read (x % 100 == 9)."""
        if not isinstance(message_id,str) or not message_id: raise AppError('INVALID_MAIL_ACTION')
        rows=self._call('user:listDeliveryHistory',{'mid':message_id,**_box(mboxa)},True).get('var')
        if not isinstance(rows,list): raise AppError('INVALID_RESPONSE')
        result=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('to'),str): continue
            result.append({'to':row['to'],'name':str(row.get('to_name') or ''),
                           'result':row['result'] if type(row.get('result')) is int else None,
                           'recall':row['rclResult'] if type(row.get('rclResult')) is int else None,
                           'recallable':row.get('recallable') is True,'time':str(row.get('modtime') or row.get('rectime') or '')})
        return result
    def scheduled_messages(self,limit=100):
        return [m for m in self.list_messages(self.DRAFTS_FOLDER_ID,0,limit) if m.flags.get('scheduleDelivery')]
    def unschedule(self,message_id):
        """Cancel a scheduled send the way webmail does: reopen the draft and save it unscheduled.

        Verified 2026-10-17 (send-to-self): the draft stays in the drafts folder
        without flags.scheduleDelivery. Success is claimed only when a fresh list
        no longer shows the message as scheduled."""
        if not isinstance(message_id,str) or not message_id: raise AppError('INVALID_MAIL_ACTION')
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        if not any(m.remote_id==message_id for m in self.scheduled_messages()): raise AppError('NOT_SCHEDULED')
        try: value=self._session.open_draft(message_id)
        except CoremailError as error: raise AppError(error.code) from None
        keep={k:value[k] for k in ('account','to','cc','bcc','subject','content','isHtml','saveSentCopy','priority','requestReadReceipt','attachments','charset') if k in value}
        response=self._call('%mbox/compose',{'action':'save','id':value['id'],'returnInfo':True,'attrs':{**keep,'scheduleDate':None}})
        if not isinstance(response.get('draftId'),str): raise AppError('SCHEDULE_UNCONFIRMED')
        if any(m.remote_id==message_id for m in self.scheduled_messages()): raise AppError('SCHEDULE_UNCONFIRMED')
        return {'message_id':message_id,'draft_id':response['draftId']}
    RECALL_CODES = {'FA_UNSUPPORT_RECALL':'RECALL_UNSUPPORTED','FA_MAIL_EXPIRED':'RECALL_EXPIRED','FA_MAIL_NOT_FOUND':'RECALL_NOT_FOUND',
                    'FS_MTA7504':'RECALL_LATER','FS_DA0106':'RECALL_LATER','FS_MTA_N11':'RECALL_LATER'}
    def recall_message(self,message_id):
        """Recall a sent message, following webmail (analysis/web-login 173.03931.js, module 5524).

        Webmail first asks mbox:cancelDeliver {tinfo, mid} (stops delivery still in
        the queue); on FA_MAIL_NOT_FOUND it calls mbox:recallMessage2 {mid, tid},
        whose var maps each recipient to a mail_rcl_state code. Verified on the
        deployment only as far as send-to-self allows: such a message is
        recallable:false and both calls answer FA_MAIL_NOT_FOUND / FA_UNSUPPORT_RECALL.
        Nothing is reported as recalled without the server's per-recipient answer."""
        if not isinstance(message_id,str) or not message_id: raise AppError('INVALID_MAIL_ACTION')
        if self._session is None: raise AppError('NOT_AUTHENTICATED')
        infos=self._call('mbox:getMessageInfos',{'ids':[message_id]},True).get('var')
        info=next((i for i in infos if isinstance(i,dict) and i.get('id')==message_id),None) if isinstance(infos,list) else None
        if info is None: raise AppError('RECALL_NOT_FOUND')
        tinfo=info.get('tinfo') if isinstance(info.get('tinfo'),str) and info.get('tinfo') else None
        params={'mid':message_id}
        if tinfo:
            cancelled=self._session.call_code('mbox:cancelDeliver',{'tinfo':tinfo,'mid':message_id})
            if cancelled.get('code')=='S_OK': return {'cancelled':True,'states':{}}
            if cancelled.get('code')!='FA_MAIL_NOT_FOUND': raise AppError(self.RECALL_CODES.get(cancelled.get('code'),'RECALL_UNCONFIRMED'))
            params['tid']=tinfo.rsplit(':',1)[-1]
        response=self._session.call_code('mbox:recallMessage2',params)
        if response.get('code')!='S_OK': raise AppError(self.RECALL_CODES.get(response.get('code'),'RECALL_UNCONFIRMED'))
        states=response.get('var')
        if not isinstance(states,dict) or not states: raise AppError('RECALL_UNCONFIRMED')
        return {'cancelled':False,'states':{str(k):v for k,v in states.items() if isinstance(k,str) and '@' in k}}
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
        self._root_name=None
