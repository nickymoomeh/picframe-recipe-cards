# Focused image-CDN robots redirect fix (v0.4.1)

The Good Food image CDN's robots file redirects to an API Gateway hostname with an unresolvable `www.` prefix. Direct recipe image requests can return 200, including WebP content from a .jpg URL. The former client therefore discarded every uncached photo before attempting the valid image URL.

## Explicit, narrow operator setting

```toml
allow_broken_cdn_robots_redirect = false
```

The default remains strict. To opt into the known-fault exception for your personal frame, change the setting to `true` in `~/.config/recipe-card/config.toml`.

The client still requests the CDN robots file. The exception activates only when all of these are true:

1. The request was for `https://images.immediate.co.uk/robots.txt`.
2. The client followed the exact published redirect to `https://www.fh8mfb9c5i.execute-api.eu-west-1.amazonaws.com/robots.txt`.
3. That redirected request failed specifically with a DNS resolution error.
4. The operator explicitly enabled the setting.

It does not rewrite the hostname, use the alternate endpoint that returns 403, ignore a valid policy, crawl the CDN, change the user agent to impersonate a browser or follow image redirects. A successful image response alone does not activate the exception. There is no general `respect_robots_txt=false` setting.

Current valid robots rules are saved atomically under the configured state directory in `cdn-robots.json`. When the known fault occurs in exception mode, previously saved rules still apply, including Disallow and request spacing. Old cached restrictions are not discarded to make downloads work; corrupt cached policies fail closed. No synthetic Allow policy is created from a network failure. With no cached policy, the explicitly enabled exception authorizes the application's narrowly constrained request path; it is not treated as robots-derived permission.

Generic DNS failures, timeouts, SSL errors, unexpected redirects, malformed policies and other robots failures do not qualify. HTTP 401/403/429 on robots or images still stop downloads; image access/rate restrictions are never bypassed or retried. The application still accepts only the known HTTPS Good Food image host/path and photos obtained from matching Recipe structured data. The recipe website's robots checks are always enforced.

This enabled mode is an explicit operator policy exception, **not** strict RFC 9309 compliance. [RFC 9309 section 2.3.1.4](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.4) specifies fail-closed behaviour for unreachable robots files. Retaining a strict default and clearly recording the operator exception avoids misrepresenting an inaccessible file as permission. This setting does not confer content rights or override authentication/access controls.

## Update and preview on the Pi

Run manually; these instructions do not enable any service:

```sh
cd /home/pi4/picframe-recipe-cards
git pull --ff-only
.venv/bin/python -m pip install .
nano ~/.config/recipe-card/config.toml
```

Add the following while keeping your existing settings:

```toml
allow_broken_cdn_robots_redirect = true
queue_enabled = false
```

If those keys already exist, edit them rather than adding duplicate TOML keys. Keeping queueing false lets you test the photo fix without posting cards, including if an already-enabled recipe timer is checking the configuration.

```sh
.venv/bin/recipe-card validate-config
.venv/bin/recipe-card refresh
.venv/bin/recipe-card list
.venv/bin/recipe-card generate --output /home/pi4/recipe-preview.png
```

A warning records use of the explicitly enabled exception once per refresh. If the CDN later returns valid rules, those rules take precedence even with the flag still true. Set the flag back to false whenever you want strict mode. Existing cached photos remain available offline in either mode.

Live verification before publication successfully refreshed four real recipe photos and generated a real Sloe gin 1920 × 1200 card with the exact recipe QR. Card design, discovery, URL/photo matching, metadata/photo caches, snapshot cleanup, operating window and alternating weather coordination were not changed. No Pi service was enabled or restarted, and the original photos were not added to GitHub.
