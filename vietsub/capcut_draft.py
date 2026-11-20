"""CapCut desktop draft interchange, with speed/tone on a prepared media segment.

The desktop JSON fields follow pyCapCut's public model (MIT):
https://github.com/GuanYixuan/pyCapCut/blob/main/pycapcut/segment.py
TTS-only pitch and visual edits are baked once; native speed preserves BGM tone.
Legacy options keep their original global tone flag. No installed CapCut is required.
"""
import copy
import json
import os
from pathlib import Path
import time
import uuid
import zipfile

from vietsub import manifest
from vietsub.editor import process, renderer
from vietsub.editor.options import normalize


def document(name, media, metadata, options):
    """Build one self-contained timeline (microseconds, not milliseconds)."""
    options = normalize(options)
    material_id, segment_id, speed_id = (uuid.uuid4().hex for _ in range(3))
    source_duration = round(metadata['duration'] * 1_000_000)
    target_duration = round(source_duration / options['speed'])
    clip = {'alpha': 1.0, 'flip': {'horizontal': False, 'vertical': False}, 'rotation': 0.0,
            'scale': {'x': 1.0, 'y': 1.0}, 'transform': {'x': 0.0, 'y': 0.0}}
    segment = {'id': segment_id, 'material_id': material_id,
               'source_timerange': {'start': 0, 'duration': source_duration},
               'target_timerange': {'start': 0, 'duration': target_duration},
               'speed': options['speed'],
               'is_tone_modify': False if 'tts_change_pitch' in options else options['change_audio_pitch'],
               'volume': 1.0, 'last_nonzero_volume': 1.0, 'visible': True, 'reverse': False,
               'clip': clip, 'uniform_scale': {'on': True, 'value': 1.0},
               'extra_material_refs': [speed_id], 'common_keyframes': [], 'keyframe_refs': [],
               'track_attribute': 0, 'track_render_index': 0, 'enable_adjust': True,
               'enable_color_curves': True, 'enable_color_wheels': True, 'enable_lut': True}
    material = {'id': material_id, 'type': 'video', 'path': str(Path(media).resolve()),
                'material_name': Path(media).name, 'duration': source_duration,
                'width': metadata['width'], 'height': metadata['height'], 'has_audio': metadata['has_audio'],
                'category_id': '', 'category_name': 'local', 'check_flag': 63487,
                'crop': {'upper_left_x': 0.0, 'upper_left_y': 0.0, 'upper_right_x': 1.0,
                         'upper_right_y': 0.0, 'lower_left_x': 0.0, 'lower_left_y': 1.0,
                         'lower_right_x': 1.0, 'lower_right_y': 1.0},
                'crop_ratio': 'free', 'crop_scale': 1.0, 'audio_fade': None}
    materials = {key: [] for key in ('audios', 'texts', 'effects', 'video_effects', 'audio_effects',
        'audio_fades', 'canvases', 'transitions', 'material_animations', 'stickers', 'masks', 'common_mask',
        'sound_channel_mappings', 'vocal_separations', 'beats', 'loudnesses', 'speeds', 'videos')}
    materials['videos'] = [material]
    materials['speeds'] = [{'id': speed_id, 'type': 'speed', 'mode': 0, 'speed': options['speed'], 'curve_speed': None}]
    platform = {'app_id': 359289, 'app_source': 'cc', 'app_version': '6.7.0', 'os': 'windows'}
    return {'id': uuid.uuid4().hex, 'name': name, 'version': 360000, 'new_version': '140.0.0',
            'fps': 30.0, 'duration': target_duration, 'source': 'default', 'color_space': 0,
            'canvas_config': {'width': metadata['width'], 'height': metadata['height'], 'ratio': 'original', 'background': None},
            'platform': platform, 'last_modified_platform': platform, 'materials': materials,
            'tracks': [{'id': uuid.uuid4().hex, 'type': 'video', 'attribute': 0, 'flag': 0, 'segments': [segment]}],
            'config': {'maintrack_adsorb': True, 'material_save_mode': 0, 'video_mute': False},
            'keyframes': {key: [] for key in ('videos', 'audios', 'texts', 'effects', 'filters', 'stickers', 'adjusts', 'handwrites')},
            'relationships': [], 'keyframe_graph_list': [], 'update_time': int(time.time()),
            'vietsub': {'editor_options': options, 'pitch_semitones_baked': options['pitch_semitones'],
                        'pitch_scope': 'tts' if 'tts_change_pitch' in options else 'legacy_global',
                         'tts_speed_pitch_baked': options['speed'] if options.get('tts_change_pitch') else 1.0,
                         'visual_edits_baked': True, 'audio_mix_baked': True}}


