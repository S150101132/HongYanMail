import threading
import time
import unittest
from PySide6.QtCore import QCoreApplication
from coremail_desktop.session.controller import SessionController
from coremail_desktop.errors import AppError

class SessionTests(unittest.TestCase):
    def test_failed_service_initialization_can_retry_without_partial_adapter(self):
        from coremail_desktop.session.worker import SessionWorker
        adapters=[]
        class Adapter:
            closed=False
            def close(self): self.closed=True
        def factory():
            adapter=Adapter(); adapters.append(adapter); return adapter
        def services(adapter):
            if len(adapters)==1: raise OSError('private local path')
            return {'ready':lambda:True}
        worker=SessionWorker(factory,services)
        with self.assertRaises(OSError): worker._initialize()
        self.assertIsNone(worker.adapter)
        self.assertTrue(adapters[0].closed)
        worker._initialize()
        self.assertTrue(worker.services['ready']())

    def test_internal_failure_diagnostics_do_not_contain_exception_values(self):
        from coremail_desktop.session.worker import SessionWorker
        class Adapter:
            def login(self): raise ValueError('secret-password-and-cookie')
            def close(self): pass
        worker=SessionWorker(Adapter)
        errors=[]
        worker.failed.connect(lambda task,generation,error:errors.append(error))
        worker.execute('task','login',{},0)
        self.assertEqual(errors[0].code,'INTERNAL_ERROR')
        self.assertEqual(errors[0].category,'login:ValueError')

    def test_crypto_error_code_is_preserved_without_exception_text(self):
        from coremail_desktop.crypto.models import CryptoError
        app = QCoreApplication.instance() or QCoreApplication([])
        class Adapter:
            def close(self): pass
        def decrypt(): raise CryptoError('CERT_LOCKED')
        controller = SessionController(Adapter,{'decrypt':decrypt})
        failures = []
        controller.failed.connect(lambda *args:failures.append(args))
        controller.submit('decrypt',{},0)
        deadline = time.monotonic()+3
        while not failures and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        controller.close()
        self.assertEqual(failures[0][2].code,'CERT_LOCKED')
    def test_worker_owns_session_and_preserves_generation(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        threads = []
        class Adapter:
            def __init__(self): threads.append(threading.get_ident())
            def list_folders(self): threads.append(threading.get_ident()); return []
            def close(self): threads.append(threading.get_ident())
        controller = SessionController(Adapter)
        results = []
        controller.completed.connect(lambda *args:results.append(args))
        first = controller.submit('list_folders',{},7)
        controller.submit('list_folders',{},9)
        deadline = time.monotonic()+3
        while len(results)<2 and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        controller.close()
        self.assertEqual([(x[0],x[1]) for x in results][0],(first,7))
        self.assertEqual(len(set(threads)),1)
        self.assertNotEqual(threads[0],threading.get_ident())
    def test_multiple_expired_requests_emit_one_auth_prompt(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        class Adapter:
            def list_folders(self): raise AppError('NOT_AUTHENTICATED')
            def close(self): pass
        controller = SessionController(Adapter)
        prompts, failures = [],[]
        controller.auth_required.connect(lambda:prompts.append(1))
        controller.failed.connect(lambda *args:failures.append(args))
        for _ in range(3): controller.submit('list_folders',{},0)
        deadline = time.monotonic()+3
        while len(failures)<3 and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        controller.close()
        self.assertEqual(len(prompts),1)
        self.assertEqual(len(failures),3)
