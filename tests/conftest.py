from dataclasses import replace
from datetime import UTC, datetime
import json

from PIL import Image
import pytest

from recipe_cards.config import Config
from recipe_cards import source, service, photos
from recipe_cards.storage import save


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected real HTTP request in test")
    monkeypatch.setattr(source, "request", blocked)
    monkeypatch.setattr(photos, "fetch_bytes", blocked)
    monkeypatch.setattr(service, "utcnow", lambda: datetime(2026, 10, 31, 12, tzinfo=UTC))


@pytest.fixture
def now():
    return datetime(2026, 10, 31, 12, tzinfo=UTC)


@pytest.fixture
def cfg(tmp_path):
    return replace(Config(), source_mode="local", automated_metadata_permission=False, image_source="local", queue_mode="independent", queue_enabled=True, output_dir=str(tmp_path / "FrameCards"), state_dir=str(tmp_path / "state"), manifest=str(tmp_path / "recipes.json"))


@pytest.fixture
def entries(cfg, tmp_path):
    Image.new("RGB", (1000, 700), "tomato").save(tmp_path / "food.png")
    items = [dict(title=f"Recipe {i}", url=f"https://www.bbcgoodfood.com/recipes/test-{i}", image="food.png", image_permission="owned", category="trending") for i in range(3)]
    save(cfg.manifest, {"schema": 1, "recipes": items})
    return items


@pytest.fixture
def populated(cfg, entries, now):
    assert service.refresh(cfg, now)
    return cfg


@pytest.fixture
def fake_frame(monkeypatch):
    class Frame:
        card = dict(active=False, type=None, pending=0, photos_until_next=8)
        calls = []
        error = None
        def http(self, url, timeout, payload=None):
            if payload is None:
                return json.dumps({"card": self.card})
            self.calls.append(payload)
            if self.error:
                raise self.error
            self.card = dict(active=False, type=None, pending=1, photos_until_next=8)
            return json.dumps({"ok": True, "action": "queue_card"})
    frame = Frame()
    monkeypatch.setattr(source, "request", frame.http)
    return frame
