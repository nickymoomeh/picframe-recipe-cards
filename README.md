# picframe-recipe-cards

Standalone Python recipe cards for a Raspberry Pi 4, Debian 13 ARM64 and picframe3. Renders 1920 × 1200 PNGs with Pillow, keeps a persistent offline catalogue and submits immutable snapshots through the existing local command API. No Docker, database server, browser or resident generator daemon.

![Demonstration card](gallery/demo-1920x1200.png)

The pictured recipe, description, times and rating are **fictional fixture data**. The image is AI-generated and is not Good Food photography. The demonstration QR is a recipe-link example; the fixture does not describe that published recipe. Do not import demonstration data into your live catalogue.

## Updated scheduling: weather → recipes → weather

The recommended configuration now implements:

**Weather 30 s → eight 20 s photographs → Recipe 45 s → eight photographs → repeat.**

One scheduler decides displays: picframe3's existing slideshow controller, with the opt-in `card_sequence` rule supplied in [integration/picframe-card-sequence.patch](integration/picframe-card-sequence.patch). The weather generator keeps its existing three-minute replenishment; the recipe service checks each minute and supplies its next turn. Both remain standalone. No shared coordinator or new display timer is needed.

Card starts are approximately 190/205 seconds apart (3:10/3:25), plus fades. Each type appears about every 6½ minutes. The old fifteen-minute recipe queue interval is ignored in `frame_sequence` mode. Default FIFO cannot guarantee this sequence on its own.

**Queueing is disabled by default, and nothing has been enabled or installed on your Pi.** Review [the design, failure policy and manual test procedure](docs/SCHEDULING.md) before applying the optional frame patch. It persists the next card type in the frame's existing library and exposes last-shown receipts so normal restarts and missed active-card polls do not reset alternation. If a required card is unavailable, photographs continue while that turn waits; another weather card is not substituted.

## Recipe selection

The catalogue now mixes three verified sources while keeping the existing design and scheduling:

- **Trending/popular:** only the actual Trending Recipes section on News and Trends, plus recipe cards from up to two collections in the recipe hub's labelled popular-collections section.
- **New:** direct recipe links in the `/recipes` hub's **Latest added recipes…** section. General guides, news and reviews are excluded; no “new” status is inferred from a modification date.
- **Seasonal:** the collection for the current month in Europe/London, plus a collection from the hub's current Seasonal inspiration section. Optional `seasonal_urls` can override/augment these; set `seasonal_auto = false` to use only explicit URLs.

Candidates are interleaved two trending/popular, one new and one seasonal, deduplicated by canonical recipe URL and limited to `max_recipes` recipe-page checks per refresh. Empty categories yield their slots to the others. Collection traversal is bounded and first-page only; there is no broad site crawl or pagination. A broken section is logged while healthy sources remain usable; if none yields verified recipes the old cache is retained.

Every live candidate must have a direct `/recipes/<slug>` link and matching Recipe structured-data identity/canonical page. Its image is read from that exact Recipe object. Collection heroes, article images, related-recipe images and generic substitutes are never used. In original-photo mode, old local-image overrides do not replace that verified recipe image. Existing cached recipes and rotation history need no migration. New cards use the same layout with a `NEW` category label.

## Original Good Food photographs

The defaults now use **public Good Food metadata and the original recipe photographs for your personal frame**: `source_mode = "goodfood"`, `image_source = "goodfood"`. No local image catalogue or repeated permission confirmation is required. The small image URL in each recipe's structured data identifies its original CDN asset; thumbnail resize parameters are removed, the original photo is downloaded once, then a frame-sized copy is cached for offline use. Cards retain Good Food source attribution and a recipe QR link. No ingredients or instructions are reproduced.

Downloads use the known HTTPS Good Food image CDN only, with size/decode limits, timeouts, spacing, bounded retries and robots checks. Cached photos are reused without repeat image requests. Local photo overrides remain available. See [source notes](docs/SOURCES.md).

**Live verification found a CDN issue:** its robots file redirects to a hostname that currently fails DNS resolution. The client follows that published redirect and retains cached photos if crawl rules cannot be retrieved. Consequently the live test could not download new photos; no successful live-photo preview is claimed. A previously cached or manually saved original photo remains usable offline.

## Install and inspect a preview

Clone the repository to `/home/pi4/picframe-recipe-cards`. Run as `pi4`:

```sh
sudo apt update
sudo apt install -y python3-venv fonts-dejavu-core git
git clone https://github.com/nickymoomeh/picframe-recipe-cards.git /home/pi4/picframe-recipe-cards
cd /home/pi4/picframe-recipe-cards
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/recipe-card preview --fixture examples/recipes.json --output /home/pi4/recipe-demo.png
```

`preview` needs no config, cache or network. `generate` later renders cached data without fetching or queueing. Both write outside the configured live output directory so queued images cannot be overwritten accidentally. No privileges are required for normal operation; apt and initial lingering setup need sudo.

## Configure and import recipes

