"""Daily English · 每日一句: one sentence a day, written into the window title bar.

The sentence comes from qhsou's 金山词霸 feed (a plain GET: nothing about the user is sent). The built-in list below
stands in until the feed answers, and when it cannot be reached; there the date alone decides,
so a day keeps its sentence through a restart. Its phonetics and scenes are kept for a later view.
Add new built-in sentences at the end.
"""
import datetime
import hashlib
import http.client
import json
import unicodedata
import urllib.request

API_URL = 'https://api.qhsou.com/api/ciba.php'
API_SECONDS = 20
TEXT_CHARS = 200

FIELDS = ('id', 'tag', 'en', 'ipa', 'zh', 'scene')
_SENTENCES = (
    ('e001', '会议', "Let's circle back to this after the meeting.", "/lɛts ˈsɝkəl bæk tu ðɪs ˈæftɚ ðə ˈmitɪŋ/", '这个我们会后再聊。', '会上某个话题一时谈不完，礼貌地先放一放。'),
    ('e002', '会议', 'Could you walk us through the numbers?', '/kʊd ju wɔk ʌs θru ðə ˈnʌmbɚz/', '能带我们过一下这些数字吗？', '请对方逐项讲解报表或报价。'),
    ('e003', '会议', "I'd like to add one thing here.", '/aɪd laɪk tu æd wʌn θɪŋ hɪr/', '这里我想补充一点。', '别人讲完后自然地接话，不显得打断。'),
    ('e004', '会议', "Let's take this offline.", '/lɛts teɪk ðɪs ˌɔfˈlaɪn/', '这个我们会下单独聊。', '只跟两三个人有关的细节，不占大家的时间。'),
    ('e005', '会议', 'Are we all on the same page?', '/ɑr wi ɔl ɑn ðə seɪm peɪdʒ/', '大家的理解一致吗？', '讲完方案后确认各方理解没有偏差。'),
    ('e006', '会议', 'Sorry, could you repeat that last part?', '/ˈsɑri kʊd ju rɪˈpit ðæt læst pɑrt/', '不好意思，最后那部分能再说一遍吗？', '电话会信号不好或没听清时使用。'),
    ('e007', '会议', "Let's wrap up with the action items.", '/lɛts ræp ʌp wɪð ði ˈækʃən ˈaɪtəmz/', '最后我们过一下待办事项。', '会议收尾，确认谁做什么。'),
    ('e008', '会议', "I'll keep this brief.", '/aɪl kip ðɪs brif/', '我长话短说。', '汇报开头，让大家知道不会占用太久。'),
    ('e009', '会议', "Who's going to take the lead on this?", '/huz ˈɡoʊɪŋ tu teɪk ðə lid ɑn ðɪs/', '这件事由谁牵头？', '任务分工时明确负责人。'),
    ('e010', '会议', 'Can we move on to the next item?', '/kæn wi muv ɑn tu ðə nɛkst ˈaɪtəm/', '我们进入下一项吧？', '当前议题已有结论，推进议程。'),
    ('e011', '会议', 'Let me share my screen.', '/lɛt mi ʃɛr maɪ skrin/', '我来共享一下屏幕。', '线上会议开始演示前。'),
    ('e012', '会议', "You're on mute.", '/jʊr ɑn mjut/', '你没开麦。', '线上会议提醒对方静音了。'),
    ('e013', '会议', 'I see your point, but I have a concern.', '/aɪ si jʊr pɔɪnt bʌt aɪ hæv ə kənˈsɝn/', '我明白你的意思，不过我有点担心。', '先认可对方，再礼貌地提出不同意见。'),
    ('e014', '会议', "Let's put a pin in that for now.", '/lɛts pʊt ə pɪn ɪn ðæt fɔr naʊ/', '这点我们先放一放。', '话题重要但现在不是时候，稍后再回来谈。'),
    ('e015', '项目', "We're on track to finish by Friday.", '/wɪr ɑn træk tu ˈfɪnɪʃ baɪ ˈfraɪdeɪ/', '我们按计划周五前能完成。', '向上级或客户汇报进度正常。'),
    ('e016', '项目', "We're running behind schedule.", '/wɪr ˈrʌnɪŋ bɪˈhaɪnd ˈskɛdʒul/', '我们的进度落后了。', '如实说明项目延迟，后面通常接原因和对策。'),
    ('e017', '项目', "What's blocking you right now?", '/wʌts ˈblɑkɪŋ ju raɪt naʊ/', '你现在卡在哪儿了？', '站会或跟进时了解同事遇到的障碍。'),
    ('e018', '项目', 'This is a dependency we need to flag.', '/ðɪs ɪz ə dɪˈpɛndənsi wi nid tu flæɡ/', '这是个需要提前提示的依赖项。', '提醒各方：这件事要等别的工作先完成。'),
    ('e019', '项目', "Let's break this down into smaller tasks.", '/lɛts breɪk ðɪs daʊn ˈɪntu ˈsmɔlɚ tæsks/', '我们把它拆成几个小任务吧。', '工作太大无从下手时。'),
    ('e020', '项目', 'We need to push the deadline back.', '/wi nid tu pʊʃ ðə ˈdɛdˌlaɪn bæk/', '我们得把截止日期往后推。', '和相关方沟通延期。'),
    ('e021', '项目', 'Can you give me a rough estimate?', '/kæn ju ɡɪv mi ə rʌf ˈɛstəmət/', '能给我一个大概的估计吗？', '询问工期或费用，不要求十分精确。'),
    ('e022', '项目', "That's out of scope for this phase.", '/ðæts aʊt əv skoʊp fɔr ðɪs feɪz/', '这不在这一阶段的范围内。', '客户或同事提出额外要求时划清边界。'),
    ('e023', '项目', "Let's agree on the next steps.", '/lɛts əˈɡri ɑn ðə nɛkst stɛps/', '我们把下一步定下来吧。', '讨论结束前落实后续动作。'),
    ('e024', '项目', "I'll follow up with the vendor today.", '/aɪl ˈfɑloʊ ʌp wɪð ðə ˈvɛndɚ təˈdeɪ/', '我今天去跟供应商跟进。', '承担一项跟催工作。'),
    ('e025', '项目', 'We should escalate this to management.', '/wi ʃʊd ˈɛskəˌleɪt ðɪs tu ˈmænɪdʒmənt/', '这件事我们应该上报给管理层。', '问题超出当前权限，需要上级介入。'),
    ('e026', '项目', 'The site team is waiting on your approval.', '/ðə saɪt tim ɪz ˈweɪtɪŋ ɑn jʊr əˈpruvəl/', '现场团队在等你批准。', '催审批时点明影响，语气仍然客气。'),
    ('e027', '项目', "Let's align on priorities first.", '/lɛts əˈlaɪn ɑn praɪˈɔrətiz fɝst/', '我们先对齐一下优先级。', '事情太多、各方想法不一致时。'),
    ('e028', '项目', 'Is there a workaround for now?', '/ɪz ðɛr ə ˈwɝkəˌraʊnd fɔr naʊ/', '目前有临时解决办法吗？', '根本问题一时解决不了，先保证工作继续。'),
    ('e029', '项目', "We can't afford another delay.", '/wi kænt əˈfɔrd əˈnʌðɚ dɪˈleɪ/', '我们不能再延期了。', '强调时间紧迫，语气坚定。'),
    ('e030', '项目', 'Please keep me in the loop.', '/pliz kip mi ɪn ðə lup/', '有进展请随时告诉我。', '邮件结尾，希望持续知情。'),
    ('e031', '职场', "Just a quick reminder about tomorrow's deadline.", '/dʒʌst ə kwɪk rɪˈmaɪndɚ əˈbaʊt təˈmɑroʊz ˈdɛdˌlaɪn/', '提醒一下明天的截止日期。', '催办邮件的开头，不显得咄咄逼人。'),
    ('e032', '职场', 'Thanks for getting back to me so quickly.', '/θæŋks fɔr ˈɡɛtɪŋ bæk tu mi soʊ ˈkwɪkli/', '谢谢你这么快回复我。', '回复邮件的开头。'),
    ('e033', '职场', 'Please let me know if you have any questions.', '/pliz lɛt mi noʊ ɪf ju hæv ˈɛni ˈkwɛstʃənz/', '如有问题请告诉我。', '邮件最常用的结尾之一。'),
    ('e034', '职场', "I'll get back to you by end of day.", '/aɪl ɡɛt bæk tu ju baɪ ɛnd əv deɪ/', '我今天下班前回复你。', '暂时答不了，先给出明确的回复时间。'),
    ('e035', '职场', 'Could you take a look when you have a moment?', '/kʊd ju teɪk ə lʊk wɛn ju hæv ə ˈmoʊmənt/', '你有空时能帮忙看一下吗？', '请同事审阅文件，不施加压力。'),
    ('e036', '职场', 'Sorry for the late reply.', '/ˈsɑri fɔr ðə leɪt rɪˈplaɪ/', '抱歉回复晚了。', '隔了一段时间才回邮件。'),
    ('e037', '职场', 'As discussed, please find the report attached.', '/æz dɪˈskʌst pliz faɪnd ðə rɪˈpɔrt əˈtætʃt/', '如之前沟通的，报告见附件。', '会后或电话后发送材料。'),
    ('e038', '职场', "I'm afraid I can't commit to that date.", '/aɪm əˈfreɪd aɪ kænt kəˈmɪt tu ðæt deɪt/', '恐怕我无法承诺那个日期。', '委婉拒绝不现实的时间要求。'),
    ('e039', '职场', 'Let me double-check and confirm.', '/lɛt mi ˈdʌbəl tʃɛk ænd kənˈfɝm/', '我再核实一下，然后确认。', '不确定时不急着下结论。'),
    ('e040', '职场', 'It would be great if you could help with this.', '/ɪt wʊd bi ɡreɪt ɪf ju kʊd hɛlp wɪð ðɪs/', '如果你能帮忙就太好了。', '客气地请求协助。'),
    ('e041', '职场', "I'm tied up at the moment.", '/aɪm taɪd ʌp æt ðə ˈmoʊmənt/', '我现在抽不开身。', '正在忙，稍后再处理。'),
    ('e042', '职场', 'Could we reschedule to next week?', '/kʊd wi riˈskɛdʒul tu nɛkst wik/', '我们能改到下周吗？', '原定时间不合适，提议改期。'),
    ('e043', '职场', "I'll loop in the right person.", '/aɪl lup ɪn ðə raɪt ˈpɝsən/', '我把合适的人拉进来。', '问题不归自己管，转给对口同事并抄送。'),
    ('e044', '职场', "Let's not reinvent the wheel.", '/lɛts nɑt ˌriɪnˈvɛnt ðə wil/', '我们别重复造轮子。', '已有现成做法时，建议直接沿用。'),
    ('e045', '职场', 'That makes sense to me.', '/ðæt meɪks sɛns tu mi/', '我觉得有道理。', '表示理解和认同，比 I agree 更自然。'),
    ('e046', '职场', 'I appreciate your help on this.', '/aɪ əˈpriʃiˌeɪt jʊr hɛlp ɑn ðɪs/', '感谢你在这件事上的帮助。', '比 thank you 更郑重的感谢。'),
    ('e047', '职场', 'Good catch, I missed that.', '/ɡʊd kætʃ aɪ mɪst ðæt/', '发现得好，我漏掉了。', '同事指出错误时大方承认。'),
    ('e048', '职场', "I'm happy to help with that.", '/aɪm ˈhæpi tu hɛlp wɪð ðæt/', '这个我很乐意帮忙。', '答应别人的请求。'),
    ('e049', '日常', "How's your day going so far?", '/haʊz jʊr deɪ ˈɡoʊɪŋ soʊ fɑr/', '今天过得怎么样？', '和同事寒暄，开启闲聊。'),
    ('e050', '日常', 'Long time no see!', '/lɔŋ taɪm noʊ si/', '好久不见！', '再次遇到许久没见的同事或朋友。'),
    ('e051', '日常', "I'm heading out for lunch, want to join?", '/aɪm ˈhɛdɪŋ aʊt fɔr lʌntʃ wɑnt tu dʒɔɪn/', '我去吃午饭，一起吗？', '随口邀请同事一起吃饭。'),
    ('e052', '日常', 'No worries, take your time.', '/noʊ ˈwɝiz teɪk jʊr taɪm/', '没关系，不着急。', '对方道歉或让你等时。'),
    ('e053', '日常', 'Have a safe trip back.', '/hæv ə seɪf trɪp bæk/', '回去路上注意安全。', '送别出差的同事或客户。'),
    ('e054', '日常', 'It slipped my mind, sorry.', '/ɪt slɪpt maɪ maɪnd ˈsɑri/', '抱歉，我忘了。', '忘了某件事时，比 I forgot 委婉。'),
    ('e055', '日常', 'Let me get this one.', '/lɛt mi ɡɛt ðɪs wʌn/', '这次我来请。', '吃饭或喝咖啡时抢着买单。'),
    ('e056', '日常', 'Have a good weekend!', '/hæv ə ɡʊd ˈwikˌɛnd/', '周末愉快！', '一周最后一天下班时道别。'),
    ('e057', '日常', 'Could you give me a hand with this?', '/kʊd ju ɡɪv mi ə hænd wɪð ðɪs/', '能帮我个忙吗？', '当面请同事搭把手。'),
    ('e058', '日常', "I'm still getting used to the heat here.", '/aɪm stɪl ˈɡɛtɪŋ just tu ðə hit hɪr/', '我还在适应这里的高温。', '在海外现场和当地同事聊天气。'),
    ('e059', '日常', 'What do you usually do for lunch around here?', '/wʌt du ju ˈjuʒuəli du fɔr lʌntʃ əˈraʊnd hɪr/', '你们这边午饭一般怎么解决？', '到新地方和当地同事搭话。'),
    ('e060', '日常', 'Thanks for having me.', '/θæŋks fɔr ˈhævɪŋ mi/', '谢谢邀请我来。', '受邀参会或拜访结束时。'),
)
SENTENCES = tuple(dict(zip(FIELDS, row)) for row in _SENTENCES)
BY_ID = {s['id']: s for s in SENTENCES}
# A fixed shuffle: work and everyday sentences are mixed.
ORDER = sorted(BY_ID, key=lambda i: hashlib.sha256(i.encode('ascii')).digest())


