# synthDrivers/UML.py
# Copyright (C) 2022 Yukio Nozawa, ACT Laboratory
# Some code provided by the NVDA community

import config
from logHandler import log
import synthDriverHandler
from speech.commands import IndexCommand, LangChangeCommand
import speech
import queue
import threading
import time
import os
import re
from datetime import datetime
from . import _umlCodes

# On NVDA startup, SynthDriver objects are imported first. If confspec is in UML GlobalPlugin, accessing to the config values seems to make an invalid cache and breaks UML config. Define conficspec here.
confspec = {
    "primaryLanguage": "string(default=ja)",
    "strategy": "string(default=sentence)",
    "japanese": "string(default=_)",
    "fallback": "string(default=_)",
    "checkForUpdatesOnStartup": "boolean(default=True)",
    "volumeOffset_ja": "integer(default=0, min=-100, max=100)",
    "volumeOffset_en": "integer(default=0, min=-100, max=100)",
    "rateOffset_ja": "integer(default=0, min=-100, max=100)",
    "rateOffset_en": "integer(default=0, min=-100, max=100)",
}
config.conf.spec["UML_global"] = confspec

# We need to hook into speech.speak function for evaluating correct language. This is because NVDA pre-composes character descriptions of which we must switch the language.

origSpeak = None
origSpeechWithoutPausesInstance = None
UMLInstance = None
isHooking = False


class InitializationError(Exception):
    pass


bgQueue = queue.Queue()

_LOG_BASE = r"\\wsl.localhost\Ubuntu\home\satoshi\GitHub\personal\UML\uml_sayall_debug"
_LOG_FILE = _LOG_BASE + ".log"
_LOG_SESSIONS_TO_KEEP = 3
_LOG_PREVIEW_CHARS = 2000
_LOG_QUEUE_MAX_ITEMS = 8192
_LOG_WRITER_JOIN_TIMEOUT_SEC = 1.0
_debugLogQueue = queue.Queue(maxsize=_LOG_QUEUE_MAX_ITEMS)
_debugLogStopEvent = threading.Event()
_debugLogThread = None
_debugLogAccepting = False
_debugLogStateLock = threading.Lock()

