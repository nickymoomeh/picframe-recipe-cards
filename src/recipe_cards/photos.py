"""Original Good Food CDN photographs, cached for the user's personal frame."""
import hashlib
import io
import logging
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, ProxyHandler
from urllib.robotparser import RobotFileParser

from PIL import Image, ImageOps

from .source import AGENT, Redirects
from .storage import atomic

LOG = logging.getLogger(__name__)
HOST = 'images.immediate.co.uk'
ROBOTS_REDIRECT = 'https://www.fh8mfb9c5i.execute-api.eu-west-1.amazonaws.com/robots.txt'


class PhotoRedirects(Redirects):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url == f'https://{HOST}/robots.txt' and newurl == ROBOTS_REDIRECT:
            # Follow the CDN's published robots location only, never an image
            # redirect or an arbitrary redirect supplied by external metadata.
            from urllib.request import HTTPRedirectHandler
            return HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def original_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError('Invalid recipe photograph URL')
    u = urlsplit(value)
    if (u.scheme != 'https' or u.hostname != HOST or u.username or u.password
            or u.port not in (None, 443)
            or not re.fullmatch(r'/production/volatile/sites/30/[A-Za-z0-9/_-]+\.(?:jpe?g|png|webp)', u.path, re.I)
            or '..' in u.path):
        raise ValueError('Photograph must be a public Good Food image on its known HTTPS CDN')
    # The page's small thumbnail is a normal CDN transform of this same asset.
    # Fetch its original image, retaining aspect ratio when normalizing/cropping.
    return urlunsplit(('https', HOST, u.path, '', ''))


def fetch_bytes(url, timeout, limit):
    opener = build_opener(ProxyHandler({}), PhotoRedirects())
    with opener.open(Request(url, headers={'User-Agent': AGENT}), timeout=timeout) as response:
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Photograph/robots response exceeds size limit')
        return raw


class PhotoClient:
    def __init__(self, cfg):
        self.cfg, self.robots, self.last = cfg, None, None
        self.robots_error = None
        self.spacing = cfg.request_spacing

    def _get(self, url, limit):
        for attempt in range(2):
            if self.last is not None:
                time.sleep(max(0, self.spacing - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                return fetch_bytes(url, self.cfg.request_timeout, limit)
            except HTTPError as exc:
                if exc.code < 500 or attempt:
                    raise
            except (URLError, OSError):
                if attempt:
                    raise
            time.sleep(2 ** (attempt + 1))
        raise ValueError('Photograph request failed')

    def _start(self):
        rules = RobotFileParser()
        try:
            raw = self._get(f'https://{HOST}/robots.txt', 128_000).decode('utf-8')
            if 'user-agent:' not in raw.lower():
                raise ValueError('Invalid CDN robots response')
            rules.parse(raw.splitlines())
        except HTTPError as exc:
            # A genuinely absent robots file imposes no crawl rules. Access
            # restrictions and rate limits are never treated as permission.
            if exc.code not in (404, 410):
                raise
            rules.parse(['User-agent: *', 'Allow: /'])
        self.robots = rules
        rate = self.robots.request_rate(AGENT)
        self.spacing = max(self.spacing, self.robots.crawl_delay(AGENT) or 0,
                           rate.seconds / rate.requests if rate and rate.requests > 0 else 0)

    def cache(self, url):
        url = original_url(url)
        folder = self.cfg.state / 'images'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (hashlib.sha256(url.encode()).hexdigest() + '.jpg')
        if target.is_symlink():
            raise ValueError('Refusing symlinked image cache')
        if target.is_file():
            try:
                with Image.open(target) as image:
                    image.load()
                return str(target)
            except (OSError, ValueError):
                LOG.warning('Repairing damaged cached photograph %s', target.name)
        if len(list(folder.glob('*.jpg'))) >= 310 and not target.exists():
            raise ValueError('Image cache safety limit reached')
        if self.robots_error:
            raise ValueError(self.robots_error)
        if self.robots is None:
            try:
                self._start()
            except (OSError, ValueError) as exc:
                self.robots_error = f"CDN crawl rules unavailable; cached photos retained: {exc}"
                raise
        if not self.robots.can_fetch(AGENT, url):
            raise ValueError('CDN robots.txt does not permit this photograph')
        try:
            raw = self._get(url, 20_000_000)
        except HTTPError as exc:
            if exc.code in (401, 403, 429):
                self.robots_error = f"CDN refused image access (HTTP {exc.code}); stopping downloads for this refresh"
            raise
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 24_000_000:
                raise ValueError('Photograph exceeds 24 megapixels')
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1920, 1600), Image.Resampling.LANCZOS)
            atomic(target, lambda f: image.save(f, format='JPEG', quality=94))
        return str(target)
