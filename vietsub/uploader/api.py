"""Publish Hub Flask boundary: scoped media, explicit login, metadata and queue actions."""
from contextlib import ExitStack
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import secrets
import shutil
from threading import Event, RLock, Thread
import time
import uuid

from flask import Blueprint, jsonify, request, send_file
from vietsub import manifest, publish, redaction, storage
from .queue import UploadQueue, ACTIVE, TERMINAL, UPLOAD_MODES, metadata, schedule_time, atomic_json


class UploaderAPI:
    def __init__(self, runtime):
        self.app=runtime;self.bp=Blueprint('uploader',__name__);self.lock=RLock();self.instances={}
        self.sessions={};self.meta_tasks={};self.media={};self.closed=False;self._yt_client=None;self._routes()

    def _selectors(self):
        from . import selector_store
        return selector_store.load_selectors(self.app.CONFIG_DIR)

    def _youtube_client(self):
        with self.lock:
            if self._yt_client is None:
                from .youtube_api import YouTubeAPIClient
                self._yt_client = YouTubeAPIClient(
                    protect_secret=self.app.protect_text_secret,
                    unprotect_secret=self.app.unprotect_config_secret,
                    ffprobe_resolver=lambda: self.app.resolve_ffprobe_executable(),
                    log=self.app.log_to_app,
                )
            return self._yt_client

    def services(self):
        root=os.path.abspath(os.path.join(self.app.CONFIG_DIR,'uploader'))
        with self.lock:
            if root not in self.instances:
                from .profiles import ProfileStore
                from .browser import BrowserRuntime
                store=ProfileStore(root)
                browser=BrowserRuntime(
                    store,
                    lambda:self.app.resolve_browser_executable(),
                    log=self.app.log_to_app,
                    selectors_provider=lambda:self._selectors()[0],
                )
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

        config = store.get_config(job['profile_id'])
        if job['platform'] == 'youtube' and config.get('upload_method') == 'youtube_api':
            from .youtube_api import YouTubeAuthError
            client = self._youtube_client()
            with store.lease(job['profile_id']):
                try:
                    result = client.upload(
                        job['profile_id'],
                        store.oauth_path(job['profile_id']),
                        job['video'],
                        job['metadata'],
                        visibility=job['visibility'],
                        cancel=cancel,
                        progress=report,
                        before_publish=checkpoint,
                    )
                    try:
                        store.update_session(job['profile_id'], 'alive', 'oauth_connected')
                    except Exception:
                        pass
                    return result
                except YouTubeAuthError as error:
                    try:
                        store.update_session(job['profile_id'], 'expired', error.session_detail)
                    except Exception:
                        pass
                    raise

        upload_mode = job.get('upload_mode', 'auto')
        debug_mode = bool(job.get('debug_mode', False))
        confirm_event = queue.confirm_event(job['id'])

        def on_awaiting_submit(timeout_seconds):
            deadline = time.time() + float(timeout_seconds)
            progress(status='awaiting_user_submit', phase='awaiting_user_submit', step='submit',
                     percent=88, awaiting_deadline=deadline)

        kwargs = {
            'visibility': job['visibility'],
            'cancel': cancel,
            'progress': report,
            'before_publish': checkpoint,
            'artifact_dir': str(directory),
        }
        try:
            sig = inspect.signature(browser.upload)
            accepts_var = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            param_names = set(sig.parameters)
        except (TypeError, ValueError):
            accepts_var, param_names = True, set()

        optional = {
            'upload_mode': upload_mode,
            'debug_mode': debug_mode,
            'confirm_submitted': confirm_event,
            'on_awaiting_submit': on_awaiting_submit,
            'job_id': job['id'],
        }
        for key, val in optional.items():
            if accepts_var or key in param_names:
                kwargs[key] = val
        if accepts_var or 'selectors' in param_names:
            selectors, status = self._selectors()
            if status.get('warning'):
                self.app.log_to_app(f"[Publish Hub] {status['warning']}", 'system')
            kwargs['selectors'] = selectors
        return browser.upload(job['profile_id'], job['video'], job['metadata'], **kwargs)

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

    def any_active(self):
        with self.lock:
            if any(task.get('status') in {'queued','running'} for task in self.sessions.values()):
                return True
            if any(task.get('status') in {'queued','running'} for task in self.meta_tasks.values()):
                return True
            instances=list(self.instances.values())
        return any(any(job.get('status') in ACTIVE for job in item[2].snapshot()) for item in instances)

    def _check_admission_open(self):
        if self.app.shutdown_event.is_set():
            raise RuntimeError('App đang đóng.')
        update_lock = getattr(self.app, 'update_install_lock', None)
        if update_lock is not None and update_lock.locked():
            raise RuntimeError('App đang chuẩn bị cài đặt bản cập nhật; không thể bắt đầu tác vụ Publish Hub mới.')

    def _trim_cache(self,mapping,*,terminal_only=False,limit=256):
        evicted=[]
        while len(mapping)>limit:
            if terminal_only:
                victim=next((key for key,item in mapping.items() if item.get('status') in TERMINAL|{'cancelled'}),None)
                if victim is None:
                    break
            else:
                victim=next(iter(mapping))
            item=mapping.pop(victim,None)
            if item is not None:
                evicted.append(item)
        return evicted

    def _prune_media_disk(self, *, max_age_seconds=86400 * 7, evicted_items=()):
        """Clean expired or evicted standalone MP4 folders while honoring active UploadQueue paths."""
        media_root = Path(self.app.CONFIG_DIR) / 'uploader' / 'media'
        try:
            manifest._safe_path(str(media_root))
        except (OSError, ValueError):
            return
        protected = self.protected_paths()
        now = time.time()
        targets = []
        for item in evicted_items:
            ident = item.get('id') if isinstance(item, dict) else ''
            if isinstance(ident, str) and len(ident) == 32 and all(c in '0123456789abcdef' for c in ident):
                targets.append(media_root / ident)
        if media_root.is_dir():
            for entry in list(media_root.iterdir()):
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if len(entry.name) != 32 or any(c not in '0123456789abcdef' for c in entry.name):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if (now - mtime) >= max_age_seconds:
                    targets.append(entry)
        seen = set()
        for folder in targets:
            norm = os.path.normcase(os.path.abspath(str(folder)))
            if norm in seen:
                continue
            seen.add(norm)
            video_file = folder / 'video.mp4'
            if storage.canonical(str(video_file)) in protected:
                continue
            try:
                manifest._safe_path(str(folder))
                if folder.is_dir() and not folder.is_symlink():
                    shutil.rmtree(folder, ignore_errors=True)
            except (OSError, ValueError):
                pass

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
        self._prune_media_disk()

    def shutdown(self):
        with self.lock:
            self.closed=True
            instances=list(self.instances.values())
            sessions=list(self.sessions.values())
            meta_tasks=list(self.meta_tasks.values())
        for task in sessions:
            task['_cancel'].set();task['_finish'].set()
        for _,_,queue in instances:queue.shutdown()
        for task in sessions:
            th = task.get('_thread')
            if th and th.is_alive():
                th.join(timeout=3)
        for task in meta_tasks:
            th = task.get('_thread')
            if th and th.is_alive():
                th.join(timeout=3)

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

    @staticmethod
    def _fit_pipeline_metadata(platform, base_meta, visibility):
        """Tailor fallback title & hashtags to platform limits; fail closed on missing required fields."""
        import re
        from .adapters import PLATFORM_TAG_LIMITS, PLATFORM_TITLE_LIMITS, validate_platform_metadata
        title_limit = min(120, PLATFORM_TITLE_LIMITS.get(platform, 120))
        raw_title = str((base_meta or {}).get('title') or '').replace('\r', ' ').replace('\n', ' ').strip()
        raw_title = re.sub(r'\s{2,}', ' ', raw_title) or 'Video VietSub'
        if len(raw_title) > title_limit:
            cut = raw_title[:title_limit].rstrip(' ,;:-—–')
            if ' ' in cut and raw_title[title_limit] != ' ':
                word_cut = cut.rsplit(' ', 1)[0].rstrip(' ,;:-—–')
                if len(word_cut) >= max(4, title_limit // 2):
                    cut = word_cut
            raw_title = cut or raw_title[:title_limit]
        raw_desc = str((base_meta or {}).get('description') or '').strip()[:5000]
        raw_tags = (base_meta or {}).get('hashtags') or []
        if isinstance(raw_tags, str):
            raw_tags = re.split(r'[\s,]+', raw_tags.strip()) if raw_tags.strip() else []
        clean_tags = []
        seen = set()
        for tag in raw_tags:
            if not isinstance(tag, str):
                continue
            token = tag.strip()
            if not token:
                continue
            if not token.startswith('#'):
                token = '#' + token
            if re.fullmatch(r'#[\w]{1,80}', token) and token.casefold() not in seen:
                seen.add(token.casefold())
                clean_tags.append(token)
        tag_limit = PLATFORM_TAG_LIMITS.get(platform, 30)
        clean_tags = clean_tags[:tag_limit]
        fitted = metadata({'title': raw_title, 'description': raw_desc, 'hashtags': clean_tags})
        # Fail closed on strict platform requirements (e.g. Bilibili public requires >= 1 hashtag).
        validate_platform_metadata(platform, fitted, visibility=visibility)
        return fitted

    def enqueue_pipeline_upload(self, job_id, publish_defaults, *, preset_id=''):
        """Safely enqueue a rendered project into UploadQueue from Auto Pipeline using deterministic request_id."""
        from .adapters import PLATFORMS, UploadError
        if self.app.shutdown_event.is_set():
            return {'status': 'skipped', 'jobs': [], 'job_ids': [], 'warnings': ['Ứng dụng đang đóng; bỏ qua xếp hàng Publish Hub.'], 'error': ''}
        update_lock = getattr(self.app, 'update_install_lock', None)
        if update_lock is not None and update_lock.locked():
            return {'status': 'skipped', 'jobs': [], 'job_ids': [], 'warnings': ['App đang cài đặt bản cập nhật; bỏ qua xếp hàng Publish Hub.'], 'error': ''}
        if not isinstance(publish_defaults, dict):
            return {'status': 'skipped', 'jobs': [], 'job_ids': [], 'warnings': ['Thiếu cấu hình publish_defaults trong hồ sơ kênh.'], 'error': ''}
        visibility = publish_defaults.get('visibility', 'private')
        upload_mode = publish_defaults.get('upload_mode', 'assisted')
        confirm_public_auto = publish_defaults.get('confirm_public_auto') is True
        if visibility not in {'private', 'public', 'draft'} or upload_mode not in UPLOAD_MODES:
            return {'status': 'error', 'jobs': [], 'job_ids': [], 'warnings': [], 'error': 'Cấu hình visibility/upload_mode không hợp lệ.'}
        if visibility == 'public' and upload_mode == 'auto' and not confirm_public_auto:
            msg = 'Đã chặn tự động đăng Công khai (auto) vì hồ sơ kênh chưa bật xác nhận cho phép tự đăng công khai.'
            self.app.log_to_app(f'[Auto Pipeline] {msg}', 'error')
            return {'status': 'skipped', 'jobs': [], 'job_ids': [], 'warnings': [msg], 'error': msg}
        raw_ids = list(dict.fromkeys(pid for pid in (publish_defaults.get('profile_ids') or []) if isinstance(pid, str) and pid))
        if not raw_ids:
            msg = 'Hồ sơ kênh chưa chọn tài khoản Publish Hub mặc định.'
            return {'status': 'skipped', 'jobs': [], 'job_ids': [], 'warnings': [msg], 'error': ''}

        store, _, queue = self.services()
        known_by_id = {p['id']: p for p in store.list()}
        warnings = []
        errors = []
        existing_ids = []
        for pid in raw_ids:
            if pid not in known_by_id:
                warnings.append(f'Tài khoản Publish Hub {pid[:8]} không còn tồn tại; đã bỏ qua.')
            else:
                existing_ids.append(pid)
        if not existing_ids:
            return {'status': 'skipped', 'jobs': [], 'job_ids': [], 'warnings': warnings, 'error': '; '.join(warnings)}

        queued_jobs = []
        # Decouple queueing from non-blocking runtime browser lease (store.lease):
        # reading profile registry under operation_admission_lock lets batch video 2+
        # queue cleanly while video 1 is actively uploading or awaiting manual submit.
        with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
            self._check_admission_open()
            _, _, value, path = self._project(job_id, ready=True)
            base_meta = copy.deepcopy(value.get('metadata') or {})
            if not str(base_meta.get('title') or '').strip():
                base_meta['title'] = value.get('project_name') or 'Video VietSub'
            stat = os.stat(path)
            source = {'job_id': job_id, 'video': path}
            for pid in existing_ids:
                try:
                    profile = store.get(pid)
                except KeyError:
                    warnings.append(f'Tài khoản {pid[:8]} vừa bị xoá; đã bỏ qua.')
                    continue
                pname = profile.get('name') or pid[:8]
                plat = profile.get('platform', '')
                if profile.get('session_status') not in {'alive', 'ready'}:
                    warnings.append(f'{pname} ({plat}): phiên đăng nhập chưa sẵn sàng; bỏ qua tự xếp hàng.')
                    continue
                if visibility not in PLATFORMS.get(plat, {}).get('modes', ()):
                    warnings.append(f'{pname} ({plat}): không hỗ trợ chế độ {visibility}; không tự hạ chế độ.')
                    continue
                if plat == 'youtube' and store.get_config(pid).get('upload_method') == 'youtube_api':
                    if visibility == 'public' and not confirm_public_auto:
                        msg = (
                            f'{pname} (YouTube API v3): YouTube Data API v3 không có bước dừng bấm Đăng thủ công (assisted); '
                            'đã chặn tự đăng Công khai khi hồ sơ chưa bật xác nhận công khai.'
                        )
                        warnings.append(msg)
                        errors.append(msg)
                        self.app.log_to_app(f'[Auto Pipeline] {msg}', 'error')
                        continue
                    yt_status = self._youtube_client().oauth_status(store.oauth_path(pid))
                    if not yt_status.get('connected'):
                        warnings.append(f'{pname} (YouTube API v3): chưa kết nối OAuth2; bỏ qua.')
                        continue
                try:
                    fitted_meta = self._fit_pipeline_metadata(plat, base_meta, visibility)
                except (UploadError, ValueError) as meta_err:
                    msg = f'{pname} ({plat}): {meta_err}'
                    warnings.append(msg)
                    errors.append(msg)
                    self.app.log_to_app(f'[Auto Pipeline] {msg}', 'error')
                    continue
                meta_digest = hashlib.sha256(
                    json.dumps(fitted_meta, sort_keys=True, ensure_ascii=False).encode('utf-8')
                ).hexdigest()[:16]
                req_seed = f'pipe|{job_id}|{preset_id}|{storage.canonical(path)}|{stat.st_size}|{stat.st_mtime_ns}|{pid}|{visibility}|{upload_mode}|{meta_digest}'
                request_id = 'pipe_' + hashlib.sha256(req_seed.encode('utf-8')).hexdigest()[:24]
                try:
                    created = queue.submit(
                        source, [profile], fitted_meta, visibility, None, request_id,
                        upload_mode=upload_mode, debug_mode=False,
                    )
                    queued_jobs.extend(created)
                except (ValueError, RuntimeError) as submit_err:
                    msg = f'{pname} ({plat}): {redaction.redact_error(submit_err)}'
                    warnings.append(msg)
                    errors.append(msg)
                    self.app.log_to_app(f'[Auto Pipeline] {msg}', 'error')
        status = 'queued' if queued_jobs else ('error' if errors else 'skipped')
        return {
            'status': status,
            'jobs': [self._public_job(j) for j in queued_jobs],
            'job_ids': [j['id'] for j in queued_jobs],
            'warnings': warnings,
            'error': '; '.join(errors) if (not queued_jobs and errors) else '',
        }

    def _profile_payload(self,profile,*,store=None):
        value=copy.deepcopy(profile)
        if value.get('session_status')=='alive':value['session_status']='ready'
        value['detail']=value.get('session_detail','')
        if store is None:
            try:
                store = self.services()[0]
            except Exception:
                store = None
        upload_method = 'browser'
        if store is not None:
            try:
                upload_method = store.get_config(profile['id']).get('upload_method', 'browser')
            except Exception:
                upload_method = 'browser'
        value['upload_method'] = upload_method
        if profile.get('platform') == 'youtube' and store is not None:
            try:
                value['youtube_api'] = self._youtube_client().oauth_status(store.oauth_path(profile['id']))
            except Exception:
                value['youtube_api'] = {'configured': False, 'connected': False, 'channel_title': '', 'token_expires_at': None, 'scopes': []}
        return value

    def _task(self,task):
        return {key:copy.deepcopy(value) for key,value in task.items() if not key.startswith('_')}

    def _session_screenshot_info(self,task):
        result=task.get('result')
        if not isinstance(result,dict):
            return '',''
        screenshot=result.get('screenshot_path') or ''
        if not screenshot:
            return '',''
        profile_root=Path(self.app.CONFIG_DIR)/'uploader'/'profiles'/task['profile_id']
        try:
            Path(screenshot).resolve().relative_to(profile_root.resolve())
        except ValueError:
            return '',''
        if not storage.safe_regular(screenshot):
            return '',''
        sign=hmac.new(self.app.API_TOKEN.encode('ascii'),f"upload-session-screen|{task['id']}|{screenshot}".encode('utf-8'),hashlib.sha256).hexdigest()
        return screenshot,sign

    def _public_session(self,task):
        value=self._task(task)
        result=value.get('result')
        if isinstance(result,dict):
            result.pop('screenshot_path',None)
            _,sign=self._session_screenshot_info(task)
            result['screenshot_url']=f"/api/uploader/sessions/{value['id']}/screenshot?signature={sign}" if sign else ''
        return value

    def _session_start(self,profile_id,action):
        with self.app.operation_admission_lock:
            self._check_admission_open()
            store,browser,queue=self.services();record=store.get(profile_id)
            if queue.profile_active(profile_id):raise RuntimeError('Tài khoản có upload đang chờ/chạy; huỷ/chờ trước khi đăng nhập.')
            if action == 'youtube_oauth' and record['platform'] != 'youtube':
                raise ValueError('Chỉ tài khoản YouTube mới hỗ trợ kết nối YouTube Data API v3.')
            with self.lock:
                if any(t.get('profile_id')==profile_id and t['status'] in {'queued','running'} for t in self.sessions.values()):
                    raise RuntimeError('Đang có thao tác login/check cho tài khoản này.')
                identifier=uuid.uuid4().hex;cancel,finish=Event(),Event()
                task={'id':identifier,'profile_id':profile_id,'action':action,'status':'queued','error':'',
                      '_cancel':cancel,'_finish':finish,'created_at':time.time()}
                self.sessions[identifier]=task
                self._trim_cache(self.sessions,terminal_only=True)
                snapshot=self._public_session(task)
        def execute():
            with self.lock:
                if identifier in self.sessions:self.sessions[identifier]['status']='running'
            try:
                if action == 'youtube_oauth':
                    from .youtube_api import YouTubeAuthError
                    client = self._youtube_client()
                    with store.lease(profile_id):
                        try:
                            status_info = client.start_oauth_flow(profile_id, store.oauth_path(profile_id), cancel=cancel)
                        except YouTubeAuthError as error:
                            store.update_session(profile_id, 'expired', error.session_detail)
                            raise
                        store.update_config(profile_id, upload_method='youtube_api', already_leased=True)
                        updated = store.update_session(profile_id, 'alive', 'oauth_connected')
                    result = {'id': profile_id, 'platform': 'youtube', 'status': 'alive',
                              'session_status': 'alive', 'session_detail': updated['session_detail'],
                              'youtube_api': status_info}
                elif action == 'check' and record['platform'] == 'youtube' and store.get_config(profile_id).get('upload_method') == 'youtube_api':
                    from .youtube_api import YouTubeAuthError
                    client = self._youtube_client()
                    with store.lease(profile_id):
                        try:
                            client.ensure_access_token(store.oauth_path(profile_id))
                            updated = store.update_session(profile_id, 'alive', 'oauth_connected')
                            result = {'id': profile_id, 'platform': 'youtube', 'status': 'alive',
                                      'session_status': 'alive', 'session_detail': updated['session_detail']}
                        except YouTubeAuthError as error:
                            updated = store.update_session(profile_id, 'expired', error.session_detail)
                            result = {'id': profile_id, 'platform': 'youtube', 'status': 'expired',
                                      'session_status': 'expired', 'session_detail': updated['session_detail']}
                else:
                    result=browser.login(profile_id,cancel=cancel,finish=finish) if action=='login' else browser.probe(profile_id)
                with self.lock:
                    if identifier in self.sessions:
                        self.sessions[identifier]['result']=result
                        self.sessions[identifier]['status']='cancelled' if cancel.is_set() else 'success'
            except Exception as error:
                with self.lock:
                    if identifier in self.sessions:
                        self.sessions[identifier].update(status='cancelled' if cancel.is_set() else 'error',error=redaction.redact_error(error)[:2000])
        worker = Thread(target=execute,name='uploader-session',daemon=True)
        with self.lock:
            if identifier in self.sessions:
                self.sessions[identifier]['_thread'] = worker
        worker.start()
        return snapshot

    def _job_trace_info(self,job):
        trace=job.get('trace_path') or ''
        if not trace:
            return '',''
        root=Path(self.app.CONFIG_DIR)/'uploader'/'profiles'/job['profile_id']/job['platform']/'artifacts'/'traces'
        try:
            Path(trace).resolve().relative_to(root.resolve())
        except ValueError:
            return '',''
        if not storage.safe_regular(trace):
            return '',''
        sign=hmac.new(self.app.API_TOKEN.encode('ascii'),f"upload-trace|{job['id']}|{trace}".encode('utf-8'),hashlib.sha256).hexdigest()
        return trace,sign

    def _public_job(self,job):
        value={key:copy.deepcopy(val) for key,val in job.items()
               if key not in {'video','metadata','video_mtime_ns','request_fingerprint','screenshot_path','trace_path'}}
        screenshot=job.get('screenshot_path')
        value['screenshot_url']=''
        if screenshot:
            root=Path(self.app.CONFIG_DIR)/'uploader'/'artifacts'/job['id']
            try:
                Path(screenshot).resolve().relative_to(root.resolve())
                if storage.safe_regular(screenshot):
                    sign=hmac.new(self.app.API_TOKEN.encode('ascii'),f"upload-screen|{job['id']}|{screenshot}".encode('utf-8'),hashlib.sha256).hexdigest()
                    value['screenshot_url']=f"/api/uploader/queue/{job['id']}/screenshot?signature={sign}"
            except ValueError:
                pass
        _,trace_sign=self._job_trace_info(job)
        value['trace_url']=f"/api/uploader/queue/{job['id']}/trace?signature={trace_sign}" if trace_sign else ''
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
        def invalid(error):return jsonify({'error':redaction.redact_error(error)}),400
        @bp.errorhandler(KeyError)
        def missing(error):return jsonify({'error':redaction.redact_error(error.args[0] if error.args else error)}),404
        @bp.errorhandler(RuntimeError)
        @bp.errorhandler(OSError)
        def conflict(error):return jsonify({'error':redaction.redact_error(error)}),409

        @bp.get('/api/uploader/capabilities')
        def capabilities():
            from .adapters import PLATFORMS
            _, selector_status = self._selectors()
            return jsonify({
                'browser_available':bool(self.app.resolve_browser_executable()),
                'timezone':'Asia/Saigon',
                'scheduling':'local',
                'upload_modes':['assisted','auto'],
                'default_upload_mode':'assisted',
                'debug_trace_supported':True,
                'youtube_api_supported':True,
                'selector_override':selector_status,
                'platforms':[{'key':key,**value} for key,value in PLATFORMS.items()],
            })

        @bp.route('/api/uploader/profiles',methods=['GET','POST'])
        def profiles():
            store,_,_=self.services()
            if request.method=='POST':
                data=self._body(('platform','name'),('platform','name'))
                return jsonify(self._profile_payload(store.create(data['platform'],data['name']),store=store)),201
            return jsonify({'profiles':[self._profile_payload(profile,store=store) for profile in store.list()]})

        @bp.delete('/api/uploader/profiles/<profile_id>')
        def delete_profile(profile_id):
            store,_,queue=self.services()
            def check_unused():
                with self.app.operation_admission_lock:
                    with self.lock:
                        if any(t.get('profile_id')==profile_id and t.get('status') in {'queued','running'} for t in self.sessions.values()):
                            raise RuntimeError('Tài khoản đang có thao tác login/check đang chạy.')
                    if queue.profile_active(profile_id):
                        raise RuntimeError('Tài khoản còn upload đang chờ/chạy.')
            store.delete(profile_id,before_delete=check_unused,clean_artifacts=True)
            stale_job_ids = [j['id'] for j in queue.snapshot() if j.get('profile_id') == profile_id and j.get('status') in TERMINAL]
            if stale_job_ids:
                queue._remove_job_artifacts(stale_job_ids)
            with self.lock:
                for key in [k for k,t in self.sessions.items() if t.get('profile_id')==profile_id and t.get('status') not in {'queued','running'}]:
                    self.sessions.pop(key,None)
            return jsonify({'ok':True})

        @bp.post('/api/uploader/profiles/<profile_id>/config')
        def update_profile_config(profile_id):
            store,_,queue=self.services()
            data=self._body(('upload_method',),('upload_method',))
            with store.lease(profile_id):
                if queue.profile_active(profile_id):
                    raise RuntimeError('Tài khoản còn upload đang chờ/chạy; chờ xong trước khi đổi phương thức đăng.')
                with self.lock:
                    if any(t.get('profile_id')==profile_id and t.get('status') in {'queued','running'} for t in self.sessions.values()):
                        raise RuntimeError('Tài khoản đang có thao tác đăng nhập/kiểm tra đang chạy.')
                prev_method = store.get_config(profile_id).get('upload_method', 'browser')
                store.update_config(profile_id, upload_method=data['upload_method'], already_leased=True)
                record = store.get(profile_id)
                if record['platform'] == 'youtube' and data['upload_method'] == 'youtube_api':
                    yt_status = self._youtube_client().oauth_status(store.oauth_path(profile_id))
                    if yt_status.get('connected'):
                        record = store.update_session(profile_id, 'alive', 'oauth_connected')
                    elif record.get('session_status') == 'alive':
                        record = store.update_session(profile_id, 'unknown', 'unknown')
                elif record['platform'] == 'youtube' and data['upload_method'] == 'browser' and prev_method == 'youtube_api':
                    if record.get('session_status') == 'alive' or str(record.get('session_detail', '')).startswith('oauth_'):
                        record = store.update_session(profile_id, 'unknown', 'unknown')
            return jsonify(self._profile_payload(record, store=store))

        @bp.post('/api/uploader/profiles/<profile_id>/youtube-oauth/config')
        def youtube_oauth_config(profile_id):
            store,_,queue=self.services()
            data=self._body(('client_id','client_secret','client_secret_json'))
            raw_json = data.get('client_secret_json') or ''
            cid = data.get('client_id') or ''
            csec = data.get('client_secret') or ''
            if not raw_json and not (cid and csec):
                raise ValueError('Nhập Client ID + Client Secret hoặc dán nội dung client_secret.json (Desktop app).')
            client=self._youtube_client()
            with store.lease(profile_id):
                record=store.get(profile_id)
                if record['platform']!='youtube':
                    raise ValueError('Chỉ tài khoản YouTube mới hỗ trợ cấu hình YouTube Data API v3.')
                if queue.profile_active(profile_id):
                    raise RuntimeError('Tài khoản còn upload đang chờ/chạy.')
                with self.lock:
                    if any(t.get('profile_id')==profile_id and t.get('status') in {'queued','running'} for t in self.sessions.values()):
                        raise RuntimeError('Tài khoản đang có thao tác đăng nhập/OAuth2 đang chạy.')
                status_info = client.save_client_config(
                    store.oauth_path(profile_id),
                    client_id=cid,
                    client_secret=csec,
                    client_secret_json=raw_json,
                )
                store.update_config(profile_id, upload_method='youtube_api', already_leased=True)
                if status_info.get('connected'):
                    record = store.update_session(profile_id, 'alive', 'oauth_connected')
                else:
                    record = store.update_session(profile_id, 'unknown', 'unknown')
            return jsonify(self._profile_payload(record, store=store))

        @bp.post('/api/uploader/profiles/<profile_id>/youtube-oauth/start')
        def youtube_oauth_start(profile_id):
            self._body(())
            return jsonify(self._session_start(profile_id, 'youtube_oauth')), 202

        @bp.post('/api/uploader/profiles/<profile_id>/youtube-oauth/disconnect')
        def youtube_oauth_disconnect(profile_id):
            store,_,queue=self.services()
            data=self._body(('keep_client',))
            keep_client=data.get('keep_client',False)
            if type(keep_client) is not bool:
                raise ValueError('keep_client phải là boolean.')
            client=self._youtube_client()
            with store.lease(profile_id):
                record=store.get(profile_id)
                if record['platform']!='youtube':
                    raise ValueError('Chỉ tài khoản YouTube mới hỗ trợ YouTube Data API v3.')
                if queue.profile_active(profile_id):
                    raise RuntimeError('Tài khoản còn upload đang chờ/chạy.')
                with self.lock:
                    if any(t.get('profile_id')==profile_id and t.get('status') in {'queued','running'} for t in self.sessions.values()):
                        raise RuntimeError('Tài khoản đang có thao tác đăng nhập/OAuth2 đang chạy.')
                client.disconnect(store.oauth_path(profile_id), keep_client=keep_client)
                if store.get_config(profile_id).get('upload_method') == 'youtube_api':
                    record = store.update_session(profile_id, 'unknown', 'oauth_revoked')
            return jsonify(self._profile_payload(record, store=store))

        @bp.post('/api/uploader/profiles/<profile_id>/login')
        def login(profile_id):
            self._body(());return jsonify(self._session_start(profile_id,'login')),202

        @bp.post('/api/uploader/profiles/<profile_id>/check')
        def check(profile_id):
            self._body(());return jsonify(self._session_start(profile_id,'check')),202

        @bp.route('/api/uploader/sessions/<identifier>',methods=['GET'])
        def session_status(identifier):
            with self.lock:
                if identifier not in self.sessions:raise KeyError('Không tìm thấy login/check task.')
                return jsonify(self._public_session(self.sessions[identifier]))

        @bp.get('/api/uploader/sessions/<identifier>/screenshot')
        def session_screenshot(identifier):
            with self.lock:
                if identifier not in self.sessions:raise KeyError('Không tìm thấy login/check task.')
                screenshot,sign=self._session_screenshot_info(self.sessions[identifier])
            if not sign or not secrets.compare_digest(sign.encode('ascii'),request.args.get('signature','').encode('utf-8')):
                return jsonify({'error':'Screenshot không khả dụng hoặc liên kết sai.'}),403
            if not storage.safe_regular(screenshot):
                return jsonify({'error':'Screenshot không còn khả dụng.'}),403
            resp = send_file(screenshot,mimetype='image/png')
            resp.headers['Cache-Control'] = 'no-store'
            return resp

        @bp.post('/api/uploader/sessions/<identifier>/finish')
        def finish_login(identifier):
            self._body(())
            with self.lock:
                if identifier not in self.sessions:raise KeyError('Không tìm thấy login task.')
                self.sessions[identifier]['_finish'].set()
                return jsonify(self._public_session(self.sessions[identifier]))

        @bp.post('/api/uploader/sessions/<identifier>/cancel')
        def cancel_login(identifier):
            self._body(())
            with self.lock:
                if identifier not in self.sessions:raise KeyError('Không tìm thấy login task.')
                self.sessions[identifier]['_cancel'].set();self.sessions[identifier]['_finish'].set()
                return jsonify(self._public_session(self.sessions[identifier]))

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
            admitted = False
            with self.app.operation_admission_lock,self.app.queue_state_lock:
                self._check_admission_open()
                if self.app.preview_lock.locked():
                    raise RuntimeError('Hãy chờ VietSub/preview xong trước khi tạo metadata AI.')
                _,result,value,_=self._project(job_id,ready=True)
                srt_path=value['assets'].get('translated_srt') or ''
                if not srt_path or not storage.safe_regular(srt_path):raise ValueError('Project không có SRT Việt để tạo metadata.')
                admitted = self.app.workflow_lock.acquire(blocking=False)
                if not admitted:
                    raise RuntimeError('Hãy chờ VietSub/preview xong trước khi tạo metadata AI.')
            identifier=uuid.uuid4().hex
            task={'id':identifier,'job_id':job_id,'status':'queued','error':'','_srt_path':storage.canonical(srt_path)}
            with self.lock:
                self.meta_tasks[identifier]=task
                self._trim_cache(self.meta_tasks,terminal_only=True)
                snapshot=self._task(task)
            def generate():
                with self.lock:
                    if identifier in self.meta_tasks:self.meta_tasks[identifier]['status']='running'
                try:
                    if self.app.shutdown_event.is_set():raise RuntimeError('App đang đóng.')
                    if not storage.safe_regular(srt_path):raise ValueError('Project không có SRT Việt để tạo metadata.')
                    # Share the legacy cancellation event while holding workflow_lock;
                    # /api/cancel must stop this acknowledged workflow as well.
                    self.app.cancel_event.clear()
                    self.app.workflow_context.cancel_event=self.app.cancel_event
                    config=self.app.get_translation_runtime_config(require_ready=True)
                    prompt=publish.build_prompt(self.app.read_text_file(srt_path),result.get('project_name',''))
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
                    proposal={key:pack[key] for key in ('title','description','hashtags')}
                    with self.lock:
                        if identifier in self.meta_tasks:
                            self.meta_tasks[identifier]['metadata']=proposal
                            self.meta_tasks[identifier]['status']='success'
                except Exception as error:
                    with self.lock:
                        if identifier in self.meta_tasks:
                            self.meta_tasks[identifier].update(status='error',error=redaction.redact_error(error)[:2000])
                finally:
                    if admitted:self.app.workflow_lock.release()
            worker = Thread(target=generate,name='uploader-metadata',daemon=True)
            with self.lock:
                if identifier in self.meta_tasks:
                    self.meta_tasks[identifier]['_thread'] = worker
            try:
                worker.start()
            except Exception:
                if admitted:
                    self.app.workflow_lock.release()
                raise
            return jsonify(snapshot),202

        @bp.get('/api/uploader/metadata/<identifier>')
        def metadata_status(identifier):
            with self.lock:
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
                if path.exists():path.unlink(missing_ok=True)
                meta_file=directory/'media.json'
                if meta_file.exists():meta_file.unlink(missing_ok=True)
                shutil.rmtree(directory,ignore_errors=True)
                raise
            with self.lock:
                self.media[(os.path.abspath(self.app.CONFIG_DIR),identifier)]=value
                evicted=self._trim_cache(self.media)
            if evicted:
                self._prune_media_disk(evicted_items=evicted)
            return jsonify(self._media_payload(value)),201

        @bp.get('/api/uploader/media/<identifier>/video')
        def media_video(identifier):
            value=self._media(identifier);expected=self._media_payload(value)['video_url'].partition('signature=')[2]
            if not secrets.compare_digest(expected.encode('ascii'),request.args.get('signature','').encode('utf-8')):return jsonify({'error':'Liên kết MP4 không hợp lệ.'}),403
            if not storage.safe_regular(value['path']):raise KeyError('MP4 đã bị xóa.')
            return send_file(value['path'],conditional=True,mimetype='video/mp4')

        @bp.delete('/api/uploader/queue/finished')
        def clear_finished_queue():
            data = request.get_json(silent=True)
            if data is None:
                data = {}
            if not isinstance(data, dict) or set(data) - {'include_uncertain'}:
                raise ValueError('JSON xoá lịch sử Publish Hub chỉ nhận tuỳ chọn include_uncertain.')
            include_uncertain = data.get('include_uncertain', False)
            if type(include_uncertain) is not bool:
                raise ValueError('include_uncertain phải là boolean.')
            _, _, queue = self.services()
            outcome = queue.clear_finished(include_uncertain=include_uncertain)
            return jsonify({'ok': True, **outcome, 'jobs': [self._public_job(j) for j in queue.snapshot()]})

        @bp.route('/api/uploader/queue',methods=['GET','POST'])
        def queue():
            store,_,queue=self.services()
            if request.method=='GET':return jsonify({'jobs':[self._public_job(j) for j in queue.snapshot()]})
            data=self._body(
                ('job_id','media_id','profile_ids','metadata','visibility','scheduled_at','request_id','confirm_public','upload_mode','debug_mode'),
                ('profile_ids','metadata','visibility','request_id'),
            )
            if bool(data.get('job_id'))==bool(data.get('media_id')):raise ValueError('Chọn một project hoặc một MP4.')
            if any(key in data and data[key] is not None and (not isinstance(data[key],str) or len(data[key])>256)
                   for key in ('job_id','media_id')):
                raise ValueError('Project/media ID không hợp lệ.')
            if not isinstance(data['request_id'],str) or not 8 <= len(data['request_id']) <= 100:
                raise ValueError('request_id không hợp lệ.')
            if (not isinstance(data['profile_ids'],list) or not data['profile_ids'] or len(data['profile_ids'])>30
                    or any(not isinstance(identifier,str) for identifier in data['profile_ids'])):
                raise ValueError('Danh sách tài khoản không hợp lệ.')
            upload_mode = data.get('upload_mode', 'auto')
            if upload_mode not in UPLOAD_MODES:
                raise ValueError('upload_mode chỉ hỗ trợ auto hoặc assisted.')
            debug_mode = data.get('debug_mode', False)
            if type(debug_mode) is not bool:
                raise ValueError('debug_mode phải là boolean.')

            def _check_replay(prior_jobs):
                submitted_schedule=data.get('scheduled_at')
                try:
                    if submitted_schedule:
                        dt=datetime.fromisoformat(submitted_schedule.replace('Z','+00:00'))
                        if dt.tzinfo is None or dt.utcoffset() is None:
                            raise ValueError()
                        stamp=dt.timestamp()
                    else:
                        stamp=None
                    expected_profiles=set(data['profile_ids'])
                except (ValueError,TypeError,AttributeError):
                    raise ValueError('Payload request_id cũ không hợp lệ.') from None
                if (set(job['profile_id'] for job in prior_jobs)!=expected_profiles
                        or any(job.get('job_id','')!=(data.get('job_id') or '') or job.get('media_id','')!=(data.get('media_id') or '')
                               or job['metadata']!=metadata(data['metadata']) or job['visibility']!=data['visibility']
                               or job.get('upload_mode','auto')!=upload_mode or bool(job.get('debug_mode',False))!=debug_mode
                               or job.get('scheduled_at')!=stamp for job in prior_jobs)):
                    raise ValueError('request_id đã dùng cho payload khác; không đăng lại.')
                return jsonify({'jobs':[self._public_job(job) for job in prior_jobs]}),202

            ids=list(dict.fromkeys(data['profile_ids']))
            from .adapters import PLATFORMS
            visibility=data['visibility']
            with self.app.operation_admission_lock,self.app.queue_state_lock:
                prior=[job for job in queue.snapshot() if job.get('request_id')==data['request_id']]
                if prior:
                    return _check_replay(prior)
                self._check_admission_open()
                if visibility=='public' and data.get('confirm_public') is not True:
                    raise ValueError('Cần xác nhận rõ trước khi đăng công khai.')
                scheduled=schedule_time(data.get('scheduled_at'))
                profiles=[store.get(i) for i in ids]
                if any(profile.get('session_status') not in {'alive','ready'} for profile in profiles):
                    raise ValueError('Chỉ xếp hàng tài khoản đã đăng nhập và kiểm tra phiên sẵn sàng.')
                if not isinstance(visibility,str) or any(visibility not in PLATFORMS[p['platform']]['modes'] for p in profiles):
                    raise ValueError('Có nền tảng không hỗ trợ chế độ hiển thị đã chọn; không tự chuyển sang public.')
                for profile in profiles:
                    if profile['platform'] == 'youtube' and store.get_config(profile['id']).get('upload_method') == 'youtube_api':
                        yt_status = self._youtube_client().oauth_status(store.oauth_path(profile['id']))
                        if not yt_status.get('connected'):
                            raise ValueError('Tài khoản YouTube (API v3) chưa hoàn tất kết nối OAuth2.')
                if data.get('job_id'):
                    if not isinstance(data['job_id'],str):raise ValueError('Project ID không hợp lệ.')
                    _,_,_,path=self._project(data['job_id'],ready=True)
                    source={'job_id':data['job_id'],'video':path}
                else:
                    value=self._media(data['media_id']);source={'media_id':value['id'],'video':value['path']}
                records=queue.submit(source,profiles,data['metadata'],visibility,scheduled,data['request_id'],
                                     upload_mode=upload_mode,debug_mode=debug_mode)
            return jsonify({'jobs':[self._public_job(j) for j in records]}),202

        @bp.post('/api/uploader/queue/<identifier>/cancel')
        def cancel_upload(identifier):
            self._body(());return jsonify(self._public_job(self.services()[2].cancel(identifier)))

        @bp.post('/api/uploader/queue/<identifier>/confirm-submitted')
        def confirm_submitted(identifier):
            self._body(());return jsonify(self._public_job(self.services()[2].confirm_submitted(identifier)))

        @bp.post('/api/uploader/queue/<identifier>/retry')
        def retry_upload(identifier):
            data=self._body(('confirm_uncertain',));confirm=data.get('confirm_uncertain',False)
            if type(confirm) is not bool:raise ValueError('confirm_uncertain phải là boolean.')
            store,_,queue=self.services();job=queue.get(identifier)
            with self.app.operation_admission_lock,self.app.queue_state_lock:
                self._check_admission_open()
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
            resp = send_file(job['screenshot_path'],mimetype='image/png')
            resp.headers['Cache-Control'] = 'no-store'
            return resp

        @bp.get('/api/uploader/queue/<identifier>/trace')
        def trace(identifier):
            job=self.services()[2].get(identifier)
            trace_path,sign=self._job_trace_info(job)
            if not sign or not secrets.compare_digest(sign.encode('ascii'),request.args.get('signature','').encode('utf-8')):
                return jsonify({'error':'Trace không khả dụng hoặc liên kết sai.'}),403
            if not storage.safe_regular(trace_path):
                return jsonify({'error':'Trace không còn khả dụng.'}),403
            resp = send_file(
                trace_path,
                mimetype='application/zip',
                as_attachment=True,
                download_name=f"vietsub-trace-{job['id'][:12]}.zip",
            )
            resp.headers['Cache-Control'] = 'no-store'
            return resp


def register(runtime):
    adapter=UploaderAPI(runtime);runtime.app.register_blueprint(adapter.bp);return adapter