```sh
mkdir -p ~/.config/recipe-card ~/.local/state/recipe-card /home/pi4/FrameCards
cp config.example.toml ~/.config/recipe-card/config.toml
cp examples/recipes.empty.json ~/.config/recipe-card/recipes.json
nano ~/.config/recipe-card/config.toml
nano ~/.config/recipe-card/recipes.json
.venv/bin/recipe-card validate-config
.venv/bin/recipe-card refresh
.venv/bin/recipe-card list
.venv/bin/recipe-card generate --output /home/pi4/recipe-preview.png
.venv/bin/recipe-card status
.venv/bin/recipe-card queue --dry-run
```

Keep `queue_enabled = false` while reviewing previews. In the default live mode the manifest is optional: `refresh` discovers recipes and downloads their original photos. In local mode an empty catalogue cannot queue anything. **At least two usable recipes** are needed for ongoing rotation: a one-recipe catalogue is shown once and then skipped until another becomes available, preventing immediate repeats across restarts.

The manifest has `schema: 1` and a `recipes` array. A single entry looks like this; replace the examples with verified, permitted metadata:

```json
{
  "title": "Your chosen recipe title",
  "url": "https://www.bbcgoodfood.com/recipes/your-chosen-slug",
  "description": "Your own short description or permitted source description.",
  "prep": "15m",
  "cook": "40m",
  "difficulty": "Easy",
  "rating": 4.6,
  "rating_count": 25,
  "category": "trending",
  "image": "photos/my-dish.jpg",
  "image_permission": "owned",
  "credit": "Photo: your name"
}
```

The JSON example is for optional local mode (`source_mode = "local"`); in that mode title, URL, local image and image permission are required. Omit unknown metadata rather than inventing it. Categories are `trending`, `new` or `seasonal`. `image_permission` is `owned`, `licensed` or `permission`, reflecting rights you actually hold. Put required image attribution/licence in `credit` (maximum 140 characters/two rendered lines; excessive credit is rejected, not silently truncated). Relative photo paths resolve against the manifest directory; absolute paths work. Local images are EXIF-corrected and normalized into the persistent JPEG cache. Duplicate canonical recipe URLs and identical image content are deduplicated.

The manifest is optional in live mode. With `image_source = "goodfood"`, photos always come from matching recipe metadata and local-image overrides are ignored. Explicit `image_source = "local"` remains available for manual imports/overrides. Seasonal sources may be recipe collections or legacy labelled sections. Recipes with missing/inaccessible photos are skipped; if all discovery sources fail, the previous cache is retained.

## Configuration and CLI

Defaults and comments are in [config.example.toml](config.example.toml). Configuration defaults to `~/.config/recipe-card/config.toml`; explicit `--config PATH` goes **before** the command. Missing explicit files, unknown keys and invalid types are errors. Paths must be absolute or start with `~`. The supplied Debian configuration selects DejaVu Sans; unconfigured fonts have a scalable Pillow fallback.

| Setting | Default / behaviour |
| --- | --- |
| `queue_enabled` | **false** until you deliberately test real queueing |
| `queue_mode` | `frame_sequence`; requires the included optional frame patch |
| `duration` | 45 seconds, after frame fade-in |
| `refresh_hours` | 12, including overnight; last attempt persisted on failure |
| `recipes_url`, `include_new` | `/recipes` hub and newly added recipes enabled |
| `include_trending`, `include_seasonal`, `seasonal_auto` | All enabled; bounded popular collections and current-month seasonal discovery |
| `queue_minutes` | 15; used only in legacy `independent` mode |
| `window_start`, `window_end` | 07:00 inclusive to 20:30 exclusive |
| `timezone` | Europe/London, including BST/GMT |
| `max_recipes` | 24, configurable up to 100 |
| `retention_days`, `max_snapshots` | 1 day and 256 PNGs |
| `request_timeout`, `request_spacing` | 10 seconds and at least 2 seconds |

Equal window endpoints mean all day; overnight windows work. UTC epochs determine intervals, local time determines eligibility. Missed scheduled runs coalesce without backlog. A long metadata refresh can delay replenishment; the frame continues photos if a turn is not ready. It never substitutes the wrong type. Metadata refresh does not rewrite queued images.

| Command | Effect |
| --- | --- |
| `validate-config` | Validate configuration; no HTTP |
| `refresh` | Refresh Good Food metadata/original photos, or import an optional local catalogue |
| `generate --output PATH` | Cache-only preview outside live output directory |
| `preview --fixture PATH --output PATH` | Labelled fixture preview with no cache or network |
| `queue` | Replenish an eligible recipe turn; respects enabled flag/window/rotation |
| `tick` | Due refresh, then eligible replenishment |
| `status` | Offline cache age/count, schedules, queue mode and last accepted request |
| `list` | Cached titles, categories and URLs |

Every command accepts `--dry-run` **after** its name. Dry runs make no HTTP calls, render no files and change no state. A queue dry run cannot promise admission without inspecting the real frame. `queue`/`refresh` exit 0 on success, 1 on skipped/failed work; operational/config errors exit 2. `tick` handles outages without restart loops and logs diagnostics. `status`/`list` are read-only.

