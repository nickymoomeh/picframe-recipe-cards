# Good Food source and original photographs

Updated for the user's explicit instruction to use Good Food's original photographs on their personal photo frame. Live metadata and original-photo caching are the defaults; no additional permission prompt or local replacement photography is required. Queueing remains disabled until manual testing.

## Public recipe source discovery

Checked 31 October 2026:

- [News and Trends](https://www.bbcgoodfood.com/news-trends): use only its named **Trending recipes** section. Other sections contain articles, reviews and guides; they are not recipe feeds.
- [Recipes hub](https://www.bbcgoodfood.com/recipes): the **Latest added recipes…** section provides newly added public recipe links. Its **Most popular recipe collections this week** carousel provides collection links; expand at most two, using only typed public recipe cards from their results. The hub's **Seasonal inspiration** section supplies current editorial seasonal collections.
- [October recipes](https://www.bbcgoodfood.com/recipes/collection/october-recipes): verified as a genuine month-specific collection with seasonal produce recipes. The application uses the current local month rather than hard-coding October, and handles missing future-month collections as partial discovery failures. It also tries one hub seasonal collection. No seasonal classification is inferred from a generic article or guessed recipe title.

Headings are explicitly recognized and bounded to their own known card block/carousel, with an equal-or-higher-heading boundary as fallback. Collection cards are `article[data-item-type="recipe"]`; navigation, collection hero images, premium/app-only cards and editorial cards are excluded. Only direct HTTPS `/recipes/<slug>` URLs can become candidates. The mixed Atom feed is not used as a recipe/trending source.

Candidate selection takes two popular/trending, one new and one seasonal in turn until the configured recipe-page budget (default 24) is filled. Empty categories yield slots; URLs are globally deduplicated. No pagination or unrestricted crawling occurs. The hub is fetched once per refresh even when used by all categories. At most two popular and four seasonal collection/source pages are inspected. A missing section or collection is logged without discarding other healthy sources. Access restrictions stop retrieval; if nothing verifies, the previous catalogue remains intact.

Each candidate is fetched independently and must expose a Recipe object whose URL/@id/main-page identity (or canonical page identity) matches the selected recipe. The image comes only from that object's `image` field. Images from collection pages, unrelated Recipe objects or generic local overrides cannot silently substitute in original-photo mode. Missing identity/photo causes a skip. Recipe times/difficulty/rating remain optional; no ingredients/instructions are imported. The source categories are `trending` (including popular collections), `new` (membership in Latest added recipes, not guessed publication age) and `seasonal`.

Configuration additions: `recipes_url`, `include_new` and `seasonal_auto`. Existing `source_url`, inclusion flags, manual seasonal sections and old cache/queue state remain compatible. New month choice uses Europe/London, including month rollover during BST. The renderer, QR, photo cache, alternating frame policy, durations and disabled-by-default queueing remain intact.

## Photo handling

The original photo is taken from the recipe's image metadata on `https://images.immediate.co.uk/production/volatile/sites/30/`. CDN thumbnail transformation parameters are removed to obtain that same original asset. There is no photo search or invented substitute. Only this known HTTPS host/path is accepted; credentials, off-site links, traversal and image redirects are refused.

Photos are downloaded once per original asset URL, validated with Pillow, EXIF-corrected and reduced to at most 1920 × 1600 while preserving aspect ratio. Card rendering crops without stretching. Downloads are capped at 20 MB and decoded images at 24 megapixels. A normalized cached JPEG is published atomically; identical original URLs reuse the cache, including when offline. Current/backup references and the existing bounded staging cache control storage. Failed downloads leave the existing catalogue intact.

Cards identify Good Food as the image source and link directly to the recipe by QR. Original photos and generated live cards are personal runtime/cache files, not bundled in the distributable source repository. The code's MIT licence does not transfer ownership of those images. The website footer links to [Immediate's terms](https://www.immediate.co.uk/terms-and-conditions/); this project records the user's personal-use instruction without claiming a separate content licence.

## Access behaviour and live check

The page client respects [site robots rules](https://www.bbcgoodfood.com/robots.txt). The photo client checks the image CDN's robots file, observes Crawl-delay/Request-rate, spaces requests by at least two seconds and retries transient GET failures at most once. It does not retry 401/403/429, use browser impersonation, bypass protections or fetch subscription content.

On 31 October 2026 the image CDN's `/robots.txt` returned a 301 pointing to `https://www.fh8mfb9c5i.execute-api.eu-west-1.amazonaws.com/robots.txt`. That published hostname did not resolve in the live test. The downloader supports this exact robots redirect but fails closed when its rules cannot be retrieved. A probe of the standard hostname without the unexpected www prefix returned 403; that probe is not used as an alternative runtime route. No image was downloaded during this failed live verification. The application will use existing cached photos, or retry new downloads on a later metadata refresh. A manually saved original image can be supplied as a local override if required.

## Demonstration asset provenance

`examples/demo-food.png` was generated with the built-in image generation tool for this project. It is an original AI-generated food image, not a Good Food photograph and not evidence that the fixture recipe exists. The fixture's recipe description, time and rating are fictional and labelled on the card. The repository's software licence does not grant rights to Good Food material or third-party fonts.

Final image prompt:

> Use case: photorealistic-natural. Asset type: original demonstration food photograph for a recipe card software fixture, not a real published recipe. Subject: a rustic ceramic bowl of roasted pumpkin and sage risotto, golden rice, a few roasted pumpkin cubes, crisp sage leaves, subtle steam. Editorial food photography, warm autumn window light, deep natural shadows, dark wooden table, linen napkin. Portrait composition, bowl centered with generous margins so image can crop to 4:5. No text, no logos, no watermark. Generate only the food photograph, no card design.

Card composition, typography, fractional stars, integer-module QR generation and PNG publication are performed deterministically by Pillow/qrcode. The image-generation service is not a runtime dependency and is never called by the Pi application.
