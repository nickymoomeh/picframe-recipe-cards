from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from recipe_cards import source, service
from recipe_cards.discovery import section_links, collection_links, month_collection, mix
from recipe_cards.model import Recipe
from recipe_cards.storage import save

BASE = 'https://www.bbcgoodfood.com'
IMAGE = 'https://images.immediate.co.uk/production/volatile/sites/30/2020/08/exact-dish.jpg'


def section(heading, links, kind='recipe'):
    return '<div data-testid="box"><h3>'+heading+'</h3>'+''.join(
        '<article data-item-type="'+kind+'"><a href="'+u+'"><h4>Recipe</h4></a></article>' for u in links)+'</div>'


def collection(links):
    return '<main><h1>Collection</h1>'+''.join('<article data-item-type="recipe"><a href="'+u+'">Recipe</a></article>' for u in links)+'</main>'


def recipe(url, image=IMAGE):
    return '<link rel="canonical" href="'+url+'"><h1>Recipe</h1><script type="application/ld+json">'+json.dumps(
        {'@type':'Recipe', '@id':url+'#Recipe', 'url':url, 'name':'Exact recipe', 'image':image})+'</script>'


def test_hub_sections_are_isolated_from_guides_and_unlabelled_cards():
    html = section('Most popular recipe collections this week', ['/recipes/collection/popular'], 'editorialList')
    html += '<article data-item-type="editorialList"><a href="/recipes/collection/unlabelled">No</a></article>'
    html += section('Trending guides', ['/howto/guide/recipe-guide'], 'guide')
    html += section('Latest added recipes...', ['/recipes/brand-new', '/review/oven', '/howto/guide/dinner'])
    html += section('Seasonal inspiration', ['/recipes/collection/parsnip-recipes'], 'editorialList')
    assert section_links(html, BASE, 'collections', True) == [BASE+'/recipes/collection/popular']
    assert section_links(html, BASE, 'new') == [BASE+'/recipes/brand-new']
    assert section_links(html, BASE, 'seasonal', True) == [BASE+'/recipes/collection/parsnip-recipes']


def test_collection_only_typed_public_recipe_results():
    html = '<main><h1>October recipes</h1><img src="https://example.com/hero.jpg">'
    html += '<nav><a href="/recipes/navigation">Ignore</a></nav>'
    html += '<article data-item-type="recipe"><a href="/recipes/actual">Actual</a></article>'
    html += '<article data-item-type="recipe">App only<a href="/recipes/paid">Premium</a></article>'
    html += '<article data-item-type="guide"><a href="/recipes/not-a-recipe">Guide</a></article></main>'
    assert collection_links(html, BASE+'/recipes/collection/october-recipes') == [BASE+'/recipes/actual']
    with pytest.raises(ValueError):
        collection_links(html, BASE+'/news-trends')


@pytest.mark.parametrize('instant,month', [('2026-10-31T12:00:00+00:00','october'), ('2026-11-01T00:00:00+00:00','november'), ('2026-07-31T23:30:00+00:00','august')])
def test_season_tracks_current_london_month(cfg, instant, month):
    assert month_collection(cfg, datetime.fromisoformat(instant)).endswith('/'+month+'-recipes')


def test_mixed_priorities_dedupe_and_request_budget():
    groups = {'trending':['t1','t2','t3','same'], 'new':['n1','same','n2'], 'seasonal':['s1','s2','same']}
    result = mix(groups, 8)
    assert result[:4] == [('t1','trending'),('t2','trending'),('n1','new'),('s1','seasonal')]
    assert len(result) == 8 and len({u for u,_ in result}) == 8
    assert mix({'trending':[], 'new':['n'], 'seasonal':[]}, 24) == [('n','new')]


@pytest.mark.parametrize('wrong', [BASE+'/recipes/other', 'https://evil.com/recipes/other'])
def test_recipe_identity_must_match_selected_page(wrong):
    with pytest.raises(ValueError):
        source.parse_recipe(recipe(wrong), BASE+'/recipes/requested', require_identity=True)


def test_recommendation_image_cannot_replace_exact_recipe_photo():
    wanted = BASE+'/recipes/actual'
    unrelated = json.dumps({'@type':'Recipe','url':BASE+'/recipes/recommended', 'name':'Other', 'image':'https://example.com/other.jpg'})
    html = '<script type="application/ld+json">'+unrelated+'</script>'+recipe(wanted)
    value = source.parse_recipe(html, wanted, require_identity=True)
    assert value.url == wanted and value.image_url == IMAGE


