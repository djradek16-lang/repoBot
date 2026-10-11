"""
Generator proceduralnych efektów dźwiękowych dla teleturnieju 1 z 10.
Generuje czyste pliki WAV przy użyciu wbudowanych modułów wave, math, struct (bez zewnętrznych bibliotek).
Jeśli w katalogu data/sounds_1z10/ pojawią się własne pliki (np. intro.mp3, correct.mp3 itp.), mają one pierwszeństwo.
"""

import math
import struct
import wave
from pathlib import Path
import logging

logger = logging.getLogger("AegisBot.1z10.SoundGen")

def _generate_tone_frames(
    sample_rate: int,
    freq_func,
    duration: float,
    amplitude_func = None
) -> bytes:
    total_frames = int(sample_rate * duration)
    output = bytearray()
    phase = 0.0

    for i in range(total_frames):
        t = i / sample_rate
        freq = freq_func(t)
        phase += 2.0 * math.pi * freq / sample_rate
        
        amp = 1.0
        if amplitude_func:
            amp = amplitude_func(t, duration)

        sample = math.sin(phase) + 0.25 * math.sin(2.0 * phase)
        sample = sample / 1.25 * amp
        sample = max(-1.0, min(1.0, sample))
        
        val = int(sample * 32767.0 * 0.75)
        output.extend(struct.pack("<h", val))

    return bytes(output)

def _save_wav(path: Path, data: bytes, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)

def generate_default_sounds(output_dir: Path) -> dict[str, Path]:
    """Generuje domyślne efekty dźwiękowe jeśli nie ma ich w katalogu."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_rate = 44100
    created = {}

    # 1. Buzzer (przycisk zgłoszenia)
    buzzer_path = output_dir / "buzzer.wav"
    if not buzzer_path.exists():
        def freq_b(t):
            return 980.0 if t < 0.08 else 1318.5
        def amp_b(t, dur):
            return max(0.0, 1.0 - (t / dur) ** 1.5)
        data = _generate_tone_frames(sample_rate, freq_b, 0.35, amp_b)
        _save_wav(buzzer_path, data, sample_rate)
        created["buzzer"] = buzzer_path

    # 2. Correct (pozytywny, jasny dwuton ding-dong)
    correct_path = output_dir / "correct.wav"
    if not correct_path.exists():
        def freq_c(t):
            return 659.25 if t < 0.18 else 1046.50
        def amp_c(t, dur):
            if t < 0.18:
                return math.exp(-t * 8.0)
            return math.exp(-(t - 0.18) * 4.0)
        data = _generate_tone_frames(sample_rate, freq_c, 0.7, amp_c)
        _save_wav(correct_path, data, sample_rate)
        created["correct"] = correct_path

    # 3. Wrong (niski podwójny brzęczyk błędu 1z10)
    wrong_path = output_dir / "wrong.wav"
    if not wrong_path.exists():
        total_frames = int(sample_rate * 0.6)
        output = bytearray()
        for i in range(total_frames):
            t = i / sample_rate
            is_active = (0.0 <= t < 0.22) or (0.28 <= t < 0.55)
            if is_active:
                phase = 2.0 * math.pi * 146.83 * t
                sample = (math.sin(phase) + 0.6 * math.sin(2 * phase) + 0.4 * math.sin(3 * phase)) / 2.0
                sample = sample * 0.8
            else:
                sample = 0.0
            val = int(sample * 32767.0 * 0.75)
            output.extend(struct.pack("<h", val))
        _save_wav(wrong_path, bytes(output), sample_rate)
        created["wrong"] = wrong_path

    # 4. Gong / Czas (dramatyczny sygnał upływu czasu)
    gong_path = output_dir / "gong.wav"
    if not gong_path.exists():
        def freq_g(t):
            return 329.63
        def amp_g(t, dur):
            return math.exp(-t * 2.2)
        data = _generate_tone_frames(sample_rate, freq_g, 1.4, amp_g)
        _save_wav(gong_path, data, sample_rate)
        created["gong"] = gong_path

    # 5. Intro / Fanfary (motyw fanfarowy rozpoczynający rundę/turniej)
    intro_path = output_dir / "intro.wav"
    if not intro_path.exists():
        notes = [(0.0, 0.22, 261.63), (0.22, 0.44, 329.63), (0.44, 0.66, 392.00), (0.66, 1.5, 523.25)]
        total_frames = int(sample_rate * 1.5)
        output = bytearray()
        for i in range(total_frames):
            t = i / sample_rate
            sample = 0.0
            for start, end, f in notes:
                if start <= t < end:
                    dur_note = end - start
                    t_note = t - start
                    amp = math.exp(-t_note * (1.8 if end == 1.5 else 3.5))
                    ph = 2.0 * math.pi * f * t_note
                    sample = (math.sin(ph) + 0.3 * math.sin(2.0 * ph)) / 1.3 * amp
                    break
            val = int(sample * 32767.0 * 0.8)
            output.extend(struct.pack("<h", val))
        _save_wav(intro_path, bytes(output), sample_rate)
        created["intro"] = intro_path

    return created
