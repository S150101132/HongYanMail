import unittest
from datetime import datetime, timezone
from email.message import EmailMessage

from coremail_desktop.mime.icalendar import find_invitation, parse_invitation

OUTLOOK = """BEGIN:VCALENDAR\r
METHOD:REQUEST\r
PRODID:Microsoft Exchange Server 2010\r
VERSION:2.0\r
BEGIN:VTIMEZONE\r
TZID:China Standard Time\r
BEGIN:STANDARD\r
DTSTART:16010101T000000\r
TZOFFSETFROM:+0800\r
TZOFFSETTO:+0800\r
END:STANDARD\r
END:VTIMEZONE\r
BEGIN:VEVENT\r
ORGANIZER;CN="张 晓明":mailto:zhangxm@example.invalid\r
ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN=李华:mailto:l\r
 ihua@example.invalid\r
ATTENDEE;ROLE=OPT-PARTICIPANT;PARTSTAT=ACCEPTED;CN="Wang, Fang":mailto:wangfang@example.invalid\r
SUMMARY;LANGUAGE=zh-CN:设计评审\\, 第二轮\r
DTSTART;TZID=China Standard Time:20261020T150000\r
DTEND;TZID=China Standard Time:20261020T163000\r
UID:040000008200E00074C5B7101A82E008\r
SEQUENCE:2\r
LOCATION:3 号会议室\r
DESCRIPTION:议程：\\n1. 交互\\n2. 接口\r
STATUS:CONFIRMED\r
END:VEVENT\r
END:VCALENDAR\r
"""

DST = """BEGIN:VCALENDAR
METHOD:REQUEST
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:DAYLIGHT
DTSTART:19700329T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:a
DTSTART;TZID=Europe/Berlin:20260720T100000
DTEND;TZID=Europe/Berlin:20261220T100000
SUMMARY:x
END:VEVENT
END:VCALENDAR
"""


def ms(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)


class InvitationParseTest(unittest.TestCase):
    def test_outlook_request(self):
        card = parse_invitation(OUTLOOK.encode('utf-8'))
        self.assertEqual(card['method'], 'REQUEST')
        self.assertFalse(card['cancelled'])
        self.assertEqual(card['summary'], '设计评审, 第二轮')
        self.assertEqual(card['location'], '3 号会议室')
        self.assertEqual(card['description'], '议程：\n1. 交互\n2. 接口')
        self.assertEqual(card['start'], ms(2026, 10, 20, 7, 0))  # 15:00 +08:00
        self.assertEqual(card['end'], ms(2026, 10, 20, 8, 30))
        self.assertEqual(card['organizer'], {'name': '张 晓明', 'email': 'zhangxm@example.invalid'})
        self.assertEqual([a['email'] for a in card['attendees']], ['lihua@example.invalid', 'wangfang@example.invalid'])
        self.assertEqual(card['attendees'][1]['name'], 'Wang, Fang')
        self.assertEqual(card['attendees'][1]['partstat'], 'ACCEPTED')
        self.assertTrue(card['attendees'][0]['rsvp'])
        self.assertEqual(card['sequence'], 2)

    def test_daylight_rules_from_vtimezone(self):
        card = parse_invitation(DST)
        self.assertEqual(card['start'], ms(2026, 7, 20, 8, 0))   # summer, +02:00
        self.assertEqual(card['end'], ms(2026, 12, 20, 9, 0))    # winter, +01:00

    def test_utc_all_day_duration_and_unknown_zone(self):
        base = 'BEGIN:VCALENDAR\nMETHOD:CANCEL\nBEGIN:VEVENT\nUID:b\nSTATUS:CANCELLED\n{}\nEND:VEVENT\nEND:VCALENDAR\n'
        card = parse_invitation(base.format('DTSTART:20261021T010000Z\nDURATION:PT45M'))
        self.assertTrue(card['cancelled'])
        self.assertEqual(card['end'] - card['start'], 45 * 60 * 1000)
        card = parse_invitation(base.format('DTSTART;VALUE=DATE:20261022\nDTEND;VALUE=DATE:20261023'))
        self.assertTrue(card['allDay'])
        self.assertEqual((card['startDate'], card['endDate']), ('2026-10-22', '2026-10-23'))
        card = parse_invitation(base.format('DTSTART;TZID=Mars/Olympus:20261022T090000'))
        self.assertIsNone(card['start'])
        self.assertEqual(card['floating'], '2026-10-22 09:00')
        card = parse_invitation(base.format('DTSTART;TZID="(UTC+08:00) Beijing":20261022T090000'))
        self.assertEqual(card['start'], ms(2026, 10, 22, 1, 0))

    def test_no_event_and_garbage(self):
        self.assertIsNone(parse_invitation('BEGIN:VCALENDAR\nEND:VCALENDAR'))
        self.assertIsNone(parse_invitation('hello'))

    def test_find_in_message(self):
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = 'a@example.invalid', 'b@example.invalid', '邀请'
        message.set_content('请参加')
        message.add_alternative(OUTLOOK, subtype='calendar', params={'method': 'REQUEST'})
        card = find_invitation(bytes(message))
        self.assertEqual(card['summary'], '设计评审, 第二轮')
        plain = EmailMessage(); plain['From'] = 'a@example.invalid'; plain.set_content('x')
        self.assertIsNone(find_invitation(bytes(plain)))


if __name__ == '__main__':
    unittest.main()
