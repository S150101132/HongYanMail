import unittest
from email.message import EmailMessage

from probe.connectivity import verify_read, verify_roundtrip, build_test_payload


def mime(subject='Existing mail', body='content', sender='person@example.test'):
    message = EmailMessage()
    message['From'] = sender
    message['To'] = 'wangbiao@inspur.com'
    message['Subject'] = subject
    message['Message-ID'] = '<probe@example.test>'
    message.set_content(body)
    return message.as_bytes()


class FakeSession:
    def __init__(self, messages=None, fail_send=False, raw=None):
        self.calls = []
        self.messages = messages if messages is not None else [{'id': 'old', 'subject': 'Private existing subject'}]
        self.raw = raw or mime()
        self.fail_send = fail_send
        self.sent = None

    def call(self, func, payload):
        self.calls.append((func, payload))
        if func == 'mbox:getAllFolders':
            return {'code': 'S_OK', 'var': [{'id': 0, 'name': 'Inbox'}]}
        if func == 'mbox:listMessages':
            return {'code': 'S_OK', 'var': self.messages}
        if func == '%mbox/compose':
            if self.fail_send:
                raise RuntimeError('server send failure')
            if payload['action'] == 'continue':
                return {'code': 'S_OK', 'var': {'id': 'new-compose'}}
            self.sent = payload
            return {'code': 'S_OK', 'var': 'accepted-only'}
        raise AssertionError(func)

    def get_message(self, mid):
        return self.raw


class ConnectivityTests(unittest.TestCase):
    def test_read_report_contains_counts_and_hash_but_no_subject_or_body(self):
        result = verify_read(FakeSession())
        self.assertEqual(result['read']['status'], 'passed')
        self.assertEqual(result['listed_messages'], 1)
        self.assertEqual(len(result['read']['sha256']), 64)
        self.assertNotIn('Private', str(result))
        self.assertNotIn('content', str(result))

    def test_empty_inbox_is_not_claimed_to_prove_body_download(self):
        result = verify_read(FakeSession(messages=[]))
        self.assertEqual(result['read']['status'], 'skipped_empty_inbox')

    def test_an_html_login_response_is_not_accepted_as_mail(self):
        with self.assertRaises(ValueError):
            verify_read(FakeSession(raw=b'<html><form>Login required</form></html>'))

    def test_send_payload_can_only_address_the_authenticated_account(self):
        payload = build_test_payload('wangbiao@inspur.com', 'probe-123')
        self.assertEqual(payload['attrs']['to'], ['wangbiao@inspur.com'])
        self.assertFalse(payload['attrs'].get('cc'))
        self.assertFalse(payload['attrs'].get('bcc'))
        self.assertIn('probe-123', payload['attrs']['subject'])

    def test_server_acceptance_is_not_claimed_as_delivery(self):
        result = verify_roundtrip(FakeSession(), 'wangbiao@inspur.com', timeout_seconds=0)
        self.assertEqual(result['send']['status'], 'accepted')
        self.assertEqual(result['delivery']['status'], 'not_observed')

    def test_sent_folder_cannot_be_used_as_inbox_delivery_proof(self):
        session = FakeSession()
        with self.assertRaises(ValueError):
            verify_roundtrip(session, 'wangbiao@inspur.com', folder_id=1)
        self.assertEqual(session.calls, [])

    def test_roundtrip_requires_matching_mime_marker_and_sender(self):
        session = FakeSession()
        def sent_and_delivered(func, payload):
            session.calls.append((func, payload))
            if func == '%mbox/compose':
                if payload['action'] == 'continue':
                    return {'code': 'S_OK', 'var': 'new-compose'}
                session.sent = payload
                return {'code': 'S_OK', 'var': 'new'}
            if func == 'mbox:listMessages':
                return {'code': 'S_OK', 'var': [{'id': 'new', 'subject': session.sent['attrs']['subject']}]}
            raise AssertionError(func)
        session.call = sent_and_delivered
        session.get_message = lambda mid: mime(session.sent['attrs']['subject'], session.sent['attrs']['content'], sender='wangbiao@inspur.com')
        result = verify_roundtrip(session, 'wangbiao@inspur.com', timeout_seconds=1)
        self.assertEqual(result['delivery']['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
