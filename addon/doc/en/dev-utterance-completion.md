# Developer Note: Per-Utterance Completion Guard

Date: 2026-02-17

## Why this exists

UML routes mixed text across multiple synth backends (for example, OneCore and HISS).
In `word` strategy, one line can become multiple synth chunks.

We had a race where completion for utterance **N-1** could be mistaken as completion for utterance **N** when both chunks used the same synth instance (commonly HISS).

## User-visible symptom

- Occasional noisy / high-pitch / corrupted audio
- More likely when a tiny or effectively silent chunk appears between two chunks for the same synth
- Example trigger shape:
  - HISS chunk (previous line tail)
  - OneCore chunk (short symbol-only piece such as U+2502 `│`)
  - HISS chunk (new Japanese text)

This is timing-sensitive, so reproduction can depend on cursor movement pattern.

## Root cause

Old logic in `wait_speak` / `on_done` used:

- one shared `done` event
- one shared `cur_synth`
- completion match by synth object identity only

That means: if `on_done` for old HISS arrives late while current wait is also for HISS, the current wait can be released early.

## Fix implemented

`wait_speak` now appends an internal `IndexCommand` marker to each utterance and waits for that exact marker callback.

Key properties:

- Completion is correlated to one utterance, not just synth identity.
- Internal markers are intercepted in `on_index` and not forwarded to external listeners.
- If a synth does not emit index callbacks, UML falls back to `done`-based waiting and caches that capability per synth instance.
- Cancel remains responsive while waiting for markers.

## Relevant code

- `SynthDriver.wait_speak`
- `SynthDriver.on_index`
- `SynthDriver.on_done`
- `SynthDriver._waitForMarkerOrCancel`
- `SynthDriver._markerSupportBySynth`

## Design constraints / invariants

- Do not remove per-utterance marker waiting unless replaced by another strict utterance correlation mechanism.
- Matching completion by `synth == cur_synth` alone is insufficient.
- Internal marker IDs must never leak as real NVDA index events.
- Cancel must be able to break waits quickly.

## Why this is better than character sanitization only

Character sanitization can hide specific triggers (for example, replacing box-drawing characters), but the real defect was completion correlation.
This fix addresses the underlying race for all text patterns, including unknown future symbols.

## If you touch this area later

1. Preserve per-utterance completion correlation.
2. Keep fallback behavior for synths without marker support.
3. Test at least:
   - Mixed-language line with short middle chunk
   - Rapid line navigation (up/down)
   - Cancel during speech
4. Verify no internal marker index is announced to users.
