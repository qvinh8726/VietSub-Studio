"""Validated editor preferences; no arbitrary FFmpeg command/filter input."""
import copy
import math
import re

from vietsub.manifest import crop_box

FONTS = ("Be Vietnam Pro", "Noto Sans SC", "JetBrains Mono")
PRESETS = ("standard", "anime", "social")
DEFAULTS = {
    "mask_mode": "boxblur", "crop_box": None, "blur_strength": 8, "padding_px": 8,
    "solid_color": "#111318", "solid_opacity": .75,
    "subtitle_enabled": True, "subtitle_preset": "social", "font": "Be Vietnam Pro",
    "font_size": 48, "text_color": "#ffffff", "outline": 2, "shadow": 1,
    "back_color": "#000000", "subtitle_position": "crop",
    "banner_text": "", "banner_position": "top", "watermark_enabled": False,
    "watermark_path": "", "watermark_opacity": .7, "watermark_corner": "top_right",
    "reframe": "original", "output_height": 1280, "encoder": "auto",
    "audio_mode": "original", "audio_format": "wav", "bgm_volume": .35,
    "tts_enabled": False, "tts_provider": "capcut_internal", "tts_voice": "",
    "tts_speed": 1.0, "tts_pitch": 0.0, "tts_fallback": True,
}
CHOICES = {
    "mask_mode": ("none", "boxblur", "delogo", "solid_bar"),
    "subtitle_preset": PRESETS, "font": FONTS, "subtitle_position": ("crop", "bottom", "top"),
    "banner_position": ("top", "bottom"),
    "watermark_corner": ("top_right", "top_left", "bottom_right", "bottom_left"),
    "reframe": ("original", "vertical"), "encoder": ("auto", "libx264", "h264_nvenc"),
    "audio_mode": ("original", "mute", "instrumental"), "audio_format": ("wav", "mp3"),
    "tts_provider": ("capcut_internal", "edge_tts"),
}
NUMBERS = {
    "blur_strength": (1, 40), "padding_px": (0, 100), "solid_opacity": (0, 1),
    "font_size": (12, 120), "outline": (0, 10), "shadow": (0, 10),
    "watermark_opacity": (0, 1), "bgm_volume": (0, 2),
    "tts_speed": (.5, 2), "tts_pitch": (-12, 12),
}
BOOLEANS = ("subtitle_enabled", "watermark_enabled", "tts_enabled", "tts_fallback")


def normalize(value=None, *, inherited_crop=None, base=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("Tuỳ chọn Auto Edit không hợp lệ hoặc chứa trường không hỗ trợ.")
    if base is not None and not isinstance(base, dict):
        raise ValueError("Cấu hình editor đã lưu không phải object; giữ nguyên manifest để khôi phục.")
    saved = {key: val for key, val in (base or {}).items() if key in DEFAULTS}
    result = {**copy.deepcopy(DEFAULTS), "crop_box": inherited_crop, **saved, **value}
    result["crop_box"] = crop_box(result["crop_box"])
    for key, choices in CHOICES.items():
        if not isinstance(result[key], str) or result[key] not in choices:
            raise ValueError(f"{key}: giá trị không được hỗ trợ.")
    for key, (minimum, maximum) in NUMBERS.items():
        number = result[key]
        try:
            valid = type(number) in (int, float) and math.isfinite(number) and minimum <= number <= maximum
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"{key} phải là số hữu hạn từ {minimum} đến {maximum}.")
    for key in BOOLEANS:
        if type(result[key]) is not bool:
            raise ValueError(f"{key} phải là boolean.")
    if type(result["output_height"]) is not int or result["output_height"] not in (720, 1280, 1920):
        raise ValueError("Chiều cao khung dọc phải là 720, 1280 hoặc 1920.")
    for key in ("solid_color", "text_color", "back_color"):
        if not isinstance(result[key], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", result[key]):
            raise ValueError(f"{key} phải là màu #RRGGBB.")
    for key, limit in (("banner_text", 200), ("tts_voice", 200), ("watermark_path", 2048)):
        text = result[key]
        if not isinstance(text, str) or len(text) > limit or "\x00" in text or any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise ValueError(f"{key}: chuỗi không hợp lệ hoặc quá dài.")
    if result["crop_box"] is None and result["mask_mode"] != "none":
        # An old full-frame OCR export may have no crop; do not blur the whole video.
        if "mask_mode" in value:
            raise ValueError("Hãy chọn vùng chữ nguồn trước khi bật che/làm mờ.")
        result["mask_mode"] = "none"
    if result["tts_enabled"] and not result["tts_voice"].strip():
        raise ValueError("Hãy chọn giọng đọc trước khi bật TTS.")
    if result["watermark_enabled"] and not result["watermark_path"]:
        raise ValueError("Hãy chọn ảnh PNG trước khi bật watermark.")
    return result
