from dataclasses import replace
import io
from pathlib import Path
from urllib.error import HTTPError

from PIL import Image
import pytest

from recipe_cards import photos, service, source
from recipe_cards.storage import save

URL = 'https://images.immediate.co.uk/production/volatile/sites/30/2020/08/dish-abcd.jpg'
REAL_FETCH_BYTES = photos.fetch_bytes


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (100, 80), 'orange').save(out, format='JPEG')
    return out.getvalue()


@pytest.mark.parametrize('url', ['http://images.immediate.co.uk/production/volatile/sites/30/x.jpg', 'https://evil.com/x.jpg', 'https://images.immediate.co.uk.evil.com/x.jpg', 'https://user@images.immediate.co.uk/production/volatile/sites/30/x.jpg', 'https://images.immediate.co.uk/private/a.jpg', 'https://images.immediate.co.uk/production/volatile/sites/30/../x.jpg'])
def test_photo_urls_are_restricted(url):
    with pytest.raises(ValueError):
        photos.original_url(url)


def test_original_not_small_thumbnail():
    assert photos.original_url(URL + '?resize=440,400&quality=90&webp=true') == URL


def test_download_cache_and_offline_reuse(cfg, monkeypatch):
    calls = []
    def fetch(url, timeout, limit):
        calls.append(url)
        return b'User-agent: *\nAllow: /\n' if url.endswith('robots.txt') else jpeg()
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    c = photos.PhotoClient(cfg)
    path = Path(c.cache(URL + '?resize=440,400'))
    assert path.is_file() and len(calls) == 2
    monkeypatch.setattr(photos, 'fetch_bytes', lambda *a: pytest.fail('Cached image fetched twice'))
    assert photos.PhotoClient(cfg).cache(URL) == str(path)


@pytest.mark.parametrize('code', [401, 403, 429])
def test_photo_access_restrictions_not_retried(cfg, monkeypatch, code):
    calls = []
    def fetch(url, *args):
        calls.append(url)
        raise HTTPError(url, code, 'restricted', {}, None)
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    with pytest.raises(HTTPError):
        photos.PhotoClient(cfg).cache(URL)
    assert len(calls) == 1 and not list((cfg.state / 'images').glob('*.jpg'))


def test_cdn_robots_disallow(cfg, monkeypatch):
    calls = []
    def fetch(url, *args):
        calls.append(url)
        return b'User-agent: *\nDisallow: /\n'
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    with pytest.raises(ValueError, match='robots'):
        photos.PhotoClient(cfg).cache(URL)
    assert len(calls) == 1


def test_absent_cdn_robots_and_corrupt_image(cfg, monkeypatch):
    def fetch(url, *args):
        if url.endswith('robots.txt'):
            raise HTTPError(url, 404, 'not found', {}, None)
        return b'not a photograph'
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    with pytest.raises(OSError):
        photos.PhotoClient(cfg).cache(URL)
    assert not list((cfg.state / 'images').glob('*.jpg'))


def test_refresh_uses_original_recipe_photo(cfg, now, monkeypatch):
    cfg = replace(cfg, image_source='goodfood')
    entry = dict(title='Real recipe', url='https://www.bbcgoodfood.com/recipes/test', image_url=URL, credit='Photo: Good Food · bbcgoodfood.com')
    save(cfg.manifest, {'schema': 1, 'recipes': [entry]})
    monkeypatch.setattr(photos, 'fetch_bytes', lambda url,*a: b'User-agent: *\nAllow: /\n' if url.endswith('robots.txt') else jpeg())
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    assert service.refresh(cfg, now)
    recipe = service.cached(cfg)['recipes'][0]
    assert recipe.image_url == URL and Path(recipe.image).exists()
    assert recipe.credit == entry['credit']
    monkeypatch.setattr(photos, 'fetch_bytes', lambda *a: pytest.fail('Unchanged cached photo fetched'))
    assert service.refresh(cfg, now)


