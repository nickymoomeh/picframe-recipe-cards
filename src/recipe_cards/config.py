from dataclasses import dataclass, fields
from datetime import datetime, time, timedelta
from pathlib import Path
import math
import tomllib
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    source_url: str = "https://www.bbcgoodfood.com/news-trends"
    source_mode: str = "goodfood"
    automated_metadata_permission: bool = True
    image_source: str = "goodfood"
    allow_broken_cdn_robots_redirect: bool = False
    recipes_url: str = "https://www.bbcgoodfood.com/recipes"
    include_new: bool = True
    seasonal_auto: bool = True
    include_trending: bool = True
    include_seasonal: bool = True
    seasonal_urls: tuple = ()
    manifest: str = "~/.config/recipe-card/recipes.json"
    output_dir: str = "/home/pi4/FrameCards"
    state_dir: str = "~/.local/state/recipe-card"
    api_url: str = "http://localhost:9000/api/command"
    queue_enabled: bool = False
    queue_mode: str = "frame_sequence"
    duration: int = 45
    refresh_hours: float = 12
    queue_minutes: float = 15
    window_start: str = "07:00"
    window_end: str = "20:30"
    timezone: str = "Europe/London"
    width: int = 1920
    height: int = 1200
    font: str = ""
    font_bold: str = ""
    request_timeout: float = 10
    request_spacing: float = 2
    max_recipes: int = 24
    max_snapshots: int = 256
    retention_days: float = 1

    def includes(self, category):
        return {"trending": self.include_trending, "new": self.include_new, "seasonal": self.include_seasonal}.get(category, False)

    @property
    def state(self):
        return Path(self.state_dir).expanduser().resolve()

    @property
    def output(self):
        return Path(self.output_dir).expanduser().resolve()


def load(path=None):
    p = Path(path or "~/.config/recipe-card/config.toml").expanduser()
    if path is not None and not p.is_file():
        raise ValueError(f"Configuration does not exist: {p}")
    raw = tomllib.loads(p.read_text()) if p.exists() else {}
    names = {f.name for f in fields(Config)}
    if raw.keys() - names:
        raise ValueError(f"Unknown configuration keys: {sorted(raw.keys() - names)}")
    cfg = Config(**raw)
    defaults = Config()
    for f in fields(Config):
        value, default = getattr(cfg, f.name), getattr(defaults, f.name)
        if isinstance(default, bool):
            valid = type(value) is bool
        elif isinstance(default, (float, int)):
            valid = type(value) in (float, int) and math.isfinite(value) and value > 0
            if type(default) is int:
                valid = valid and type(value) is int
        elif isinstance(default, tuple):
            valid = isinstance(value, (tuple, list)) and all(isinstance(x, str) for x in value)
        else:
            valid = isinstance(value, str)
        if not valid:
            raise ValueError(f"Invalid value for {f.name}")
    if cfg.image_source not in ("goodfood", "local"):
        raise ValueError("image_source must be goodfood or local")
    if cfg.queue_mode not in ("independent", "frame_sequence"):
        raise ValueError("queue_mode must be independent or frame_sequence")
    if cfg.source_mode not in ("local", "goodfood"):
        raise ValueError("source_mode must be local or goodfood")
    if cfg.source_mode == "goodfood" and not cfg.automated_metadata_permission:
        raise ValueError("Live metadata requires automated_metadata_permission; see docs/SOURCES.md")
    from .model import source_url
    source_url(cfg.source_url, recipe=False)
    source_url(cfg.recipes_url, recipe=False)
    if len(cfg.seasonal_urls) > 4:
        raise ValueError("At most four seasonal source URLs")
    for url in cfg.seasonal_urls:
        source_url(url, recipe=False)
    ZoneInfo(cfg.timezone)
    for clock in (cfg.window_start, cfg.window_end):
        if len(clock) != 5 or time.fromisoformat(clock).second:
            raise ValueError("Windows must be HH:MM")
    if cfg.duration > 3600 or cfg.max_recipes > 100 or cfg.max_snapshots > 1000:
        raise ValueError("duration <=3600, max_recipes <=100, max_snapshots <=1000 required")
    if (cfg.width, cfg.height) != (1920, 1200):
        raise ValueError("This layout requires width=1920, height=1200")
    if cfg.request_spacing < 2 or cfg.request_timeout > 30:
        raise ValueError("request_spacing >=2 and request_timeout <=30 required")
    for name in ("output_dir", "state_dir", "manifest"):
        p = Path(getattr(cfg, name)).expanduser()
        if not p.is_absolute():
            raise ValueError(f"{name} must be absolute (or start with ~)")
    if cfg.state == cfg.output:
        raise ValueError("state_dir and output_dir must differ")
    for name in ("font", "font_bold"):
        if getattr(cfg, name) and not Path(getattr(cfg, name)).is_file():
            raise ValueError(f"{name} does not exist")
    u = urlsplit(cfg.api_url)
    if u.scheme != "http" or u.hostname not in ("localhost", "127.0.0.1", "::1") or u.path != "/api/command" or u.username or u.query or u.fragment:
        raise ValueError("api_url must be a local http /api/command endpoint")
    return cfg


def in_window(cfg, now):
    clock = now.astimezone(ZoneInfo(cfg.timezone)).time().replace(tzinfo=None)
    start, end = time.fromisoformat(cfg.window_start), time.fromisoformat(cfg.window_end)
    return start == end or (start <= clock < end if start < end else clock >= start or clock < end)


def due(now, previous, seconds):
    return previous is None or now < previous or now - previous >= seconds


def next_window(cfg, stamp):
    """Find next eligible minute on the UTC timeline, including repeated/skipped hours."""
    from datetime import UTC
    now = datetime.fromtimestamp(stamp, UTC)
    for minute in range(2881):
        candidate = now + timedelta(minutes=minute)
        if in_window(cfg, candidate):
            return candidate.isoformat()
    return None
