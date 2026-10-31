# Alternating weather and recipe cards

## Decision

Use **one display scheduler: picframe3's existing slideshow controller**. Add the small optional `card_sequence: [weather, recipe]` policy included in `integration/picframe-card-sequence.patch`. No shared coordinator, new daemon, network data source or additional frame dependency is required. Both card generators remain independent applications.

The frame already knows when a card has actually been decoded and shown, when a slide ends, how many photos have appeared, and whether playback is paused or the display is off. An external wall-clock coordinator would need to infer those facts, recover from missed active-card polls and cope with an asynchronous enqueue API. Putting a small ordering rule beside the existing photo counter is simpler and more reliable.

The optional patch is based on the existing card feature branch at `04d9aa13068263073fc1bb4a7e2a6d2d599f6e36`; the repository's original main branch did not have the card implementation. The patch is prepared for review and testing, **not installed on the Pi**.

## Exact normal-playback behaviour

```text
Weather, 30 s → 8 photos × 20 s → Recipe, 45 s → 8 photos × 20 s → Weather …
```

Weather-to-recipe card starts are approximately **190 seconds apart (3:10)**; recipe-to-weather starts are approximately **205 seconds apart (3:25)**. A full weather/recipe cycle is about **395 seconds (6:35)**, plus configured fades and normal loading delays. Each individual type appears about every 6½ minutes. The display therefore changes to a different information card approximately every 3–4 minutes.

`card_every: 8` counts photo slides, not seconds. Paired portraits count as one slide. Videos finish normally and do not count towards the eight; long videos, pauses, explicit navigation and display-off periods change the wall-clock result. Photo interval must also be `interval: 20`; changing card_every alone does not set photo duration. The first card after startup waits for the existing photo countdown; no forced slideshow navigation is used.

## Why the current timers are insufficient

The inspected frame stores pending cards in a dictionary by type. Re-queueing weather replaces the pending weather snapshot in place; it does not add another weather entry or move its FIFO position. That gives bounded pending slots and basic fairness, **not a strict alternating display rule**. The old recipe cadence of fifteen minutes cannot produce the requested sequence. Offsetting two repeating timers cannot reliably account for fades, slow photos, videos, pause/resume, reboot or failed submissions.

The frame patch chooses only the next configured type at a natural card boundary. After a successful weather display, the next eligible type becomes recipe, regardless of how often weather is queued. A second pending weather waits through the recipe turn and its following photo gap. At most one pending entry per type remains the existing queue rule.

The turn and most recent shown path for each type are stored in the frame's **existing library metadata**, not a new database. A normal restart keeps the next type and last-shown receipts; the pending queue remains memory-only, as before. The frame exposes `sequence`, `next_type`, `last_shown`, `pending_types`, `session` and `sequence_error` in the existing `card` state object. Recipes can therefore acknowledge a matching displayed snapshot even if their one-minute poll misses the 45-second active card.

An empty `card_sequence` retains the existing FIFO behaviour. With a sequence configured, only those types are accepted, and `card_consecutive: true` is refused. Invalid/unreadable persisted sequence state stops information cards while ordinary photographs continue. This protects against accidental repeated weather cards or silent order resets.

## Each application's responsibility

| Component | Responsibility | Timing |
| --- | --- | --- |
| picframe3 | Choose next type, count eight photos, display for supplied duration, persist turn/receipt | Existing slideshow loop |
| Weather generator | Refresh its data and replenish `type: weather` | Existing three-minute queue cadence; duration 30 s |
| Recipe generator | Refresh catalogue, rotate recipes, replenish only an eligible recipe turn | Existing one-minute check; metadata every 12 h; duration 45 s |

These refresh/check timers do not compete for display order. **No new coordinator timer is introduced.** Leave the weather service's existing timer and three-minute queue configuration alone. It can keep replacing its single pending weather slot safely; the frame determines when that slot may be shown.

Recipe `queue_mode = "frame_sequence"` ignores the old fifteen-minute `queue_minutes` setting. On each one-minute tick it checks the frame's next turn and exact receipt. It creates one immutable recipe snapshot only when the frame expects recipe and no recipe is pending or unresolved. It does not keep replacing an accepted recipe while paused. A 60-second submission safety throttle is not a display interval.

