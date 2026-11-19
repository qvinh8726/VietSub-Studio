"""Shared clip speed/pitch effects, independent of the speech provider.

Changing clip speed normally preserves pitch. With change_audio_pitch enabled,
rate follows speed (tape/CapCut behavior); pitch_semitones is an additional shift
in either mode. Apply to source-timeline audio AFTER cue alignment and padding.
FFmpeg's resampling shift is not formant-preserving.
"""
import math

from .alignment import atempo_chain


def audio_effects(options, *, sample_rate=48000):
    """Build a safe audio chain shared by original, instrumental and TTS tracks."""
    speed = options.get("speed", 1.0)
    pitch = options.get("pitch_semitones", 0.0)
    change_pitch = options.get("change_audio_pitch", False)
    for name, value, low, high in (("speed", speed, .5, 2), ("pitch_semitones", pitch, -12, 12)):
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number between {low} and {high}")
    if type(change_pitch) is not bool:
        raise ValueError("change_audio_pitch must be a boolean")
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("sample_rate must be an integer between 8000 and 192000")
    factor = (speed if change_pitch else 1) * 2 ** (pitch / 12)
    chain = [f"aresample={sample_rate}"]
    if not math.isclose(factor, 1, rel_tol=1e-12):
        chain += [f"asetrate={sample_rate}*{factor:.12g}", f"aresample={sample_rate}"]
    tempo = speed / factor
    if not math.isclose(tempo, 1, rel_tol=1e-12):
        chain.append(atempo_chain(tempo))
    chain.append("asetpts=N/SR/TB")
    return ",".join(chain)
