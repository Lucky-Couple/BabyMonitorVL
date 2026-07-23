# Temporal stabilization and browser alarm

## Boundary

The stabilization layer is a deterministic presentation and decision layer over successful, locally validated `FrameAnalysis` values. It does not inspect JPEG pixels, send extra model requests, add cross-frame context to a provider, or modify the original analysis.

Every successful history item therefore contains two separate contracts:

- `analysis`: the canonical single-frame VLM output after coordinate conversion, validation, and exact-coordinate duplicate handling;
- `stabilized`: the derived temporal snapshot at that point in the session.

Provider raw responses remain byte-for-byte preserved. Debug history and the annotated-frame switch let reviewers compare raw and stable boxes directly.

## Alarm hysteresis

`TemporalStabilizer` maintains a rolling window of successful analyses. Provider failures do not count as normal frames and do not advance or clear the signal.

Defaults:

- decision window: five successful analyses;
- enter confirmation: at least three votes in the current window;
- clear hysteresis: three consecutive lower-severity observations;
- alarm-active condition: stable risk is `alert`;
- `watch` remains a prominent review state but does not set `alarm_active=true`.

Each frame receives one derived raw severity from the validated structured fields. Explicit mouth/nose coverage, blanket relationship, prone posture, infant/overall risk, and cat proximity may contribute. An analysis with no infant has `unknown` severity and cannot create a new infant alarm.

The stable state begins at `unknown`/`warming_up`. Three alert votes enter `alert`; three watch-or-alert votes enter `watch`. A current alert or watch is not cleared by a single contradictory frame. It remains until three consecutive observations have lower severity, after which the current window selects the next confirmed severity.

This policy deliberately favors suppression of isolated false positives over fastest possible response. It is not a life-safety policy, and its defaults must not be presented as clinically validated.

## Stable objects and item signals

The layer extracts only boxes already supplied by the VLM:

- infant and mouth/nose;
- adult and cat;
- blanket, pillow, toy, hand, and other occluder.

Within each semantic category, detections are greedily associated to existing tracks by normalized-box IoU. Default minimum IoU is `0.2`. Matched coordinates and confidence use an exponential moving average with default alpha `0.35`. A track is displayed only after the same confirmation count as the alarm signal. It survives fewer than three consecutive misses and is then removed.

There is no pixel comparison, optical flow, appearance embedding, motion model, cross-category matching, box clamping, raw-result suppression, or hidden object fabrication. A rapidly moving object or a large VLM box jump can therefore cause a stable box to lag, disappear, or acquire a new track id. The UI explicitly labels these boxes as stable and lets the reviewer switch back to raw boxes.

Presence signals use per-frame category votes rather than track coordinates. They report `present`, `not_detected`, or `unknown`, plus support/window counts. Adult is presented before cat in the UI, but adult presence does not yet pause inference or suppress risk; that remains a separate product decision.

## API and lifecycle

- `GET /api/alarm` returns the current snapshot and bounded current-session timeline.
- `MonitorStatus.alarm` mirrors the current snapshot.
- `alarm_updated` carries the complete current snapshot after each successful analysis. The browser appends the corresponding point to its bounded local timeline instead of downloading the full timeline after every frame.
- `GET /api/alarm` is used for initial load, WebSocket reconnection resynchronization, and explicit inspection.
- Each successful history item stores its corresponding stabilized snapshot.

Starting a new monitor session clears tracks, votes, and the timeline. Stopping preserves the last current-session view until the next start, but the browser visibly labels and desaturates it as a previous-session result so it cannot be mistaken for an active alarm. The timeline has a separate telemetry bound (`STABILITY_TIMELINE_MAX_POINTS`, default 500); it does not prune image history, whose only limit remains `HISTORY_MAX_BYTES`.

## Configuration

| Variable | Default | Constraint |
| --- | ---: | --- |
| `STABILITY_WINDOW_SIZE` | `5` | `3..120` |
| `STABILITY_CONFIRMATION_FRAMES` | `3` | `2..window` |
| `STABILITY_CLEAR_FRAMES` | `3` | `1..window` |
| `STABILITY_BOX_IOU_THRESHOLD` | `0.2` | `(0,1]` |
| `STABILITY_BOX_EMA_ALPHA` | `0.35` | `(0,1]` |
| `STABILITY_TIMELINE_MAX_POINTS` | `500` | positive integer |

Changing thresholds changes product behavior and requires stabilizer unit tests, frontend contract checks, and a changelog entry. Do not tune against private household frames and then describe the result as generally validated.

## Required regression coverage

- fewer than the confirmation count of isolated alert/watch frames cannot enter that state;
- confirmed alert/watch survives fewer than the clear count of lower-severity frames;
- provider errors cannot silently clear an alarm;
- stable boxes appear only after confirmation, move by the configured EMA, and expire after the configured misses;
- sessions reset all temporal state;
- raw analysis and raw responses are unchanged;
- backend models and TypeScript interfaces stay synchronized;
- the browser visibly distinguishes raw risk/boxes from stable risk/boxes and retains the non-medical warning.
