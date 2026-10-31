"""Public Good Food recipe metadata retrieval for the personal frame."""
import json
import logging
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from .model import Recipe, source_url, duration

LOG = logging.getLogger(__name__)
AGENT = "picframe-recipe-cards/0.4"


class Redirects(HTTPRedirectHandler):
    def __init__(self, external=False):
        self.external = external

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.external:
            raise ValueError("Redirect refused; review source URL and robots permissions")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url, timeout, payload=None):
    """Bounded reads, no redirects, no implicit proxies or automatic POST retries."""
    headers = {"User-Agent": AGENT, "Accept": "application/json" if payload is not None else "text/html,application/json,text/plain"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1") and os.environ.get("RECIPE_CARD_AUTHORIZATION"):
        headers["Authorization"] = os.environ["RECIPE_CARD_AUTHORIZATION"]
    opener = build_opener(ProxyHandler({}), Redirects())
    with opener.open(Request(url, data=data, headers=headers), timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Response exceeds 2 MB")
        return raw.decode("utf-8")


class PublicClient:
    def __init__(self, cfg):
        self.cfg, self.last, self.robots = cfg, None, None
        self.spacing = cfg.request_spacing

    def get(self, url, robots=False):
        source_url(url, recipe=False)
        if not robots and (self.robots is None or not self.robots.can_fetch(AGENT, url)):
            raise ValueError(f"robots.txt does not permit {url}")
        for attempt in range(2):
            if self.last is not None:
                time.sleep(max(0, self.spacing - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                value = request(url, self.cfg.request_timeout)
                if "captcha" in value.lower() and "<title" in value.lower():
                    soup = BeautifulSoup(value, "html.parser")
                    if soup.title and any(s in soup.title.get_text().lower() for s in ("captcha", "access denied", "just a moment")):
                        raise ValueError("Access protection detected; stopping")
                return value
            except HTTPError as exc:
                # Never retry restrictions, rate limits or paywalls.
                if exc.code < 500 or attempt:
                    raise
            except (URLError, TimeoutError, OSError):
                if attempt:
                    raise
            time.sleep(2 ** (attempt + 1))
        raise ValueError("Request failed")

    def start(self):
        raw = self.get("https://www.bbcgoodfood.com/robots.txt", robots=True)
        if "user-agent:" not in raw.lower():
            raise ValueError("Invalid robots response; refusing retrieval")
        self.robots = RobotFileParser()
        self.robots.parse(raw.splitlines())
        rate = self.robots.request_rate(AGENT)
        rate_spacing = rate.seconds / rate.requests if rate and rate.requests > 0 else 0
        self.spacing = max(self.spacing, self.robots.crawl_delay(AGENT) or 0, rate_spacing)


def discover(markup, base, category="trending"):
    from .discovery import section_links
    return section_links(markup, base, category)


def parse_recipe(markup, url, category="trending", *, require_identity=False):
    soup = BeautifulSoup(markup, "html.parser")
    heading = soup.find("h1")
    if (heading is None and any(x in soup.get_text(" ", strip=True).lower() for x in ("this is a premium piece of content", "this recipe is available in our app"))) or (heading and any(x in heading.get_text(" ", strip=True).lower() for x in ("app-only", "app only"))):
        raise ValueError("Subscription recipe skipped")

    def objects(value):
        if isinstance(value, list):
            for item in value:
                yield from objects(item)
        elif isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "Recipe" or isinstance(kind, list) and "Recipe" in kind:
                yield value
            if "@graph" in value:
                yield from objects(value["@graph"])

    documents = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            documents.append(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            pass

    def fragments(value):
        if isinstance(value, list):
            for part in value:
                yield from fragments(part)
        elif isinstance(value, dict):
            yield value
            if "@graph" in value:
                yield from fragments(value["@graph"])

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in objects(data):
            # Do not synthesize recipe contents from generic page titles.
            obj = dict(obj)
            if obj.get("isAccessibleForFree") in (False, "false"):
                continue
            if require_identity:
                target = source_url(url)
                canonical = soup.find('link', rel='canonical')
                if canonical and source_url(urljoin(url, canonical.get('href', ''))) != target:
                    continue
                page = obj.get('mainEntityOfPage')
                page = page.get('@id') if isinstance(page, dict) else page
                claims = [x for x in (obj.get('url'), obj.get('@id'), page) if x]
                if not claims and canonical:
                    claims = [canonical.get('href')]
                try:
                    matched = claims and all(isinstance(x, str) and source_url(urljoin(url, x)) == target for x in claims)
                except ValueError:
                    matched = False
                if not matched:
                    continue
            identity = obj.get("@id")
            if identity:
                for fragment in fragments(documents):
                    if fragment.get("@id") == identity and "aggregateRating" in fragment:
                        obj["aggregateRating"] = fragment["aggregateRating"]
            if obj.get("url") and source_url(obj["url"]) != source_url(url):
                continue
            skill = soup.find(attrs={"data-testid": "recipe-cook-and-prep-details-skillLevel"})
            difficulty = obj.get("difficulty") or (skill.get_text(" ", strip=True) if skill else "")
            rating = obj.get("aggregateRating") or {}
            if not isinstance(rating, dict):
                rating = {}
            image = obj.get("image", "")
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url", "")
            count = rating.get("ratingCount", rating.get("reviewCount"))
            if isinstance(count, str) and count.isdecimal():
                count = int(count)
            elif type(count) is not int:
                count = None
            value = rating.get("ratingValue")
            try:
                # Preserve the source scale; never pretend a non-five scale is /5.
                if float(rating.get("bestRating", 5)) != 5 or float(rating.get("worstRating", 1)) < 0:
                    value = None
            except (TypeError, ValueError):
                value = None
            return Recipe.parse(dict(title=obj.get("name"), url=url, description=obj.get("description"),
                                     prep=duration(obj.get("prepTime")), cook=duration(obj.get("cookTime")),
                                     total=duration(obj.get("totalTime")), difficulty=difficulty,
                                     rating=value, rating_count=count, image_url=image, category=category))
    raise ValueError("No valid Recipe JSON-LD; skipping instead of guessing")


def manifest(cfg):
    p = Path(cfg.manifest).expanduser()
    if p.stat().st_size > 2_000_000:
        raise ValueError("Manifest exceeds 2 MB")
    raw = json.loads(p.read_text())
    if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("recipes"), list):
        raise ValueError("Manifest must contain schema=1 and recipes array")
    if len(raw["recipes"]) > 100:
        raise ValueError("At most 100 manifest recipes")
    return raw["recipes"], p.parent


def retrieve(cfg):
    if cfg.source_mode == "local":
        return manifest(cfg)
    p = Path(cfg.manifest).expanduser()
    entries, root = manifest(cfg) if cfg.image_source == "local" and p.exists() else ([], p.parent)
    if not cfg.automated_metadata_permission:
        raise ValueError("Live metadata permission has not been confirmed")
    client = PublicClient(cfg)
    client.start()
    from .discovery import section_links, collection_links, month_collection, mix
    groups = {'trending': [], 'new': [], 'seasonal': []}
    pages = {}

    def page(url):
        if url not in pages:
            pages[url] = client.get(url)
        return pages[url]

    def attempt(label, operation):
        try:
            return operation()
        except HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise
            LOG.warning('Discovery source %s unavailable: HTTP %s', label, exc.code)
        except (OSError, ValueError) as exc:
            LOG.warning('Discovery source %s unavailable: %s', label, exc)
        return []

    if cfg.include_trending:
        groups['trending'].extend(attempt('Trending recipes', lambda: discover(page(cfg.source_url), cfg.source_url)))
        collections = attempt('Popular collections', lambda: section_links(page(cfg.recipes_url), cfg.recipes_url, 'collections', collections=True))
        for url in collections[:2]:
            groups['trending'].extend(attempt(url, lambda url=url: collection_links(page(url), url)))
    if cfg.include_new:
        groups['new'].extend(attempt('Latest added recipes', lambda: discover(page(cfg.recipes_url), cfg.recipes_url, 'new')))
    if cfg.include_seasonal:
        urls = list(cfg.seasonal_urls)
        if cfg.seasonal_auto:
            urls.append(month_collection(cfg))
            # The hub's editorial seasonal section updates with the time of year.
            urls.extend(attempt('Seasonal inspiration', lambda: section_links(page(cfg.recipes_url), cfg.recipes_url, 'seasonal', collections=True))[:1])
        for url in dict.fromkeys(urls[:4]):
            if '/recipes/collection/' in url:
                groups['seasonal'].extend(attempt(url, lambda url=url: collection_links(page(url), url)))
            else:  # Backward-compatible manually configured labelled sections.
                groups['seasonal'].extend(attempt(url, lambda url=url: discover(page(url), url, 'seasonal')))
    candidates = mix(groups, cfg.max_recipes)
    if not candidates:
        raise ValueError('No genuine recipe candidates in any enabled source; preserve previous cache')
    result = []
    images = {source_url(x.get("url")): x for x in entries}
    for url, category in candidates:
        try:
            recipe = parse_recipe(page(url), url, category, require_identity=True).asdict()
            if cfg.image_source == 'goodfood':
                from .photos import original_url
                original_url(recipe['image_url'])  # No collection/generic/local substitute in live photo mode.
                recipe['credit'] = 'Photo: Good Food · bbcgoodfood.com'
            else:
                local = images.get(url, {})
                recipe.update({k: local.get(k, '') for k in ('image', 'credit', 'image_permission')})
            result.append(recipe)
        except HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise
            LOG.warning('Skipping recipe %s: HTTP %s', url, exc.code)
        except (OSError, ValueError) as exc:
            LOG.warning('Skipping recipe %s: %s', url, exc)
    if not result:
        raise ValueError('No verified recipe pages with their own photographs; preserve previous cache')
    return result, root
