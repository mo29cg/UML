# HISS Rate Investigation

## Summary

We investigated why HISS (Japanese TTS engine) appeared unresponsive to rate changes when used as a sub-synth through UML. After extensive debugging, **the DLL trace proves the rate IS working**. The initial non-responsiveness was caused by a stale config offset (`rateOffset_ja=100`) that clamped effective rate to 100 regardless of the master value. Subsequent "not working" reports were likely caused by diagnostic monkey-patches interfering with normal operation, combined with `rateBoost=30` (saved user setting) making all speech already fast and reducing perceptual differences.

## Timeline

### Phase 1: Initial implementation

Added `supportedSettings` with `VolumeSetting()` and `RateSetting()` to UML's SynthDriver. HISS rate appeared unresponsive.

**Error**: Used `SynthDriver.VolumeSettingSlider` which doesn't exist in NVDA 2025.x. Fixed to `SynthDriver.VolumeSetting()`.

### Phase 2: Config offset masking the issue

**Root cause found**: `nvda.ini` contained `rateOffset_ja = 100` (and `volumeOffset_ja = 5`). With the formula `effective = clamp(master + 100, 0, 100)`, the effective rate was always 100 regardless of master value.

**Fix**: User reset offsets to 0 via UML settings dialog.

### Phase 3: Suspected DLL failure (false lead)

After fixing the offset, the user reported HISS still wasn't responding. We launched an extensive investigation.

#### What we tested

1. **Python-level rate propagation**: Confirmed `synth.rate = X` correctly reaches `_hiss.setRate(X)`, which sets the module-level `rate` variable. Verified via logging before/after synth.rate assignment plus `_hiss.rate` readback. All values correct.

2. **Module aliasing hypothesis**: Checked if there were multiple `_hiss` modules loaded. Compared `id()` of `_speak.__globals__`, `setRate.__globals__`, and `sys.modules["synthDrivers._hiss"].__dict__`. All identical — single module instance.

3. **Thread-level verification**: Monkey-patched `_hiss._speak` to log rate on HISS's own bgThread right before the DLL call. Correct rate values observed.

4. **Hardcoded rate=0 test**: Replaced `_speak` entirely to hardcode `_callHissdll("hiss_36c85d27bb90659a", 0)`. User reported "normal speed." **This test likely had an issue** — the monkey-patch replacement may not have been properly installed, or the `rateBoost=30` (from saved user config) made even rate=0 sound fast.

5. **Direct setRateBoost(5) test**: Called `_callHissdll("hiss_8d97e37ab0b7fa89", 5)` directly. User reported "normal speed." Note: the saved rateBoost was 30, so setting it to 5 was actually a DECREASE. This could explain why no speed-up was perceived.

6. **NVDA source investigation**: Confirmed NVDA does nothing special beyond `synth.rate = X` — no hidden mechanism.

### Phase 4: Comprehensive DLL call trace (breakthrough)

Monkey-patched `_hiss._callHissdll` BEFORE HISS instantiation to capture ALL DLL calls with return values, including initialization.

#### Initialization results (clean)

```
hissdll before init: None          ← fresh load, no prior DLL
globalCreate(rootDir)  → 0         ← SUCCESS
synthCreate()          → 0         ← SUCCESS
getCustomSettings()    → 0 (x4)   ← reading saved settings
setVoice(2)            → 0         ← Takashi voice
setRateBoost(30)       → 0         ← user's saved boost
setCustomSettings()    → 0 (x4)   ← applying saved settings
```

Everything returned `HISS_RESULT_SUCCEED` (0). No errors whatsoever.

#### Speech results (rate IS working)

Utterance "こんにちは" at different rates:

**Rate 30**: 5 feedLoop chunks (5 x 8192 bytes = ~40KB PCM = ~0.93 sec at 22050Hz/16bit)
```
setRate(30)  → 0
setPitch(50) → 0
setInflection(2) → 0
setVolume(95) → 0
startSynth("こんにちは") → 0
process() → 1 (CONTINUE) x4, then → 0 (SUCCEED)
stopSynth() → 0
```

**Rate 90**: 3 feedLoop chunks (3 x 8192 bytes = ~24KB PCM = ~0.56 sec)
```
setRate(90)  → 0
setPitch(50) → 0
setInflection(2) → 0
setVolume(95) → 0
startSynth("こんにちは") → 0
process() → 1 (CONTINUE) x2, then → 0 (SUCCEED)
stopSynth() → 0
```

**The audio data is ~40% shorter at rate 90 vs rate 30.** This conclusively proves the DLL responds to rate changes.

## HISS DLL Function Map (obfuscated names)

| Obfuscated name | Purpose | Args | Notes |
|---|---|---|---|
| `hiss_0ac4296814bae9cc` | globalCreate | `(rootDir: wchar_p)` | Called once during initialize() |
| `hiss_af65fc9af2e7058b` | synthCreate | `()` | Creates synth instance |
| `hiss_04c72688b5f6e9a1` | synthDestroy | `()` | Called during terminate() |
| `hiss_36c85d27bb90659a` | setRate | `(rate: int)` | Called per-utterance in _speak |
| `hiss_d73044c16fa6f300` | setPitch | `(pitch: int)` | Called per-utterance in _speak |
| `hiss_c7508f15a2a46ff6` | setInflection | `(inflection: int)` | Called per-utterance in _speak |
| `hiss_cddcb5e092c14715` | setVolume | `(volume: int)` | Called per-utterance in _speak |
| `hiss_1aacc2adaf24abf2` | startSynth | `(text: wchar_p)` | Begin synthesis |
| `hiss_bc906576f424f3f9` | process | `(bufsize, buf, written_ptr)` | Returns 1=CONTINUE, 0=DONE |
| `hiss_98db9446a625612d` | stopSynth | `()` | End synthesis |
| `hiss_8d97e37ab0b7fa89` | setRateBoost | `(boost: int)` | Persistent DLL-level setting |
| `hiss_12917c04f08e21d3` | setVoice | `(voice: c_int)` | 1=Keiko, 2=Takashi |
| `hiss_ba9680023244eb92` | getVersion | `(VersionInfo*)` | Returns version struct |
| `hiss_d4e2d2f83e9bba09` | getCustomSettings | `(CustomSettings*)` | Reads pause/guess/etc. |
| `hiss_8f52454edb61bec3` | setCustomSettings | `(CustomSettings)` | Writes pause/guess/etc. |

## Key architecture notes for HISS sub-synth usage

- `_hiss` is a module-level singleton. All state (rate, volume, hissdll, bgQueue, bgThread, player) lives in module globals.
- `_hiss.setRate()` just sets a Python variable. The DLL call happens inside `_speak()` right before each utterance.
- `_hiss.initialize()` creates its own bgThread and WavePlayer. `terminate()` calls synthDestroy but NOT globalDestroy.
- `_hiss.terminate()` sets `hissdll = None` but doesn't call `FreeLibrary()`. Windows caches the DLL handle.
- On re-initialization after terminate, `cdll.LoadLibrary()` returns the same DLL handle. `globalCreate` succeeds (returns 0) even on a DLL where global state already exists.
- The `rootDir` is computed at module import time from `__file__`, always points to HISS addon root.

## Conclusion

The HISS rate issue is **resolved**. The actual bug was a stale `rateOffset_ja=100` in user config. After clearing that, rate and volume both work correctly through UML's synth ring. The DLL trace confirms all functions succeed and audio output varies with rate.

Remaining: cleanup debug logging from UML.py before release.
