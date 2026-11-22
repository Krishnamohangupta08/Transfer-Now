"""Sound effects.

Every sound is synthesised with the standard library on first run (tiny WAV
files in the app data folder), so nothing has to be shipped or downloaded.
Playback uses ``QSoundEffect`` (low latency, non-blocking).  If Qt Multimedia
or an audio device is missing the app simply stays silent.

    from ui.sounds import sounds
    sounds.play("success")
"""
from __future__ import annotations

import math
import os
import struct
import time
import wave

from PyQt6.QtCore import QObject, QUrl

RATE = 44100
VERSION = "v3"
VOLUMES = {"low": 0.30, "medium": 0.60, "high": 1.00}

# name -> list of segments (f_start Hz, f_end Hz, seconds, gain, bell)
# ``bell`` adds a soft 2nd/3rd harmonic so the tone sounds like a chime, not a beep.
SOUNDS: dict[str, list[tuple[float, float, float, float, bool]]] = {
    "click":    [(1500, 1100, 0.030, 0.55, False)],
    "select":   [(780, 780, 0.035, 0.45, False)],
    "toggle":   [(620, 880, 0.060, 0.50, False)],
    "nav":      [(520, 640, 0.055, 0.45, False)],
    "menu":     [(460, 520, 0.040, 0.35, False)],
    "open":     [(420, 720, 0.100, 0.45, False)],
    "minimize": [(900, 320, 0.130, 0.45, False)],
    "maximize": [(380, 920, 0.130, 0.45, False)],
    "restore":  [(700, 450, 0.060, 0.45, False), (450, 750, 0.070, 0.45, False)],
    "close":    [(720, 260, 0.140, 0.45, False)],
    "theme":    [(540, 540, 0.060, 0.40, True), (810, 810, 0.090, 0.40, True)],
    "drop":     [(260, 150, 0.100, 0.70, False)],
    "start":    [(440, 660, 0.110, 0.50, True)],
    "info":     [(660, 660, 0.070, 0.45, True), (880, 880, 0.110, 0.45, True)],
    "success":  [(523.25, 523.25, 0.085, 0.50, True), (659.25, 659.25, 0.085, 0.50, True),
                 (783.99, 783.99, 0.200, 0.50, True)],
    "warning":  [(587, 587, 0.100, 0.50, True), (494, 494, 0.140, 0.50, True)],
    "error":    [(392, 392, 0.120, 0.60, True), (294, 294, 0.220, 0.60, True)],
}

# a quick repeat of the same sound inside this window is ignored (no machine-gun clicks)
_MIN_GAP = {"click": 0.04, "select": 0.04, "menu": 0.06, "nav": 0.08}
_DEFAULT_GAP = 0.05


def _render(segments) -> bytes:
    """Synthesise the segments into 16-bit mono PCM, normalised to a common loudness."""
    samples: list[float] = []
    phase = 0.0
    for f0, f1, dur, gain, bell in segments:
        n = max(1, int(RATE * dur))
        atk = max(1, int(RATE * 0.004))
        rel = max(1, int(RATE * 0.006))
        for i in range(n):
            t = i / n
            freq = f0 + (f1 - f0) * t
            phase += 2 * math.pi * freq / RATE
            v = math.sin(phase)
            if bell:
                v = 0.78 * v + 0.16 * math.sin(2 * phase) + 0.06 * math.sin(3 * phase)
            env = math.exp(-4.2 * t)                 # natural decay
            env *= min(1.0, i / atk)                 # soft attack (no click)
            env *= min(1.0, (n - i) / rel)           # soft release
            samples.append(v * env * gain)
    peak = max((abs(x) for x in samples), default=1.0) or 1.0
    scale = 0.85 / peak                              # every cue peaks at the same level;
    return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, x * scale)) * 32767))
                    for x in samples)                # the Volume setting does the rest


def _write(path: str, segments) -> None:
    pcm = _render(segments)
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    os.replace(tmp, path)


class Sounds(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.enabled = True
        self.volume = VOLUMES["medium"]
        self._fx: dict[str, object] = {}
        self._last: dict[str, float] = {}
        self._ready = False
        self._settings = None
        self._last_any = 0.0

    # ------------------------------------------------------------------ setup
    def setup(self, settings, data_dir: str) -> None:
        """Generate (if needed) and load every sound. Safe to call once at startup."""
        self._settings = settings
        self._read_settings()
        settings.on_change(self._on_setting)
        try:
            from PyQt6.QtMultimedia import QSoundEffect
        except Exception:  # noqa: BLE001 — Qt Multimedia not installed
            return
        folder = os.path.join(data_dir, "sounds")
        try:
            os.makedirs(folder, exist_ok=True)
            for name, segs in SOUNDS.items():
                path = os.path.join(folder, f"{VERSION}_{name}.wav")
                if not os.path.exists(path):
                    _write(path, segs)
                fx = QSoundEffect(self)
                fx.setSource(QUrl.fromLocalFile(path))
                fx.setVolume(self.volume)
                self._fx[name] = fx
            self._ready = True
        except Exception:  # noqa: BLE001 — never let audio break the app
            self._ready = False

    def _read_settings(self) -> None:
        s = self._settings
        self.enabled = bool(s.get("sounds", True))
        self.volume = VOLUMES.get(str(s.get("sound_volume", "medium")), VOLUMES["medium"])

    def _on_setting(self, key: str) -> None:
        if key in ("sounds", "sound_volume"):
            self._read_settings()
            for fx in self._fx.values():
                try:
                    fx.setVolume(self.volume)
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------- play
    def play(self, name: str, force: bool = False) -> None:
        """Play a named sound. ``force`` ignores the on/off setting (used by 'Test sound')."""
        if not self._ready or (not self.enabled and not force):
            return
        fx = self._fx.get(name)
        if fx is None:
            return
        now = time.monotonic()
        if now - self._last.get(name, 0.0) < _MIN_GAP.get(name, _DEFAULT_GAP):
            return
        self._last[name] = now
        self._last_any = now
        try:
            fx.play()
        except Exception:  # noqa: BLE001
            pass

    @property
    def active(self) -> bool:
        return self._ready and self.enabled

    def idle_for(self, seconds: float) -> bool:
        """True if no sound has started in the last ``seconds`` (avoids stacked cues)."""
        return time.monotonic() - self._last_any >= seconds

    def duration(self, name: str) -> float:
        return sum(seg[2] for seg in SOUNDS.get(name, []))


sounds = Sounds()
