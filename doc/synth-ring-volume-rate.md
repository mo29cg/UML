# UML Synth Ring Volume/Rate Feature

## Goal

Add master volume and rate controls to UML's NVDA synth settings ring, with optional per-voice offsets configurable in the UML settings dialog.

Formula: `effective = clamp(master + offset[lang], 0, 100)`

Rate boost is intentionally excluded from unification — HISS uses a numeric value (0-100), OneCore uses a boolean. Incompatible types.

## Architecture

UML is a language-routing wrapper, not a real synth. It delegates speech to underlying NVDA synth drivers (HISS for Japanese, OneCore for English). NVDA's synth settings ring directly calls `synth.rate = X` / `synth.volume = X` on the active synth driver.

### Key NVDA internals

- `supportedSettings` tuple controls what appears on the synth ring.
- `AutoPropertyObject` metaclass wires `_get_X` / `_set_X` methods into property descriptors.
- `initSettings()` is called by NVDA after `__init__()` returns; it reads saved values from `config.conf["speech"][synthName]` and triggers the setters.
- Must use `synthDriverHandler.SynthDriver.VolumeSetting()` in class body (can't reference own class during definition).
- NVDA 2025.x uses `VolumeSetting()` / `RateSetting()`, NOT the old `VolumeSettingSlider`.

### Implementation in UML.py

- `supportedSettings = (VolumeSetting(), RateSetting())` — appears on synth ring.
- `_get_volume` / `_set_volume` / `_get_rate` / `_set_rate` — store master value, call `_applySettings()`.
- `_applySettings()` — iterates all underlying synths, reads per-voice offsets from `config.conf["UML_global"]`, applies `clamp(master + offset, 0, 100)` to each. Called from setters on MainThread.
- `_applySynthSettings(synth)` — applies to ONE synth right before speaking on UML's BgThread. Same offset logic.
- `self._volume = 100`, `self._rate = 50` initialized in `__init__` after synthInstanceMap is built.

### Config (confspec)

Added to both `UML.py` and `__init__.py`:
```
"volumeOffset_ja": "integer(default=0, min=-100, max=100)"
"volumeOffset_en": "integer(default=0, min=-100, max=100)"
"rateOffset_ja":   "integer(default=0, min=-100, max=100)"
"rateOffset_en":   "integer(default=0, min=-100, max=100)"
```

Stored in `config.conf["UML_global"]`.

### Settings dialog (settings.py)

"Voice adjustments" section with `wx.SpinCtrl` for each language's volume and rate offset (-100 to +100). `GetData()` returns offset values. `_saveSettings()` in `__init__.py` writes them and calls `synth._applySettings()` immediately.

## Files modified

- `addon/synthDrivers/UML.py` — synth driver (main changes)
- `addon/globalPlugins/UML/__init__.py` — settings save/load, confspec mirror
- `addon/globalPlugins/UML/settings.py` — dialog UI for offsets

## Status

- Volume/rate on synth ring: **working**
- Per-voice offsets: **working** (UI, storage, application)
- OneCore (English) rate/volume: **working**
- HISS (Japanese) rate/volume: **working at DLL level** (confirmed via trace — see `hiss-rate-investigation.md`)
- Debug logging still present in UML.py — needs cleanup before release
