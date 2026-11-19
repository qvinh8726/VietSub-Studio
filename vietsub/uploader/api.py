"""Publish Hub Flask boundary: scoped media, explicit login, metadata and queue actions."""
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
from threading import Event, RLock, Thread
import time
import uuid

from flask import Blueprint, jsonify, request, send_file
from vietsub import manifest, publish, storage
from .queue import UploadQueue, ACTIVE, TERMINAL, metadata, schedule_time, atomic_json


class UploaderAPI:
    def __init__(self, runtime):
        self.app=runtime;self.bp=Blueprint('uploader',__name__);self.lock=RLock();self.instances={}
        self.sessions={};self.meta_tasks={};self.media={};self.closed=False;self._routes()

    def services(self):
        root=os.path.abspath(os.path.join(self.app.CONFIG_DIR,'uploader'))
        with self.lock:
            if root not in self.instances:
                from .profiles import ProfileStore
                from .browser import BrowserRuntime
                store=ProfileStore(root)
                browser=BrowserRuntime(store,lambda:self.app.resolve_browser_executable(),log=self.app.log_to_app)
                queue=UploadQueue(Path(root)/'upload_queue.json',lambda job,cancel,progress,checkpoint:self._execute(root,job,cancel,progress,checkpoint),
                                  on_finish=self._finish)
                self.instances[root]=(store,browser,queue)
            return self.instances[root]

    def _execute(self,root,job,cancel,progress,checkpoint):
        store,browser,queue=self.instances[root]
        directory=Path(root)/'artifacts'/job['id'];manifest._safe_path(str(directory));directory.mkdir(parents=True,exist_ok=True)
        def report(value=None,message=None,**fields):
            if isinstance(value,dict):progress(**value)
            elif type(value) in (int,float):progress(percent=value,phase=str(message or 'upload')[:100])
            else:progress(**fields)
        return browser.upload(job['profile_id'],job['video'],job['metadata'],visibility=job['visibility'],cancel=cancel,
                              progress=report,before_publish=checkpoint,artifact_dir=str(directory))

    def _finish(self,task):
        job_id=task.get('job_id')
        if job_id:
            try:
                with self.app.queue_state_lock:
                    _,result=self.app.project_for_job(job_id)
                with manifest.LOCK:
                    value=manifest.snapshot(result)
                    value['stages']['uploader']={'status':task['status'],'task_id':task['id'],'platform':task['platform'],
                                                  'updated_at':int(time.time()),'error':task.get('error','')}
                    manifest.write_atomic(manifest.path_for(result),value)
            except (OSError,ValueError,KeyError):pass
        self.app.log_to_app(f"[Publish Hub] {task['platform']} · {task['status']}", 'error' if task['status'] in {'error','uncertain'} else 'system')

    def active(self,job_id):
        with self.lock:
            return (any(item[2].active(job_id) for item in self.instances.values())
                    or any(task.get('job_id')==job_id and task.get('status') in {'queued','running'} for task in self.meta_tasks.values()))

    def protected_paths(self):
        with self.lock:
            paths=set().union(*(item[2].protected_paths() for item in self.instances.values()))
            paths.update(task['_srt_path'] for task in self.meta_tasks.values()
                         if task.get('status') in {'queued','running'} and task.get('_srt_path'))
            return paths

    def initialize(self):
        with self.lock:
            if self.closed:
                if any(item[2].worker and item[2].worker.is_alive() for item in self.instances.values()):
                    raise RuntimeError('Uploader cũ chưa dừng xong; không mở song song queue mới.')
                self.instances.clear();self.closed=False
        self.services()[2].start()

    def shutdown(self):
        with self.lock:
            self.closed=True
            instances=list(self.instances.values());sessions=list(self.sessions.values())
        for task in sessions:
            task['_cancel'].set();task['_finish'].set()
        for _,_,queue in instances:queue.shutdown()

    def _body(self,allowed,required=()):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)-set(allowed) or any(k not in data for k in required):raise ValueError('JSON Publish Hub không hợp lệ.')
        return data

    def _project(self,identifier,*,ready=False):
        job,result=self.app.project_for_job(identifier)
        value=manifest.snapshot(result,status=job.get('status') or 'error',geometry_fallback=self.app.project_geometry_fallback(job))
        if ready and (job.get('status')!='success' or self.app.editor_api.jobs.active(identifier)):
            raise RuntimeError('Project chưa hoàn tất hoặc đang Auto Edit; chờ trước khi xuất bản.')
        path=value['assets'].get('edited_video') or value['assets']['video']
        if ready and (not path or not storage.safe_regular(path) or Path(path).suffix.lower()!='.mp4'):
            raise ValueError('Xuất bản cần MP4 còn tồn tại; hãy render hoặc chọn MP4 khác.')
        return job,result,value,path

    def _profile_payload(self,profile):
        value=copy.deepcopy(profile)
        if value.get('session_status')=='alive':value['session_status']='ready'
        value['detail']=value.get('session_detail','')
        return value

    def _task(self,task):
        return {key:copy.deepcopy(value) for key,value in task.items() if not key.startswith('_')}

    def _session_start(self,profile_id,action):
        store,browser,queue=self.services();store.get(profile_id)
        if queue.profile_active(profile_id):raise RuntimeError('Tài khoản có upload đang chờ/chạy; huỷ/chờ trước khi đăng nhập.')
        with self.lock:
            if any(t.get('profile_id')==profile_id and t['status'] in {'queued','running'} for t in self.sessions.values()):
                raise RuntimeError('Đang có thao tác login/check cho tài khoản này.')
            identifier=uuid.uuid4().hex;cancel,finish=Event(),Event()
            task={'id':identifier,'profile_id':profile_id,'action':action,'status':'queued','error':'',
                  '_cancel':cancel,'_finish':finish,'created_at':time.time()};self.sessions[identifier]=task
        def execute():
            task['status']='running'
            try:
                result=browser.login(profile_id,cancel=cancel,finish=finish) if action=='login' else browser.probe(profile_id)
                task['result']=result;task['status']='cancelled' if cancel.is_set() else 'success'
            except Exception as error:
                task.update(status='cancelled' if cancel.is_set() else 'error',error=str(error)[:2000])
        Thread(target=execute,name='uploader-session',daemon=True).start()
        return self._task(task)

    def _public_job(self,job):
        value={key:copy.deepcopy(val) for key,val in job.items() if key not in {'video','metadata','video_mtime_ns','request_fingerprint','screenshot_path'}}
        screenshot=job.get('screenshot_path')
        value['screenshot_url']=''
        if screenshot:
            root=Path(self.app.CONFIG_DIR)/'uploader'/'artifacts'/job['id']
            try:Path(screenshot).resolve().relative_to(root.resolve())
            except ValueError:return value
            if storage.safe_regular(screenshot):
                sign=hmac.new(self.app.API_TOKEN.encode('ascii'),f"upload-screen|{job['id']}|{screenshot}".encode('utf-8'),hashlib.sha256).hexdigest()
                value['screenshot_url']=f"/api/uploader/queue/{job['id']}/screenshot?signature={sign}"
        return value

    def _media(self,identifier):
        with self.lock:
            key=(os.path.abspath(self.app.CONFIG_DIR),identifier)
            if key in self.media:return self.media[key]
        target=Path(self.app.CONFIG_DIR)/'uploader'/'media'/identifier/'media.json'
        if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise KeyError('Không tìm thấy video MP4.')
        manifest._safe_path(str(target))
        if not target.exists():raise KeyError('Không tìm thấy MP4 đã chọn.')
        value=json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError('Thông tin MP4 hỏng.')
        expected=str(target.parent/'video.mp4')
        if value.get('path')!=expected:raise ValueError('Đường dẫn MP4 không hợp lệ.')
        return value

    def _media_payload(self,value):
        identifier=value['id'];sign=hmac.new(self.app.API_TOKEN.encode('ascii'),f"upload-media|{identifier}".encode('ascii'),hashlib.sha256).hexdigest()
        return {'id':identifier,'name':value['name'],'size_bytes':value['size_bytes'],'metadata':value['metadata'],
                'video_url':f"/api/uploader/media/{identifier}/video?signature={sign}"}

    def _routes(self):
        bp=self.bp
        @bp.errorhandler(ValueError)
        def invalid(error):return jsonify({'error':str(error)}),400
        @bp.errorhandler(KeyError)
        def missing(error):return jsonify({'error':error.args[0]}),404
        @bp.errorhandler(RuntimeError)
        @bp.errorhandler(OSError)
        def conflict(error):return jsonify({'error':str(error)}),409

        @bp.get('/api/uploader/capabilities')
        def capabilities():
            from .adapters import PLATFORMS
            return jsonify({'browser_available':bool(self.app.resolve_browser_executable()),'timezone':'Asia/Saigon','scheduling':'local',
                            'platforms':[{'key':key,**value} for key,value in PLATFORMS.items()]})

        @bp.route('/api/uploader/profiles',methods=['GET','POST'])
        def profiles():
            store,_,_=self.services()
            if request.method=='POST':
                data=self._body(('platform','name'),('platform','name'))
                return jsonify(self._profile_payload(store.create(data['platform'],data['name']))),201
            return jsonify({'profiles':[self._profile_payload(profile) for profile in store.list()]})

        @bp.delete('/api/uploader/profiles/<profile_id>')
        def delete_profile(profile_id):
            store,_,queue=self.services()
            if queue.profile_active(profile_id):raise RuntimeError('Tài khoản còn upload đang chờ/chạy.')
            store.delete(profile_id);return jsonify({'ok':True})

        @bp.post('/api/uploader/profiles/<profile_id>/login')
        def login(profile_id):
            self._body(());return jsonify(self._session_start(profile_id,'login')),202

        @bp.post('/api/uploader/profiles/<profile_id>/check')
        def check(profile_id):
            self._body(());return jsonify(self._session_start(profile_id,'check')),202

        @bp.route('/api/uploader/sessions/<identifier>',methods=['GET'])
        def session_status(identifier):
            if identifier not in self.sessions:raise KeyError('Không tìm thấy login/check task.')
            return jsonify(self._task(self.sessions[identifier]))

        @bp.post('/api/uploader/sessions/<identifier>/finish')
        def finish_login(identifier):
            self._body(())
            if identifier not in self.sessions:raise KeyError('Không tìm thấy login task.')
            self.sessions[identifier]['_finish'].set();return jsonify(self._task(self.sessions[identifier]))

        @bp.post('/api/uploader/sessions/<identifier>/cancel')
        def cancel_login(identifier):
            self._body(())
            if identifier not in self.sessions:raise KeyError('Không tìm thấy login task.')
            self.sessions[identifier]['_cancel'].set();self.sessions[identifier]['_finish'].set()
            return jsonify(self._task(self.sessions[identifier]))

        @bp.route('/api/uploader/projects/<job_id>',methods=['GET','POST'])
        def project(job_id):
            with self.app.operation_admission_lock,self.app.queue_state_lock,manifest.LOCK:
                job,result,value,path=self._project(job_id)
                if request.method=='POST':
                    data=self._body(('metadata',),('metadata',));value['metadata']=metadata(data['metadata'])
                    manifest.write_atomic(manifest.path_for(result),value)
                payload=self.app.project_handoff_payload(job,result,value,'uploader')
                return jsonify({'snapshot':payload,'metadata':value['metadata'],'asset':'edited' if value['assets'].get('edited_video') else 'source',
                                'video_url':payload['video_url']})

        @bp.post('/api/uploader/projects/<job_id>/metadata')
        def ai_metadata(job_id):
            self._body(())
            with self.app.operation_admission_lock,self.app.queue_state_lock:
                if self.app.workflow_lock.locked() or self.app.preview_lock.locked():raise RuntimeError('Hãy chờ VietSub/preview xong trước khi tạo metadata AI.')
                _,result,value,_=self._project(job_id,ready=True)
                if not storage.safe_regular(value['assets']['translated_srt']):raise ValueError('Project không có SRT Việt để tạo metadata.')
            identifier=uuid.uuid4().hex;task={'id':identifier,'job_id':job_id,'status':'queued','error':'',
                                            '_srt_path':storage.canonical(value['assets']['translated_srt'])};self.meta_tasks[identifier]=task
            def generate():
                task['status']='running'
                admitted=self.app.workflow_lock.acquire(blocking=False)
                try:
                    if not admitted:raise RuntimeError('VietSub vừa bắt đầu; hãy tạo metadata lại sau.')
                    self.app.workflow_context.cancel_event=Event()
                    config=self.app.get_translation_runtime_config(require_ready=True)
                    prompt=publish.build_prompt(self.app.read_text_file(result['translated_srt']),result.get('project_name',''))
                    if config['provider']=='notebook':
                        @self.app.browser_automation
                        def request_metadata():
                            return publish.request_notebook(self.app,config,prompt)
                        response=request_metadata()
                    else:
                        response=self.app.request_translation_completion(config,[
                            {'role':'system','content':'Bạn là biên tập viên video tiếng Việt. Chỉ trả JSON.'},
                            {'role':'user','content':prompt}],stream=False)
                    pack=publish.parse_response(response)
                    # Return a proposal; never overwrite a newer user draft or
                    # saved metadata from an asynchronous AI response.
                    task['metadata']={key:pack[key] for key in ('title','description','hashtags')}
                    task['status']='success'
                except Exception as error:task.update(status='error',error=str(error)[:2000])
                finally:
                    if admitted:self.app.workflow_lock.release()
            Thread(target=generate,name='uploader-metadata',daemon=True).start();return jsonify(self._task(task)),202

        @bp.get('/api/uploader/metadata/<identifier>')
        def metadata_status(identifier):
            if identifier not in self.meta_tasks:raise KeyError('Không tìm thấy metadata task.')
            return jsonify(self._task(self.meta_tasks[identifier]))

        @bp.post('/api/uploader/media')
        def upload_media():
            video=request.files.get('video')
            if not video or Path(video.filename or '').suffix.lower()!='.mp4':raise ValueError('Chọn file MP4.')
            identifier=uuid.uuid4().hex;directory=Path(self.app.CONFIG_DIR)/'uploader'/'media'/identifier
            manifest._safe_path(str(directory));directory.mkdir(parents=True,exist_ok=False)
            path=directory/'video.mp4'
            try:
                video.save(str(path));size=self.app.validate_local_mp4_file(str(path))
                value={'id':identifier,'path':str(path),'name':Path(video.filename.replace('\\','/')).name,'size_bytes':size,
                       'metadata':{'title':Path(video.filename).stem[:120],'description':'','hashtags':[]}}
                atomic_json(directory/'media.json',value)
            except Exception:
                if path.exists():path.unlink()
                raise
            with self.lock:self.media[(os.path.abspath(self.app.CONFIG_DIR),identifier)]=value
            return jsonify(self._media_payload(value)),201

        @bp.get('/api/uploader/media/<identifier>/video')
        def media_video(identifier):
            value=self._media(identifier);expected=self._media_payload(value)['video_url'].partition('signature=')[2]
            if not secrets.compare_digest(expected.encode('ascii'),request.args.get('signature','').encode('utf-8')):return jsonify({'error':'Liên kết MP4 không hợp lệ.'}),403
            if not storage.safe_regular(value['path']):raise KeyError('MP4 đã bị xóa.')
            return send_file(value['path'],conditional=True,mimetype='video/mp4')

        @bp.route('/api/uploader/queue',methods=['GET','POST'])
        def queue():
            store,_,queue=self.services()
            if request.method=='GET':return jsonify({'jobs':[self._public_job(j) for j in queue.snapshot()]})
            data=self._body(('job_id','media_id','profile_ids','metadata','visibility','scheduled_at','request_id','confirm_public'),
                            ('profile_ids','metadata','visibility','request_id'))
            if bool(data.get('job_id'))==bool(data.get('media_id')):raise ValueError('Chọn một project hoặc một MP4.')
            if any(key in data and data[key] is not None and (not isinstance(data[key],str) or len(data[key])>256)
                   for key in ('job_id','media_id')):
                raise ValueError('Project/media ID không hợp lệ.')
            if not isinstance(data['request_id'],str) or not 8 <= len(data['request_id']) <= 100:
                raise ValueError('request_id không hợp lệ.')
            if (not isinstance(data['profile_ids'],list) or not data['profile_ids'] or len(data['profile_ids'])>30
                    or any(not isinstance(identifier,str) for identifier in data['profile_ids'])):
                raise ValueError('Danh sách tài khoản không hợp lệ.')
            prior=[job for job in queue.snapshot() if job.get('request_id')==data['request_id']]
            if prior:
                try:
                    submitted_schedule=data.get('scheduled_at')
                    stamp=(datetime.fromisoformat(submitted_schedule.replace('Z','+00:00')).timestamp()
                           if submitted_schedule else None)
                    expected_profiles=set(data['profile_ids'])
                except (ValueError,TypeError,AttributeError):raise ValueError('Payload request_id cũ không hợp lệ.') from None
                if (set(job['profile_id'] for job in prior)!=expected_profiles
                        or any(job.get('job_id','')!=(data.get('job_id') or '') or job.get('media_id','')!=(data.get('media_id') or '')
                               or job['metadata']!=metadata(data['metadata']) or job['visibility']!=data['visibility']
                               or job.get('scheduled_at')!=stamp for job in prior)):
                    raise ValueError('request_id đã dùng cho payload khác; không đăng lại.')
                return jsonify({'jobs':[self._public_job(job) for job in prior]}),202
            ids=data['profile_ids']
            if not isinstance(ids,list) or not ids or len(ids)>30 or any(not isinstance(i,str) for i in ids):raise ValueError('Danh sách tài khoản không hợp lệ.')
            profiles=[store.get(i) for i in dict.fromkeys(ids)]
            if any(profile.get('session_status') not in {'alive','ready'} for profile in profiles):
                raise ValueError('Chỉ xếp hàng tài khoản đã đăng nhập và kiểm tra phiên sẵn sàng.')
            from .adapters import PLATFORMS
            visibility=data['visibility']
            if not isinstance(visibility,str) or any(visibility not in PLATFORMS[p['platform']]['modes'] for p in profiles):
                raise ValueError('Có nền tảng không hỗ trợ chế độ hiển thị đã chọn; không tự chuyển sang public.')
            if visibility=='public' and data.get('confirm_public') is not True:raise ValueError('Cần xác nhận rõ trước khi đăng công khai.')
            scheduled=schedule_time(data.get('scheduled_at'))
            with self.app.operation_admission_lock,self.app.queue_state_lock:
                if data.get('job_id'):
                    if not isinstance(data['job_id'],str):raise ValueError('Project ID không hợp lệ.')
                    _,_,_,path=self._project(data['job_id'],ready=True)
                    source={'job_id':data['job_id'],'video':path}
                else:
                    value=self._media(data['media_id']);source={'media_id':value['id'],'video':value['path']}
                records=queue.submit(source,profiles,data['metadata'],visibility,scheduled,data['request_id'])
            return jsonify({'jobs':[self._public_job(j) for j in records]}),202

        @bp.post('/api/uploader/queue/<identifier>/cancel')
        def cancel_upload(identifier):
            self._body(());return jsonify(self._public_job(self.services()[2].cancel(identifier)))

        @bp.post('/api/uploader/queue/<identifier>/retry')
        def retry_upload(identifier):
            data=self._body(('confirm_uncertain',));confirm=data.get('confirm_uncertain',False)
            if type(confirm) is not bool:raise ValueError('confirm_uncertain phải là boolean.')
            with self.app.operation_admission_lock,self.app.queue_state_lock:
                store,_,queue=self.services();job=queue.get(identifier)
                store.get(job['profile_id'])
                if job.get('job_id'):self._project(job['job_id'],ready=True)
                elif job.get('media_id'):self._media(job['media_id'])
                if not storage.safe_regular(job['video']):raise ValueError('Video đã bị dọn; không thể thử lại.')
                return jsonify(self._public_job(queue.retry(identifier,confirm_uncertain=confirm)))

        @bp.get('/api/uploader/queue/<identifier>/screenshot')
        def screenshot(identifier):
            job=self.services()[2].get(identifier);url=self._public_job(job)['screenshot_url']
            if not url or not secrets.compare_digest(url.partition('signature=')[2].encode('ascii'),request.args.get('signature','').encode('utf-8')):
                return jsonify({'error':'Screenshot không khả dụng hoặc liên kết sai.'}),403
            return send_file(job['screenshot_path'],mimetype='image/png')


def register(runtime):
    adapter=UploaderAPI(runtime);runtime.app.register_blueprint(adapter.bp);return adapter
