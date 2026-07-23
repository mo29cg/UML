from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class Notification:
    def __init__(self) -> None:
        self._callbacks = []

    def register(self, callback) -> None:
        self._callbacks.append(callback)

    def unregister(self, callback) -> None:
        self._callbacks = [item for item in self._callbacks if item is not callback]

    def notify(self, **kwargs) -> None:
        for callback in list(self._callbacks):
            callback(**kwargs)


class FakeConf(dict):
    def __init__(self) -> None:
        super().__init__()
        self.spec = {}
        self["UML_global"] = {
            "primaryLanguage": "ja",
            "strategy": "word",
            "japanese": "fake_ja",
            "fallback": "fake_en",
            "volumeOffset_ja": 0,
            "volumeOffset_en": 0,
            "rateOffset_ja": 0,
            "rateOffset_en": 0,
        }


class FakeSayAllHandler:
    speechWithoutPausesInstance = None

    @staticmethod
    def isRunning() -> bool:
        return True


class FakeSpeechWithoutPauses:
    def __init__(self, speakFunc):
        self.speakFunc = speakFunc


def install_nvda_stubs():
    captured = []

    config_mod = types.ModuleType("config")
    config_mod.conf = FakeConf()

    log_handler_mod = types.ModuleType("logHandler")
    log_handler_mod.log = types.SimpleNamespace(
        error=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )

    commands_mod = types.ModuleType("speech.commands")

    class IndexCommand:
        def __init__(self, index):
            self.index = index

        def __repr__(self) -> str:
            return f"IndexCommand({self.index!r})"

    class LangChangeCommand:
        def __init__(self, lang):
            self.lang = lang

        def __repr__(self) -> str:
            return f"LangChangeCommand({self.lang!r})"

    commands_mod.IndexCommand = IndexCommand
    commands_mod.LangChangeCommand = LangChangeCommand

    speech_mod = types.ModuleType("speech")
    speech_mod.Spri = types.SimpleNamespace(NORMAL="normal")
    speech_mod.speech = types.SimpleNamespace(speak=lambda *args, **kwargs: None)
    speech_mod.sayAll = types.SimpleNamespace(SayAllHandler=FakeSayAllHandler())
    speech_mod.speechWithoutPauses = types.SimpleNamespace(
        SpeechWithoutPauses=FakeSpeechWithoutPauses
    )
    speech_mod.commands = commands_mod

    synth_mod = types.ModuleType("synthDriverHandler")
    synth_mod.synthIndexReached = Notification()
    synth_mod.synthDoneSpeaking = Notification()

    class BaseSynthDriver:
        class VolumeSetting:
            pass

        class RateSetting:
            pass

        class RateBoostSetting:
            pass

    synth_mod.SynthDriver = BaseSynthDriver

    def get_synth_driver(identifier):
        class FakeSynth:
            name = identifier
            description = identifier

            def initSettings(self) -> None:
                pass

            def terminate(self) -> None:
                pass

            def cancel(self) -> None:
                pass

            def speak(self, seq) -> None:
                captured.append(
                    {
                        "synth": identifier,
                        "sequence": list(seq),
                        "text": "".join(item for item in seq if isinstance(item, str)),
                    }
                )
                for item in seq:
                    if isinstance(item, IndexCommand):
                        synth_mod.synthIndexReached.notify(synth=self, index=item.index)
                synth_mod.synthDoneSpeaking.notify(synth=self)

        return FakeSynth

    synth_mod._getSynthDriver = get_synth_driver

    sys.modules["config"] = config_mod
    sys.modules["logHandler"] = log_handler_mod
    sys.modules["speech"] = speech_mod
    sys.modules["speech.commands"] = commands_mod
    sys.modules["synthDriverHandler"] = synth_mod
    return captured


def load_uml_module():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    for module_name in [
        "addon.synthDrivers.UML",
        "config",
        "logHandler",
        "speech",
        "speech.commands",
        "synthDriverHandler",
    ]:
        sys.modules.pop(module_name, None)

    captured = install_nvda_stubs()
    module = importlib.import_module("addon.synthDrivers.UML")
    return module, captured


def run_uml_pipeline(input_sequence, last_lang="ja", strategy="word"):
    module, captured = load_uml_module()
    module._LOG_BASE = "/tmp/uml_speech_harness_debug"
    module._LOG_FILE = module._LOG_BASE + ".log"
    driver = module.SynthDriver()
    driver.last_lang = last_lang
    driver.strategy = strategy
    try:
        modified = module.modseq(input_sequence, last_lang, strategy)
        driver.speak(modified)
        module.bgQueue.join()
        return {
            "module": module,
            "modified": modified,
            "captured": list(captured),
        }
    finally:
        driver.terminate()


def sequence_text_items(seq):
    return [item for item in seq if isinstance(item, str)]


def leading_url_continuations(text_items):
    return [
        item
        for item in text_items
        if item.startswith(("/", "?", "#", "&", "=", ".", "-"))
    ]


def continuation_chunks(captured):
    return [
        entry["text"]
        for entry in captured
        if leading_url_continuations([entry["text"]])
    ]
