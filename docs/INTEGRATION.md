# Integration evidence and delivered changes

Reference repositories inspected at:

- [Frame card branch](https://github.com/nickymoomeh/Digital-Picture-Frame-Pi-2026/tree/04d9aa13068263073fc1bb4a7e2a6d2d599f6e36), especially `src/picframe3/slideshow.py`, `control/http.py`, `library/db.py`, configuration and `docs/CARDS.md`.
- [Weather generator](https://github.com/nickymoomeh/picframe-weather-cards/tree/f8dd3d2ccb8168cac5ed0045637853604bc5865b), especially service, client, CLI, configuration and user units.

The weather checkout's shipped cadence differs from the user's actual three-minute configuration. No weather source/configuration/unit was edited. The solution preserves that live three-minute weather replenishment with a 30-second duration.

## Existing behaviour

Pending cards are an insertion-ordered dictionary keyed by type. Queueing a pending type replaces its path/duration without moving its position. Different types share a 64-slot cap and photo countdown. Each successful card display resets that countdown. Photos, including paired portraits, count once; videos do not. Default non-consecutive cards require another full photo gap.

The original `/api/state` exposes an active type and pending count but not a pending-type list, saved next type, or per-path display receipt. HTTP command success means asynchronous acceptance, not proof that a card displayed. Therefore distinct types and independent timers alone do not enforce a strict repeating weather/recipe order.

## Optional patch

`integration/picframe-card-sequence.patch` adds:

- `slideshow.card_sequence`, default empty, preserving existing FIFO unless explicitly configured. With `[weather, recipe]`, the controller selects only the expected type at the ordinary card boundary. No new timer or forced navigation is added.
- A small `CardSequence` module with validation and a cursor saved through the existing library metadata API. No new database or dependency. The turn changes only after successful decode/render acceptance. A normal restart retains it.
- Additive state fields: `sequence`, `next_type`, `last_shown` (last displayed path per type), `sequence_error`, `pending_types`, and a controller `session` ID. The last-shown receipt is retained after the active card ends and through restarts.
- Local-YAML-only policy configuration; invalid/conflicting policy/state errors stop information cards while leaving normal photographs running. Initialization and receipt updates are protected across HTTP/display threads.
- Controller/persistence/failure/restart tests and a short addition to the frame's CARDS.md. Existing FIFO card tests remain unchanged and passing.

The patch is prepared against the exact feature-branch commit above. It is delivered for local review/application; it was not deployed or published. Apply-checking against a clean copy of its base commit is part of delivery verification. The supplied YAML is a fragment to merge, never a replacement frame configuration.

## Recipe service update

Default `queue_mode` is now `frame_sequence`, with `queue_enabled = false`. The minute tick imports due metadata and checks whether the frame's next turn is recipe. It ignores `queue_minutes` in this mode, creates at most one unresolved immutable request, and uses an exact matching last-shown path to release it. A new controller session proves that previous in-memory pending work has been lost; persisted receipts are checked before abandoning that work.

An unknown pending recipe is not overwritten. A definite HTTP 4xx rejection releases that attempt. Ambiguous replies are not blindly retried: wait for receipt or deliberately establish a new frame session as documented in SCHEDULING.md. The old independent mode remains available explicitly for compatibility and retains its former interval/empty-queue-grace behaviour, but it does not satisfy strict alternation.

With the new sequence mode, cleanup no longer infers successful consumption from an empty queue after a one-minute grace period. Current/unknown pending snapshots remain protected, and a hard cap prevents unlimited files.

## Boundaries

Normal-frame restart recovery is durable; arbitrary crash exactly between visible display and cursor commit is not transactional. A failed persistence write stops further information cards. Pauses, videos, navigation and missing required cards can lengthen the interval. Strict alternation waits for a missing type rather than substituting the other. No existing API supplies pending-card expiry, so operating hours govern submissions and the frame governs display power.

See SCHEDULING.md for the full rationale, manual test process, recovery steps and rollback. No live Pi or scheduled service was changed.
