import unittest
from coremail_desktop.diagnostics.redaction import sanitize_event
class DiagnosticsTests(unittest.TestCase):
    def test_allowlist_drops_secrets_in_nested_payloads(self):
        value = sanitize_event({'operation':'list_messages','operation_id':'abc','elapsed_ms':12,'error_code':'NETWORK_ERROR','body':'secretbody','sid':'secretsid','nested':{'password':'secretpass'},'path':'C:/Users/private'})
        self.assertEqual(value,{'operation':'list_messages','operation_id':'abc','elapsed_ms':12,'error_code':'NETWORK_ERROR'})
