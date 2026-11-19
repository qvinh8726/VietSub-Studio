"""Isolated graph/ASS tests plus synthetic FFmpeg smoke (temporary files only)."""
import copy
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from vietsub.editor.filters import build_video_graph, pixel_crop
from vietsub.editor.subtitles import write_ass

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "static" / "fonts"
SRT = ("\ufeff1\r\n00:00:00,001 --> 00:00:00,399\r\nXin chào 世界\r\n"
       "Dòng hai {\\pos(0,0)} C:\\new\\N\\h\r\n\r\n"
       "2\r\n00:00:00,400 --> 00:00:01,000\r\nTiếng Việt 中文 日本語\r\n")
CROP = {"x": 0.05, "y": 0.65, "width": 0.9, "height": 0.3}


def _opts(**changes):
    return {"subtitle_enabled": False, **changes}


def _png(path, width=24, height=12, rgba=(255, 0, 0, 255)):
    """Generate a real RGBA PNG using only the standard library."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\0" + bytes(rgba) * width for _ in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class PixelCropTests(unittest.TestCase):
    def test_padding_clamps_to_source_and_rounds_outwards(self):
        box = {"x": .95, "y": .9, "width": .2, "height": .2}
        self.assertEqual(pixel_crop(box, 1920, 1080, 8), {"x": 1816, "y": 964, "width": 104, "height": 116})
        self.assertEqual(pixel_crop({"x": 0, "y": 0, "width": 1, "height": 1}, 1920, 1080, 100),
                         {"x": 0, "y": 0, "width": 1920, "height": 1080})
        self.assertEqual(pixel_crop({"x": .101, "y": .201, "width": .011, "height": .011}, 100, 100, 0),
                         {"x": 10, "y": 20, "width": 2, "height": 2})

    def test_source_not_preview_coordinates_and_no_mutation(self):
        options = _opts(mask_mode="boxblur", crop_box=CROP)
        before = copy.deepcopy(options)
        box = pixel_crop(CROP, 1920, 1080, 8)
        result = build_video_graph(1920, 1080, options)
        self.assertIn("crop={width}:{height}:{x}:{y}".format(**box), result["filter_complex"])
        self.assertEqual(options, before)

    def test_tiny_positive_crop_is_at_least_one_pixel(self):
        self.assertEqual(pixel_crop({"x": .999, "y": .999, "width": 1e-12, "height": 1e-12}, 100, 100, 0),
                         {"x": 99, "y": 99, "width": 1, "height": 1})

    def test_invalid_geometry_is_rejected(self):
        bad = [None, {}, {**CROP, "x": -1}, {**CROP, "x": 1}, {**CROP, "height": 0},
               {**CROP, "width": float("nan")}, {**CROP, "y": float("inf")},
               {**CROP, "x": True}, {**CROP, "width": "0.9"}]
        for box in bad:
            with self.subTest(box=box), self.assertRaises(ValueError):
                pixel_crop(box, 1920, 1080, 8)
        for dims in [(0, 100), (100, True), (2.5, 100), (32769, 100)]:
            with self.subTest(dims=dims), self.assertRaises(ValueError):
                pixel_crop(CROP, *dims, 8)
        for padding in [-1, True, float("inf"), 10 ** 1000]:
            with self.subTest(padding=padding), self.assertRaises(ValueError):
                pixel_crop(CROP, 1920, 1080, padding)


class GraphTests(unittest.TestCase):
    def test_exact_interface_original_and_vertical_safe_dimensions(self):
        result = build_video_graph(1920, 1080, _opts())
        self.assertEqual(set(result), {"filter_complex", "output_label", "width", "height"})
        self.assertEqual((result["width"], result["height"], result["output_label"]), (1920, 1080, "[vout]"))
        self.assertEqual(build_video_graph(321, 181, _opts())["width"], 322)
        self.assertEqual(build_video_graph(321, 181, _opts())["height"], 182)
        for dims in [(1920, 1080), (720, 1280), (1080, 1920), (4096, 2160), (99, 333)]:
            for requested in [32, 319, 720, 1080, 1920, 3840]:
                with self.subTest(dims=dims, requested=requested):
                    result = build_video_graph(*dims, _opts(reframe="vertical", output_height=requested))
                    self.assertLessEqual(result["width"], 1080)
                    self.assertLessEqual(result["height"], 1920)
                    self.assertEqual(result["width"] * 16, result["height"] * 9)
                    self.assertEqual(result["width"] % 2, 0)
                    self.assertEqual(result["height"] % 2, 0)
                    self.assertIn("[bg][fg]overlay", result["filter_complex"])

    def test_each_mask_has_real_filters_and_strength(self):
        blur = build_video_graph(320, 180, _opts(mask_mode="boxblur", crop_box=CROP, blur_strength=13))["filter_complex"]
        self.assertIn("boxblur=luma_radius=13", blur)
        zero = build_video_graph(320, 180, _opts(mask_mode="boxblur", crop_box=CROP, blur_strength=0))["filter_complex"]
        self.assertIn("boxblur=luma_radius=0", zero)
        small = build_video_graph(320, 180, _opts(mask_mode="boxblur", padding_px=0,
                                               crop_box={"x": .5, "y": .5, "width": .001, "height": .001}))["filter_complex"]
        self.assertIn("boxblur=luma_radius=0", small)
        solid = build_video_graph(320, 180, _opts(mask_mode="solid_bar", crop_box=CROP))["filter_complex"]
        self.assertIn("geq=r=17:g=19:b=24", solid)
        self.assertIn("191.25*clip", solid)
        self.assertIn("sqrt(pow(max(abs(X", solid)
        for box in [CROP, {"x": 0, "y": 0, "width": 1, "height": 1}]:
            logo = build_video_graph(320, 180, _opts(mask_mode="delogo", crop_box=box))["filter_complex"]
            self.assertIn("fillborders=left=2:right=2:top=2:bottom=2:mode=smear", logo)
            self.assertIn("pad=324:184:2:2", logo)
            self.assertIn("crop=320:180:2:2", logo)
        for mode in ("boxblur", "delogo", "solid_bar"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                build_video_graph(320, 180, _opts(mask_mode=mode))

    def test_fractional_strength_and_padding_accepted_from_parent_normalizer(self):
        from vietsub.editor.options import normalize
        options = normalize({"mask_mode": "boxblur", "blur_strength": 8.5, "padding_px": 1.5}, inherited_crop=CROP)
        graph = build_video_graph(320, 180, options, ass_path="input.ass")["filter_complex"]
        self.assertIn("boxblur=luma_radius=9", graph)
        self.assertIn("crop={width}:{height}:{x}:{y}".format(**pixel_crop(CROP, 320, 180, 2)), graph)

    def test_validation_rejects_expression_and_label_injection(self):
        bad = [("mask_mode", "none;movie=evil"), ("solid_color", "red:enable=1"),
               ("text_color", "#fff"), ("back_color", "#000000\n"), ("blur_strength", "8"),
               ("blur_strength", 101), ("solid_opacity", float("nan")), ("font", "Arial,999"),
               ("font_size", float("inf")), ("outline", -1), ("shadow", True),
               ("subtitle_enabled", "false"), ("output_height", 31), ("reframe", "square"),
               ("watermark_corner", "[x];movie=evil"), ("banner_text", "evil\x00")]
        for key, value in bad:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                build_video_graph(320, 180, _opts(**{key: value}))
        for label in (0, -1, True, "[0:v]", "[1:v];movie=bad", "1:a", "watermark", 1025):
            with self.subTest(label=label), self.assertRaises(ValueError):
                build_video_graph(320, 180, _opts(), watermark_input=label)
        for path in ("", "input.ass\nmovie=evil", "evil\x00.ass"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_video_graph(320, 180, {"subtitle_enabled": True}, ass_path=path)

    def test_banner_and_subtitle_never_embedded_as_filter_expressions(self):
        banner = "字: O'Brien; [out] \\{\\pos(0,0)} %{eif:1}"
        graph = build_video_graph(320, 180, _opts(banner_text=banner), ass_path="input.ass")["filter_complex"]
        self.assertIn("ass=filename=input.ass", graph)
        self.assertNotIn(banner, graph)
        self.assertNotIn("drawtext", graph)
        with self.assertRaises(ValueError):
            build_video_graph(320, 180, {})
        with self.assertRaises(ValueError):
            build_video_graph(320, 180, _opts(banner_text="Banner"))

    def test_windows_unicode_path_escaping_and_png_input_forms(self):
        path = "C:\\Vidéos\\O'Brien,[素材]; x.ass"
        graph = build_video_graph(320, 180, {}, ass_path=path, fonts_dir="C:\\Local Fonts\\字")["filter_complex"]
        self.assertIn("C" + "\\" * 2 + ":/Vidéos/O" + "\\" * 3 + "'Brien", graph)
        self.assertIn("\\,\\[素材\\]\\;", graph)
        for label in (1, "1:v", "[1:v:0]"):
            result = build_video_graph(320, 180, _opts(), watermark_input=label)
            self.assertIn("[1:v:0]format=rgba", result["filter_complex"])
        for corner, coordinates in [("top_left", "x=4:y=4"), ("top_right", "x=W-w-4:y=4"),
                                    ("bottom_left", "x=4:y=H-h-4"), ("bottom_right", "x=W-w-4:y=H-h-4")]:
            result = build_video_graph(320, 180, _opts(watermark_corner=corner, watermark_opacity=.25), watermark_input=1)
            self.assertIn(coordinates, result["filter_complex"])
            self.assertIn("colorchannelmixer=aa=0.25", result["filter_complex"])

    def test_modules_do_not_import_app_or_optional_ai(self):
        code = """
