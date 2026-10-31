"""Run explicitly with both recipe and patched picframe3 sources on PYTHONPATH."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import time
from unittest.mock import Mock

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from picframe3.app import PicFrame
from picframe3.config import Config as FrameConfig
from picframe3.control.http import HttpServer
from picframe3.events import State
from picframe3.library.db import Library
from picframe3.media.loader import SlideLoader
from picframe3.media.prepare import PrepareOptions
from picframe3.slideshow import SlideshowController

from recipe_cards import service, source
from recipe_cards.config import Config
from recipe_cards.storage import save


@pytest.mark.asyncio
async def test_actual_recipe_generator_http_and_frame_controller(tmp_path, monkeypatch):
    frame_cfg = FrameConfig()
    folder = tmp_path / 'FrameCards'
    folder.mkdir()
    frame_cfg.slideshow.card_directory = str(folder)
    frame_cfg.slideshow.card_sequence = ['weather', 'recipe']
    frame = PicFrame(frame_cfg)
    frame.library = Library(str(tmp_path / 'library.db3'))
    frame.renderer = Mock(in_transition=False)
    frame.loader = SlideLoader((80, 50), PrepareOptions())
    frame.playlist = Mock()
    frame._publish = Mock()
    frame.state = lambda: State(card=frame.slideshow.card_state(time.monotonic()))
    monkeypatch.setattr('picframe3.slideshow.Texture.from_image', lambda image, **kw: image)
    client = TestClient(HttpServer(frame, frame_cfg.http).api)
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    monkeypatch.setattr(service, 'utcnow', lambda: now)
    def http(url, timeout, payload=None):
        response = client.get('/api/state') if payload is None else client.post('/api/command', json=payload)
        assert response.status_code == 200, response.text
        return response.text
    monkeypatch.setattr(source, 'request', http)
    cfg = replace(Config(), source_mode='local', image_source='local', queue_mode='frame_sequence', queue_enabled=True,
                  state_dir=str(tmp_path / 'state'), output_dir=str(folder), manifest=str(tmp_path / 'recipes.json'))
    local = tmp_path / 'food.jpg'
    Image.new('RGB', (160, 120), 'orange').save(local)
    save(cfg.manifest, {'schema': 1, 'recipes': [dict(title=f'Recipe {i}',
         url=f'https://www.bbcgoodfood.com/recipes/demo-{i}', image=str(local), image_permission='owned') for i in range(3)]})
    try:
        assert service.refresh(cfg, now)
        assert not service.queue(cfg, now)['queued']  # Real API says weather first.
        weather = folder / 'weather-queued-test.png'
        Image.new('RGB', (80, 50), 'blue').save(weather)
        def replenish_weather():
            assert client.post('/api/command', json=dict(action='queue_card', path=str(weather), duration=30, type='weather')).status_code == 200
        async def dispatch():
            for command in frame.bus.drain():
                await frame.handle(command)
        async def show_boundary():
            frame.slideshow.card = None
            frame.slideshow.photos_since_card = 8
            return await frame.slideshow._try_card()
        replenish_weather()
        await dispatch()
        assert await show_boundary()
        assert frame.slideshow.card['type'] == 'weather'
        first = service.queue(cfg, now)
        assert first['queued'] and Path(first['path']).is_file()
        await dispatch()
        for _ in range(3):
            replenish_weather()
            await dispatch()
        assert list(frame.slideshow.cards) == ['recipe', 'weather']
        assert not service.queue(cfg, now + timedelta(minutes=1))['queued']
        assert await show_boundary()
        assert frame.slideshow.card['type'] == 'recipe'
        assert frame.slideshow.card['duration'] == 45
        assert not service.queue(cfg, now + timedelta(minutes=2))['queued']
        assert service.journal(cfg)['records'][0]['displayed']
        assert await show_boundary()
        assert frame.slideshow.card['type'] == 'weather'
        second = service.queue(cfg, now + timedelta(minutes=3))
        assert second['queued'] and second['recipe'] != first['recipe']
        await dispatch()
        frame.slideshow = SlideshowController(frame)  # Pending requests lost, cursor/receipts persisted.
        assert frame.slideshow.card_state(time.monotonic())['next_type'] == 'recipe'
        recovered = service.queue(cfg, now + timedelta(minutes=4))
        assert recovered['queued'] and recovered['recipe'] != second['recipe']
        await dispatch()
        assert await show_boundary()
        assert frame.slideshow.card['type'] == 'recipe'
        frame.playlist.next.assert_not_called()
    finally:
        frame.loader.close()
        frame.library.close()