def test_live_mode_does_not_require_local_manifest(cfg, monkeypatch):
    cfg = replace(cfg, source_mode='goodfood', automated_metadata_permission=True, image_source='goodfood', include_seasonal=False, include_new=False)
    class Client:
        def __init__(self, cfg): pass
        def start(self): pass
        def get(self, url):
            if url.endswith('news-trends'):
                return '<h3>Trending recipes</h3><a href="/recipes/test">Test</a>'
            return '<script type="application/ld+json">{"@type":"Recipe","name":"Test","url":"https://www.bbcgoodfood.com/recipes/test","image":"'+URL+'"}</script>'
    monkeypatch.setattr(source, 'PublicClient', Client)
    entries, root = source.retrieve(cfg)
    assert entries[0]['image_url'] == URL and entries[0]['credit'].startswith('Photo: Good Food')


def test_published_robots_redirect_only():
    from urllib.request import Request
    req = Request('https://images.immediate.co.uk/robots.txt')
    redirected = photos.PhotoRedirects().redirect_request(req, None, 301, '', {}, photos.ROBOTS_REDIRECT)
    assert redirected.full_url == photos.ROBOTS_REDIRECT
    with pytest.raises(ValueError):
        photos.PhotoRedirects().redirect_request(req, None, 301, '', {}, 'https://evil.com/robots.txt')