def today_sentence(day=None):
    """The same sentence all day, the next one tomorrow; each once before any comes back."""
    return BY_ID[ORDER[(day or datetime.date.today()).toordinal() % len(ORDER)]]


def line(en, zh):
    return f'{en}　{zh}'


def today_line(day=None):
    sentence = today_sentence(day)
    return line(sentence['en'], sentence['zh'])


def _get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Hongyan'})
    with urllib.request.urlopen(request, timeout=API_SECONDS) as response:
        return response.read(200_000).decode('utf-8', 'replace')


def _clean(value):
    """Outside text going into a window title: one line, no control or direction characters."""
    if not isinstance(value, str):
        return ''
    text = ''.join(' ' if unicodedata.category(c) in ('Cc', 'Zl', 'Zp') else c for c in value if unicodedata.category(c) not in ('Cf', 'Cs', 'Co', 'Cn'))
    return ' '.join(text.split())


def fetch(day, get=None):
    """{'en', 'zh'} for that date, or None for any failure (the caller keeps the built-in line).
    Verified 2026-11-01: {"date": "2026-11-01", "en": ..., "zh": ..., "audio": ..., "image": ...};
    it always answers the server's own today, so an answer dated before `day` is not yet today's
    sentence (a later one is fine: the server's clock runs ahead of Saudi Arabia's)."""
    try:
        answer = json.loads((get or _get)(API_URL))
        if not isinstance(answer, dict) or datetime.date.fromisoformat(answer.get('date')) < day:
            return None
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        return None
    en, zh = _clean(answer.get('en')), _clean(answer.get('zh'))
    return {'en': en, 'zh': zh} if en and zh and len(en) <= TEXT_CHARS and len(zh) <= TEXT_CHARS else None