def execute(task_id, project, options, *, ffmpeg, ffprobe, fonts_dir, cancel, update,
            runtime_python=None, capcut_config=None):
    """Prepare one media file, then archive a local draft without altering sources."""
    options = normalize(options, inherited_crop=project.get('crop_box'))
    root = Path(project['project_dir']).resolve()
    directory = root / ('CapCut-' + task_id)
    manifest._safe_path(str(directory))
    directory.mkdir(exist_ok=False)
    archive = root / ('CapCut-' + task_id + '.zip')
    temporary = directory / 'export.zip.part'
    try:
        process.check_cancel(cancel)
        prepared = copy.deepcopy(project)
        prepared['project_dir'] = str(directory)
        # Bake the voice pitch at source duration; native timeline speed keeps
        # original/BGM pitch intact. Preserve the pre-v2.0.2 path for old payloads.
        scoped = 'tts_change_pitch' in options
        bake_options = {**options, 'speed': 1.0}
        # The exported media has its own directory, but speech stays cached at
        # project level so a new draft/pitch choice does not resubmit every cue.
        render_args = {'_tts_cache_dir': str(root / '.vietsub-tts-cache')}
        if scoped:
            render_args['_tts_pitch_speed'] = options['speed']
        else:
            bake_options['change_audio_pitch'] = False
        result = renderer.execute(task_id, prepared, bake_options, 'render', ffmpeg=ffmpeg, ffprobe=ffprobe,
            fonts_dir=fonts_dir, cancel=cancel, update=update, runtime_python=runtime_python, capcut_config=capcut_config,
            **render_args)
        media = Path(result['output_path'])
        metadata = renderer.probe_media(media, ffprobe)
        draft = document(project.get('project_name') or 'VietSub Studio', media, metadata, options)
        (directory / 'draft_content.json').write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'draft_meta_info.json').write_text(json.dumps({
            'draft_id': draft['id'], 'draft_name': draft['name'], 'draft_fold_path': str(directory),
            'draft_root_path': str(root), 'draft_timeline_materials_size': media.stat().st_size,
            'tm_duration': draft['duration'], 'tm_draft_create': int(time.time()*1_000_000),
            'tm_draft_modified': int(time.time()*1_000_000), 'draft_materials': []}, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'project.json').write_text(json.dumps({
            'schema_version': 1, 'project_id': project.get('project_id'), 'editor_options': options,
            'source_duration': metadata['duration'], 'duration': draft['duration']/1_000_000,
            'media': media.name, 'pitch_semitones_baked': options['pitch_semitones'],
            'pitch_scope': draft['vietsub']['pitch_scope']}, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'HUONG-DAN.txt').write_text(
            'CapCut Draft — VietSub Studio\n'
            + ('Tốc độ video là thuộc tính timeline có thể chỉnh tiếp, giữ tông âm thanh nền.\n'
               'Cao độ chỉ áp dụng giọng đọc TTS, đã bake cùng phụ đề, che chữ, logo và bản trộn.\n'
               'Đổi tốc độ trong CapCut sau khi xuất không tính lại cao độ TTS đã bake.\n' if scoped else
               'Tốc độ và công tắc đổi cao độ theo tốc độ là thuộc tính timeline có thể chỉnh tiếp.\n'
               'Cao độ semitone, phụ đề, che chữ, logo và bản trộn TTS đã được kết xuất vào video.\n')
            +
            'Thư mục này chứa draft_content.json, draft_meta_info.json và media. Sao chép cả thư mục vào thư mục drafts của CapCut desktop khi CapCut đã đóng.\n'
            'Nếu chuyển máy hoặc đổi đường dẫn, dùng Relink để trỏ lại video đi kèm.\n'
            'Tương thích định dạng JSON desktop; cần kiểm tra khi mở trên phiên bản CapCut đang dùng.\n', encoding='utf-8')
        files = [media, *(directory / name for name in ('draft_content.json', 'draft_meta_info.json', 'project.json', 'HUONG-DAN.txt'))]
        update(phase='capcut_draft', percent=99, fps=0)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            for path in files:
                with path.open('rb') as source, bundle.open(directory.name + '/' + path.name, 'w', force_zip64=True) as target:
                    while chunk := source.read(1024 * 1024):
                        process.check_cancel(cancel)
                        target.write(chunk)
        process.check_cancel(cancel)
        os.replace(temporary, archive)
        return {'output_path': str(archive), 'output_kind': 'archive', 'warnings': result.get('warnings', [])}
    except BaseException:
        # Only the just-created private directory/zip are eligible for cleanup.
        import shutil
        if directory.parent == root and directory.name == 'CapCut-' + task_id:
            shutil.rmtree(directory, ignore_errors=True)
        if archive.parent == root and archive.name == 'CapCut-' + task_id + '.zip':
            archive.unlink(missing_ok=True)
        raise
