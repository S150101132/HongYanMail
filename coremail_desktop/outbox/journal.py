import base64
from dataclasses import replace
from coremail_desktop.models import SendJob
from coremail_desktop.drafts.store import ProtectedRecords, draft_dict, decode_draft

class SendJournal(ProtectedRecords):
    def persist(self, job):
        value = {key: getattr(job, key) for key in job.__dataclass_fields__}
        value['draft'] = draft_dict(job.draft)
        value['payload'] = base64.b64encode(job.payload).decode('ascii') if job.payload is not None else None
        with self._lock: self._write(job.local_id, value)
    def load(self, local_id):
        with self._lock: value = self._read(local_id)
        value['draft'] = decode_draft(value['draft'])
        value['envelope_recipients'] = tuple(value['envelope_recipients'])
        value['payload'] = base64.b64decode(value['payload'], validate=True) if value['payload'] is not None else None
        return SendJob(**value)
    def recover(self):
        jobs = []
        for local_id in self.list_ids():
            job = self.load(local_id)
            if job.state in ('Submitting', 'Reconciling'):
                job = replace(job, state='Unknown')
                self.persist(job)
            elif job.state == 'Preparing':
                job = replace(job, state='Failed')
                self.persist(job)
            jobs.append(job)
        return jobs
