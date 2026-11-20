"""
TARZ "Voice" page: how you talk to TARZ (mode), input (mic + speech-to-text + wake word),
output (speaker + text-to-speech + speed + volume).

Settings are saved to ~/.tarz/voice_settings.json on every change, so the rest of the app just does:

    from voice_settings import load_settings
    cfg = load_settings()          # {"mode": "wake", "stt": "gemini_live", "tts": "groq_orpheus", ...}

Run this file alone to preview the page.
"""
import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QRadioButton, QSlider,
                               QVBoxLayout, QWidget)

SETTINGS_PATH = Path.home() / ".tarz" / "voice_settings.json"

DEFAULTS = {
    "mode": "wake",              # chat | hotkey | wake | hybrid
    "mic": "Default microphone",
    "stt": "gemini_live",        # gemini_live | whisper_local | cartesia
    "wake_word": "hey tars",
    "sensitivity": 60,           # 0..100
    "speaker": "Default speakers",
    "tts": "groq_orpheus",       # groq_orpheus | cartesia_sonic | windows | off
    "speed": 100,                # percent, 70..150
    "volume": 80,                # 0..100
}

MODES = [
    ("chat", "Chat only", "Type to talk. Mic stays off."),
    ("hotkey", "Hotkey", "Hold a key, speak, release."),
    ("wake", "Always listening", "Say the wake word, like Alexa."),
    ("hybrid", "Hybrid", "Chat window plus wake word."),
]
STT = [("gemini_live", "Gemini Live", "Free"),
       ("whisper_local", "Whisper on this PC", "Offline"),
       ("cartesia", "Cartesia ink-2", "Free tier")]
TTS = [("groq_orpheus", "Groq Orpheus", "Free"),
       ("cartesia_sonic", "Cartesia Sonic-3", "Free tier"),
       ("windows", "Windows voice", "Offline"),
       ("off", "Off, text only", "")]


def load_settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def save_settings(cfg: dict):
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass  # never crash the UI over a settings file


def _devices(kind: str, default_label: str) -> list:
    """Real device names via sounddevice or pyaudio if installed, otherwise just the default.
    (QtMultimedia's QMediaDevices can hard-crash on machines with no audio service, so it is not used.)"""
    names = [default_label]
    try:
        import sounddevice as sd
        ch = "max_input_channels" if kind == "in" else "max_output_channels"
        for d in sd.query_devices():
            if d[ch] > 0 and d["name"] not in names:
                names.append(d["name"])
        return names
    except Exception:
        pass
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        ch = "maxInputChannels" if kind == "in" else "maxOutputChannels"
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info[ch] > 0 and info["name"] not in names:
                names.append(info["name"])
        pa.terminate()
    except Exception:
        pass
    return names


STYLE = """
#page{background:#0a0a0a}
QLabel{color:#fff;font-family:Inter,'Segoe UI',sans-serif;font-size:14px}
QLabel#h{font-size:20px}
QLabel#mono{font-family:'Geist Mono',Consolas,monospace;font-size:11px;color:#9a9ea5;letter-spacing:1px}
QLabel#badge{font-family:'Geist Mono',Consolas,monospace;font-size:11px;color:#9a9ea5}
QFrame#card{background:#191919;border:1px solid #212327;border-radius:8px}
QFrame#mode{background:#191919;border:1px solid #212327;border-radius:8px}
QFrame#mode:hover{border-color:#4a4d54}
QFrame#mode[selected="true"]{background:#ffffff;border-color:#ffffff}
QFrame#mode QLabel#mt{font-size:16px}
QFrame#mode QLabel#md{color:#9a9ea5}
QFrame#mode[selected="true"] QLabel#mt{color:#0a0a0a}
QFrame#mode[selected="true"] QLabel#md{color:#363a3f}
QFrame#sep{background:#212327;max-height:1px;min-height:1px;border:none}
QRadioButton{color:#dadbdf;font-size:14px;spacing:12px;background:transparent}
QRadioButton::indicator{width:14px;height:14px;border-radius:8px;border:1px solid #7d8187;background:transparent}
QRadioButton::indicator:checked{width:6px;height:6px;border-radius:8px;border:5px solid #ffffff;background:#0a0a0a}
QComboBox,QLineEdit{background:#1a1c20;border:1px solid #212327;border-radius:8px;padding:10px 14px;color:#fff;font-size:14px}
QLineEdit:focus,QComboBox:focus{border-color:#ffffff}
QLineEdit:disabled,QComboBox:disabled{color:#5a5e65}
QComboBox QAbstractItemView{background:#1a1c20;border:1px solid #212327;selection-background-color:#2a2c31;color:#fff}
QComboBox::drop-down{border:none;width:28px}
QSlider{min-height:24px}
QSlider::groove:horizontal{height:4px;background:#2a2c31;border-radius:2px}
QSlider::add-page:horizontal{background:#2a2c31;border-radius:2px}
QSlider::sub-page:horizontal{background:#ffffff;border-radius:2px}
QSlider::handle:horizontal{width:14px;height:14px;margin:-5px 0;background:#ffffff;border-radius:7px}
QSlider::sub-page:horizontal:disabled{background:#4a4d54}
QPushButton#primary{background:#ffffff;color:#0a0a0a;border:1px solid #ffffff;border-radius:16px;padding:6px 16px}
QPushButton#primary:hover{background:#e6e6e6}
"""


class ModeCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key, title, desc):
        super().__init__()
        self.key = key
        self.setObjectName("mode")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(6)
        t, d = QLabel(title), QLabel(desc)
        t.setObjectName("mt")
        d.setObjectName("md")
        d.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addStretch(1)
        self.setProperty("selected", False)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, _):
        self.clicked.emit(self.key)


def _mono(text):
    lab = QLabel(text.upper())
    lab.setObjectName("mono")
    return lab


def _card():
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(24, 22, 24, 24)
    lay.setSpacing(8)
    return f, lay


class VoicePage(QWidget):
    # emitted with the full settings dict after every change
    changed = Signal(dict)
    # connect to your TTS: speak("Hey, this is TARZ.")
    test_voice = Signal(dict)

    def __init__(self, settings: dict | None = None):
        super().__init__()
        self.setObjectName("page")
        self.setStyleSheet(STYLE)
        self.s = {**DEFAULTS, **
                  (settings if settings is not None else load_settings())}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(20)

        head = QHBoxLayout()
        title = QLabel("Voice")
        title.setObjectName("h")
        test = QPushButton("Test voice")
        test.setObjectName("primary")
        test.setCursor(Qt.PointingHandCursor)
        test.clicked.connect(lambda: self.test_voice.emit(dict(self.s)))
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(test)
        root.addLayout(head)

        # ---- mode cards
        root.addWidget(_mono("// How you talk to TARZ"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.cards = {}
        for key, t, d in MODES:
            c = ModeCard(key, t, d)
            c.setMinimumHeight(96)
            c.clicked.connect(lambda k: self._set("mode", k))
            self.cards[key] = c
            row.addWidget(c, 1)
        root.addLayout(row)

        # ---- input + output
        cols = QHBoxLayout()
        cols.setSpacing(20)

        inp, il = _card()
        il.addWidget(_mono("Input"))
        il.addSpacing(8)
        il.addWidget(_mono("Microphone"))
        self.mic = self._combo(_devices("in", "Default microphone"), "mic")
        il.addWidget(self.mic)
        il.addSpacing(12)
        il.addWidget(_mono("Speech to text"))
        self._options(il, "stt", STT)
        il.addSpacing(12)
        il.addWidget(_mono("Wake word"))
        self.wake = QLineEdit(self.s["wake_word"])
        self.wake.editingFinished.connect(
            lambda: self._set("wake_word", self.wake.text().strip().lower() or DEFAULTS["wake_word"]))
        il.addWidget(self.wake)
        il.addSpacing(8)
        il.addWidget(_mono("Sensitivity"))
        self.sens = self._slider(0, 100, "sensitivity")
        il.addWidget(self.sens)
        il.addStretch(1)

        out, ol = _card()
        ol.addWidget(_mono("Output"))
        ol.addSpacing(8)
        ol.addWidget(_mono("Speaker"))
        self.spk = self._combo(_devices("out", "Default speakers"), "speaker")
        ol.addWidget(self.spk)
        ol.addSpacing(12)
        ol.addWidget(_mono("Text to speech"))
        self._options(ol, "tts", TTS)
        ol.addSpacing(12)
        sv = QHBoxLayout()
        sv.setSpacing(24)
        for label, lo, hi, key in (("Speed", 70, 150, "speed"), ("Volume", 0, 100, "volume")):
            col = QVBoxLayout()
            col.addWidget(_mono(label))
            col.addWidget(self._slider(lo, hi, key))
            sv.addLayout(col, 1)
        ol.addLayout(sv)
        ol.addStretch(1)

        cols.addWidget(inp, 1)
        cols.addWidget(out, 1)
        root.addLayout(cols, 1)
        self._refresh()

    # ---- builders
    def _combo(self, names, key):
        cb = QComboBox()
        cb.addItems(names)
        if self.s[key] in names:
            cb.setCurrentText(self.s[key])
        cb.currentTextChanged.connect(lambda v: self._set(key, v))
        return cb

    def _slider(self, lo, hi, key):
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(int(self.s[key]))
        sl.valueChanged.connect(lambda v: self._set(key, v))
        return sl

    def _options(self, lay, key, options):
        group = QButtonGroup(self)          # keeps each radio set exclusive
        for i, (val, text, badge) in enumerate(options):
            if i:
                sep = QFrame()
                sep.setObjectName("sep")
                lay.addWidget(sep)
            row = QHBoxLayout()
            rb = QRadioButton(text)
            rb.setChecked(self.s[key] == val)
            rb.toggled.connect(lambda on, v=val: on and self._set(key, v))
            group.addButton(rb)
            b = QLabel(badge.upper())
            b.setObjectName("badge")
            row.addWidget(rb)
            row.addStretch(1)
            row.addWidget(b)
            wrap = QWidget()
            wrap.setLayout(row)
            row.setContentsMargins(0, 8, 0, 8)
            lay.addWidget(wrap)

    # ---- state
    def _set(self, key, value):
        if self.s.get(key) == value:
            return
        self.s[key] = value
        save_settings(self.s)
        self._refresh()
        self.changed.emit(dict(self.s))

    def _refresh(self):
        for k, c in self.cards.items():
            c.set_selected(k == self.s["mode"])
        # wake word only matters then
        listening = self.s["mode"] in ("wake", "hybrid")
        self.wake.setEnabled(listening)
        self.sens.setEnabled(listening)
        self.mic.setEnabled(self.s["mode"] != "chat")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    page = VoicePage()
    page.changed.connect(lambda cfg: print("changed:", cfg))
    page.test_voice.connect(lambda cfg: print("test voice with", cfg["tts"]))
    page.resize(1040, 700)
    page.show()
    sys.exit(app.exec())
