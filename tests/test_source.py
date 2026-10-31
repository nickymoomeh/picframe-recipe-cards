from dataclasses import replace
import json
from urllib.error import HTTPError, URLError

import pytest

from recipe_cards.model import Recipe, source_url, duration
from recipe_cards import source

URL = "https://www.bbcgoodfood.com/recipes/test"


def markup(obj):
    return '<script type="application/ld+json">' + json.dumps(obj) + '</script>'


def test_jsonld_graph_split_rating_and_skill():
    html = markup({"@graph": [{"@type": ["Recipe"], "@id": URL + "#Recipe", "name": "Peas &amp; mint", "prepTime": "PT10M", "cookTime": "PT1H5M", "description": "A <b>fresh</b> bowl", "image": [{"url": "https://example.com/image.jpg"}]}]})
    html += markup({"@id": URL + "#Recipe", "aggregateRating": {"ratingValue": "4.7", "ratingCount": "28"}})
    html += '<div data-testid="recipe-cook-and-prep-details-skillLevel"><strong>Easy</strong></div>'
    recipe = source.parse_recipe(html, URL)
    assert recipe.title == "Peas & mint"
    assert recipe.prep == "10m" and recipe.cook == "1h 5m"
    assert recipe.rating == 4.7 and recipe.rating_count == 28
    assert recipe.description == "A fresh bowl" and recipe.difficulty == "Easy"
    assert recipe.image_url == "https://example.com/image.jpg"


def test_missing_metadata_and_invalid_scripts():
    r = source.parse_recipe('<script type="application/ld+json">broken</script>' + markup({"@type": "Recipe", "name": "Soup"}), URL)
    assert r.rating is None and r.cook == "" and r.description == "" and r.difficulty == ""
    with pytest.raises(ValueError):
        source.parse_recipe('<title>Not a recipe</title>', URL)


@pytest.mark.parametrize("rating,count,expected", [(0, 0, None), (4.5, None, None), ("nan", 2, None), (6, 2, None), ("4.3", 12, 4.3), (0, 4, 0)])
def test_rating_validation(rating, count, expected):
    assert Recipe.parse(dict(title="Soup", url=URL, rating=rating, rating_count=count)).rating == expected


@pytest.mark.parametrize("url", ["http://www.bbcgoodfood.com/recipes/test", "https://www.bbcgoodfood.com.evil.com/recipes/test", "https://evil.com/recipes/test", "https://user@www.bbcgoodfood.com/recipes/test", "https://www.bbcgoodfood.com/recipes/%2e%2e/test", "https://www.bbcgoodfood.com/recipes/../test", "https://www.bbcgoodfood.com/recipes/collection/a", "file:///etc/passwd"])
def test_reject_urls(url):
    with pytest.raises(ValueError):
        source_url(url)


def test_dedupe_normalize_and_section_isolation():
    html = '<h3>Trending right now</h3><a href="/recipes/outside">bad</a><h3>Trending recipes</h3><article><a href="/recipes/test?x=y">one</a><h4>Title</h4><a href="/recipes/test#x">two</a></article><article>Premium<a href="/recipes/paid">Paid</a></article><a href="https://evil.com/recipes/test">bad</a><h3>Trending videos</h3><a href="/recipes/video">no</a>'
    assert source.discover(html, URL) == [URL]
    with pytest.raises(ValueError):
        source.discover('<h3>Something changed</h3>', URL)


@pytest.mark.parametrize("raw,expected", [("PT45M", "45m"), ("PT1H30M", "1h 30m"), ("P2D", "2d"), (None, ""), ("banana", ""), ("P", "")])
def test_duration(raw, expected):
    assert duration(raw) == expected


def test_paywall_and_scale():
    with pytest.raises(ValueError):
        source.parse_recipe("This is a premium piece of content" + markup({"@type": "Recipe", "name": "Paid"}), URL)
    r = source.parse_recipe(markup({"@type": "Recipe", "name": "X", "aggregateRating": {"ratingValue": 9, "bestRating": 10, "ratingCount": 3}}), URL)
    assert r.rating is None


def test_robots_and_request_spacing(cfg, monkeypatch):
    calls, sleeps = [], []
    def get(url, timeout, payload=None):
        calls.append(url)
        return 'User-agent: *\nDisallow: /recipes/private\nCrawl-delay: 3\n' if url.endswith('robots.txt') else '<h1>OK</h1>'
    monkeypatch.setattr(source, 'request', get)
    monkeypatch.setattr(source.time, 'sleep', sleeps.append)
    c = source.PublicClient(cfg)
    with pytest.raises(ValueError):
        c.get(URL)
    c.start()
    c.get(URL)
    with pytest.raises(ValueError):
        c.get('https://www.bbcgoodfood.com/recipes/private')
    assert len(calls) == 2 and sleeps and c.spacing == 3


@pytest.mark.parametrize('code', [401, 403, 429])
def test_restrictions_are_not_retried(cfg, monkeypatch, code):
    c = source.PublicClient(cfg)
    c.robots = source.RobotFileParser()
    c.robots.parse(['User-agent: *', 'Allow: /'])
    calls = []
    def fail(*args):
        calls.append(1)
        raise HTTPError(URL, code, 'blocked', {}, None)
    monkeypatch.setattr(source, 'request', fail)
    with pytest.raises(HTTPError):
        c.get(URL)
    assert len(calls) == 1


def test_transient_retry_bounded(cfg, monkeypatch):
    c = source.PublicClient(cfg)
    c.robots = source.RobotFileParser()
    c.robots.parse(['User-agent: *', 'Allow: /'])
    calls = []
    def fail(*args):
        calls.append(1)
        raise URLError('offline')
    monkeypatch.setattr(source, 'request', fail)
    monkeypatch.setattr(source.time, 'sleep', lambda _: None)
    with pytest.raises(URLError):
        c.get(URL)
    assert len(calls) == 2


def test_permission_gate_before_network(cfg, entries):
    with pytest.raises(ValueError, match='permission'):
        source.retrieve(replace(cfg, source_mode='goodfood'))


def test_redirects_refused():
    with pytest.raises(ValueError):
        source.Redirects().redirect_request(None, None, 302, '', {}, 'https://evil.com/')
