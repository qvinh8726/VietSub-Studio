"""Publish Hub API tests: temp profiles/media, mock browser, never real accounts."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

try:
    from . import _isolation
except ImportError:
    import _isolation
import app
from vietsub import manifest
from vietsub.uploader.api import UploaderAPI

RAW='1\n00:00:00,000 --> 00:00:01,000\n你好世界\n'
VI='1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n'
MP4=b'\x00\x00\x00\x18ftypmp42'+b'x'*30


class UploaderAPITests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vietsub-upload-api-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.client=app.app.test_client()
        self.enterContext(patch.dict(app.app.config,TESTING=True))
        self.enterContext(patch.object(app,'CONFIG_DIR',str(self.root)))
        self.enterContext(patch.object(app,'CONFIG_PATH',str(self.root/'config.json')))
        self.enterContext(patch.object(app,'current_job_id',''))
        self.enterContext(patch.object(app,'prefetch_job_id',''))
        self.enterContext(patch.object(app,'log_to_app'))
        self.enterContext(patch.object(app.uploader_api,'instances',{}))
        self.enterContext(patch.object(app.uploader_api,'sessions',{}))
        self.addCleanup(app.uploader_api.shutdown)
        self.result={'project_dir':str(self.root),'project_name':'clip','video':str(self.root/'clip.mp4'),
                     'source_video':str(self.root/'clip.mp4'),'raw_srt':str(self.root/'clip.raw.srt'),
                     'translated_srt':str(self.root/'clip.vi.srt')}
        Path(self.result['video']).write_bytes(MP4)
        Path(self.result['raw_srt']).write_text(RAW,encoding='utf-8');Path(self.result['translated_srt']).write_text(VI,encoding='utf-8')
        self.enterContext(patch.object(app,'workflow_jobs',[{'id':'project-1','status':'success','result_ready':True,
                          'result':self.result,'source_video':self.result['video'],'source_type':'local'}]))
        self.meta={'title':'Tiêu đề','description':'Mô tả','hashtags':['#Việt']}

    def profile(self,platform='youtube'):
        response=self.client.post('/api/uploader/profiles',json={'platform':platform,'name':'Kênh thử'})
        self.assertEqual(response.status_code,201,response.get_json())
        value=response.get_json()
        app.uploader_api.services()[0].update_session(value['id'],'alive','session_alive')
        return value

    def test_create_profiles_independent_no_secret_or_paths_returned(self):
        first=self.profile();second=self.profile('tiktok')
        self.assertNotEqual(first['id'],second['id'])
        profiles=self.client.get('/api/uploader/profiles').get_json()['profiles']
        self.assertEqual(len(profiles),2)
        text=json.dumps(profiles)
        for private in ('cookies','storage_state','profile_dir','user_data_dir'):self.assertNotIn(private,text)
        self.assertEqual(self.client.delete('/api/uploader/profiles/'+first['id']).status_code,200)

    def test_save_project_metadata_and_edited_video_selected(self):
        _,value=manifest.save_project(self.result)
        edited=self.root/'edited.mp4';edited.write_bytes(MP4)
        value['assets']['edited_video']=str(edited);manifest.write_atomic(manifest.path_for(self.result),value)
        response=self.client.post('/api/uploader/projects/project-1',json={'metadata':self.meta})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['asset'],'edited')
        self.assertIn('asset=edited',response.get_json()['video_url'])
        self.assertEqual(manifest.snapshot(self.result)['metadata'],self.meta)

    def test_arbitrary_mp4_managed_and_signed_no_unscoped_path(self):
        response=self.client.post('/api/uploader/media',data={'video':(io.BytesIO(MP4),'video.mp4')})
        self.assertEqual(response.status_code,201,response.get_json())
        media=response.get_json()
        self.assertNotIn('path',media)
        with self.client.get(media['video_url']) as response:self.assertEqual(response.get_data(),MP4)
        self.assertEqual(self.client.get('/api/uploader/media/'+media['id']+'/video').status_code,403)
        self.assertEqual(self.client.post('/api/uploader/media',data={'video':(io.BytesIO(b'bad'),'video.mp4')}).status_code,400)

    def test_public_confirmation_and_visibility_never_downgraded(self):
        profile=self.profile()
        body={'job_id':'project-1','profile_ids':[profile['id']],'metadata':self.meta,'visibility':'public','request_id':'public-request'}
        self.assertEqual(self.client.post('/api/uploader/queue',json=body).status_code,400)
        body['visibility']='draft'
        self.assertEqual(self.client.post('/api/uploader/queue',json=body).status_code,400) # YouTube no explicit draft
        self.assertEqual(self.client.post('/api/uploader/queue',json={**body,'visibility':'private','profile_ids':[{}]}).status_code,400)

    def test_scheduled_queue_protects_video_and_duplicate_id_returns_same_jobs(self):
        from datetime import datetime,timedelta,timezone
        profile=self.profile()
        scheduled=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        body={'job_id':'project-1','profile_ids':[profile['id']],'metadata':self.meta,'visibility':'private',
              'scheduled_at':scheduled,'request_id':'stable-request'}
        response=self.client.post('/api/uploader/queue',json=body)
        self.assertEqual(response.status_code,202,response.get_json());job=response.get_json()['jobs'][0]
        again=self.client.post('/api/uploader/queue',json=body).get_json()['jobs'][0]
        self.assertEqual(job['id'],again['id'])
        self.assertTrue(app.editor_uses_job('project-1'))
        cleanup=self.client.post('/api/queue/bulk-delete',json={'ids':['project-1'],'delete_files':True}).get_json()
        self.assertEqual(cleanup['deleted_ids'],[])
        self.assertEqual(self.client.delete('/api/uploader/profiles/'+profile['id']).status_code,409)
        self.client.post('/api/uploader/queue/'+job['id']+'/cancel',json={})
        self.assertFalse(app.editor_uses_job('project-1'))
        self.assertTrue(Path(self.result['video']).exists())

    def test_retry_uses_same_admission_locks_as_cleanup(self):
        from datetime import datetime,timedelta,timezone
        profile=self.profile();store,browser,queue=app.uploader_api.services()
        body={'job_id':'project-1','profile_ids':[profile['id']],'metadata':self.meta,'visibility':'private',
              'scheduled_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'request_id':'retry-boundary'}
        identifier=self.client.post('/api/uploader/queue',json=body).get_json()['jobs'][0]['id']
        queue.cancel(identifier)
        from contextlib import contextmanager
        class Guard:
            def __init__(self):self.entered=False
            def __enter__(self):self.entered=True;return self
            def __exit__(self,*args):self.entered=False
        admission=Guard();state=Guard()
        def retry(task,**kwargs):
            self.assertTrue(admission.entered and state.entered)
            return queue.get(task)
        with patch.object(app,'operation_admission_lock',admission),patch.object(app,'queue_state_lock',state),patch.object(queue,'retry',side_effect=retry):
            self.assertEqual(self.client.post('/api/uploader/queue/'+identifier+'/retry',json={}).status_code,200)

    def test_native_fallback_reinitializes_stopped_queue(self):
        adapter=app.uploader_api
        adapter.initialize();first=adapter.services()[2]
        adapter.shutdown();adapter.initialize();second=adapter.services()[2]
        self.assertIsNot(first,second)
        self.assertFalse(second.stop.is_set())

    def test_current_security_contract_still_required(self):
        with patch.dict(app.app.config,TESTING=False):
            self.assertEqual(self.client.get('/api/uploader/profiles').status_code,403)
            headers={app.API_TOKEN_HEADER:app.API_TOKEN}
            self.assertEqual(self.client.get('/api/uploader/profiles',headers=headers).status_code,200)
            self.assertEqual(self.client.post('/api/uploader/profiles',json={'platform':'youtube','name':'x'},
                             headers={**headers,'Origin':'https://evil.test'}).status_code,403)


if __name__=='__main__':unittest.main()
