# Delivery verification — scheduling update

Verified locally on 31 October 2026, macOS ARM64, Python 3.14.8. No Pi deployment, live frame endpoint, service enablement or weather modification occurred.

## Focused CDN fix — v0.4.1

Completed before publication:

- Added one default-false setting, `allow_broken_cdn_robots_redirect`. Only a typed DNS failure after the exact known CDN robots redirect can activate the explicit exception. No global robots-off switch or hostname rewrite was added.
- Valid robots responses are saved atomically in `cdn-robots.json`; cached rules, including Disallow and crawl spacing, remain enforced during the known fault. Corrupt policies fail closed. Other robots failures and explicit HTTP restrictions remain blocking.
- **139 recipe tests passed**, including strict default failure, explicit known-fault activation, transport-stage classification, current/cached Disallow, corrupt cache, nonmatching errors, image 401/403/429, boolean validation and WebP bytes served from a .jpg URL. The actual recipe/frame integration test was rerun and passed. The unchanged frame patch had 97 passing tests in the prior verification.
- Ran the actual `recipe-card refresh` CLI with a temporary, four-recipe-budget local configuration: original-photo mode and the exception enabled, **queue_enabled=false**. It successfully cached **four usable real recipes/photos**.
- Ran the actual cache-only `generate` CLI. It created a **1920 × 1200 RGB PNG** for [Sloe gin](https://www.bbcgoodfood.com/recipes/sloe-gin) using the image identified by that recipe's JSON-LD (`sloe-gin-5868e97.jpg`). The CDN transformation query was removed for download; normal cached normalization/cropping was preserved.
- Visually inspected the real card. An independent zxing-cpp decode returned the exact recipe URL. No queue journal existed in the temporary state, confirming no queued submission. The real photo/card stays in local user outputs and is excluded from the public repository.

The application was tested from macOS, not remotely executed on the Pi. The Pi diagnosis provided by the user matches the reproduced fault. Nothing on the Pi was deployed, restarted or enabled. The strict default still requires the user to explicitly enable the scoped exception to work through this known robots dependency failure.

Earlier source/photo checks below are retained as historical verification before this fix; their failed live-photo result is superseded by the successful v0.4.1 check above.

## Recipe-source refinement

The existing application was extended without redesigning its renderer or scheduling. Discovery now combines the actual Trending recipes section, up to two popular recipe collections linked by the recipe hub, Latest added recipes on `/recipes`, and current-month/editorial seasonal collections. It validates exact recipe identities and takes each image from the matching Recipe object. General news, guides, reviews, collection heroes and related-recipe images are excluded.

A bounded live metadata check verified **24 genuine recipe pages: 15 trending/popular, 2 newly added and 7 seasonal**. Every candidate's Recipe identity matched its selected URL, and every photo URL came from that matched object. No images were downloaded and no frame command was sent in this check. The previously documented photo-CDN robots/DNS issue remains separate and unresolved; successful photo downloading is not claimed.

New tests cover known section/carousel boundaries, typed collection cards, editorial-list collection links, month rollover in Europe/London, mixed-category priority/deduplication/budgets, strict recipe identity, exclusion of unrelated Recipe images, rejection of missing photos, new-category cache/filter/render support, a partial source failure, and ignoring generic local overrides in original-photo mode. The recipe/frame end-to-end test was rerun successfully. The 97 frame tests from the unchanged patch remain applicable; the patch was not edited in this refinement.

## Original-photo update

The user requested original Good Food photographs for personal frame use. Defaults now retrieve live metadata and original CDN assets without a local manifest or additional confirmation. Queueing remains disabled. Photo tests cover host/path restrictions, full-resolution asset URLs, robots rules/redirect handling, bounded retries, cached/offline reuse, image decoding, access/rate-limit failures and refresh integration.

**Live download check did not succeed:** the CDN robots endpoint redirected to an unresolvable hostname. The client now follows that exact published redirect and fails closed when rules are unavailable, retaining existing cached images. No new live-photo card was generated, and successful real-image retrieval is not claimed. Details are in SOURCES.md. Original Good Food images remain personal runtime assets and are not bundled into the source ZIP.

## Current automated results

- **139 recipe tests passed**: original source, rendering, cache, offline, DST, rotation, atomic-write and snapshot tests, plus alternating-mode contract validation, exact receipts, missed active-card polls, frame restart recovery, ambiguous replies, definite HTTP rejection, unknown pending recipe protection and a deterministic three-hour simulation.
- **97 frame tests passed** across existing cards, new card sequence, configuration, frame core and UI schema. This runs the actual patched SlideshowController with normal test loaders/render mocks and a real temporary library. It covers reversed arrival order, repeated weather replacement, eight-photo spacing, no adjacent cards, invalid/missing expected files, decoder pause, persistence errors, corrupt state, normal restarts and default FIFO compatibility. One dependency deprecation warning from Starlette/httpx remains; no test failed.
- **1 end-to-end test passed**, connecting the real recipe service to the patched frame's actual FastAPI command/state endpoints, command bus, SlideshowController and persisted library through an in-process HTTP test client. It generated immutable recipe PNGs, displayed weather/recipe/weather, acknowledged exact paths and recovered lost pending work after controller restart. No network or real graphics hardware was used.

Total: **237 passing tests across those three runs**. The recipe suite uses mocked HTTP; the end-to-end test uses an in-process API. The physical display is not exercised.

The three-hour timing simulator keeps the weather producer on its existing 180-second cadence and recipes on a 60-second check. With eight 20-second photos, observed card starts alternate weather/recipe with gaps of **190 and 205 seconds** (fades omitted in this deterministic simulator). No speculative duplicate recipe submission is generated while a request remains pending.

## Reproduction

From the recipe repository:

```sh
python -m pytest -q
```

From the patched frame test checkout, with existing web/dev dependencies:

```sh
python -m pytest -q tests/test_cards.py tests/test_card_sequence.py tests/test_config.py tests/test_frame_core.py tests/test_uischema.py
```

With both sources and dependencies available, run the combined test explicitly:

```sh
PYTHONPATH=/path/to/frame/src:/path/to/picframe-recipe-cards/src python -m pytest -q -o asyncio_default_fixture_loop_scope=function /path/to/picframe-recipe-cards/integration/test_frame_recipe_sequence.py
```

CI includes the recipe Python 3.11/3.13 matrix and a frame patch job pinned to the inspected base commit. The remote CI jobs have not been executed in this session.

## Packaging/manual checks

- The optional patch was checked against a clean copy of frame commit `04d9aa13068263073fc1bb4a7e2a6d2d599f6e36`; existing source whitespace was checked with git diff --check.
- Source distribution and platform-independent wheel build successfully. The source ZIP includes code, tests, optional frame patch, mergeable YAML settings, documentation, units and preview. Development environments and build caches are excluded.
- The original installed-CLI smoke checks covered validate-config, local refresh, cache-only generate, status/list, dry runs and fixture preview. This update also validates the disabled-by-default sequence configuration and new status policy.
- The unchanged 1920 × 1200 demonstration PNG was visually inspected in the initial delivery. Its QR independently decoded to `https://www.bbcgoodfood.com/recipes/pumpkin-risotto` using zxing-cpp 3.1.1. Photo, metadata and rating are visibly labelled demonstration data. The Mac preview uses an Arial fallback; Debian config uses DejaVu Sans.
- No weather files, queues, configurations or service definitions were edited. Only a local frame research checkout was patched to test the proposed change; the delivered patch must be reviewed/applied manually.

## Remaining acceptance checks

Verify physical ten-foot readability, phone QR scanning, Pi loading/fade times, videos/portrait pairs, pause/resume, normal frame reboot, configured operating hours and systemd boot/lingering on the actual device. Keep recipe queueing disabled until previews are approved, then perform the supervised manual queue test in SCHEDULING.md before enabling its timer.

The frame has no atomic transaction spanning visible pixels and durable cursor state, nor pending-card expiry. A crash between render acceptance and cursor commit can lose the newest turn; storage errors stop cards. An ambiguous request with no eventual receipt requires deliberate recovery rather than a blind retry. These limits and the strict wait-for-missing-type policy are explicitly documented; exactly-once display under arbitrary failures is not claimed.
