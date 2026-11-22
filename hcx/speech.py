"""Speech for HyperTalk's `speak` and the speech externals (MacinTalk,
PlainTalk), using the Linux voices.

The Mac voices were 68k/PowerPC code and can't run here, so speech goes to
speech-dispatcher (the service GNOME's screen reader uses), or to eSpeak NG
when speech-dispatcher isn't there. Mac voice names are mapped to a voice of
the same kind (male, female, child), and MacinTalk's rate (words per
minute) and pitch (Hz) are converted. Without any of these, speaking does
nothing and `the speech` is always "done"."""

import shutil
import subprocess
import threading

# The voices of PlainTalk 1.x, which `the voices` lists: name -> (kind, pitch offset)
MAC_VOICES = {
    "Agnes": ("female1", 0), "Kathy": ("female2", 0), "Victoria": ("female3", 0),
    "Princess": ("child_female", 0), "Vicki": ("female1", 0),
    "Bruce": ("male1", 0), "Fred": ("male2", 0), "Ralph": ("male3", -10),
    "Junior": ("child_male", 0), "Albert": ("male3", -20), "Bad News": ("male1", -40),
    "Good News": ("male1", 40), "Bahh": ("male2", 20), "Bells": ("female1", 30),
    "Boing": ("male1", 30), "Bubbles": ("male2", 20), "Cellos": ("male1", -20),
    "Deranged": ("male2", 30), "Hysterical": ("male1", 40), "Pipe Organ": ("male1", -30),
    "Trinoids": ("male2", 50), "Whisper": ("female2", 0), "Zarvox": ("male3", -30),
}
_GENDER = {"male": "male1", "female": "female1", "neutral": "male2", "robotic": "male3",
           "child": "child_male", "young": "child_male", "old": "male3"}
_ESPEAK = {"male1": "en+m1", "male2": "en+m3", "male3": "en+m7", "female1": "en+f1",
           "female2": "en+f2", "female3": "en+f4", "child_male": "en+m2", "child_female": "en+f5"}


def voice_kind(voice):
    """(speech-dispatcher voice type, pitch offset) for a Mac voice name or a
    gender word ("female", "male old"...)."""
    v = str(voice or "").strip()
    if not v:
        return "male1", 0
    for name, kp in MAC_VOICES.items():
        if v.lower() == name.lower() or v.lower().split(",")[0].strip() == name.lower():
            return kp
    words = v.lower().split()
    kind = None
    for w in words:
        if w in ("female", "male", "neutral", "robotic"):
            kind = _GENDER[w]
    if "young" in words or "child" in words:
        kind = "child_female" if kind == "female1" else "child_male"
    elif "old" in words and kind in (None, "male1"):
        kind = "male3"
    return kind or "male1", 0


class Speech:
    def __init__(self):
        self.rate = 175         # words per minute (MacinTalk's normal is 150 to 180)
        self.pitch = 0          # -100 to 100 around the voice's own pitch
        self.voice = ""
        self._lock = threading.Lock()
        self._queue = []        # texts waiting (subprocess backends)
        self._current = ""
        self._pending = 0       # messages sent to speech-dispatcher and not finished
        self._proc = None
        self._client = None
        self._backend = None

    # -- backends -----------------------------------------------------------------------------
    def backend(self):
        if self._backend is None:
            self._backend = "none"
            try:
                import speechd
                self._client = speechd.SSIPClient("hcx")
                self._client.set_language("en")
                self._backend = "speechd"
            except Exception:  # noqa: BLE001
                self._client = None
                for exe in ("espeak-ng", "espeak", "spd-say"):
                    if shutil.which(exe):
                        self._backend = exe
                        break
        return self._backend

    def available(self):
        return self.backend() != "none"

    # -- speaking -----------------------------------------------------------------------------
    def speak(self, text, voice=None):
        text = str(text).replace("\r", "\n").strip()
        if not text:
            return
        b = self.backend()
        if b == "none":
            return
        kind, poff = voice_kind(voice if voice is not None else self.voice)
        rate = max(-100, min(100, int((self.rate - 175) / 2)))
        pitch = max(-100, min(100, self.pitch + poff))
        if b == "speechd":
            import speechd
            c = self._client
            try:
                c.set_voice(kind.upper())
                c.set_rate(rate)
                c.set_pitch(pitch)

                def done(event_type, *_args, **_kw):
                    with self._lock:
                        if event_type == speechd.CallbackType.BEGIN:
                            self._current = text
                        else:
                            self._pending = max(0, self._pending - 1)
                            if self._pending == 0:
                                self._current = ""
                with self._lock:
                    self._pending += 1
                    if not self._current:
                        self._current = text
                c.speak(text, callback=done, event_types=(speechd.CallbackType.BEGIN, speechd.CallbackType.END,
                                                          speechd.CallbackType.CANCEL))
            except Exception:  # noqa: BLE001
                with self._lock:
                    self._pending = 0
                    self._current = ""
            return
        with self._lock:
            self._queue.append((text, kind, rate, pitch))
            start = self._proc is None and len(self._queue) == 1 and not self._current
        if start:
            threading.Thread(target=self._worker, daemon=True).start()

    def _command(self, text, kind, rate, pitch):
        b = self._backend
        if b == "spd-say":
            return ["spd-say", "-w", "-l", "en", "-t", kind, "-r", str(rate), "-p", str(pitch), "--", text]
        wpm = max(80, min(450, int(self.rate)))
        return [b, "-v", _ESPEAK.get(kind, "en"), "-s", str(wpm), "-p", str(max(0, min(99, 50 + pitch // 2))), "--", text]

    def _worker(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._current = ""
                    self._proc = None
                    return
                text, kind, rate, pitch = self._queue.pop(0)
                self._current = text
                try:
                    self._proc = subprocess.Popen(self._command(text, kind, rate, pitch), stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    self._proc = None
                    self._queue = []
                    self._current = ""
                    return
                proc = self._proc
            proc.wait()

    def stop(self):
        b = self._backend
        if b == "speechd" and self._client is not None:
            try:
                self._client.cancel()
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._pending = 0
                self._current = ""
            return
        with self._lock:
            self._queue = []
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
            if b == "spd-say":
                try:
                    subprocess.run(["spd-say", "-S"], timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError):
                    pass

    def busy(self):
        with self._lock:
            if self._backend == "speechd":
                return self._pending > 0
            return bool(self._queue) or (self._proc is not None and self._proc.poll() is None)

    def current(self):
        """`the speech`: the text being spoken, or "done"."""
        if not self.busy():
            return "done"
        with self._lock:
            return self._current or "done"

    def set_rate_wpm(self, wpm):
        self.rate = max(60, min(500, int(wpm)))

    def set_pitch_hz(self, hz):
        """MacinTalk pitch in Hz (110 is a normal male voice)."""
        self.pitch = max(-100, min(100, int((float(hz) - 110) * 1.2)))


_SPEECH = None


def get():
    global _SPEECH
    if _SPEECH is None:
        _SPEECH = Speech()
    return _SPEECH