_URL_RE = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_@])
    (?:
        (?:(?:https?|ftp)://(?:[^@\s/]+@)?(?:www\.)?)
        |www\.
    )
    (?:
        (?:[A-Za-z0-9-]+\.)+(?:[A-Za-z]{2,}|xn--[A-Za-z0-9-]{2,})
        |(?:[^\s/?#:<>"'()（）「」『』【】\[\]{}<>、。]+\.)+[^\s/?#:<>"'()（）「」『』【】\[\]{}<>、。]{2,}
        |localhost
        |(?:\d{1,3}\.){3}\d{1,3}
        |\[[0-9A-Fa-f:.]+\]
    )
    (?::\d{1,5})?
    (?:[/?#][-A-Za-z0-9._~%!$&*+,;=:@/?#]*)?
    """
)
_URL_FOLDED_CONTINUATION_RE = re.compile(
    r"""\r?\n
    (?=[-._~%!$&*+,;=:@/?#])
    (?!-\s)
    [-A-Za-z0-9._~%!$&*+,;=:@/?#]+
    """,
    re.VERBOSE,
)
_URL_FOLDED_BREAK_RE = re.compile(r"\r?\n(?=[-._~%!$&*+,;=:@/?#])")
_URL_ITEM_CONTINUATION_RE = re.compile(r"^(?!-\s)[-A-Za-z0-9._~%!$&*+,;=:@/?#]+")
_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_ENGLISH_ORDINAL_RE = re.compile(
    r"\d+(?:,\d{3})*(?:st|nd|rd|th)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_JAPANESE_NUMBER_SUFFIXES = (
    "パーセント",
    "ポイント",
    "か月",
    "カ月",
    "ヶ月",
    "箇月",
    "週間",
    "時間",
    "世帯",
    "文字",
    "万",
    "億",
    "兆",
    "京",
    "件",
    "人",
    "名",
    "個",
    "回",
    "枚",
    "台",
    "本",
    "冊",
    "円",
    "歳",
    "才",
    "階",
    "番",
    "号",
    "点",
    "行",
    "列",
    "社",
    "店",
    "戸",
    "組",
    "匹",
    "羽",
    "頭",
    "脚",
    "杯",
    "着",
    "足",
    "箱",
    "袋",
    "票",
    "通",
    "時",
    "分",
    "秒",
    "日",
    "月",
    "年",
    "週",
)


def _rotate_debug_logs():
    oldest = f"{_LOG_BASE}.{_LOG_SESSIONS_TO_KEEP - 1}.log"
    if os.path.exists(oldest):
        os.remove(oldest)
    for i in range(_LOG_SESSIONS_TO_KEEP - 2, 0, -1):
        src = f"{_LOG_BASE}.{i}.log"
        dst = f"{_LOG_BASE}.{i + 1}.log"
        if os.path.exists(src):
            os.replace(src, dst)
    if os.path.exists(_LOG_FILE):
        os.replace(_LOG_FILE, f"{_LOG_BASE}.1.log")


def _debug_log_writer():
    logFile = None
    try:
        while not _debugLogStopEvent.is_set() or not _debugLogQueue.empty():
            try:
                line = _debugLogQueue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if logFile is None:
                    logFile = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
                logFile.write(line + "\n")
            except Exception:
                if logFile is not None:
                    try:
                        logFile.close()
                    except Exception:
                        pass
                    logFile = None
            finally:
                _debugLogQueue.task_done()
    finally:
        if logFile is not None:
            try:
                logFile.close()
            except Exception:
                pass


def _start_debug_log_writer():
    global _debugLogThread, _debugLogAccepting
    with _debugLogStateLock:
        _debugLogStopEvent.clear()
        if _debugLogThread is not None and _debugLogThread.is_alive():
            _debugLogAccepting = True
            return
        _debugLogThread = threading.Thread(
            target=_debug_log_writer,
            name="synthDrivers.UML.DebugLogWriter",
            daemon=True,
        )
        _debugLogAccepting = True
        _debugLogThread.start()


def _stop_debug_log_writer():
    global _debugLogThread, _debugLogAccepting
    with _debugLogStateLock:
        _debugLogAccepting = False
        thread = _debugLogThread
        _debugLogStopEvent.set()
    if thread is not None:
        thread.join(timeout=_LOG_WRITER_JOIN_TIMEOUT_SEC)
    with _debugLogStateLock:
        if (
            thread is not None
            and _debugLogThread is thread
            and not thread.is_alive()
        ):
            _debugLogThread = None


def _write_debug_log(event, **fields):
    if not _debugLogAccepting:
        return
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        payload = " | ".join(f"{k}={v}" for k, v in fields.items())
        line = f"[{timestamp}] {event}"
        if payload:
            line += f" | {payload}"
        _debugLogQueue.put_nowait(line)
    except Exception:
        pass


def _isSayAllRunning():
    try:
        return bool(
            speech.sayAll.SayAllHandler is not None
            and speech.sayAll.SayAllHandler.isRunning()
        )
    except Exception:
        return False


def _summarizeSequence(seq):
    if seq is None:
        return {
            "items": 0,
            "textItems": 0,
            "textChars": 0,
            "indexes": "-",
            "langs": "-",
            "preview": "",
        }
    textParts = []
    indexes = []
    langs = []
    textItems = 0
    textChars = 0
    for item in seq:
        if isinstance(item, str):
            textItems += 1
            textChars += len(item)
            if len(" ".join(textParts)) < _LOG_PREVIEW_CHARS:
                textParts.append(item.strip())
        elif isinstance(item, IndexCommand):
            indexes.append(str(item.index))
        elif isinstance(item, LangChangeCommand):
            langs.append(item.lang)
    preview = " ".join(part for part in textParts if part)
    preview = preview.replace("\r", " ").replace("\n", " ")
    if len(preview) > _LOG_PREVIEW_CHARS:
        preview = preview[:_LOG_PREVIEW_CHARS] + "..."
    return {
        "items": len(seq),
        "textItems": textItems,
        "textChars": textChars,
        "indexes": ",".join(indexes[:6]) if indexes else "-",
        "langs": ",".join(langs[:6]) if langs else "-",
        "preview": preview,
    }


def _logSayAll(event, seq=None, force=False, **fields):
    if not force and not _isSayAllRunning():
        return
    summary = _summarizeSequence(seq)
    payload = {
        "thread": threading.current_thread().name,
        **fields,
        **summary,
    }
    _write_debug_log(event, **payload)


class BgThread(threading.Thread):
    def __init__(self):
        super().__init__(
            name=f"{self.__class__.__module__}.{self.__class__.__qualname__}")
        self.setDaemon(True)

    def run(self):
        global isSpeaking
        while True:
            func, args, kwargs = bgQueue.get()
            if not func:
                break
            try:
                func(*args, **kwargs)
            except:
                log.error("Error running function from queue", exc_info=True)
            bgQueue.task_done()


def _execWhenDone(func, *args, mustBeAsync=True, **kwargs):
    global bgQueue
    if mustBeAsync or bgQueue.unfinished_tasks != 0:
        # Either this operation must be asynchronous or There is still an operation in progress.
        # Therefore, run this asynchronously in the background thread.
        bgQueue.put((func, args, kwargs))
    else:
        func(*args, **kwargs)


class SayAllWatcher(threading.Thread):
    """On NVDA startup, sayAllHandler is not instantiated by NVDA. We want to hook into the object. So we use a dedicated background thread for watching sayAllHandler existence."""

    def run(self):
        while(True):
            if speech.sayAll.SayAllHandler is None:
                continue
            # found sayAllHandler
            global origSpeechWithoutPausesInstance
            origSpeechWithoutPausesInstance = speech.sayAll.SayAllHandler.speechWithoutPausesInstance
            speech.sayAll.SayAllHandler.speechWithoutPausesInstance = speech.speechWithoutPauses.SpeechWithoutPauses(
                speakFunc=hookedSpeak)
            break
        # end loop until sayAllHandler is available


class SynthDriver(synthDriverHandler.SynthDriver):
    name = 'UML'
    description = 'Universal multilingual'
    _supportedSettings = [
        synthDriverHandler.SynthDriver.VolumeSetting(),
        synthDriverHandler.SynthDriver.RateSetting(),
    ]
    if hasattr(synthDriverHandler.SynthDriver, "RateBoostSetting"):
        _supportedSettings.append(
            synthDriverHandler.SynthDriver.RateBoostSetting()
        )
    supportedSettings = tuple(_supportedSettings)
    supportedCommands = {IndexCommand, }
    supportedNotifications = {
        synthDriverHandler.synthIndexReached, synthDriverHandler.synthDoneSpeaking
    }
    default_lang = _umlCodes.JAPANESE

    @classmethod
    def check(cls):
        return True

    def __init__(self):
        _rotate_debug_logs()
        _start_debug_log_writer()
        _write_debug_log("uml_init")
        self.strategy = "word"
        if "strategy" in config.conf["UML_global"]:
            self.strategy = config.conf["UML_global"]["strategy"]
        self.primary_lang = "ja"
        if "primaryLanguage" in config.conf["UML_global"]:
            # For some reason, primaryLanguage might be inaccessible on NVDA startup. Still haven't figured out why. Maybe configSpec is not loaded yet?
            self.primary_lang = config.conf["UML_global"]["primaryLanguage"]
        self.last_lang = self.primary_lang
        self.synthIdentifierMap = {
            'en': config.conf["UML_global"]["fallback"],
            'ja': config.conf["UML_global"]["japanese"],
        }

        self.synthInstanceMap = {}
        for k, v in self.synthIdentifierMap.items():
            try:
                synth = synthDriverHandler._getSynthDriver(v)()
                synth.initSettings()
                self.synthInstanceMap[k] = synth
            except BaseException as e:
                raise InitializationError(
                    "Failed to load %s (reason: %s)" % (v, e))
            # end wrap errors on exception
        # end load synth for all languages
        self._volume = 100
        self._rate = 50
        self._rateBoost = True
        self.cur_synth = None
        # Some backends (notably HISS) can fire index / done callbacks
        # synchronously on the calling thread while synth.speak is still on stack.
        # This must be re-entrant, otherwise on_index/on_done can deadlock
        # against wait_speak on leading callback indexes.
        self.lock = threading.RLock()
        self.lastindex = None
        self._internalIndexBase = 1000000000
        self._nextInternalIndex = self._internalIndexBase
        self._activeWaitIndex = None
        self._activeWaitIndexEvent = None
        self._activeWaitIndexIsInternal = False
        self._markerSupportBySynth = {}
        self._cancelWaitEvent = threading.Event()
        self._debugSpeakSeq = 0
        self._debugWaitSeq = 0
        synthDriverHandler.synthDoneSpeaking.register(self.on_done)
        synthDriverHandler.synthIndexReached.register(self.on_index)
        self.done = threading.Event()
        self.thread = BgThread()
        self.thread.daemon = True
        self.thread.start()
        # Hook into NVDA internal, an evil cat!
        self.setHook()

    def setHook(self):
        global origSpeak, UMLInstance
        origSpeak = speech.speech.speak
        UMLInstance = self
        speech.speech.speak = hookedSpeak
        _write_debug_log("setHook")
        global isHooking
        isHooking = True
        w = SayAllWatcher()
        w.setDaemon(True)
        w.start()

    def terminate(self):
        global isHooking
        _write_debug_log("terminate_start", isHooking=isHooking, force=True)
        if isHooking:
            global origSpeak, UMLInstance, origSpeechWithoutPausesInstance
            speech.speech.speak = origSpeak
            speech.sayAll.SayAllHandler.speechWithoutPausesInstance = origSpeechWithoutPausesInstance
            origSpeak = None
            origSpeechWithoutPausesInstance = None
            UMLInstance = None
            isHooking = False
        # end unhook
        for v in self.synthInstanceMap.values():
            v.terminate()
        synthDriverHandler.synthDoneSpeaking.unregister(self.on_done)
        synthDriverHandler.synthIndexReached.unregister(self.on_index)
        bgQueue.put((None, None, None))
        self.thread.join()
        _write_debug_log("terminate_done", force=True)
        _stop_debug_log_writer()

    def speak(self, seq):
        self._debugSpeakSeq += 1
        speakId = self._debugSpeakSeq
        _logSayAll(
            "uml_speak_enter",
            seq,
            speakId=speakId,
            lastLang=self.last_lang,
            strategy=self.strategy,
        )
        synth = self.synthInstanceMap[self.last_lang]
        textList = []
        for i, item in enumerate(seq):
            if isinstance(item, LangChangeCommand):
                # monkeypatch: I don't know when it has changed, but apparently it receives "ja_JP" here, originally it was ja.
                code = item.lang.split("_")[0]
                if code == self.last_lang:
                    continue
                if textList:
                    if any(isinstance(entry, str) for entry in textList):
                        _logSayAll(
                            "uml_speak_flush_before_lang_switch",
                            textList,
                            speakId=speakId,
                            fromLang=self.last_lang,
                            toLang=code,
                            synth=id(synth),
                        )
                        _execWhenDone(self.wait_speak, synth, textList[:])
                        textList = []
                    # Preserve leading callback / boundary indexes until they can be
                    # attached to the next real text chunk. Firing them early can
                    # desynchronize say-all on structured content.
                # end textList exists
                self.last_lang = code
                if code == 'en':
                    synth = self.synthInstanceMap['en']
                else:
                    synth = self.synthInstanceMap['ja']
            # end LangChangeCommand
            elif isinstance(item, IndexCommand):
                textList.append(item)
            elif isinstance(item, str):
                textList.append(item)
        # do the final speaking
        if textList:
            _logSayAll(
                "uml_speak_flush_final",
                textList,
                speakId=speakId,
                lang=self.last_lang,
                synth=id(synth),
            )
            _execWhenDone(self.wait_speak, synth, textList[:])
            textList = []
        _logSayAll("uml_speak_notify_done", speakId=speakId)
        _execWhenDone(self.notify_done)

    def wait_speak(self, synth, seq):
        self._debugWaitSeq += 1
        waitId = self._debugWaitSeq
        _logSayAll(
            "wait_speak_enter",
            seq,
            force=True,
            waitId=waitId,
            synth=id(synth),
            markerSupport=self._markerSupportBySynth.get(id(synth), True),
            sayAllRunning=_isSayAllRunning(),
            queueDepth=bgQueue.unfinished_tasks,
        )
        # If seq contains no text (e.g. only IndexCommands from a language switch
        # boundary), skip the engine entirely and fire index notifications directly.
        # Without this, two deadlocks can occur:
        #   1. Some engines (OneCore, SAPI5) never fire synthDoneSpeaking for
        #      text-less sequences, so done.wait() blocks forever.
        #   2. HISS may run indexReached synchronously on the calling thread when
        #      there is no text, which re-enters self.lock and deadlocks.
        if not any(isinstance(item, str) for item in seq):
            _write_debug_log(
                "wait_speak_textless",
                thread=threading.current_thread().name,
                waitId=waitId,
                synth=id(synth),
                indexes=_summarizeSequence(seq)["indexes"],
            )
            for item in seq:
                if isinstance(item, IndexCommand):
                    synthDriverHandler.synthIndexReached.notify(
                        synth=self, index=item.index
                    )
            return
        synthKey = id(synth)
        useMarker = self._markerSupportBySynth.get(synthKey, True)
        markerIndex = None
        markerEvent = None
        markerIsInternal = False
        seqToSpeak = list(seq)
        if useMarker:
            textChars = sum(len(item) for item in seq if isinstance(item, str))
            indexTimeoutSec = self._estimateIndexTimeoutSec(textChars)
            markerEvent = threading.Event()
            trailingIndex = seq[-1] if seq and isinstance(seq[-1], IndexCommand) else None
            if trailingIndex and not self._isInternalIndex(trailingIndex.index):
                # Reuse NVDA's own trailing index when available. Appending an internal
                # marker after it can cause some synths to skip the external end index,
                # which then stalls say-all progression on structured content.
                markerIndex = trailingIndex.index
                _logSayAll(
                    "wait_speak_marker_reuse_external",
                    seq,
                    force=True,
                    waitId=waitId,
                    markerIndex=markerIndex,
                    timeoutSec=indexTimeoutSec,
                    synth=id(synth),
                )
            else:
                # IMPORTANT: We must correlate completion per utterance (not only per synth),
                # otherwise stale done events from a prior utterance can release this wait
                # too early (same synth object). See addon/doc/en/dev-utterance-completion.md.
                markerIndex = self._allocateInternalIndex()
                markerIsInternal = True
                seqToSpeak.append(IndexCommand(markerIndex))
                _logSayAll(
                    "wait_speak_marker_allocated",
                    seq,
                    force=True,
                    waitId=waitId,
                    markerIndex=markerIndex,
                    timeoutSec=indexTimeoutSec,
                    synth=id(synth),
                )
        with self.lock:
            self.done.clear()
            self._cancelWaitEvent.clear()
            self._activeWaitIndex = markerIndex
            self._activeWaitIndexEvent = markerEvent
            self._activeWaitIndexIsInternal = markerIsInternal
            self.cur_synth = synth
            self._applySynthSettings(synth)
            _logSayAll(
                "wait_speak_call_synth",
                seqToSpeak,
                force=True,
                waitId=waitId,
                markerIndex=markerIndex,
                synth=id(synth),
            )
            synth.speak(seqToSpeak)
        try:
            if useMarker:
                markerWaitResult = self._waitForMarkerOrCancel(
                    markerEvent, indexTimeoutSec
                )
                _write_debug_log(
                    "wait_speak_marker_result",
                    thread=threading.current_thread().name,
                    waitId=waitId,
                    markerIndex=markerIndex,
                    result=markerWaitResult,
                    synth=id(synth),
                )
                if markerWaitResult == "marker":
                    self._markerSupportBySynth[synthKey] = True
                    return
                if markerWaitResult == "cancel":
                    return
                # Fallback path if a backend fails to report index callbacks.
                self._markerSupportBySynth[synthKey] = False
                log.warning(
                    "wait_speak marker timeout: synth=%s marker=%d timeout=%.2fs; falling back to done event"
                    % (
                        synth,
                        markerIndex,
                        indexTimeoutSec,
                    )
                )
            self.done.wait()
        finally:
            with self.lock:
                if self._activeWaitIndex == markerIndex:
                    self._activeWaitIndex = None
                    self._activeWaitIndexEvent = None
                    self._activeWaitIndexIsInternal = False
            _write_debug_log(
                "wait_speak_exit",
                thread=threading.current_thread().name,
                waitId=waitId,
                markerIndex=markerIndex,
                synth=id(synth),
            )

    def cancel(self):
        _write_debug_log(
            "cancel",
            thread=threading.current_thread().name,
            sayAllRunning=_isSayAllRunning(),
            curSynth=id(self.cur_synth) if self.cur_synth else "-",
            queueDepth=bgQueue.unfinished_tasks,
            force=True,
        )
        try:
            while True:
                item = bgQueue.get_nowait()
                bgQueue.task_done()
        except queue.Empty:
            pass

        for v in self.synthInstanceMap.values():
            v.cancel()
        self.lastindex = None
        self._cancelWaitEvent.set()
        self.done.set()

    def on_done(self, synth):
        if synth == self:
            return
        _write_debug_log(
            "on_done",
            thread=threading.current_thread().name,
            synth=id(synth),
            curSynth=id(self.cur_synth) if self.cur_synth else "-",
            matched=bool(synth == self.cur_synth),
            sayAllRunning=_isSayAllRunning(),
            force=True,
        )
        with self.lock:
            if synth == self.cur_synth:
                self.done.set()

    def notify_done(self):
        _write_debug_log(
            "notify_done",
            thread=threading.current_thread().name,
            sayAllRunning=_isSayAllRunning(),
            force=True,
        )
        synthDriverHandler.synthDoneSpeaking.notify(synth=self)

    def on_index(self, synth=None, index=None):
        if synth == self:
            return
        _write_debug_log(
            "on_index",
            thread=threading.current_thread().name,
            synth=id(synth) if synth else "-",
            index=index,
            activeWaitIndex=self._activeWaitIndex,
            activeWaitIndexIsInternal=self._activeWaitIndexIsInternal,
            curSynth=id(self.cur_synth) if self.cur_synth else "-",
            isInternal=self._isInternalIndex(index),
            sayAllRunning=_isSayAllRunning(),
            force=True,
        )
        with self.lock:
            if (
                index == self._activeWaitIndex
                and synth == self.cur_synth
                and self._activeWaitIndexEvent is not None
            ):
                self._activeWaitIndexEvent.set()
        if self._isInternalIndex(index):
            return
        # We dont' care which synth this came from, pass it on
        synthDriverHandler.synthIndexReached.notify(synth=self, index=index)

    def _allocateInternalIndex(self):
        idx = self._nextInternalIndex
        self._nextInternalIndex += 1
        if self._nextInternalIndex > 0x7FFFFFFF:
            self._nextInternalIndex = self._internalIndexBase
        return idx

    def _waitForMarkerOrCancel(self, markerEvent, timeoutSec):
        deadline = time.monotonic() + timeoutSec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            if markerEvent.wait(timeout=min(0.05, remaining)):
                return "marker"
            if self._cancelWaitEvent.is_set():
                return "cancel"

    def _isInternalIndex(self, index):
        return isinstance(index, int) and index >= self._internalIndexBase

    @staticmethod
    def _estimateIndexTimeoutSec(textChars):
        # Scale timeout by text length to cover slower engines, with reasonable bounds.
        return max(2.0, min(30.0, 2.0 + (float(textChars) / 12.0)))

    @property
    def language(self):
        return self.last_lang

    def _get_volume(self):
        return self._volume

    def _set_volume(self, value):
        self._volume = value
        self._applySettings()

    def _get_rate(self):
        return self._rate

    def _set_rate(self, value):
        self._rate = value
        self._applySettings()

    def _get_rateBoost(self):
        return self._rateBoost

    def _set_rateBoost(self, value):
        self._rateBoost = bool(value)
        self._applySettings()

    @staticmethod
    def _clampPercent(value):
        return max(0, min(100, int(value)))

    def _getOffset(self, kind, lang):
        try:
            return int(config.conf["UML_global"]["%sOffset_%s" % (kind, lang)])
        except Exception:
            return 0

    def _isLikelyHiss(self, lang, synth):
        identifier = self.synthIdentifierMap.get(lang, "")
        if isinstance(identifier, str) and "hiss" in identifier.lower():
            return True
        synthClass = getattr(synth, "__class__", None)
        synthHints = [
            getattr(synth, "name", ""),
            getattr(synth, "description", ""),
            getattr(synthClass, "__name__", ""),
            getattr(synthClass, "__module__", ""),
        ]
        return "hiss" in " ".join(str(item) for item in synthHints).lower()

    def _getRateBoostMode(self, lang, synth):
        try:
            currentRateBoost = getattr(synth, "rateBoost")
        except Exception:
            currentRateBoost = None
        if isinstance(currentRateBoost, bool):
            return "boolean"
        if isinstance(currentRateBoost, (int, float)):
            return "numeric"
        if self._isLikelyHiss(lang, synth):
            return "numeric"
        return None

    def _applyLangSettings(self, lang, synth):
        eff_rate = self._clampPercent(self._rate + self._getOffset("rate", lang))
        eff_vol = self._clampPercent(self._volume + self._getOffset("volume", lang))
        try:
            synth.rate = eff_rate
        except Exception:
            pass
        rateBoostMode = self._getRateBoostMode(lang, synth)
        if rateBoostMode == "boolean":
            try:
                synth.rateBoost = self._rateBoost
            except Exception:
                pass
        elif rateBoostMode == "numeric":
            targetRateBoost = eff_rate if self._rateBoost else 0
            try:
                synth.rateBoost = targetRateBoost
            except Exception:
                pass
        try:
            synth.volume = eff_vol
        except Exception:
            pass

    def _applySynthSettings(self, synth):
        for lang, candidate in self.synthInstanceMap.items():
            if candidate is synth:
                self._applyLangSettings(lang, synth)
                return

    def _applySettings(self):
        if not hasattr(self, "synthInstanceMap") or not self.synthInstanceMap:
            return
        for lang, synth in self.synthInstanceMap.items():
            self._applyLangSettings(lang, synth)


def stringsplit(s, last_lang, strategy):
    """Processes speaking text. Returns a newly generated part of SpeechSequence."""
    if s.strip() == '':
        return []

    url_matches = list(_URL_RE.finditer(s))
    if not url_matches:
        return _stringsplit_text(s, last_lang, strategy)

    lst = []
    start = 0
    current_lang = last_lang
    for match in url_matches:
        if match.start() < start:
            continue
        if match.start() > start:
            part = _stringsplit_text(s[start:match.start()], current_lang, strategy)
            lst.extend(part)
            current_lang = _last_lang_from_sequence(part, current_lang)
        url_end = _extend_url_end(s, match.end())
        url = _normalize_url_for_speech(s[match.start():url_end])
        # Keep URLs intact. Splitting inside a URL can prevent the speech
        # dictionary from seeing the whole link and can leave path fragments.
        lst.extend([LangChangeCommand(_umlCodes.ENGLISH), url])
        current_lang = _umlCodes.ENGLISH
        start = url_end

    if start < len(s):
        lst.extend(_stringsplit_text(s[start:], current_lang, strategy))
    return lst


def _stringsplit_text(s, last_lang, strategy):
    return stringsplit_word(s, last_lang) if strategy == "word" else stringsplit_sentence(s, last_lang)


def _last_lang_from_sequence(seq, fallback):
    for item in reversed(seq):
        if isinstance(item, LangChangeCommand):
            return item.lang.split("_")[0] if item.lang else fallback
    return fallback


def _extend_url_end(s, end):
    while True:
        match = _URL_FOLDED_CONTINUATION_RE.match(s, end)
        if match is None:
            return end
        end = match.end()


def _normalize_url_for_speech(url):
    return _URL_FOLDED_BREAK_RE.sub("", url)


def _text_ends_with_url(s):
    end = len(s.rstrip())
    if end == 0:
        return False
    for match in _URL_RE.finditer(s[:end]):
        if _extend_url_end(s, match.end()) == end:
            return True
    return False


def _starts_with_url_continuation(s):
    match = _URL_ITEM_CONTINUATION_RE.match(s)
    if match is None:
        return False
    continuation = match.group(0)
    return len(continuation) > 1 or s == "/"


def _merge_split_url_strings(seq):
    merged = []
    for item in seq:
        if (
            isinstance(item, str)
            and merged
            and isinstance(merged[-1], str)
            and _text_ends_with_url(merged[-1])
            and _starts_with_url_continuation(item)
        ):
            merged[-1] += item
            continue
        merged.append(item)
    return merged


def _strong_language_kind(char):
    """Return a language only for Japanese characters or ASCII letters."""
    if char2kind(ord(char)) == _umlCodes.JAPANESE:
        return _umlCodes.JAPANESE
    if ('A' <= char <= 'Z') or ('a' <= char <= 'z'):
        return _umlCodes.ENGLISH
    return None


def _is_part_of_multi_dot_number(s, start, end):
    """Do not reinterpret version numbers or IP-like dotted sequences as decimals."""
    has_numeric_part_before = (
        start >= 2 and s[start - 1] == '.' and s[start - 2].isdigit()
    )
    has_numeric_part_after = (
        end + 1 < len(s) and s[end] == '.' and s[end + 1].isdigit()
    )
    return has_numeric_part_before or has_numeric_part_after


def _number_context_language(s, start, end, last_lang):
    # A directly attached Japanese counter or unit is more tightly bound to the
    # number than an English label on the left. Keep the number and suffix in
    # the same Japanese chunk (for example, "pending: 72件").
    if any(s.startswith(suffix, end) for suffix in _JAPANESE_NUMBER_SUFFIXES):
        return _umlCodes.JAPANESE

    # Prefer the nearest real language character on the left. Neutral characters
    # such as whitespace, punctuation, brackets, and other numbers are ignored.
    for pos in range(start - 1, -1, -1):
        kind = _strong_language_kind(s[pos])
        if kind is not None:
            return kind

    # A number at the beginning of a text item has no left context. In that case,
    # use the nearest real language character on the right.
    for pos in range(end, len(s)):
        kind = _strong_language_kind(s[pos])
        if kind is not None:
            return kind

    return last_lang


def _number_spans(s, last_lang):
    spans = {}
    english_ordinals = {
        match.start(): match
        for match in _ENGLISH_ORDINAL_RE.finditer(s)
    }
    for match in _NUMBER_RE.finditer(s):
        start, end = match.span()
        ordinal_match = english_ordinals.get(start)
        if ordinal_match is not None:
            # Keep English ordinals such as "2nd" intact even when they follow
            # Japanese text. NVDA applies speech dictionaries after UML splits
            # the sequence, so splitting "2nd" into "2" and "nd" prevents a
            # dictionary entry for the complete ordinal from matching.
            spans[start] = (
                ordinal_match.end(),
                _umlCodes.ENGLISH,
            )
            continue
        if _is_part_of_multi_dot_number(s, start, end):
            continue
        spans[start] = (
            end,
            _number_context_language(s, start, end, last_lang),
        )
    return spans


def stringsplit_word(s, last_lang):
    if s.strip() == '':
        return []

    lst = []
    start = 0
    number_spans = _number_spans(s, last_lang)
    if 0 in number_spans:
        lastkind = number_spans[0][1]
    else:
        # set kind using the first char
        lastkind = str2kind(s, 0, last_lang)

    pos = 0
    while pos < len(s):
        number_span = number_spans.get(pos)
        if number_span is not None:
            number_end, kind = number_span
            if kind != lastkind:
                lst.extend([LangChangeCommand(lastkind), s[start:pos]])
                lastkind = kind
                start = pos
            pos = number_end
            continue

        c = s[pos]
        u = ord(c)
        if u == 32:
            pos += 1
            continue  # spaces don't change anything
        kind = str2kind(s, pos, lastkind)
        if kind != lastkind:  # switched languages
            lst.extend([LangChangeCommand(lastkind), s[start:pos]])
            lastkind = kind
            start = pos
        # end kind is changed?
        pos += 1
    # end while
    # The final piece of text that wasn't inserted yet
    lst.extend([LangChangeCommand(lastkind), s[start:]])
    return lst


def stringsplit_sentence(s, last_lang):
    if s.strip() == '':
        return []

    lst = []
    start = 0
    found = _umlCodes.ENGLISH

    for pos, c in enumerate(s):
        u = ord(c)
        if u == 32:
            continue  # spaces don't change anything
        kind = char2kind(u)
        if kind != _umlCodes.ENGLISH:
            found = kind
            break
        # end found non-fallback
    # end enumerate
    lst.extend([LangChangeCommand(found), s])
    return lst


def modseq(seq, last_lang, strategy):
    """NVDA's LangChangeCommand only refers markup information like html lang attribute. We want more dynamic change. Process the input sequence and insert LangChangeCommand here."""
    newseq = []
    for item in _merge_split_url_strings(seq):
        # NVDA uses every IndexCommand for callback delivery and utterance boundaries.
        # Dropping adjacent indexes can break say-all continuation on structured content.
        if isinstance(item, LangChangeCommand):
            # Prioritize original LangChangeCommand
            continue
        if isinstance(item, str):
            # Convert some chars which some Japanese synths cannot read properly
            item = item.translate(jpn_translate)
            newseq.extend(stringsplit(item, last_lang, strategy))
        else:
            newseq.append(item)
    return newseq


jpn_translate = {
    0x2170: 0x2160,
    0x2171: 0x2161,
    0x2172: 0x2162,
    0x2173: 0x2163,
    0x2174: 0x2164,
    0x2175: 0x2165,
    0x2176: 0x2166,
    0x2177: 0x2167,
    0x2178: 0x2168,
    0x2179: 0x2169,
}


def char2kind(u):
    """Returns kind of character, which is represented by a unicode codepoint. Used as an internal function from str2kind."""
    if (u >= 0x3000 and u <= 0x30ff) or (u >= 0x4e00 and u <= 0x9fbf) or (u >= 0xff00 and u <= 0xffef) or (u >= 0x2160 and u <= 0x2169):
        return _umlCodes.JAPANESE
    # end japanese
    # Currently, non-Japanese is considered as English. Add logic when supporting other languages.
    return _umlCodes.ENGLISH


def str2kind(s, idx, lastkind):
    """Returns kind at the specified index of the specified string."""
    char = ord(s[idx])
    if char >= 0x30 and char <= 0x39:
        # Use the last language for numbers
        return lastkind
    return char2kind(ord(s[idx]))


def hookedSpeak(speechSequence, symbolLevel=None, priority=speech.Spri.NORMAL):
    if UMLInstance is None:
        return origSpeak(speechSequence, symbolLevel, priority)
    seq = modseq(speechSequence, UMLInstance.last_lang, UMLInstance.strategy)
    _logSayAll(
        "hooked_speak",
        seq,
        force=True,
        sayAllRunning=_isSayAllRunning(),
        symbolLevel=symbolLevel,
        priority=priority,
        inputItems=len(speechSequence) if speechSequence is not None else 0,
        outputItems=len(seq),
    )
    origSpeak(seq, symbolLevel, priority)