import importlib.abc, sys
class BlockHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'app', 'torch', 'numpy', 'cv2', 'onnxruntime', 'demucs', 'audio_separator'}:
            raise RuntimeError('Unexpected heavy import: ' + fullname)
sys.meta_path.insert(0, BlockHeavy())
from vietsub.editor.filters import build_video_graph
from vietsub.editor.subtitles import write_ass
assert build_video_graph(100, 100, {'subtitle_enabled':False})['width'] == 100
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


class AssTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vietsub-ass-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.srt, self.ass = self.root / "input.srt", self.root / "output.ass"
        self.srt.write_text(SRT, encoding="utf-8", newline="")

    def read_ass(self, options=None, width=1920, height=1080):
        result = write_ass(self.srt, self.ass, width, height, options or {})
        self.assertEqual(result, str(self.ass))
        return self.ass.read_text(encoding="utf-8")

    def test_unicode_multiline_braces_and_backslashes_safe(self):
        text = self.read_ass()
        self.assertIn("Xin chào 世界\\NDòng hai", text)
        self.assertIn("Tiếng Việt 中文 日本語", text)
        self.assertIn("\\{\\\u2060pos(0,0)\\}", text)
        self.assertNotIn("{\\pos(0,0)}", text)
        self.assertIn("C:\\\u2060new\\\u2060N\\\u2060h", text)
        self.assertEqual(len([row for row in text.splitlines() if row.startswith("Dialogue:")]), 2)
        self.assertIn("0:00:00.00,0:00:00.40", text)

    def test_each_local_font_and_preset_style_parameters(self):
        for preset in ("standard", "anime", "social"):
            for font in ("Be Vietnam Pro", "Noto Sans SC", "JetBrains Mono"):
                with self.subTest(preset=preset, font=font):
                    text = self.read_ass({"subtitle_preset": preset, "font": font, "font_size": 55,
                                          "text_color": "#12abef", "back_color": "#123456", "outline": 3, "shadow": 2})
                    self.assertIn(f"Style: Subtitle,{font},55,&H00EFAB12", text)
                    self.assertIn("&H40563412" if preset == "social" else "&H00563412", text)
                    self.assertIn(",3,3,2,5," if preset == "social" else ",1,3,2,5,", text)

    def test_crop_position_tracks_vertical_foreground_not_css_or_full_frame(self):
        crop = {"x": .2, "y": .7, "width": .6, "height": .2}
        opts = {"crop_box": crop, "padding_px": 0, "subtitle_position": "crop", "reframe": "vertical"}
        text = self.read_ass(opts)
        self.assertIn("PlayResX: 1080", text)
        self.assertIn("PlayResY: 1920", text)
        # Foreground 1080x606, offset y657. ROI center is at source y864.
        self.assertIn(r"\an5\pos(540,1141.8)", text)
        self.assertIn("Style: Subtitle,Be Vietnam Pro,26.933333", text)
        graph = build_video_graph(1920, 1080, opts, ass_path="input.ass")
        self.assertIn(f"PlayResY: {graph['height']}", text)
        fallback = self.read_ass({"subtitle_position": "crop"})
        self.assertIn(r"\an2\pos(960,1042.2)", fallback)

    def test_top_bottom_banner_is_fixed_for_entire_video_and_literal(self):
        for position in ("top", "bottom"):
            text = self.read_ass({"subtitle_enabled": False, "banner_text": "字: {\\p1}\nA,B [Events]",
                                  "banner_position": position})
            events = [row for row in text.splitlines() if row.startswith("Dialogue:")]
            self.assertEqual(len(events), 1)
            self.assertIn("0:00:00.00,999:59:59.99,Banner", events[0])
            self.assertIn(r"\an8" if position == "top" else r"\an2", events[0])
            self.assertIn("\\{\\\u2060p1\\}\\NA,B [Events]", events[0])
        write_ass(None, self.ass, 320, 180, _opts(banner_text="Banner only"))
        self.assertIn("Banner only", self.ass.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            write_ass(None, self.ass, 320, 180, {})

    def test_invalid_srt_never_overwrites_existing_output(self):
        invalid = ["", "hello", "1\n00:00:01,000 --> 00:00:00,000\nx\n", "1\n00:00:00,000 --> 00:00:00,000\nx\n",
                   "1\n00:60:00,000 --> 00:00:01,000\nx\n", "1\n00:00:00,000 --> 00:00:01,000\n",
                   "1\n00:00:00,000 --> 00:00:01,000\nx\x00\n",
                   "1\n00:00:00,000 --> 00:00:01,000\nx\n2\n00:00:01,000 --> 00:00:02,000\ny\n",
                   "1\n00:00:02,000 --> 00:00:03,000\nx\n\n2\n00:00:01,000 --> 00:00:02,000\ny\n",
                   "1\n00:00:00,000 --> 00:00:01,000\nx\n\n1\n00:00:01,000 --> 00:00:02,000\ny\n"]
        self.ass.write_text("do not replace", encoding="utf-8")
        for raw in invalid:
            with self.subTest(raw=raw):
                self.srt.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    write_ass(self.srt, self.ass, 320, 180, {})
                self.assertEqual(self.ass.read_text(encoding="utf-8"), "do not replace")
                self.assertEqual(set(path.name for path in self.root.iterdir()), {"input.srt", "output.ass"})
        self.srt.write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            write_ass(self.srt, self.ass, 320, 180, {})

    def test_output_atomic_failure_preserves_old_and_input_not_replaced(self):
        self.ass.write_text("previous valid output", encoding="utf-8")
        with patch("vietsub.editor.subtitles.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                write_ass(self.srt, self.ass, 320, 180, {})
        self.assertEqual(self.ass.read_text(encoding="utf-8"), "previous valid output")
        self.assertEqual(set(path.name for path in self.root.iterdir()), {"input.srt", "output.ass"})
        before = self.srt.read_bytes()
        with self.assertRaises(ValueError):
            write_ass(self.srt, self.srt, 320, 180, {})
        self.assertEqual(self.srt.read_bytes(), before)

    def test_multiple_blank_separators_and_text_that_looks_like_ass_are_literal(self):
        self.srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n[Events]\nDialogue: 0,0:00:00.00\n\n\n  \n"
                            "2\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")
        text = self.read_ass()
        events = [row for row in text.splitlines() if row.startswith("Dialogue:")]
        self.assertEqual(len(events), 2)
        self.assertIn(r"[Events]\NDialogue: 0,0:00:00.00", events[0])

    def test_short_and_overlapping_cues_are_preserved(self):
        self.srt.write_text("1\n00:00:00.005 --> 00:00:00.006\na\n\n2\n00:00:00,005 --> 00:00:00,011\nb\n", encoding="utf-8")
        text = self.read_ass()
        self.assertIn("0:00:00.00,0:00:00.01", text)
        self.assertIn("0:00:00.00,0:00:00.02", text)


class FfmpegIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg:
            raise unittest.SkipTest("FFmpeg executable unavailable; real graph integration not run")
        result = subprocess.run([cls.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(result.stderr)
        required = {"ass", "boxblur", "delogo", "fillborders", "geq", "overlay", "scale", "colorchannelmixer"}
        present = set(re.findall(r"^\s*[.A-Z|]{2,3}\s+(\w+)\s", result.stdout + result.stderr, re.MULTILINE))
        missing = sorted(required - present)
        if missing:
            raise unittest.SkipTest("FFmpeg missing required video filters: " + ", ".join(missing))
        if not all((FONTS / file).is_file() for file in ("be-vietnam-pro-regular.ttf", "noto-sans-sc.ttf", "jetbrains-mono.ttf")):
            raise unittest.SkipTest("Vendored local fonts unavailable; burn-in integration not run")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vietsub-ffmpeg-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.srt = self.root / "input.srt"
        self.srt.write_text(SRT, encoding="utf-8", newline="")
        _png(self.root / "watermark.png")

    def render(self, options, *, width=320, height=180, ass_name="input.ass", watermark=False,
               source="testsrc2", frames=2, encode=False, font_dir=None):
        opts = _opts(**options)
        if opts.get("subtitle_enabled") or opts.get("banner_text", "").strip():
            write_ass(self.srt, self.root / ass_name, width, height, opts)
        graph = build_video_graph(width, height, opts, ass_path=ass_name,
                                  watermark_input=1 if watermark else None, fonts_dir=font_dir or FONTS)
        source_filter = f"{source}{':' if '=' in source else '='}size={width}x{height}:rate=5:duration=0.4"
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-v", "info", "-y", "-filter_complex_threads", "1",
                   "-f", "lavfi", "-i", source_filter]
        if watermark:
            command += ["-i", "watermark.png"]
        command += ["-filter_complex", graph["filter_complex"], "-map", graph["output_label"], "-an", "-frames:v", str(frames)]
        if encode:
            target = self.root / "output.mp4"
            command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(target)]
        else:
            target = self.root / "output.rgb"
            command += ["-c:v", "rawvideo", "-pix_fmt", "rgb24", "-f", "rawvideo", str(target)]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
        self.assertEqual(result.returncode, 0, "\n" + result.stderr + "\n" + graph["filter_complex"])
        data = target.read_bytes()
        if not encode:
            self.assertEqual(len(data), graph["width"] * graph["height"] * 3 * frames)
        return graph, data, result.stderr, target

    def test_all_mask_modes_original_and_vertical_with_ass_png(self):
        for mode in ("none", "boxblur", "delogo", "solid_bar"):
            for reframe in ("original", "vertical"):
                with self.subTest(mode=mode, reframe=reframe):
                    graph, data, stderr, _ = self.render({"mask_mode": mode, "crop_box": CROP,
                        "subtitle_enabled": True, "subtitle_preset": "social", "font_size": 24,
                        "subtitle_position": "crop", "banner_text": "Bản dịch 中文 {\\p1}",
                        "reframe": reframe, "output_height": 320}, watermark=True)
                    self.assertTrue(data)
                    self.assertIn("Be Vietnam Pro", stderr)
                    self.assertIn("fontselect", stderr)
                    self.assertEqual((graph["width"], graph["height"]), (320, 180) if reframe == "original" else (180, 320))

    def test_every_crop_edge_and_tiny_box_delogo_boxblur_render(self):
        boxes = [{"x": 0, "y": 0, "width": 1, "height": 1},
                 {"x": 0, "y": .5, "width": .5, "height": .5},
                 {"x": .5, "y": 0, "width": .5, "height": .5},
                 {"x": .999, "y": .999, "width": .001, "height": .001}]
        for mode in ("delogo", "boxblur", "solid_bar"):
            for box in boxes:
                with self.subTest(mode=mode, box=box):
                    self.render({"mask_mode": mode, "crop_box": box, "padding_px": 0}, frames=1)

    def test_real_path_escaping_apostrophe_colon_unicode_metacharacters(self):
        name = "O'Brien, [字]; sample.ass"
        font_folder = self.root / "Fonts O'Brien, [字]; local"
        font_folder.mkdir()
        shutil.copyfile(FONTS / "be-vietnam-pro-regular.ttf", font_folder / "font.ttf")
        # Both relative filename and absolute Windows drive-colon paths execute.
        self.render({"subtitle_enabled": True}, ass_name=name, frames=1, font_dir=font_folder)
        self.render({"subtitle_enabled": True}, ass_name=str(self.root / name), frames=1, font_dir=font_folder)

    def test_local_presets_fonts_and_all_watermark_corners_execute(self):
        for preset, font in [("standard", "Be Vietnam Pro"), ("anime", "Noto Sans SC"), ("social", "JetBrains Mono")]:
            with self.subTest(preset=preset):
                self.render({"subtitle_enabled": True, "subtitle_preset": preset, "font": font}, frames=1)
        for corner in ("top_left", "top_right", "bottom_left", "bottom_right"):
            with self.subTest(corner=corner):
                self.render({"watermark_corner": corner, "watermark_opacity": .4}, watermark=True, frames=1)

    def test_png_alpha_and_opacity_are_multiplied_at_each_corner(self):
        _png(self.root / "watermark.png", rgba=(255, 0, 0, 128))
        for corner in ("top_left", "top_right", "bottom_left", "bottom_right"):
            with self.subTest(corner=corner):
                graph, data, _, _ = self.render({"watermark_corner": corner, "watermark_opacity": .5},
                                                 watermark=True, source="color=c=black", frames=1)
                x = 14 if corner.endswith("left") else 262
                y = 14 if corner.startswith("top") else 154
                offset = (y * graph["width"] + x) * 3
                self.assertTrue(48 < data[offset] < 80, data[offset:offset + 3])
                middle = (90 * graph["width"] + 160) * 3
                self.assertLess(max(data[middle:middle + 3]), 10)
        _, invisible, _, _ = self.render({"watermark_opacity": 0}, watermark=True, source="color=c=black", frames=1)
        self.assertLess(max(invisible), 10)

    def test_rounded_translucent_bar_and_zero_opacity_change_actual_pixels(self):
        # Uniform white video makes exact corner/center alpha behavior observable.
        options = {"mask_mode": "solid_bar", "padding_px": 0, "solid_color": "#000000", "solid_opacity": .5,
                   "crop_box": {"x": .25, "y": .25, "width": .5, "height": .5}}
        graph, data, _, _ = self.render(options, source="color=c=white", frames=1)
        def pixel(x, y):
            offset = (y * graph["width"] + x) * 3
            return sum(data[offset:offset + 3]) / 3
        self.assertGreater(pixel(80, 45), 230)  # rounded corner remains source
        self.assertTrue(110 < pixel(160, 90) < 145)  # center is half transparent
        self.assertGreater(pixel(10, 10), 245)  # outside mask unchanged
        options["solid_opacity"] = 0
        _, transparent, _, _ = self.render(options, source="color=c=white", frames=1)
        self.assertTrue(all(value >= 245 for value in transparent))

    def test_ass_burn_changes_pixels_and_escaped_override_stays_literal(self):
        self.srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n{\\\\alpha&HFF&}Visible\\\\N literal\n", encoding="utf-8")
        options = {"subtitle_enabled": True, "font_size": 28, "subtitle_preset": "standard",
                   "outline": 0, "shadow": 0, "subtitle_position": "top"}
        graph, burned, _, _ = self.render(options, source="color=c=black", frames=1)
        _, blank, _, _ = self.render({}, source="color=c=black", frames=1)
        self.assertNotEqual(burned, blank)
        bright = [(index // 3 % graph["width"], index // 3 // graph["width"])
                  for index in range(0, len(burned), 3) if max(burned[index:index + 3]) > 100]
        self.assertGreater(len(bright), 100)
        self.assertTrue(all(y < graph["height"] // 2 for _, y in bright))

    def test_odd_original_and_extreme_aspect_vertical_remain_bounded(self):
        for width, height, reframe in [(321, 181, "original"), (4000, 4, "vertical"), (4, 4000, "vertical")]:
            with self.subTest(width=width, height=height, reframe=reframe):
                self.render({"reframe": reframe, "output_height": 320}, width=width, height=height,
                            source="testsrc", frames=1)

    def test_vertical_maximum_canvas_real_h264_encode_and_probe(self):
        if not self.ffprobe:
            self.skipTest("ffprobe unavailable; actual encoded dimensions not inspected")
        encoders = subprocess.run([self.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20)
        self.assertEqual(encoders.returncode, 0, encoders.stderr)
        if "libx264" not in encoders.stdout:
            self.skipTest("libx264 unavailable; core filter integration covered separately")
        graph, _, _, target = self.render({"reframe": "vertical", "output_height": 9999}, frames=1, encode=True)
        probe = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height,codec_name,pix_fmt", "-of", "json", str(target)],
                               capture_output=True, text=True, timeout=20)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        stream = json.loads(probe.stdout)["streams"][0]
        self.assertEqual((stream["width"], stream["height"], stream["codec_name"]), (1080, 1920, "h264"))
        self.assertEqual(stream["pix_fmt"], "yuv420p")
        self.assertEqual((graph["width"], graph["height"]), (1080, 1920))


if __name__ == "__main__":
    unittest.main()
