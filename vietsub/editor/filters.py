"""Pure FFmpeg video graph construction; no processes, app or optional AI imports.

The caller supplies input 0's *display-oriented, square-pixel* source dimensions,
passes the graph as one argv item (never through a shell), and maps ``[vout]``.
Encoder discovery/probing and media/path authorization belong to the renderer.
ASS is applied after reframing; :func:`subtitles.write_ass` uses the same layout.

A delogo region touching an edge is surrounded by two temporary, smeared pixels
before filtering, then cropped back. This supplies the filter's mandatory sample
margin, not an AI reconstruction of missing pixels outside the source. Very tiny
boxblur regions have their radius reduced to the filter's legal maximum. Numeric
blur strengths are rounded up to whole pixels; fractional padding is rounded up.
"""
from collections.abc import Mapping
import math
import os
import re

__all__ = ["build_video_graph", "pixel_crop"]

_FONTS = ("Be Vietnam Pro", "Noto Sans SC", "JetBrains Mono")
_DEFAULTS = {
    "mask_mode": "none", "crop_box": None, "blur_strength": 8,
    "padding_px": 8, "solid_color": "#111318", "solid_opacity": 0.75,
    "subtitle_enabled": True, "subtitle_preset": "social", "font": "Be Vietnam Pro",
    "font_size": 48, "text_color": "#ffffff", "outline": 2, "shadow": 1,
    "back_color": "#000000", "subtitle_position": "bottom", "banner_text": "",
    "banner_position": "top", "watermark_opacity": 0.7,
    "watermark_corner": "top_right", "reframe": "original", "output_height": 1920,
    "speed": 1.0,
}
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def _number(value, name, minimum, maximum, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    if integer and int(value) != value:
        raise ValueError(f"{name} must be an integer")
    return int(value) if integer else value


def _dimensions(width, height):
    return (_number(width, "width", 2, 32768, integer=True),
            _number(height, "height", 2, 32768, integer=True))


def _validated_options(options):
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")
    opts = {key: options.get(key, default) for key, default in _DEFAULTS.items()}
    choices = {
        "mask_mode": ("none", "boxblur", "delogo", "solid_bar"),
        "subtitle_preset": ("standard", "anime", "social"), "font": _FONTS,
        "subtitle_position": ("crop", "bottom", "top"),
        "banner_position": ("top", "bottom"),
        "watermark_corner": ("top_right", "top_left", "bottom_right", "bottom_left"),
        "reframe": ("original", "vertical"),
    }
    for key, allowed in choices.items():
        if not isinstance(opts[key], str) or opts[key] not in allowed:
            raise ValueError(f"Unsupported {key}")
    if not isinstance(opts["subtitle_enabled"], bool):
        raise ValueError("subtitle_enabled must be a boolean")
    for key in ("solid_color", "text_color", "back_color"):
        if not isinstance(opts[key], str) or not _COLOR.fullmatch(opts[key]):
            raise ValueError(f"{key} must be a #RRGGBB color")
    for key, minimum, maximum, integer in (
        ("blur_strength", 0, 100, False), ("padding_px", 0, 32768, False),
        ("solid_opacity", 0, 1, False), ("watermark_opacity", 0, 1, False),
        ("font_size", 4, 512, False), ("outline", 0, 32, False),
        ("shadow", 0, 32, False), ("output_height", 32, 32768, True),
        ("speed", .5, 2, False),
    ):
        opts[key] = _number(opts[key], key, minimum, maximum, integer=integer)
    text = opts["banner_text"]
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError("banner_text must be text of at most 10000 characters")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text) or "\x7f" in text:
        raise ValueError("banner_text contains control characters")
    return opts