When the frame session changes after restart, the generator releases its lost pending request and can supply a newly selected recipe for the persisted recipe turn. A matching last-shown receipt is resolved first. Cleanup uses these exact receipts/session changes instead of the old empty-queue grace-period inference.

## Failure policy

**Strict alternation takes priority over substituting the wrong type.** If the required recipe or weather image is missing, invalid or unavailable, photos continue and the frame waits for that type. The other pending type is not displayed twice in a row. After the missing producer recovers and supplies a usable card, playback resumes from the waiting turn. Cached recipe metadata and downloaded original images allow the normal recipe cycle during internet outages.

No design can preserve strict alternation and also continue weather cards indefinitely while no recipe is available. This implementation makes that tradeoff explicit instead of silently breaking the sequence. At least two usable cached recipes are required for ongoing non-repeated recipe rotation.

A definite HTTP 4xx rejection releases that recipe attempt for the next check. An ambiguous POST timeout is not retried blindly: wait for a matching display receipt or a new frame session. If a request was lost before enqueue and no receipt ever arrives, stop the recipe timer and deliberately restart the frame at a convenient time to establish a fresh session; this also clears pending weather cards, which its producer replenishes. This conservative failure mode prevents speculative duplicate requests. It cannot promise exactly-once delivery across arbitrary network, disk or process failures.

The cursor is persisted after the renderer accepts the displayed card. A crash between display and the database commit can lose that newest turn; display and durable storage cannot form one atomic transaction. A persistence error stops further information cards, logs a clear error and leaves photos running. Do not discard the library state merely to hide such an error. After repairing storage, inspect the saved next turn before resetting anything.

Submission obeys 07:00–20:30 Europe/London. The existing frame operating-hours setting controls actual display power. Already-pending cards have no expiry in this API and may remain through a display-off period. A restart resets the photo countdown, not the persisted card type turn.

## Test without enabling scheduling

1. Review `gallery/demo-1920x1200.png` and generate your own cache-only previews. Queueing is **disabled by default** (`queue_enabled = false`). No service has been enabled by this work.
2. Set up a test checkout of the frame **card feature branch**, and check the patch before applying:

   ```sh
   cd /path/to/frame-test-checkout
   git rev-parse HEAD
   git apply --check /path/to/picframe-recipe-cards/integration/picframe-card-sequence.patch
   git apply /path/to/picframe-recipe-cards/integration/picframe-card-sequence.patch
   python -m pytest -q tests/test_cards.py tests/test_card_sequence.py tests/test_config.py tests/test_frame_core.py tests/test_uischema.py
   ```

   Use the frame's development environment with its existing web/test dependencies installed. A changed installed version may require adapting the patch; do not apply it blindly to a different base. The patch adds one small policy module, optional configuration, controller hooks and tests; it does not change weather source code.
3. Merge `integration/frame-settings.example.yaml` into the **test** frame config, retaining all other settings. Verify `interval: 20`, `card_every: 8`, `card_consecutive: false` and `card_sequence: [weather, recipe]`. Do not replace the entire configuration with the fragment.
4. In recipe configuration, keep `queue_mode = "frame_sequence"`, refresh the public recipe/photo cache and verify at least two usable recipes. `queue --dry-run` makes no frame contact or state changes. Leave `queue_enabled = false` until you deliberately choose to test real queueing.
5. For a supervised real test only, set recipe `queue_enabled = true`, keep the recipe timer disabled and manually invoke `recipe-card queue` when `/api/state` reports `card.next_type: recipe`. A queue while the frame wants weather safely skips. The weather timer can remain unchanged. Observe weather → eight photos → recipe → eight photos → weather. A second recipe appearance requires another manual `queue` after the following weather appears. Test pause/resume and a deliberate frame restart as well.
6. Only after you approve the result, manually enable the supplied **existing recipe timer** as described in README. It is the only recipe timer required. Do not create another queue timer or coordinator.

To revert: disable recipe queueing, stop the recipe timer, let pending recipe cards drain or deliberately restart the frame, remove the sequence config and reverse the patch in the matching checkout with `git apply -R`. Default FIFO is then restored. Keep the old library sequence record unless deliberately resetting the cycle; it is ignored with sequencing disabled. Do not delete weather files or alter shared lingering.