def test_missing_recipe_identity_or_premium_recipe_is_rejected():
    raw = '<script type="application/ld+json">'+json.dumps({'@type':'Recipe','name':'Untied'})+'</script>'
    with pytest.raises(ValueError):
        source.parse_recipe(raw, BASE+'/recipes/actual', require_identity=True)
    with pytest.raises(ValueError):
        source.parse_recipe(recipe(BASE+'/recipes/actual').replace('"@type": "Recipe"','"@type": "Recipe", "isAccessibleForFree": false'), BASE+'/recipes/actual', require_identity=True)


def test_new_category_is_cached_filtered_and_renderable(cfg, entries, now, tmp_path):
    item = dict(entries[0], category='new')
    save(cfg.manifest, {'schema':1,'recipes':[item]})
    assert service.refresh(cfg, now)
    r = service.cached(cfg)['recipes'][0]
    assert r.category == 'new'
    from recipe_cards.render import render
    render(cfg, r, tmp_path/'new-card.png')
    assert not service.cached(replace(cfg, include_new=False))['recipes']


def test_three_sources_validate_exact_pages_and_ignore_generic_overrides(cfg, monkeypatch):
    cfg = replace(cfg, source_mode='goodfood', automated_metadata_permission=True, image_source='goodfood', max_recipes=6)
    month = BASE+'/recipes/collection/october-recipes'
    monkeypatch.setattr('recipe_cards.discovery.month_collection', lambda _: month)
    hub = section('Most popular recipe collections this week', ['/recipes/collection/popular'], 'editorialList')
    hub += section('Latest added recipes...', ['/recipes/new-one', '/howto/guide/not-a-recipe'])
    hub += section('Seasonal inspiration', ['/recipes/collection/season'], 'editorialList')
    pages = {cfg.source_url:section('Trending recipes',['/recipes/trend-one']), cfg.recipes_url:hub,
             BASE+'/recipes/collection/popular':collection(['/recipes/popular-one']),
             month:collection(['/recipes/season-one']), BASE+'/recipes/collection/season':collection(['/recipes/season-two'])}
    calls = []
    class Client:
        def __init__(self,cfg): pass
        def start(self): pass
        def get(self,url):
            calls.append(url)
            return pages[url] if url in pages else recipe(url)
    monkeypatch.setattr(source,'PublicClient',Client)
    save(cfg.manifest, {'schema':1,'recipes':[dict(title='Other',url=BASE+'/recipes/new-one',image='generic.jpg',image_permission='owned')]})
    values,_ = source.retrieve(cfg)
    assert {x['category'] for x in values} == {'trending','new','seasonal'}
    assert all(x['image_url'] == IMAGE and x['image'] == '' for x in values)
    assert calls.count(cfg.recipes_url) == 1 and len(calls) <= cfg.max_recipes+5
    assert not any('/howto/' in x for x in calls)


def test_one_broken_source_does_not_discard_healthy_new_recipes(cfg, monkeypatch):
    cfg = replace(cfg,source_mode='goodfood',automated_metadata_permission=True,image_source='goodfood',include_seasonal=False)
    class Client:
        def __init__(self,cfg): pass
        def start(self): pass
        def get(self,url):
            if url == cfg.source_url:
                raise HTTPError(url,404,'gone',{},None)
            if url == cfg.recipes_url:
                return section('Latest added recipes...', ['/recipes/new-one'])
            return recipe(url)
    monkeypatch.setattr(source,'PublicClient',Client)
    values,_ = source.retrieve(cfg)
    assert len(values) == 1 and values[0]['category'] == 'new'


def test_missing_exact_photo_skips_recipe_in_live_mode(cfg, monkeypatch):
    cfg = replace(cfg,source_mode='goodfood',automated_metadata_permission=True,image_source='goodfood',include_new=False,include_seasonal=False)
    class Client:
        def __init__(self,cfg): pass
        def start(self): pass
        def get(self,url):
            if url == cfg.source_url:
                return section('Trending recipes',['/recipes/no-photo'])
            if url == cfg.recipes_url:
                return '<main>No collections</main>'
            return recipe(url, '')
    monkeypatch.setattr(source,'PublicClient',Client)
    with pytest.raises(ValueError,match='own photographs'):
        source.retrieve(cfg)