def pixel_crop(crop_box, width, height, padding_px):
    """Convert a normalized source ROI to an outward-rounded, padded pixel box.

    ``crop_box`` has finite numeric ``x/y`` in [0,1) and ``width/height`` in
    (0,1]. Overshooting right/bottom edges is clamped. Padding is in *source*
    pixels (a fractional padding is rounded up) and never moves the region
    outside the source. Missing/empty/invalid
    regions raise ValueError rather than unexpectedly masking the entire video.
    """
    width, height = _dimensions(width, height)
    padding = math.ceil(_number(padding_px, "padding_px", 0, 32768))
    if not isinstance(crop_box, Mapping):
        raise ValueError("crop_box is required and must be a mapping")
    values = {}
    for key in ("x", "y", "width", "height"):
        try:
            values[key] = _number(crop_box[key], f"crop_box.{key}", 0, 1)
        except KeyError as exc:
            raise ValueError(f"Missing crop_box.{key}") from exc
    if values["x"] >= 1 or values["y"] >= 1 or values["width"] <= 0 or values["height"] <= 0:
        raise ValueError("crop_box must cover a nonempty source region")
    # Suppress tiny floating-point overshoots at mathematically integral edges.
    left = max(0, min(width - 1, math.floor(values["x"] * width + 1e-9)) - padding)
    top = max(0, min(height - 1, math.floor(values["y"] * height + 1e-9)) - padding)
    right = min(width, math.ceil(min(1, values["x"] + values["width"]) * width - 1e-9) + padding)
    bottom = min(height, math.ceil(min(1, values["y"] + values["height"]) * height - 1e-9) + padding)
    # Subpixel nonempty boxes still cover at least one source pixel.
    right = min(width, max(left + 1, right))
    bottom = min(height, max(top + 1, bottom))
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _layout(width, height, opts):
    """Shared source-to-ASS-canvas transform. opts has already been validated."""
    width, height = _dimensions(width, height)
    if opts["reframe"] == "original":
        # Pad only the right/bottom odd pixel for safe yuv420 encoders. No crop.
        return {"width": width + width % 2, "height": height + height % 2,
                "foreground_width": width, "foreground_height": height, "x": 0, "y": 0}
    # Exact 9:16 AND even dimensions: H is a multiple of 32, W a multiple of 18.
    out_height = min(1920, opts["output_height"]) // 32 * 32
    out_width = out_height * 9 // 16
    factor = min(out_width / width, out_height / height)
    fit_width = max(2, min(out_width, int(width * factor) // 2 * 2))
    fit_height = max(2, min(out_height, int(height * factor) // 2 * 2))
    return {"width": out_width, "height": out_height, "foreground_width": fit_width,
            "foreground_height": fit_height, "x": (out_width - fit_width) // 2,
            "y": (out_height - fit_height) // 2}


def _canvas_crop(crop_box, width, height, opts, layout):
    box = pixel_crop(crop_box, width, height, opts["padding_px"])
    sx, sy = layout["foreground_width"] / width, layout["foreground_height"] / height
    return {"x": layout["x"] + box["x"] * sx, "y": layout["y"] + box["y"] * sy,
            "width": box["width"] * sx, "height": box["height"] * sy}


def _decimal(value):
    return format(value, ".6f").rstrip("0").rstrip(".") or "0"


def _filter_path(path):
    """Escape both AVOption and filtergraph levels, with no shell quoting.

    In particular, a drive colon needs two backslashes and an apostrophe three
    at the graph level. Relative filenames in a renderer-owned temp cwd remain
    simplest. This accepts PathLike and preserves Unicode; it does no I/O.
    """
    try:
        value = os.fspath(path)
    except TypeError as exc:
        raise ValueError("Filter paths must be text paths") from exc
    if not isinstance(value, str) or not value or len(value) > 32767:
        raise ValueError("Filter paths must be nonempty text paths")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Filter paths cannot contain control characters")
    if os.name == "nt" or re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", value):
        value = value.replace("\\", "/")
    # Escaping is deliberate at both parser levels, not shell interpolation.
    value = "".join("\\" + char if char in "\\': " else char for char in value)
    return "".join("\\" + char if char in "\\'[],; " else char for char in value)


def _watermark_label(value):
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 1024:
        return f"[{value}:v:0]"
    if isinstance(value, str):
        match = re.fullmatch(r"(?:\[([1-9]\d*):v(?::0)?\]|([1-9]\d*):v(?::0)?)", value)
        if match:
            index = int(match.group(1) or match.group(2))
            if index <= 1024:
                return f"[{index}:v:0]"
    raise ValueError("watermark_input must be a nonzero video input index, e.g. 1 or '1:v'")


def build_video_graph(width, height, options, *, ass_path=None, watermark_input=None, fonts_dir=None):
    """Return ``{filter_complex, output_label, width, height}`` without I/O.

    ``output_label`` is ``'[vout]'``, ready for FFmpeg ``-map``. width/height
    describe the actual even output canvas. Original mode retains all source
    pixels (an odd right/bottom edge is padded); vertical mode fits the entire
    source above a blurred cover background, max 1080x1920. output_height is
    rounded down to a multiple of 32 for an exact, encoder-safe 9:16 frame.

    Masking requires crop_box. Selected subtitles or a nonblank banner require
    an ASS path produced by write_ass; user banner/text is NEVER put in a graph.
    A PNG is supplied separately as input 1+; it is aspect-fitted within 20% of
    the output canvas, multiplied by watermark_opacity, and repeated to video
    EOF. The renderer owns PNG validation, argv inputs and capability probing.
    """
    width, height = _dimensions(width, height)
    opts = _validated_options(options)
    layout = _layout(width, height, opts)
    out_width, out_height = layout["width"], layout["height"]
    burn_ass = opts["subtitle_enabled"] or bool(opts["banner_text"].strip())
    if burn_ass and ass_path is None:
        raise ValueError("Enabled subtitles/banner require ass_path from write_ass")
    if fonts_dir is not None:
        escaped_fonts = _filter_path(fonts_dir)
    else:
        escaped_fonts = None
    graph = [f"[0:v:0]setpts=(PTS-STARTPTS)/{_decimal(opts['speed'])},format=yuv444p,setsar=1[src]"]
    current = "src"
    if opts["mask_mode"] != "none":
        box = pixel_crop(opts["crop_box"], width, height, opts["padding_px"])
        x, y, w, h = (box[key] for key in ("x", "y", "width", "height"))
        if opts["mask_mode"] == "delogo":
            graph.append(f"[{current}]pad={width + 4}:{height + 4}:2:2,"
                         "fillborders=left=2:right=2:top=2:bottom=2:mode=smear,"
                         f"delogo=x={x + 2}:y={y + 2}:w={w}:h={h}:show=0,"
                         f"crop={width}:{height}:2:2[masked]")
        else:
            graph.append(f"[{current}]split=2[maskbase][maskroi]")
            if opts["mask_mode"] == "boxblur":
                radius = min(math.ceil(opts["blur_strength"]), min(w, h) // 2)
                graph.append(f"[maskroi]crop={w}:{h}:{x}:{y},"
                             f"boxblur=luma_radius={radius}:luma_power=2:"
                             f"chroma_radius={radius}:chroma_power=2[mask]")
            else:
                red, green, blue = (int(opts["solid_color"][i:i + 2], 16) for i in (1, 3, 5))
                radius = min(12, w / 2, h / 2)
                qx = f"max(abs(X-{_decimal((w - 1) / 2)})-{_decimal(w / 2 - radius)},0)"
                qy = f"max(abs(Y-{_decimal((h - 1) / 2)})-{_decimal(h / 2 - radius)},0)"
                alpha = (f"{_decimal(255 * opts['solid_opacity'])}*"
                         f"clip({_decimal(radius + 0.5)}-sqrt(pow({qx},2)+pow({qy},2)),0,1)")
                graph.append(f"[maskroi]crop={w}:{h}:{x}:{y},format=gbrap,"
                             f"geq=r={red}:g={green}:b={blue}:a='{alpha}'[mask]")
            graph.append(f"[maskbase][mask]overlay=x={x}:y={y}:format=auto:eof_action=repeat[masked]")
        current = "masked"
    if opts["reframe"] == "vertical":
        # Crop the cover background BEFORE upscaling. A very wide/tall source
        # must not create a giant off-canvas intermediate just to crop it away.
        bg_width = min(width, max(1, math.ceil(height * out_width / out_height)))
        bg_height = min(height, max(1, math.ceil(width * out_height / out_width)))
        radius = min(math.ceil(opts["blur_strength"]), min(out_width, out_height) // 2)
        graph.extend([
            f"[{current}]split=2[bgsource][fgsource]",
            f"[bgsource]crop={bg_width}:{bg_height}:(iw-ow)/2:(ih-oh)/2,"
            f"scale={out_width}:{out_height}:flags=bicubic,setsar=1,"
            f"boxblur=luma_radius={radius}:luma_power=2:chroma_radius={radius}:chroma_power=2[bg]",
            f"[fgsource]scale={layout['foreground_width']}:{layout['foreground_height']}:flags=lanczos,setsar=1[fg]",
            f"[bg][fg]overlay=x={layout['x']}:y={layout['y']}:format=auto:eof_action=repeat[reframed]",
        ])
        current = "reframed"
    elif out_width != width or out_height != height:
        graph.append(f"[{current}]pad={out_width}:{out_height}:0:0:color=black[even]")
        current = "even"
    if burn_ass:
        subtitle_filter = f"ass=filename={_filter_path(ass_path)}"
        if escaped_fonts is not None:
            subtitle_filter += f":fontsdir={escaped_fonts}"
        graph.append(f"[{current}]{subtitle_filter}[subtitled]")
        current = "subtitled"
    if watermark_input is not None:
        watermark_label = _watermark_label(watermark_input)
        max_w, max_h = max(2, out_width // 5 // 2 * 2), max(2, out_height // 5 // 2 * 2)
        margin = max(2, round(min(out_width, out_height) * 0.025))
        right = opts["watermark_corner"].endswith("right")
        bottom = opts["watermark_corner"].startswith("bottom")
        wx, wy = f"W-w-{margin}" if right else str(margin), f"H-h-{margin}" if bottom else str(margin)
        graph.extend([
            f"{watermark_label}format=rgba,scale={max_w}:{max_h}:force_original_aspect_ratio=decrease,"
            f"colorchannelmixer=aa={_decimal(opts['watermark_opacity'])}[watermark]",
            f"[{current}][watermark]overlay=x={wx}:y={wy}:format=auto:eof_action=repeat:repeatlast=1[watermarked]",
        ])
        current = "watermarked"
    graph.append(f"[{current}]format=yuv420p,setsar=1[vout]")
    return {"filter_complex": ";".join(graph), "output_label": "[vout]",
            "width": out_width, "height": out_height}