def test_failed_robots_fetch_does_not_create_false_deny_state(cfg, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(1)
        raise OSError('robots host unavailable')
    monkeypatch.setattr(photos, 'fetch_bytes', fail)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    c = photos.PhotoClient(cfg)
    with pytest.raises(OSError):
        c.cache(URL)
    assert c.robots is None and len(calls) == 2
    with pytest.raises(ValueError, match='unavailable'):
        c.cache(URL)
    assert len(calls) == 2


def test_image_rate_limit_stops_subsequent_downloads(cfg, monkeypatch):
    calls = []
    def fetch(url, *args):
        calls.append(url)
        if url.endswith('robots.txt'):
            return b'User-agent: *\nAllow: /\n'
        raise HTTPError(url, 429, 'rate limited', {}, None)
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    c = photos.PhotoClient(cfg)
    with pytest.raises(HTTPError):
        c.cache(URL)
    with pytest.raises(ValueError, match='429'):
        c.cache(URL.replace('dish-abcd', 'other'))
    assert len(calls) == 2  # One robots request and one image; no retry or other image.


def broken_redirect():
    import socket
    return photos.BrokenRobotsRedirectError(socket.gaierror(-2, 'known redirect DNS failure'))


def webp():
    out = io.BytesIO()
    Image.new('RGB', (160, 100), 'green').save(out, format='WEBP')
    return out.getvalue()


def test_known_dns_exception_is_explicit_and_decodes_webp(cfg, monkeypatch):
    calls = []
    def fetch(url, *args):
        calls.append(url)
        if url.endswith('robots.txt'):
            raise broken_redirect()
        return webp()  # A .jpg source URL may negotiate WebP bytes.
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    strict = photos.PhotoClient(cfg)
    with pytest.raises(photos.BrokenRobotsRedirectError):
        strict.cache(URL)
    assert len(calls) == 2 and not any(x == URL for x in calls)
    calls.clear()
    enabled = photos.PhotoClient(replace(cfg, allow_broken_cdn_robots_redirect=True))
    target = Path(enabled.cache(URL))
    with Image.open(target) as image:
        assert image.format == 'JPEG' and image.size == (160, 100)
    assert calls == ['https://images.immediate.co.uk/robots.txt'] * 2 + [URL]
    assert enabled.robots is None  # No invented Allow policy.
    assert not (cfg.state / 'cdn-robots.json').exists()


def test_explicit_current_disallow_is_not_overridden(cfg, monkeypatch):
    cfg = replace(cfg, allow_broken_cdn_robots_redirect=True)
    calls = []
    def fetch(url, *a):
        calls.append(url)
        return b'User-agent: *\nDisallow: /production/\n'
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    with pytest.raises(ValueError, match='does not permit'):
        photos.PhotoClient(cfg).cache(URL)
    assert len(calls) == 1
    assert (cfg.state / 'cdn-robots.json').is_file()


def test_cached_disallow_survives_known_dns_failure(cfg, monkeypatch):
    cfg = replace(cfg, allow_broken_cdn_robots_redirect=True)
    save(cfg.state / 'cdn-robots.json', dict(schema=1, origin=photos.HOST,
         text='User-agent: *\nDisallow: /production/\nCrawl-delay: 8\n', retrieved=1))
    calls = []
    def fetch(url,*a):
        calls.append(url)
        raise broken_redirect()
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    c = photos.PhotoClient(cfg)
    with pytest.raises(ValueError, match='does not permit'):
        c.cache(URL)
    assert len(calls) == 2 and c.spacing == 8


@pytest.mark.parametrize('error', [HTTPError('url',403,'forbidden',{},None), HTTPError('url',429,'limited',{},None), TimeoutError('timeout'), OSError('ordinary DNS/network failure')])
def test_exception_never_allows_other_robots_failures(cfg, monkeypatch, error):
    cfg = replace(cfg, allow_broken_cdn_robots_redirect=True)
    calls = []
    def fetch(url,*a):
        calls.append(url)
        raise error
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    with pytest.raises((OSError, ValueError)):
        photos.PhotoClient(cfg).cache(URL)
    assert calls and all(x.endswith('robots.txt') for x in calls)


@pytest.mark.parametrize('code',[401,403,429])
def test_exception_does_not_bypass_image_access_restrictions(cfg, monkeypatch, code):
    cfg = replace(cfg, allow_broken_cdn_robots_redirect=True)
    calls = []
    def fetch(url,*a):
        calls.append(url)
        if url.endswith('robots.txt'):
            raise broken_redirect()
        raise HTTPError(url,code,'blocked',{},None)
    monkeypatch.setattr(photos, 'fetch_bytes', fetch)
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    c = photos.PhotoClient(cfg)
    with pytest.raises(HTTPError):
        c.cache(URL)
    with pytest.raises(ValueError,match=str(code)):
        c.cache(URL.replace('dish-abcd','second'))
    assert len(calls) == 3


def test_corrupt_cached_policy_is_not_discarded_to_enable_exception(cfg, monkeypatch):
    cfg = replace(cfg, allow_broken_cdn_robots_redirect=True)
    cfg.state.mkdir(parents=True)
    (cfg.state / 'cdn-robots.json').write_text('broken')
    monkeypatch.setattr(photos, 'fetch_bytes', lambda *a: (_ for _ in ()).throw(broken_redirect()))
    monkeypatch.setattr(photos.time, 'sleep', lambda _: None)
    with pytest.raises(ValueError,match='invalid'):
        photos.PhotoClient(cfg).cache(URL)


@pytest.mark.parametrize('url,followed,is_known', [('https://images.immediate.co.uk/robots.txt',True,True), ('https://images.immediate.co.uk/robots.txt',False,False), (URL,True,False)])
def test_transport_classifies_only_dns_after_exact_redirect(monkeypatch,url,followed,is_known):
    import socket
    from urllib.error import URLError
    class Opener:
        def __init__(self,redirects): self.redirects=redirects
        def open(self,req,timeout):
            self.redirects.followed_known_robots = followed
            raise URLError(socket.gaierror(-2,'DNS failure'))
    monkeypatch.setattr(photos,'build_opener',lambda proxy,redirects: Opener(redirects))
    with pytest.raises(URLError) as error:
        REAL_FETCH_BYTES(url,10,128000)
    assert isinstance(error.value,photos.BrokenRobotsRedirectError) is is_known


def test_exception_flag_requires_real_boolean(tmp_path):
    from recipe_cards.config import load
    p=tmp_path/'config.toml'
    p.write_text('allow_broken_cdn_robots_redirect = "false"\n')
    with pytest.raises(ValueError,match='allow_broken'):
        load(p)
