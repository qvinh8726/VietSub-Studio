"""Phase 2 editor API/queue/options safety, isolated media and mocked worker boundary."""
import copy
import io
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

try:
    from . import _isolation  # noqa: F401
except ImportError:
    import _isolation  # noqa: F401
import app
from vietsub import manifest
from vietsub.editor.api import _png
from vietsub.editor.jobs import EditorJobs
from vietsub.editor.options import normalize
from vietsub.editor import process, renderer

RAW = "1\n00:00:00,000 --> 00:00:01,000\n你好世界\n"
VI = "1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n"
CROP = {"x": .04, "y": .64, "width": .92, "height": .30}


class EditorOptionsTests(unittest.TestCase):
    def test_unknown_commands_nonfinite_and_boolean_numbers_are_rejected(self):
        for options in ({"filter_complex": "movie=secret"}, {"encoder": "shell"}, {"blur_strength": True},
                        {"font_size": float("inf")}, {"tts_speed": 10**400}, {"tts_enabled": "false"},
                        {"crop_box": {**CROP, "x": 1}}, {"text_color": "red;movie=secret"},
                        {"font": "Injected,0,0"}, {"output_height": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                normalize(options)

    def test_defaults_do_not_mask_whole_unknown_frame_and_styles_roundtrip(self):
        self.assertEqual(normalize()["mask_mode"], "none")
        self.assertEqual(normalize(inherited_crop=CROP)["mask_mode"], "boxblur")
        options = normalize({"reframe": "vertical", "subtitle_preset": "anime"}, inherited_crop=CROP)
        self.assertEqual(normalize({}, base=options), options)


class EditorQueueTests(unittest.TestCase):
    def setUp(self):
        self.jobs = EditorJobs()
        self.addCleanup(self.jobs.shutdown)

    def test_success_progress_and_finish_commit_precede_terminal_status(self):
        events = []
        def execute(identifier, cancel, update):
            update(percent=50, fps=24, phase="render")
            events.append("render")
            return {"output_path": "final.mp4", "output_kind": "video"}
        def finish(task, state):
            self.assertEqual(task["status"], "running")
            events.append(state)
        task = self.jobs.submit("p1", "render", execute, finish=finish)
        self.jobs.work.join()
        result = self.jobs.snapshot(task["id"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["percent"], 100)
        self.assertEqual(events, ["render", "success"])

    def test_cancel_protects_inputs_and_rejects_second_task_for_same_project(self):
        entered = threading.Event()
        def execute(identifier, cancel, update):
            entered.set(); cancel.wait(3); process.check_cancel(cancel)
        task = self.jobs.submit("p1", "render", execute, protected_paths=[__file__])
        self.assertTrue(entered.wait(2))
        self.assertTrue(self.jobs.active("p1"))
        self.assertIn(os.path.normcase(os.path.abspath(__file__)), self.jobs.protected_paths())
        with self.assertRaises(RuntimeError):
            self.jobs.submit("p1", "tts", execute)
        self.jobs.cancel(task["id"]); self.jobs.work.join()
        self.assertEqual(self.jobs.snapshot(task["id"])["status"], "cancelled")
        self.assertFalse(self.jobs.active("p1"))
        self.assertEqual(self.jobs.protected_paths(), set())

    def test_preview_cancel_after_publish_discards_only_task_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / '.vietsub-editor-previews'; root.mkdir()
            previous = root / ('d'*32 + '.wav'); previous.write_bytes(b'previous')
            def execute(identifier, cancel, update):
                output = root / (identifier + '.wav'); output.write_bytes(b'new')
                cancel.set()
                return {'output_path': str(output), 'output_kind': 'audio'}
            task = self.jobs.submit('p1', 'audio_preview', execute)
            self.jobs.work.join()
            self.assertEqual(self.jobs.snapshot(task['id'])['status'], 'cancelled')
            self.assertEqual(list(root.iterdir()), [previous])
            self.assertEqual(previous.read_bytes(), b'previous')

    def test_preview_manifest_commit_failure_discards_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / '.vietsub-editor-previews'; root.mkdir()
            def execute(identifier, cancel, update):
                output = root / (identifier + '.wav'); output.write_bytes(b'new')
                return {'output_path': str(output), 'output_kind': 'audio'}
            def finish(task, state):
                if state == 'success':
                    raise OSError('manifest locked')
            task = self.jobs.submit('p1', 'audio_preview', execute, finish=finish)
            self.jobs.work.join()
            self.assertEqual(self.jobs.snapshot(task['id'])['status'], 'error')
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(self.jobs.snapshot(task['id'])['output_path'], '')

    def test_failed_commit_is_not_reported_as_success(self):
        def fail(task, state):
            if state == "success":
                raise PermissionError("manifest locked")
        task = self.jobs.submit("p1", "render", lambda *args: {}, finish=fail)
        self.jobs.work.join()
        self.assertEqual(self.jobs.snapshot(task["id"])["status"], "error")

    def test_progress_parser_uses_microseconds_and_real_fps(self):
        updates = []
        read = renderer.progress_reader(10, lambda **value: updates.append(value))
        read("fps=24.3"); read("out_time_us=5000000"); read("progress=continue")
        self.assertEqual(updates[-1]["percent"], 50)
        self.assertEqual(updates[-1]["fps"], 24.3)
        read("out_time_us=NaN")
        self.assertLessEqual(updates[-1]["percent"], 99.9)


class EditorAPITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vietsub-editor-api-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.dict(app.app.config, TESTING=True))
        self.jobs = EditorJobs()
        self.addCleanup(self.jobs.shutdown)
        self.enterContext(patch.object(app.editor_api, "jobs", self.jobs))
        self.enterContext(patch.object(app, "CONFIG_DIR", str(self.root)))
        self.enterContext(patch.object(app, "CONFIG_PATH", str(self.root / "config.json")))
        self.enterContext(patch.object(app, "shutdown_event", threading.Event()))
        self.enterContext(patch.object(app, "current_job_id", ""))
        self.enterContext(patch.object(app, "prefetch_job_id", ""))
        self.enterContext(patch.object(app, "log_to_app"))
        self.project = {"project_dir": str(self.root), "project_name": "clip", "video": str(self.root / "clip.mp4"),
                        "source_video": str(self.root / "clip.mp4"), "raw_srt": str(self.root / "clip.raw.srt"),
                        "translated_srt": str(self.root / "clip.vi.srt"),
                        "crop_coords": {"crop_"+key: val for key, val in CROP.items()}}
        Path(self.project["video"]).write_bytes(b"video")
        Path(self.project["raw_srt"]).write_text(RAW, encoding="utf-8")
        Path(self.project["translated_srt"]).write_text(VI, encoding="utf-8")
        self.job = {"id": "one", "status": "success", "result_ready": True, "result": self.project,
                    "source_type": "local", "source_video": self.project["video"]}
        self.enterContext(patch.object(app, "workflow_jobs", [self.job]))
        self.client = app.app.test_client()

    def test_draft_export_preserves_speed_pitch_and_signed_zip(self):
        from vietsub import capcut_draft
        output = self.root / 'draft.zip'
        def execute(identifier, project, options, **kwargs):
            self.assertEqual(options['speed'], 1.5)
            self.assertIs(options['change_audio_pitch'], True)
            self.assertEqual(options['pitch_semitones'], -2)
            output.write_bytes(b'zip fixture')
            return {'output_path': str(output), 'output_kind': 'archive'}
        with (patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(capcut_draft, 'execute', side_effect=execute)):
            response = self.client.post('/api/editor/projects/one/capcut-draft', json={'options': {
                'speed': 1.5, 'change_audio_pitch': True, 'pitch_semitones': -2}})
            self.assertEqual(response.status_code, 202, response.get_json())
            self.jobs.work.join()
        task = self.client.get('/api/editor/jobs/' + response.get_json()['id']).get_json()
        self.assertEqual(task['status'], 'success', task)
        self.assertEqual(task['action'], 'capcut_draft')
        self.assertEqual(manifest.snapshot(self.project)['editor_options']['speed'], 1.5)
        self.assertEqual(Path(self.project['video']).read_bytes(), b'video')
        with self.client.get(task['output_url']) as response:
            self.assertEqual(response.mimetype, 'application/zip')
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertEqual(response.get_data(), b'zip fixture')
        self.assertEqual(self.client.get(task['output_url'].split('?')[0]).status_code, 403)
        with patch.dict(app.app.config, TESTING=False):
            self.assertEqual(self.client.post('/api/editor/projects/one/capcut-draft', json={'options': {}}).status_code, 403)

    def test_ducking_is_optional_without_changing_default_mix(self):
        for enabled in (True, False):
            options = normalize({'ducking_enabled': enabled})
            filters, label = renderer._audio_graph(True, None, 1, options, 10)
            self.assertEqual('sidechaincompress' in ';'.join(filters), enabled)
            self.assertIn('amix=inputs=2', ';'.join(filters))
            self.assertEqual(label, '[aout]')
        self.assertIs(normalize({})['ducking_enabled'], True)
        with self.assertRaises(ValueError):
            normalize({'ducking_enabled': 'false'})

    def test_get_options_is_pure_and_save_crop_never_changes_ocr_geometry(self):
        path = manifest.path_for(self.project)
        response = self.client.get('/api/editor/projects/one')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Path(path).exists())
        self.assertEqual(response.get_json()["options"]["crop_box"], CROP)
        crop = {**CROP, "y": .5}
        response = self.client.post('/api/editor/projects/one', json={"options": {"crop_box": crop}})
        self.assertEqual(response.status_code, 200)
        saved = manifest.snapshot(self.project)
        self.assertEqual(saved["crop_box"], CROP)
        self.assertEqual(saved["editor_options"]["crop_box"], crop)

    def test_no_arbitrary_filter_input_paths_or_unregistered_watermark(self):
        for options in ({"ffmpeg_args": ["-i", "secret"]}, {"watermark_path": str(self.root / "secret.png")},
                        {"tts_enabled": True}, {"watermark_enabled": True}):
            response = self.client.post('/api/editor/projects/one', json={"options": options})
            self.assertEqual(response.status_code, 400, response.get_json())

    def test_queue_render_then_signed_output_and_manifest_keep_source(self):
        output = self.root / "final.mp4"
        def execute(identifier, project, options, action, **kwargs):
            output.write_bytes(b"rendered")
            return {"output_path": str(output), "output_kind": "video", "assets": {"edited_video": str(output)}}
        with (patch.object(app, "resolve_ffmpeg_executable", return_value="ffmpeg"),
              patch.object(app, "resolve_ffprobe_executable", return_value="ffprobe"),
              patch.object(renderer, "execute", side_effect=execute)):
            response = self.client.post('/api/editor/jobs', json={"job_id": "one", "action": "render", "options": {}})
            self.assertEqual(response.status_code, 202, response.get_json())
            task_id = response.get_json()["id"]
            self.jobs.work.join()
        result = self.client.get('/api/editor/jobs/'+task_id).get_json()
        self.assertEqual(result["status"], "success", result)
        self.assertNotIn("_protected_paths", result)
        self.assertEqual(manifest.snapshot(self.project)["assets"]["edited_video"], str(output))
        self.assertEqual(Path(self.project["video"]).read_bytes(), b"video")
        with self.client.get(result["output_url"]) as response:
            self.assertEqual(response.get_data(), b"rendered")
        self.assertEqual(self.client.get(f'/api/editor/jobs/{task_id}/output').status_code, 403)

    def test_editor_locks_cleanup_remove_retry_and_options_while_busy(self):
        entered, release = threading.Event(), threading.Event()
        def execute(*args, **kwargs):
            entered.set(); release.wait(3)
            process.check_cancel(kwargs["cancel"])
            return {}
        with (patch.object(app, "resolve_ffmpeg_executable", return_value="ffmpeg"),
              patch.object(app, "resolve_ffprobe_executable", return_value="ffprobe"),
              patch.object(renderer, "execute", side_effect=execute)):
            response = self.client.post('/api/editor/jobs', json={"job_id": "one", "action": "render"})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(entered.wait(2))
            try:
                deleted = self.client.post('/api/queue/bulk-delete', json={"ids": ["one"], "delete_files": True})
                self.assertEqual(deleted.get_json()["deleted_ids"], [])
                self.assertEqual(self.client.delete('/api/queue/one').status_code, 409)
                self.assertEqual(self.client.post('/api/editor/projects/one', json={"options": {}}).status_code, 409)
                self.assertEqual(self.client.post('/api/editor/jobs', json={"job_id": "one", "action": "tts"}).status_code, 400)
                self.client.post('/api/editor/jobs/'+response.get_json()["id"]+'/cancel', json={})
            finally:
                release.set(); self.jobs.work.join()
        self.assertTrue(Path(self.project["video"]).exists())

    def test_old_token_host_origin_and_multipart_validation_apply_to_new_routes(self):
        with patch.dict(app.app.config, TESTING=False):
            self.assertEqual(self.client.get('/api/editor/projects/one').status_code, 403)
            headers = {app.API_TOKEN_HEADER: app.API_TOKEN}
            self.assertEqual(self.client.get('/api/editor/projects/one', headers=headers).status_code, 200)
            response = self.client.post('/api/editor/projects/one/watermark',
                                        data={"image": (io.BytesIO(b"not PNG"), "bad.png")}, headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.client.post('/api/editor/projects/one', json={"options": {}},
                             headers={**headers, "Origin": "https://evil.test"}).status_code, 403)

    def test_stale_optional_runtime_keeps_ffmpeg_capabilities_available(self):
        with (patch.object(app.editor_api, 'config', return_value={'editor_runtime_python':'C:/missing/python.exe'}),
              patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(renderer, 'detect_encoders', return_value={'libx264':True,'h264_nvenc':False})):
            response = self.client.get('/api/editor/capabilities')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ffmpeg'])
        self.assertFalse(response.get_json()['optional']['edge_tts']['available'])

    def test_rejected_admission_cannot_overwrite_last_good_manifest(self):
        _, value = manifest.save_project(self.project)
        value['stages']['editor'] = {'status':'success','task_id':'a'*32,'action':'render'}
        target = manifest.path_for(self.project); manifest.write_atomic(target, value)
        before = Path(target).read_bytes()
        with (patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(self.jobs, 'submit', side_effect=RuntimeError('queue full'))):
            response = self.client.post('/api/editor/jobs', json={'job_id':'one','action':'render'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Path(target).read_bytes(), before)
        self.assertIsNone(self.jobs.for_project('one'))

    def test_last_good_video_remains_available_after_later_audio_or_failure(self):
        output = self.root / 'good.mp4'; output.write_bytes(b'last good')
        audio = self.root / 'new.wav'; audio.write_bytes(b'audio')
        _, value = manifest.save_project(self.project)
        value['assets']['edited_video'] = str(output)
        value['last_editor_render'] = {'status':'success','task_id':'a'*32,'action':'render',
                                        'output_path':str(output),'output_kind':'video','warnings':[]}
        value['stages']['editor'] = {'status':'error','task_id':'b'*32,'action':'render','error':'failed rerender'}
        manifest.write_atomic(manifest.path_for(self.project), value)
        response = self.client.get('/api/editor/projects/one').get_json()
        self.assertEqual(response['active_task']['status'], 'error')
        self.assertEqual(response['last_render']['status'], 'success')
        with self.client.get(response['last_render']['output_url']) as media:
            self.assertEqual(media.get_data(), b'last good')
        again = self.client.get('/api/editor/projects/one').get_json()
        self.assertEqual(again['active_task']['id'], 'b'*32)
        self.assertEqual(again['last_render']['id'], 'a'*32)

    def test_runtime_configuration_is_explicit_and_capcut_catalog_is_offline(self):
        import sys
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('no network')):
            response = self.client.get('/api/editor/voices?provider=capcut_internal&language=en')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(voice['language'].startswith('en') for voice in response.get_json()['voices']))
        response = self.client.post('/api/editor/runtime', json={'python_path': sys.executable,
                                    'model_dir': str(self.root), 'model_filename': 'sample.onnx', 'capcut_compat': False})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(app.load_app_config()['editor_capcut_compat'])
        self.assertEqual(app.load_app_config()['editor_separation_model_dir'], str(self.root))
        for data in ({'python_path': 'relative/python.exe'}, {'model_filename': '../escape.onnx'},
                     {'capcut_compat': 'true'}, {'unknown': 'pip install arbitrary'}):
            self.assertEqual(self.client.post('/api/editor/runtime', json=data).status_code, 400)

    def test_success_task_reloads_from_manifest_without_resubmitting(self):
        output = self.root / 'finished.mp4'; output.write_bytes(b'finished')
        _, value = manifest.save_project(self.project)
        value['assets']['edited_video'] = str(output)
        value['stages']['editor'] = {'status': 'success', 'task_id': 'f'*32, 'action': 'render',
                                     'output_path': str(output), 'output_kind': 'video', 'warnings': []}
        manifest.write_atomic(manifest.path_for(self.project), value)
        response = self.client.get('/api/editor/projects/one')
        task = response.get_json()['active_task']
        self.assertEqual(task['status'], 'success')
        self.assertTrue(task['restored'])
        self.assertEqual(task['percent'], 100)
        with self.client.get(task['output_url']) as media:
            self.assertEqual(media.get_data(), b'finished')
        self.assertIsNone(self.jobs.worker)

    def test_interrupted_reservation_becomes_error_and_preserves_last_good_asset(self):
        output = self.root / 'old.mp4'; output.write_bytes(b'old')
        _, value = manifest.save_project(self.project)
        value['assets']['edited_video'] = str(output)
        value['stages']['editor'] = {'status': 'queued', 'task_id': 'f'*32, 'action': 'render'}
        manifest.write_atomic(manifest.path_for(self.project), value)
        task = self.client.get('/api/editor/projects/one').get_json()['active_task']
        self.assertEqual(task['status'], 'error')
        self.assertIn('App đã đóng', task['error'])
        self.assertEqual(output.read_bytes(), b'old')
        self.assertIsNone(self.jobs.worker)

    def test_audio_preview_contract_signed_output_and_saved_options(self):
        old_video = self.root / 'previous.mp4'; old_video.write_bytes(b'previous')
        _, saved = manifest.save_project(self.project)
        saved['assets']['edited_video'] = str(old_video)
        saved['last_editor_render'] = {'task_id': 'e'*32, 'action': 'render', 'status': 'success'}
        manifest.write_atomic(manifest.path_for(self.project), saved)
        requested = {'speed': 1.5, 'change_audio_pitch': True, 'pitch_semitones': -4}
        def execute(identifier, project, options, action, **kwargs):
            self.assertEqual(action, 'audio_preview')
            self.assertEqual({key: options[key] for key in requested}, requested)
            output = self.root / (identifier + '.wav'); output.write_bytes(b'preview')
            return {'output_path': str(output), 'output_kind': 'audio', 'assets': {},
                    'duration': 3.0, 'preview_source': 'original', 'warnings': []}
        with (patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(renderer, 'execute', side_effect=execute)):
            response = self.client.post('/api/editor/projects/one/audio-preview', json={'options': requested})
            self.assertEqual(response.status_code, 202, response.get_json())
            self.assertEqual(response.get_json()['action'], 'audio_preview')
            identifier = response.get_json()['id']
            self.jobs.work.join()
        task = self.client.get('/api/editor/jobs/' + identifier).get_json()
        self.assertEqual((task['status'], task['output_kind'], task['duration'], task['preview_source']),
                         ('success', 'audio', 3, 'original'))
        with self.client.get(task['output_url']) as output:
            self.assertEqual(output.mimetype, 'audio/wav')
            self.assertEqual(output.get_data(), b'preview')
        self.assertEqual(self.client.get('/api/editor/jobs/' + identifier + '/output').status_code, 403)
        saved = manifest.snapshot(self.project)
        self.assertEqual(saved['assets']['edited_video'], str(old_video))
        self.assertEqual(saved['last_editor_render']['task_id'], 'e'*32)
        self.assertEqual({key: saved['editor_options'][key] for key in requested}, requested)
        self.assertEqual(saved['stages']['editor']['duration'], 3)
        self.jobs.tasks.clear(); self.jobs.events.clear()
        restored = self.client.get('/api/editor/projects/one').get_json()
        self.assertEqual(restored['active_task']['preview_source'], 'original')
        self.assertEqual(restored['active_task']['duration'], 3)
        self.assertEqual(restored['last_render']['id'], 'e'*32)

    def test_audio_preview_cache_retains_eight_and_never_removes_unrelated_files(self):
        directory = self.root / '.vietsub-editor-previews'; directory.mkdir()
        for index in range(10):
            path = directory / (f'{index:032x}' + '.wav'); path.write_bytes(b'old')
            os.utime(path, ns=(1_000_000_000 * (index + 1), 1_000_000_000 * (index + 1)))
        unrelated = directory / 'my-audio.wav'; unrelated.write_bytes(b'keep')
        def execute(identifier, *args, **kwargs):
            output = directory / (identifier + '.wav'); output.write_bytes(b'new')
            return {'output_path': str(output), 'output_kind': 'audio'}
        with (patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(renderer, 'execute', side_effect=execute)):
            response = self.client.post('/api/editor/projects/one/audio-preview', json={})
            self.assertEqual(response.status_code, 202)
            self.jobs.work.join()
        self.assertEqual(len(list(directory.iterdir())), 9)
        self.assertEqual(unrelated.read_bytes(), b'keep')
        self.assertFalse((directory / ('0'*32 + '.wav')).exists())
        self.assertTrue((directory / (response.get_json()['id'] + '.wav')).exists())

    def test_audio_preview_rejects_invalid_body_and_incomplete_project(self):
        for body in ({'speed': 2}, {'options': {'speed': 5}}, {'options': {'change_audio_pitch': 'true'}},
                     {'options': {'pitch_semitones': float('nan')}}, {'action': 'render'},
                     {'options': {'tts_enabled': True}}):
            response = self.client.post('/api/editor/projects/one/audio-preview', json=body)
            self.assertEqual(response.status_code, 400, response.get_json())
        self.job['status'] = 'running'
        response = self.client.post('/api/editor/projects/one/audio-preview', json={'options': {}})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.jobs.tasks)

    def test_audio_preview_uses_existing_admission_protection_and_cancel(self):
        entered, release = threading.Event(), threading.Event()
        def execute(*args, **kwargs):
            entered.set(); release.wait(3)
            process.check_cancel(kwargs['cancel'])
        with (patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(renderer, 'execute', side_effect=execute)):
            response = self.client.post('/api/editor/projects/one/audio-preview', json={'options': {}})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(entered.wait(2))
            identifier = response.get_json()['id']
            try:
                self.assertEqual(self.client.post('/api/editor/projects/one/audio-preview', json={}).status_code, 409)
                self.assertEqual(self.client.delete('/api/queue/one').status_code, 409)
                self.assertEqual(self.client.post('/api/editor/projects/one', json={'options': {}}).status_code, 409)
                response = self.client.post('/api/editor/jobs/' + identifier + '/cancel', json={})
                self.assertEqual(response.status_code, 200)
            finally:
                release.set(); self.jobs.work.join()
        self.assertEqual(self.jobs.snapshot(identifier)['status'], 'cancelled')
        self.assertTrue(Path(self.project['video']).exists())
        self.assertEqual(self.jobs.protected_paths(), set())

    def test_audio_preview_auth_and_origin_match_existing_endpoints(self):
        with patch.dict(app.app.config, TESTING=False):
            self.assertEqual(self.client.post('/api/editor/projects/one/audio-preview', json={}).status_code, 403)
            headers = {app.API_TOKEN_HEADER: app.API_TOKEN, 'Origin': 'https://evil.test'}
            self.assertEqual(self.client.post('/api/editor/projects/one/audio-preview', json={}, headers=headers).status_code, 403)

    def test_tts_submission_uses_current_options_and_streams_counts_and_logs(self):
        _, saved = manifest.save_project(self.project)
        saved['editor_options'] = normalize({'tts_voice': 'old-voice', 'tts_speed': .75, 'tts_fallback': False})
        manifest.write_atomic(manifest.path_for(self.project), saved)
        requested = {'tts_voice': 'BV421_vivn_streaming', 'tts_provider': 'capcut_internal',
                     'tts_speed': 1.25, 'tts_pitch': 2, 'tts_fallback': True}
        entered, release = threading.Event(), threading.Event()
        def execute(identifier, project, options, action, **kwargs):
            self.assertEqual(action, 'tts')
            self.assertEqual({key: options[key] for key in requested}, requested)
            emit = renderer.tts_progress(kwargs['update'])
            emit({'stage': 'tts_cue_done', 'current': 12, 'completed': 12, 'total': 85, 'percent': 14,
                  'message': 'Đang tạo giọng: câu 12/85 (14%)'})
            emit({'stage': 'tts_poll', 'message': 'CapCut HTTP 200; ret=0; status=running'})
            entered.set()
            release.wait(3)
            process.check_cancel(kwargs['cancel'])
            return {}
        with (patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(renderer, 'execute', side_effect=execute)):
            response = self.client.post('/api/editor/jobs', json={'job_id': 'one', 'action': 'tts', 'options': requested})
            self.assertEqual(response.status_code, 202, response.get_json())
            identifier = response.get_json()['id']
            try:
                self.assertTrue(entered.wait(2))
                live = self.client.get('/api/editor/jobs/' + identifier).get_json()
                self.assertEqual((live['phase'], live['completed'], live['total'], live['percent']), ('tts_poll', 12, 85, 14))
                self.assertIn('HTTP 200', live['message'])
                self.assertTrue(any('status=running' in call.args[0] for call in app.log_to_app.call_args_list))
                stored = manifest.snapshot(self.project)['editor_options']
                self.assertEqual({key: stored[key] for key in requested}, requested)
                self.client.post('/api/editor/jobs/' + identifier + '/cancel', json={})
            finally:
                release.set()
                self.jobs.work.join()
        self.assertEqual(self.jobs.snapshot(identifier)['status'], 'cancelled')

    def test_tts_fallback_false_overrides_saved_true(self):
        _, saved = manifest.save_project(self.project)
        saved['editor_options'] = normalize({'tts_voice': 'BV421_vivn_streaming', 'tts_fallback': True})
        manifest.write_atomic(manifest.path_for(self.project), saved)
        def execute(identifier, project, options, action, **kwargs):
            self.assertIs(options['tts_fallback'], False)
            return {}
        with (patch.object(app, 'resolve_ffmpeg_executable', return_value='ffmpeg'),
              patch.object(app, 'resolve_ffprobe_executable', return_value='ffprobe'),
              patch.object(renderer, 'execute', side_effect=execute)):
            response = self.client.post('/api/editor/jobs', json={'job_id': 'one', 'action': 'tts',
                                                                 'options': {'tts_fallback': False}})
            self.assertEqual(response.status_code, 202, response.get_json())
            self.jobs.work.join()
        self.assertEqual(self.jobs.snapshot(response.get_json()['id'])['status'], 'success')
        self.assertIs(manifest.snapshot(self.project)['editor_options']['tts_fallback'], False)

    def test_optional_runtime_absence_does_not_import_heavy_modules(self):
        import sys
        self.assertNotIn("audio_separator.separator", sys.modules)
        self.assertNotIn("demucs", sys.modules)
        with (patch.object(app, "resolve_ffmpeg_executable", return_value=None),
              patch.object(app, "resolve_ffprobe_executable", return_value=None)):
            response = self.client.get('/api/editor/capabilities')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ffmpeg"])
        self.assertIn("install_command", response.get_json()["optional"]["audio_separator"])


if __name__ == '__main__':
    unittest.main()
