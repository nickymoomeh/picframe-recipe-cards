from dataclasses import replace
from pathlib import Path
import os

from PIL import Image, ImageDraw
import pytest

from recipe_cards.model import Recipe
from recipe_cards.render import render, fit, crop_photo, font, wrap
from recipe_cards.storage import atomic, read, save, lock
from recipe_cards import service


def test_long_title_layout(cfg):
    for title in ['A very long title ' * 50, 'W' * 300, 'Pumpkin & sage risotto', 'é' * 200]:
        face, lines, leading = fit(title, cfg, 816, 415, maximum=108, minimum=64)
        assert len(lines) * leading <= 415
        assert all(face.getlength(line) <= 816 for line in lines)
    assert wrap('', font(cfg, 36), 816) == []


def test_render_dimensions_atomic_and_missing_metadata(cfg, tmp_path):
    image = tmp_path / 'food.png'
    Image.new('RGB', (300, 600), 'red').save(image)
    r = Recipe('Soup', 'https://www.bbcgoodfood.com/recipes/soup', image=str(image))
    output = tmp_path / 'preview.png'
    render(cfg, r, output)
    with Image.open(output) as card:
        assert card.size == (1920, 1200) and card.mode == 'RGB'
        assert card.getpixel((1910, 0)) == (255, 0, 0)
    assert not list(tmp_path.glob('.recipe-tmp-*'))
    original = output.read_bytes()
    with pytest.raises(OSError):
        render(cfg, replace(r, image='/does/not/exist.jpg'), output)
    assert output.read_bytes() == original


def test_crop_preserves_geometry(tmp_path):
    image = Image.new('RGB', (800, 400), 'black')
    ImageDraw.Draw(image).ellipse((300, 100, 500, 300), fill='white')
    p = tmp_path / 'circle.png'
    image.save(p)
    cropped = crop_photo(p, (400, 400))
    bounds = cropped.getbbox()
    assert abs((bounds[2] - bounds[0]) - (bounds[3] - bounds[1])) <= 1


def test_atomic_interruption_preserves_previous(tmp_path):
    p = tmp_path / 'cache.json'
    save(p, {'old': True})
    def interrupted(f):
        f.write(b'partial')
        raise OSError('power interruption simulation')
    with pytest.raises(OSError):
        atomic(p, interrupted)
    assert read(p) == {'old': True} and not list(tmp_path.glob('.recipe-tmp-*'))


def test_cache_backup_recovery_and_offline(populated, now, monkeypatch):
    cfg = populated
    assert service.refresh(cfg, now)
    (cfg.state / 'cache.json').write_text('{broken')
    recovered = service.cached(cfg)
    assert len(recovered['recipes']) == 3
    def fail(_):
        raise OSError('offline')
    monkeypatch.setattr(service.source, 'retrieve', fail)
    assert service.refresh(cfg, now) is False
    assert len(service.cached(cfg)['recipes']) == 3
    render(cfg, service.cached(cfg)['recipes'][0], cfg.output / 'offline-preview.png')


def test_bad_entries_keep_old_cache(populated, now):
    cfg = populated
    before = service.cached(cfg)['retrieved']
    save(cfg.manifest, {'schema': 1, 'recipes': [{'title': '', 'url': 'https://evil.com'}]})
    assert service.refresh(cfg, now) is False
    assert service.cached(cfg)['retrieved'] == before


def test_permitted_images_required(cfg, entries, now):
    entries[0]['image_permission'] = ''
    entries[1]['image'] = 'https://example.com/food.jpg'
    entries[2]['image'] = 'missing.png'
    save(cfg.manifest, {'schema': 1, 'recipes': entries})
    assert service.refresh(cfg, now) is False
    assert service.cached(cfg)['recipes'] == []


def test_refresh_deduplicates(cfg, entries, now):
    save(cfg.manifest, {'schema': 1, 'recipes': [entries[0], entries[0], entries[1]]})
    assert service.refresh(cfg, now)
    assert len(service.cached(cfg)['recipes']) == 2


def test_lock_exclusion(cfg):
    with lock(cfg):
        with pytest.raises(ValueError, match='active'):
            with lock(cfg):
                pass


def test_corrupt_cached_image_skipped_for_other_recipe(cfg, entries, now, tmp_path):
    # Distinct image bytes make corruption affect one recipe rather than the
    # content-deduplicated shared image used by the basic fixture.
    for i, item in enumerate(entries):
        p = tmp_path / f'food-{i}.jpg'
        Image.new('RGB', (100, 100), (i * 70, 40, 30)).save(p)
        item['image'] = str(p)
    save(cfg.manifest, {'schema': 1, 'recipes': entries})
    assert service.refresh(cfg, now)
    first = service.cached(cfg)['recipes'][0]
    Path(first.image).write_bytes(b'broken image')
    chosen = service.choose(cfg, {})
    assert chosen is not None and chosen.url != first.url


def test_required_credit_never_silently_truncated():
    with pytest.raises(ValueError, match='credit'):
        Recipe.parse(dict(title='X', url='https://www.bbcgoodfood.com/recipes/test', credit='a' * 141))
