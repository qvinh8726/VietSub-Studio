"""Durable uploader state-machine tests, never posts to an external account."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from vietsub.uploader.queue import UploadQueue, metadata, schedule_time


class QueueTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vietsub-upload-queue-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.video=self.root/'video.mp4';self.video.write_bytes(b'video')
        self.path=self.root/'queue.json';self.queues=[]
        self.profiles=[{'id':'profile-1','platform':'youtube'},{'id':'profile-2','platform':'tiktok'}]
        self.source={'video':str(self.video),'job_id':'vietsub-job'}
        self.meta={'title':'Tiêu đề','description':'Mô tả','hashtags':['#Việt']}

    def tearDown(self):
        for queue in self.queues:queue.shutdown()

    def queue(self,execute):
        queue=UploadQueue(self.path,execute);self.queues.append(queue);return queue

    def test_metadata_and_schedule_strict_offsets(self):
        for value in ({'title':''},{'title':'ok','hashtags':'#bad'},{'title':'ok','hashtags':['#bad space']},
                      {'title':'ok','command':'shell'}):
            with self.subTest(value=value),self.assertRaises(ValueError):metadata(value)
        with self.assertRaises(ValueError):schedule_time('2030-01-01T12:00:00')
        self.assertEqual(schedule_time('2026-11-19T15:00:00+07:00',now=datetime(2026,11,19,7,tzinfo=timezone.utc).timestamp()),
                         datetime(2026,11,19,8,tzinfo=timezone.utc).timestamp())

    def test_one_platform_error_isolated_and_checkpoint_before_final_submit(self):
        done=threading.Event();calls=[]
        def execute(job,cancel,progress,checkpoint):
            calls.append(job['platform']);progress(phase='upload',percent=50)
            if job['platform']=='youtube':raise RuntimeError('selector changed')
            checkpoint();self.assertTrue(queue.get(job['id'])['commit_attempted']);done.set()
            return {'status':'draft','url':'','message':'saved'}
        queue=self.queue(execute)
        jobs=queue.submit(self.source,self.profiles,self.meta,'draft',None,'request-0001')
        self.assertTrue(done.wait(3))
        queue.shutdown()
        states={job['platform']:job['status'] for job in queue.snapshot()}
        self.assertEqual(states,{'youtube':'error','tiktok':'success'})
        self.assertEqual(calls,['youtube','tiktok'])
        fresh=UploadQueue(self.path,lambda *args:None)
        self.assertEqual(len(fresh.snapshot()),2)

    def test_post_commit_failure_is_uncertain_not_auto_retried(self):
        reached=threading.Event()
        def execute(job,cancel,progress,checkpoint):
            checkpoint();reached.set();raise RuntimeError('lost confirmation')
        queue=self.queue(execute)
        task=queue.submit(self.source,self.profiles[:1],self.meta,'public',None,'request-0002')[0]
        self.assertTrue(reached.wait(3));queue.shutdown()
        self.assertEqual(queue.get(task['id'])['status'],'uncertain')
        with self.assertRaises(ValueError):queue.retry(task['id'])

    def test_request_id_idempotence_and_payload_conflict(self):
        queue=self.queue(lambda *args:{'status':'success'})
        scheduled=queue.clock()+3600
        first=queue.submit(self.source,self.profiles,self.meta,'draft',scheduled,'stable-request')
        second=queue.submit(self.source,self.profiles,self.meta,'draft',scheduled,'stable-request')
        self.assertEqual([j['id'] for j in first],[j['id'] for j in second])
        with self.assertRaises(ValueError):queue.submit(self.source,self.profiles,{**self.meta,'title':'other'},'draft',scheduled,'stable-request')
        self.assertEqual(len(queue.snapshot()),2)

    def test_scheduled_source_protected_and_cancel_releases(self):
        queue=self.queue(lambda *args:{'status':'success'})
        job=queue.submit(self.source,self.profiles[:1],self.meta,'draft',queue.clock()+3600,'future-request')[0]
        self.assertTrue(queue.active('vietsub-job'))
        self.assertIn(os.path.normcase(str(self.video)),queue.protected_paths())
        queue.cancel(job['id'])
        self.assertFalse(queue.active('vietsub-job'))
        self.assertFalse(queue.protected_paths())

    def test_restart_running_checkpoint_is_never_requeued(self):
        queue=self.queue(lambda *args:{'status':'success'})
        job=queue.submit(self.source,self.profiles[:1],self.meta,'draft',queue.clock()+3600,'restart-request')[0]
        queue.shutdown()
        payload=json.loads(self.path.read_text(encoding='utf-8'))
        payload['jobs'][0].update(status='running',commit_attempted=True)
        self.path.write_text(json.dumps(payload),encoding='utf-8')
        fresh=UploadQueue(self.path,lambda *args:None)
        self.assertEqual(fresh.get(job['id'])['status'],'uncertain')
        self.assertIsNone(fresh.worker)

    def test_disk_failure_prevents_submission_and_before_publish_checkpoint(self):
        queue=self.queue(lambda *args:None)
        with patch.object(queue,'_persist',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):queue.submit(self.source,self.profiles[:1],self.meta,'draft',None,'disk-request')
        self.assertEqual(queue.snapshot(),[])
        self.assertIsNone(queue.worker)

    def test_recipient_order_batch_and_schedule_cannot_bypass_duplicate_guard(self):
        queue=self.queue(lambda *args:{'status':'success'})
        future=queue.clock()+3600
        queue.submit(self.source,self.profiles,self.meta,'private',future,'original-batch')
        for profiles in (self.profiles[:1],list(reversed(self.profiles))):
            with self.assertRaises(ValueError):
                queue.submit(self.source,profiles,self.meta,'private',future+100,'replacement-'+profiles[0]['id'])
        self.assertEqual(len(queue.snapshot()),2)

    def test_encoded_size_rejected_before_self_unreadable_queue_written(self):
        queue=self.queue(lambda *args:{'status':'success'})
        with patch('vietsub.uploader.queue.MAX_QUEUE_BYTES',400):
            with self.assertRaises(ValueError):
                queue.submit(self.source,self.profiles,self.meta,'private',queue.clock()+3600,'size-request')
        self.assertFalse(self.path.exists())
        self.assertEqual(queue.snapshot(),[])

    def test_checkpoint_failure_latches_stop_even_if_browser_wraps_exception(self):
        from vietsub.uploader.adapters import VerificationRequired
        entered=threading.Event();calls=[]
        def execute(job,cancel,progress,checkpoint):
            calls.append(job['profile_id'])
            try:checkpoint()
            except Exception:raise VerificationRequired('sanitized storage error') from None
            return {'status':'success'}
        queue=self.queue(execute);persist=queue._persist
        def failure(jobs):
            if any(j.get('phase')=='committing' for j in jobs):
                entered.set();raise PermissionError('locked checkpoint')
            return persist(jobs)
        with patch.object(queue,'_persist',side_effect=failure):
            queue.submit(self.source,self.profiles,self.meta,'private',None,'checkpoint-request')
            self.assertTrue(entered.wait(3))
            if queue.worker:queue.worker.join(3)
        self.assertTrue(queue.stop.is_set())
        self.assertEqual(calls,['profile-1'])
        self.assertEqual(queue.snapshot()[0]['status'],'error')
        self.assertEqual(queue.snapshot()[1]['status'],'queued')

    def test_browser_receives_private_snapshot_not_mutable_source(self):
        completed=threading.Event();observed=[]
        def execute(job,cancel,progress,checkpoint):
            self.assertNotEqual(job['video'],str(self.video))
            self.video.write_bytes(b'different-PRIVATE-video')
            observed.append(Path(job['video']).read_bytes())
            checkpoint();completed.set();return {'status':'success'}
        queue=self.queue(execute)
        queue.submit(self.source,self.profiles[:1],self.meta,'private',None,'snapshot-request')
        self.assertTrue(completed.wait(3));queue.shutdown()
        self.assertEqual(observed,[b'video'])
        self.assertFalse(list((self.root/'input-snapshots').glob('*')))

    def test_retry_cannot_duplicate_a_newer_successful_same_target(self):
        completed=threading.Event()
        def execute(job,cancel,progress,checkpoint):
            checkpoint();completed.set();return {'status':'success'}
        queue=self.queue(execute)
        old=queue.submit(self.source,self.profiles[:1],self.meta,'private',queue.clock()+3600,'old-retry')[0]
        queue.cancel(old['id'])
        queue.submit(self.source,self.profiles[:1],self.meta,'private',None,'new-success')
        self.assertTrue(completed.wait(3))
        queue.shutdown()
        with self.assertRaises(ValueError):queue.retry(old['id'])
        self.assertEqual(sum(j['status']=='success' for j in queue.snapshot()),1)

    def test_corrupt_or_future_queue_not_overwritten(self):
        for raw in ('bad','[]','{"version":99,"jobs":[]}'):
            self.path.write_text(raw,encoding='utf-8')
            with self.assertRaises(ValueError):UploadQueue(self.path,lambda *args:None)
            self.assertEqual(self.path.read_text(),raw)


if __name__=='__main__':unittest.main()
