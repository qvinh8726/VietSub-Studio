"""Speed/pitch chains for aligned audio, independent of the speech provider.

``audio_effects`` retains the legacy global-audio contract. New editor options
carry ``tts_change_pitch``; their background chain uses pitch-preserving tempo
and only speech receives the semitone / speed-linked pitch shift.
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
    return _chain(speed, factor, sample_rate)


def _chain(speed, factor, sample_rate):
    chain = [f"aresample={sample_rate}"]
    if not math.isclose(factor, 1, rel_tol=1e-12):
        chain += [f"asetrate={sample_rate}*{factor:.12g}", f"aresample={sample_rate}"]
    tempo = speed / factor
    if not math.isclose(tempo, 1, rel_tol=1e-12):
        chain.append(atempo_chain(tempo))
    chain.append("asetpts=N/SR/TB")
    return ",".join(chain)


def track_audio_effects(options, *, track, sample_rate=48000, pitch_speed=None):
    """Scope new pitch options to speech; preserve legacy callers unchanged.

    ``pitch_speed`` separates the pitch reference from the timeline speed when
    preparing a CapCut draft: bake the voice pitch at source duration, then let
    CapCut apply pitch-preserving native speed to the prepared mix exactly once.
    """
    if track not in {"tts", "background"}:
        raise ValueError("track must be tts or background")
    if "tts_change_pitch" not in options:
        return audio_effects(options, sample_rate=sample_rate)
    linked = options["tts_change_pitch"]
    if type(linked) is not bool:
        raise ValueError("tts_change_pitch must be a boolean")
    if track == "background":
        return audio_effects({**options, "change_audio_pitch": False, "pitch_semitones": 0}, sample_rate=sample_rate)
    speed = options.get("speed", 1.0)
    reference = speed if pitch_speed is None else pitch_speed
    # Validate both speeds using the same strict bounds as the public helper.
    audio_effects({"speed": speed}, sample_rate=sample_rate)
    audio_effects({**options, "speed": reference, "change_audio_pitch": linked}, sample_rate=sample_rate)
    factor = (reference if linked else 1) * 2 ** (options.get("pitch_semitones", 0) / 12)
    return _chain(speed, factor, sample_rate)