For authenticated local frame APIs set `RECIPE_CARD_AUTHORIZATION` to the complete Authorization header value, or add it to `~/.config/recipe-card/environment` and `chmod 600` that file. Credentials are not committed or placed in state/config. API requests are restricted to loopback HTTP and refuse redirects/proxy environment variables. Keep FrameCards outside scanned photo directories, and ensure the frame user can read its images/parents.

Legacy `queue_mode = "independent"` retains interval-based recipe admission for an unpatched frame; it **does not meet strict alternation** and is only for compatibility. Drain unresolved legacy requests before switching modes. The new mode fails closed if its frame sequence/receipt contract is absent or unhealthy.

## Manual frame integration and later scheduling

Follow [docs/SCHEDULING.md](docs/SCHEDULING.md) to inspect/apply the patch to a matching **test checkout** and merge [the YAML fragment](integration/frame-settings.example.yaml). No existing frame configuration is overwritten by this repository. Weather source code, service units and queue interval require no changes; check its duration is 30 seconds. The frame uses `card_every: 8`, `interval: 20`, `card_consecutive: false` and `card_sequence: [weather, recipe]`.

After reviewing recipe previews, you may manually set `queue_enabled = true` and test `recipe-card queue` while keeping the recipe timer disabled. It skips safely until the frame expects recipe. Observe the sequence and verify physical readability, QR scanning and pause/restart behaviour before enabling unattended checks.

Only after you approve testing, run these yourself:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/recipe-card.service systemd/recipe-card.timer ~/.config/systemd/user/
systemctl --user daemon-reload
sudo loginctl enable-linger pi4
systemctl --user enable --now recipe-card.timer
systemctl --user list-timers recipe-card.timer
systemctl --user status recipe-card.timer
journalctl --user -u recipe-card.service -n 50
# Stop scheduled recipe checks:
systemctl --user disable --now recipe-card.timer
```

The oneshot timer checks each minute without overlapping itself. A lock excludes concurrent mutating CLI commands. No extra recipe queue timer or coordinator is needed. The unit restricts writes to state and FrameCards; create both beforehand. If paths/user change, update config, WorkingDirectory, ExecStart and ReadWritePaths together. Logs go to the user journal. The development Mac cannot exercise systemd boot/lingering or physical Pi rendering.

## Reliability and recovery

Recipe metadata/state/PNG publication uses same-directory temporary files, file fsync, atomic rename and directory fsync. Invalid refreshes never replace the validated catalogue. A backup catalogue is tried if the primary is corrupt. Cached images survive loss of original files/internet access. Offline cache remains usable indefinitely with its age reported in status. No usable cache means no recipe request, while photos continue.

Recipe snapshots have unique, immutable paths. Frame sequence receipts identify the exact last recipe path shown; normal frame session changes identify lost memory-only pending requests. An ambiguous POST response remains unresolved until receipt/restart, rather than generating speculative duplicates. Only resolved journal-owned snapshots older than retention are deleted; unknown, pending, symlinked and weather files remain. The hard file cap defers new work rather than deleting uncertain requests. The default cycle produces about 123 recipe snapshots/day in the display window; 256 slots accommodate one-day retention. Increase the cap if shortening photo intervals or extending retention. Images are normalized/deduplicated with current/backup references retained and staging leftovers reclaimed on subsequent refresh. Preview files are user-managed.

Frame display acceptance and disk persistence cannot be one atomic transaction: a crash between them can lose the newest saved turn. Persistence errors stop information cards while leaving photos running. The detailed failure/recovery policy is in SCHEDULING.md. No claim is made of exactly-once display under arbitrary hardware/network failures or strict expiry of a pending card at 20:30.

A corrupt recipe journal fails closed; refresh/previews still work. Stop the recipe timer, drain pending recipes or deliberately restart the frame, then archive the corrupt queue.json and inspect orphan recipe PNGs before resuming. A new journal resets recipe rotation; it does not reset the frame's persisted next-type cursor. Do not delete state/files while pending requests could still reference them.

Troubleshooting: start with `status`, `list` and the user journal. Check two usable recipes, fonts, image access, authentication and matching frame patch/config. “Waiting for weather” and “previous recipe display receipt” are normal deferrals. A missing recipe delays cards, not photos. A missing heading/JSON-LD or 401/403/429 stops source retrieval; use local mode rather than bypassing restrictions. For an unresolved lost POST, use the supervised fresh-frame-session procedure in SCHEDULING.md.

## Development, updating and removal

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

See [verification results](docs/VERIFICATION.md). Recipe tests mock HTTP. The optional frame patch includes its own tests against the inspected controller, with a CI job pinned to its base commit. Physical viewing at ten feet, phone QR scanning and unattended systemd operation still require your Pi test. Title typography is the main distant-reading element; attribution/QR are for closer viewing.

For updates, disable recipe checks, back up config/state, update/reinstall, validate and preview before resuming. Uninstall by disabling/stopping recipe-card.timer/service, removing their two user unit files, then running `systemctl --user daemon-reload`. Remove checkout/config/state and inspected recipe snapshots only after pending requests drain. Revert the optional frame patch/config as described in SCHEDULING.md if required. Leave weather files/services and shared lingering alone.

Code: MIT. User content retains its original rights. This independent project is not affiliated with Good Food.
