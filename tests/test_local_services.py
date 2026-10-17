import unittest
from unittest.mock import patch
from coremail_desktop.errors import AppError


class LocalServicesTests(unittest.TestCase):
    def test_denied_storage_does_not_block_service_creation(self):
        from coremail_desktop.storage.services import local_services
        with patch('coremail_desktop.storage.services.DraftStore',side_effect=PermissionError), patch('coremail_desktop.storage.services.SendJournal',side_effect=PermissionError):
            services=local_services(object(),'unused')
            for name,params in [('list_drafts',{}),('recover_pending',{}),('request_send',{'draft':object()})]:
                with self.assertRaises(AppError) as error: services[name](**params)
                self.assertEqual(error.exception.code,'LOCAL_STORAGE_UNAVAILABLE')

    def test_failed_initialization_can_retry(self):
        from coremail_desktop.storage.services import local_services
        with patch('coremail_desktop.storage.services.DraftStore') as store:
            store.side_effect=[PermissionError(),store.return_value]
            store.return_value.list_summaries.return_value=[]
            services=local_services(object(),'unused')
            with self.assertRaises(AppError): services['list_drafts']()
            self.assertEqual(services['list_drafts'](),[])
