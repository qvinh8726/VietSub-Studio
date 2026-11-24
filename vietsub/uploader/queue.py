"""Durable, failure-isolated publication queue with an irreversible-submit checkpoint."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from threading import Event, RLock, Thread
import time
import uuid

from vietsub import manifest, redaction, storage

MAX_QUEUE_BYTES = 8 * 1024 * 1024
TERMINAL = {'success', 'error', 'needs_verification', 'cancelled', 'uncertain'}
ACTIVE = {'queued', 'scheduled', 'running'}


def metadata(value):
    if not isinstance(value, dict) or set(value) - {'title','description','hashtags'}:
        raise ValueError('Metadata cần title, description và hashtags.')
    result = {}
    for key, limit in (('title',120),('description',5000)):
        text = value.get(key,'')
        if not isinstance(text,str) or len(text)>limit or any(ord(c)<32 and c not in '\n\t' for c in text):
            raise ValueError(f'{key} không hợp lệ hoặc quá dài.')
        result[key]=text.strip()
    if not result['title']:
        raise ValueError('Cần tiêu đề video trước khi xếp lịch đăng.')
    tags=value.get('hashtags',[])
    if not isinstance(tags,list) or len(tags)>30 or any(not isinstance(t,str) or not re.fullmatch(r'#[\w]{1,80}',t) for t in tags):
        raise ValueError('Hashtag cần mảng tối đa30 chuỗi #không_dấu_cách.')
    result['hashtags']=list(dict.fromkeys(tags))
    return result


def schedule_time(value, now=None):
    if value is None or value=='': return None
    if not isinstance(value,str) or len(value)>80: raise ValueError('Lịch đăng phải là ISO8601 có múi giờ.')
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None or dt.utcoffset() is None: raise ValueError()
        stamp=dt.timestamp()
    except (ValueError, OverflowError):
        raise ValueError('Lịch đăng cần ngày giờ hợp lệ kèm UTC offset, ví dụ +07:00.') from None
    now=time.time() if now is None else now
    if not now-60<=stamp<=now+366*86400: raise ValueError('Lịch đăng phải trong vòng366 ngày và không nằm trong quá khứ.')
    return stamp


def atomic_json(path,value):
    path=Path(path); manifest._safe_path(str(path)); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        manifest._safe_path(str(path));os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


class UploadQueue:
    def __init__(self,path,executor,*,on_finish=None,clock=time.time):
        self.path=Path(path); self.executor=executor; self.on_finish=on_finish; self.clock=clock
        self.lock=RLock(); self.wake=Event();self.stop=Event();self.worker=None;self.events={}
        self.jobs=[];self._load();self._cleanup_orphan_snapshots()

    def _load(self):
        manifest._safe_path(str(self.path))
        if not self.path.exists():return
        if self.path.stat().st_size>8*1024*1024:raise ValueError('Upload queue quá lớn; giữ nguyên để khôi phục.')
        try: value=json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (ValueError,UnicodeError,RecursionError):raise ValueError('Upload queue JSON hỏng; không ghi đè.') from None
        if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1 or not isinstance(value.get('jobs'),list):
            raise ValueError('Upload queue schema không hỗ trợ; không ghi đè.')
        if len(value['jobs'])>500:raise ValueError('Upload queue vượt500 bản ghi.')
        seen=set()
        interrupted=False
        for job in value['jobs']:
            if (not isinstance(job,dict) or not isinstance(job.get('id'),str) or not re.fullmatch('[0-9a-f]{32}',job['id'])
                    or job['id'] in seen or job.get('status') not in TERMINAL|ACTIVE):
                raise ValueError('Upload queue record không hợp lệ; giữ nguyên dữ liệu.')
            seen.add(job['id'])
            metadata(job.get('metadata'))
            if not isinstance(job.get('video'),str) or not isinstance(job.get('profile_id'),str):raise ValueError('Queue thiếu video/profile.')
            scheduled=job.get('scheduled_at')
            if scheduled is not None and (type(scheduled) not in (int,float) or not math.isfinite(scheduled)):
                raise ValueError('Queue schedule không hợp lệ.')
            if job['status']=='running':
                interrupted=True
                job.update(status='uncertain' if job.get('commit_attempted') else 'error',phase='interrupted',
                           error='App đã đóng khi upload. Kiểm tra nền tảng trước khi thử lại; không tự đăng lại.')
            self.jobs.append(job)
        self.events={job['id']:Event() for job in self.jobs}
        if interrupted:
            self._persist(self.jobs)

    def _cleanup_orphan_snapshots(self):
        private_root=self.path.parent/'input-snapshots'
        try:
            manifest._safe_path(str(private_root))
        except (OSError,ValueError):
            return
        if not private_root.is_dir():
            return
        with self.lock:
            active_ids={job['id'] for job in self.jobs if job['status'] in ACTIVE}
        for entry in list(private_root.iterdir()):
            if entry.is_symlink() or (os.lstat(entry).st_file_attributes if os.name=='nt' else 0) & 0x400:
                continue
            prefix=entry.name.split('-',1)[0]
            if not re.fullmatch(r'[0-9a-f]{32}',prefix) or prefix in active_ids:
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry,ignore_errors=True)
                elif entry.is_file():
                    entry.unlink(missing_ok=True)
            except OSError:
                pass

    def _persist(self,jobs):
        payload={'version':1,'jobs':jobs}
        if len(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')) > MAX_QUEUE_BYTES:
            raise ValueError('Upload queue vượt giới hạn8MiB; không nhận thêm dữ liệu để giữ khả năng mở lại.')
        atomic_json(self.path,payload)

    @staticmethod
    def publication_key(source,profile_id,meta,visibility):
        value={'video':storage.canonical(source['video']),'profile_id':profile_id,
               'metadata':meta,'visibility':visibility}
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.jobs)

    def get(self,identifier):
        with self.lock:
            job=next((j for j in self.jobs if j['id']==identifier),None)
            if not job:raise KeyError('Không tìm thấy upload job.')
            return copy.deepcopy(job)

    def _update(self,identifier,**fields):
        with self.lock:
            candidate=copy.deepcopy(self.jobs)
            job=next(j for j in candidate if j['id']==identifier);job.update(fields,updated_at=self.clock())
            self._persist(candidate);self.jobs=candidate

    def submit(self,source,profiles,meta,visibility,scheduled,request_id):
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request_id):raise ValueError('Cần request_id ổn định để chống đăng trùng.')
        meta=metadata(meta)
        if not profiles or len(profiles)>30:raise ValueError('Chọn1–30 tài khoản.')
        from .adapters import UploadError, validate_platform_metadata
        for profile in profiles:
            try:
                validate_platform_metadata(profile['platform'], meta, visibility=visibility)
            except UploadError as error:
                raise ValueError(str(error)) from None
        if not storage.safe_regular(source['video']):raise ValueError('Video không còn khả dụng.')
        info=os.stat(source['video'])
        canonical={**source,'profiles':[p['id'] for p in profiles],'metadata':meta,'visibility':visibility,'scheduled_at':scheduled}
        fingerprint=hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
        with self.lock:
            prior=[j for j in self.jobs if j.get('request_id')==request_id]
            if prior:
                if any(j['request_fingerprint']!=fingerprint for j in prior):raise ValueError('request_id đã dùng cho nội dung khác.')
                return copy.deepcopy(prior)
            if self.stop.is_set():raise RuntimeError('Uploader đang đóng.')
            if len(self.jobs)+len(profiles)>500:raise RuntimeError('Upload history đạt giới hạn500; hãy lưu/xóa lịch sử kết thúc.')
            for profile in profiles:
                publication=self.publication_key(source,profile['id'],meta,visibility)
                duplicate=next((job for job in self.jobs if job['profile_id']==profile['id']
                                 and self.publication_key(job,job['profile_id'],job['metadata'],job['visibility'])==publication
                                 and job['status'] in ACTIVE|{'success','uncertain'}),None)
                if duplicate:raise ValueError('Nội dung và tài khoản này đã có job chờ/đăng/thành công hoặc chưa rõ kết quả; không tạo bản đăng trùng.')
            records=[]
            for profile in profiles:
                records.append({'id':uuid.uuid4().hex,'profile_id':profile['id'],'platform':profile['platform'],
                    'job_id':source.get('job_id',''),'media_id':source.get('media_id',''),'video':source['video'],
                    'video_size':info.st_size,'video_mtime_ns':info.st_mtime_ns,'metadata':copy.deepcopy(meta),'visibility':visibility,
                    'scheduled_at':scheduled,'status':'scheduled' if scheduled and scheduled>self.clock() else 'queued',
                    'phase':'waiting','percent':0,'error':'','result_url':'','screenshot_path':'','commit_attempted':False,
                    'created_at':self.clock(),'request_id':request_id,'request_fingerprint':fingerprint})
            self._persist([*self.jobs,*records]);self.jobs.extend(records)
            for record in records:self.events[record['id']]=Event()
        self.start();self.wake.set();return copy.deepcopy(records)

    def start(self):
        with self.lock:
            if self.stop.is_set():return
            if self.worker is None or not self.worker.is_alive():
                self.worker=Thread(target=self._loop,name='uploader-queue',daemon=True);self.worker.start()

    def active(self,job_id):
        with self.lock:return any(j.get('job_id')==job_id and j['status'] in ACTIVE for j in self.jobs)

    def profile_active(self,profile_id):
        with self.lock:return any(j['profile_id']==profile_id and j['status'] in ACTIVE for j in self.jobs)

    def protected_paths(self):
        with self.lock:return {storage.canonical(j['video']) for j in self.jobs if j['status'] in ACTIVE}

    def cancel(self,identifier):
        with self.lock:
            job=self.get(identifier)
            if job['status'] in {'queued','scheduled'}:self._update(identifier,status='cancelled',phase='cancelled')
            self.events[identifier].set()
        self.wake.set();return self.get(identifier)

    def retry(self,identifier,*,confirm_uncertain=False):
        with self.lock:
            job=self.get(identifier)
            if job['status'] not in {'error','cancelled','needs_verification','uncertain'}:raise ValueError('Chỉ thử lại job lỗi/huỷ/xác minh/chưa rõ kết quả.')
            if job.get('commit_attempted') and not confirm_uncertain:raise ValueError('Có thể video đã đăng. Xác nhận đã kiểm tra nền tảng trước khi thử lại.')
            if self.profile_active(job['profile_id']):raise RuntimeError('Tài khoản còn job đang chờ/chạy.')
            publication=self.publication_key(job,job['profile_id'],job['metadata'],job['visibility'])
            if any(other['id']!=identifier and other['status'] in ACTIVE|{'success','uncertain'}
                   and self.publication_key(other,other['profile_id'],other['metadata'],other['visibility'])==publication
                   for other in self.jobs):
                raise ValueError('Nội dung này đã được đăng hoặc có job chưa rõ kết quả; không retry bản cũ trùng lặp.')
            self._update(identifier,status='queued',phase='waiting',scheduled_at=None,error='',percent=0,commit_attempted=False,
                         screenshot_path='',result_url='',message='',started_at=None,finished_at=None)
            self.events[identifier]=Event()
        self.start();self.wake.set();return self.get(identifier)

    def _execute(self,job):
        identifier=job['id'];cancel=self.events[identifier]
        committed=False
        persistence_failed=False
        snapshot_dir=None
        try:
            if cancel.is_set():raise RuntimeError('Đã huỷ upload.')
            if not storage.safe_regular(job['video']):raise ValueError('Video đã bị di chuyển/xóa.')
            info=os.stat(job['video'])
            if (info.st_size,info.st_mtime_ns)!=(job['video_size'],job['video_mtime_ns']):raise ValueError('Video đã đổi sau khi xếp lịch; không upload file khác.')
            self._update(identifier,status='running',phase='snapshot',started_at=self.clock())
            # Upload an independent, task-private copy, never the mutable source
            # pathname after navigation/auth waits. No hardlink (edits would leak).
            private_root=self.path.parent/'input-snapshots'
            manifest._safe_path(str(private_root));private_root.mkdir(mode=0o700,parents=True,exist_ok=True)
            snapshot_dir=Path(tempfile.mkdtemp(prefix=identifier+'-',dir=private_root))
            snapshot=snapshot_dir/'video.mp4'
            with open(job['video'],'rb') as source, snapshot.open('xb') as target:
                opened=os.fstat(source.fileno())
                if (opened.st_size,opened.st_mtime_ns)!=(job['video_size'],job['video_mtime_ns']):
                    raise ValueError('Video đã đổi trước khi tạo bản sao upload.')
                while True:
                    if cancel.is_set() or self.stop.is_set():raise RuntimeError('Đã huỷ sao chép video upload.')
                    chunk=source.read(1024*1024)
                    if not chunk:break
                    target.write(chunk)
                target.flush();os.fsync(target.fileno())
                after=os.fstat(source.fileno())
            named=os.stat(job['video'],follow_symlinks=False)
            if ((opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
                    or (opened.st_dev,opened.st_ino)!=(named.st_dev,named.st_ino)):
                raise ValueError('Video thay đổi trong khi sao chép; không upload.')
            self._update(identifier,phase='prepare')
            def checkpoint():
                nonlocal committed,persistence_failed
                if cancel.is_set() or self.stop.is_set():raise RuntimeError('Đã huỷ trước bước đăng cuối.')
                try:
                    self._update(identifier,commit_attempted=True,phase='committing')
                except Exception:
                    # This latch lives outside sanitized browser/adapter errors.
                    # Even if they wrap the exception, no later account may post.
                    persistence_failed=True;self.stop.set();self.wake.set()
                    raise
                committed=True
            def progress(**fields):
                allowed={key:value for key,value in fields.items() if key in {'phase','percent'}}
                if 'percent' in allowed:
                    n=allowed['percent'];allowed['percent']=max(0,min(99,float(n))) if type(n) in (int,float) and math.isfinite(n) else 0
                self._update(identifier,**allowed)
            task_input=copy.deepcopy(job);task_input['video']=str(snapshot)
            result=self.executor(task_input,cancel,progress,checkpoint) or {}
            if result.get('status') not in {'success','draft'}:raise RuntimeError('Không có xác nhận upload/draft từ nền tảng.')
            if not committed:raise RuntimeError('Adapter thiếu checkpoint trước khi đăng; không công bố thành công.')
            self._update(identifier,status='success',phase='done',percent=100,result_url=result.get('url',''),message=redaction.redact_text(result.get('message','')),finished_at=self.clock())
        except Exception as error:
            from .adapters import VerificationRequired
            state='uncertain' if committed else 'error' if persistence_failed else 'needs_verification' if isinstance(error,VerificationRequired) else 'cancelled' if cancel.is_set() or self.stop.is_set() else 'error'
            self._update(identifier,status=state,phase=state,error=('Lỗi lưu checkpoint; uploader đã dừng để tránh đăng thiếu xác nhận.' if persistence_failed else redaction.redact_error(error)[:3000]),
                         screenshot_path=getattr(error,'screenshot_path',''),finished_at=self.clock())
        finally:
            if snapshot_dir is not None:
                # Exact random task-owned directory verified above; no project walk.
                if snapshot_dir.parent == self.path.parent/'input-snapshots' and snapshot_dir.name.startswith(identifier+'-'):
                    shutil.rmtree(snapshot_dir,ignore_errors=True)
            if self.on_finish:
                try:self.on_finish(self.get(identifier))
                except Exception:pass  # durable queue remains authoritative

    def _loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            with self.lock:
                pending=[j for j in self.jobs if j['status'] in {'queued','scheduled'}]
                due=next((copy.deepcopy(j) for j in pending if not j.get('scheduled_at') or j['scheduled_at']<=self.clock()),None)
                delay=min([max(.1,j['scheduled_at']-self.clock()) for j in pending if j.get('scheduled_at')]+[60])
            if due:
                try:self._execute(due)
                except Exception:
                    # Persistence failure must never reach another final click.
                    self.stop.set();return
            else:self.wake.wait(delay)

    def shutdown(self):
        self.stop.set();self.wake.set()
        with self.lock:
            for event in self.events.values():event.set()
            worker=self.worker
        if worker:worker.join(timeout=3)
