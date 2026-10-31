from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import logging
import math
from pathlib import Path
import uuid

from PIL import Image, ImageOps

from . import source
from .config import in_window, due, next_window
from .model import Recipe
from .render import render
from .storage import atomic, read, save, remove_temps

LOG = logging.getLogger(__name__)


def utcnow():
    return datetime.now(UTC)


def cached(cfg):
    for name in ("cache.json", "cache.backup.json"):
        raw = read(cfg.state / name)
        if not isinstance(raw, dict) or raw.get("schema") != 1:
            continue
        try:
            stamp = raw["retrieved"]
            if type(stamp) not in (float, int) or not math.isfinite(stamp) or stamp <= 0:
                raise ValueError("Invalid cache timestamp")
            if not isinstance(raw["recipes"], list) or len(raw["recipes"]) > 100:
                raise ValueError("Invalid cache recipe array")
            recipes, seen = [], set()
            for item in raw["recipes"]:
                try:
                    r = Recipe.parse(item)
                    p = Path(r.image)
                    if p.is_symlink() or p.parent != cfg.state / "images" or not p.is_file():
                        continue
                    if r.url not in seen and cfg.includes(r.category):
                        recipes.append(r)
                        seen.add(r.url)
                except (ValueError, OSError):
                    continue
            if recipes:
                return {"retrieved": stamp, "recipes": recipes}
        except (KeyError, TypeError, ValueError) as exc:
            LOG.warning("Invalid %s: %s", name, exc)
    return {"retrieved": None, "recipes": []}


def schedule(cfg):
    raw = read(cfg.state / "schedule.json")
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in ("refresh_attempt", "refresh_success") and type(v) in (float, int) and math.isfinite(v)}


def journal(cfg):
    p = cfg.state / "queue.json"
    raw = read(p)
    if raw is None and not p.exists():
        return {"schema": 1, "records": []}
    if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("records"), list):
        raise ValueError("Queue journal corrupt: queueing stopped; see README recovery")
    try:
        if len(raw["records"]) > cfg.max_snapshots:
            raise ValueError("Too many queue records")
        for record in raw["records"]:
            p = Path(record["path"])
            if not p.name.startswith("recipe-queued-") or p.suffix != ".png" or p.parent != cfg.output:
                raise ValueError("Invalid journal path")
            if type(record["attempt"]) not in (float, int) or not math.isfinite(record["attempt"]):
                raise ValueError("Invalid journal timestamp")
            if type(record["released"]) is not bool or not isinstance(record["url"], str):
                raise ValueError("Invalid journal record")
        for key in ("last_attempt",):
            if key in raw and (type(raw[key]) not in (float, int) or not math.isfinite(raw[key])):
                raise ValueError("Invalid queue timestamp")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Queue journal invalid; queueing stopped: {exc}") from exc
    return raw


def cache_image(cfg, original, root, photo_client=None):
    if cfg.image_source == "goodfood" and not original.get("image"):
        from .photos import PhotoClient
        return (photo_client or PhotoClient(cfg)).cache(original.get("image_url"))
    if original.get("image_permission") not in ("owned", "licensed", "permission"):
        raise ValueError("A local image_permission of owned, licensed or permission is required")
    name = original.get("image")
    if not isinstance(name, str) or not name or "://" in name:
        raise ValueError("Supply a local photograph; remote image downloading is disabled")
    p = Path(name).expanduser()
    if not p.is_absolute():
        p = root / p
    if p.stat().st_size > 20_000_000:
        raise ValueError("Local photograph exceeds 20 MB")
    folder = cfg.state / "images"
    folder.mkdir(parents=True, exist_ok=True)
    with Image.open(p) as image:
        if image.width * image.height > 24_000_000:
            raise ValueError("Image exceeds 24 megapixels")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1920, 1600), Image.Resampling.LANCZOS)
        digest = hashlib.sha256(image.tobytes() + str(image.size).encode()).hexdigest()
        target = folder / f"{digest}.jpg"
        if not target.exists():
            if len(list(folder.glob("*.jpg"))) >= 310:
                raise ValueError("Image cache safety limit reached; inspect orphan files")
            atomic(target, lambda f: image.save(f, format="JPEG", quality=94))
    return str(target)


def clean_images(cfg):
    keep = set()
    for name in ("cache.json", "cache.backup.json"):
        raw = read(cfg.state / name)
        if isinstance(raw, dict) and isinstance(raw.get("recipes"), list):
            keep.update(x.get("image") for x in raw["recipes"] if isinstance(x, dict))
    for p in (cfg.state / "images").glob("*.jpg"):
        if str(p) not in keep and not p.is_symlink() and len(p.stem) == 64:
            p.unlink(missing_ok=True)


def refresh(cfg, now):
    state = schedule(cfg)
    state["refresh_attempt"] = now.timestamp()
    save(cfg.state / "schedule.json", state)  # Backoff survives failure/reboot.
    try:
        clean_images(cfg)  # Reclaim orphan staging images from interrupted refreshes.
        entries, root = source.retrieve(cfg)
        from .photos import PhotoClient
        photo_client = PhotoClient(cfg)
        recipes, seen = [], set()
        for item in entries:
            if len(recipes) >= cfg.max_recipes:
                break
            try:
                r = Recipe.parse(item)
                if r.url in seen or not cfg.includes(r.category):
                    continue
                r = replace(r, image=cache_image(cfg, item, root, photo_client))
                recipes.append(r)
                seen.add(r.url)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                LOG.warning("Skipping unusable recipe: %s", exc)
        if not recipes:
            raise ValueError("No usable recipes with readable photos; existing cache retained")
        previous = cached(cfg)
        if previous["recipes"]:
            save(cfg.state / "cache.backup.json", dict(schema=1, retrieved=previous["retrieved"], recipes=[r.asdict() for r in previous["recipes"]]))
        save(cfg.state / "cache.json", dict(schema=1, retrieved=now.timestamp(), recipes=[r.asdict() for r in recipes]))
        state["refresh_success"] = now.timestamp()
        save(cfg.state / "schedule.json", state)
        clean_images(cfg)
        LOG.info("Refreshed %s usable recipes", len(recipes))
        return True
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        LOG.warning("Refresh failed; keeping offline cache: %s", exc)
        return False


def choose(cfg, history):
    recipes = cached(cfg)["recipes"]
    if not recipes:
        return None
    # Do not immediately repeat even a single-item catalogue; wait for a new recipe.
    candidates = [r for r in recipes if r.url != history.get("last_selected")]
    if not candidates:
        return None
    seen = history.get("rotation", [])
    ordered = [r for r in candidates if r.url not in seen] + [r for r in candidates if r.url in seen]
    for r in ordered:
        try:
            with Image.open(r.image) as photograph:
                photograph.load()
            return r
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            LOG.warning("Skipping damaged cached photo for %s: %s", r.url, exc)
    return None


def frame_state(cfg):
    value = json.loads(source.request(cfg.api_url.removesuffix("/command") + "/state", cfg.request_timeout))
    card = value.get("card") if isinstance(value, dict) else None
    if not isinstance(card, dict) or type(card.get("pending")) is not int or not 0 <= card["pending"] <= 64 or type(card.get("active")) is not bool or type(card.get("photos_until_next")) is not int or card["photos_until_next"] < 0:
        raise ValueError("Frame lacks the inspected card-state contract; refusing to queue")
    if card["active"] and not isinstance(card.get("type"), str):
        raise ValueError("Invalid active card type")
    return card


def reconcile(cfg, state, card, now):
    changed = False
    for record in state["records"]:
        if not record["released"] and now - record["attempt"] >= 60 and (card["pending"] == 0 or card["active"] and card["type"] == "recipe"):
            # Empty queue or decoded recipe proves no pending file still needs this snapshot.
            # One minute grace lets the async command bus settle before interpreting emptiness.
            record["released"] = True
            changed = True
    if changed:
        save(cfg.state / "queue.json", state)


def cleanup(cfg, state, now):
    retained = []
    for record in state["records"]:
        p = Path(record["path"])
        if record["released"] and now - record["attempt"] > cfg.retention_days * 86400:
            if p.is_symlink():
                retained.append(record)
                continue
            p.unlink(missing_ok=True)
        else:
            retained.append(record)
    state["records"] = retained
    save(cfg.state / "queue.json", state)
    remove_temps(cfg.output, now)
    remove_temps(cfg.state, now)
    remove_temps(cfg.state / "images", now)


def queue(cfg, now, dry_run=False):
    stamp = now.timestamp()
    if not cfg.queue_enabled or not in_window(cfg, now):
        return {"queued": False, "reason": "Queue disabled or outside display window"}
    if cfg.queue_mode == "frame_sequence":
        from .sequence import queue as replenish
        return replenish(cfg, now, dry_run)
    state = journal(cfg)
    if not due(stamp, state.get("last_attempt"), cfg.queue_minutes * 60):
        return {"queued": False, "reason": "Queue interval not due"}
    recipe = choose(cfg, state)
    if dry_run:
        return {"queued": False, "dry_run": True, "recipe": recipe.url if recipe else None,
                "reason": "Would inspect frame state then submit one immutable recipe PNG; no network or writes"}
    try:
        card = frame_state(cfg)
        reconcile(cfg, state, card, stamp)
        cleanup(cfg, state, stamp)
        if any(not r["released"] for r in state["records"]):
            return {"queued": False, "reason": "Previous recipe unresolved; waiting for consumption or empty queue"}
        if card["pending"] > 1 or card["active"] and card["type"] == "recipe":
            return {"queued": False, "reason": "Deferring: frame queue busy"}
        if recipe is None:
            return {"queued": False, "reason": "No usable non-repeated recipe in cache"}
        cfg.output.mkdir(parents=True, exist_ok=True)
        if len(state["records"]) >= cfg.max_snapshots or len(list(cfg.output.glob("recipe-queued-*.png"))) >= cfg.max_snapshots:
            return {"queued": False, "reason": "Snapshot cap reached; refusing to remove unproven pending files"}
        path = cfg.output / f"recipe-queued-{uuid.uuid4().hex}.png"
        if path.exists() or path.is_symlink():
            raise ValueError("Snapshot name already exists; refusing to overwrite")
        render(cfg, recipe, path)
        if not in_window(cfg, utcnow()):
            path.unlink(missing_ok=True)  # Never submitted; safe to discard.
            return {"queued": False, "reason": "Display window closed during generation"}
        record = dict(path=str(path), url=recipe.url, attempt=stamp, released=False, accepted=False)
        state["records"].append(record)
        state["last_attempt"], state["last_selected"] = stamp, recipe.url
        rotation = state.get("rotation", [])
        urls = [r.url for r in cached(cfg)["recipes"]]
        if recipe.url in rotation:
            rotation = []
        state["rotation"] = [u for u in rotation if u in urls] + [recipe.url]
        save(cfg.state / "queue.json", state)  # Reserve BEFORE POST; no POST retries.
        result = json.loads(source.request(cfg.api_url, cfg.request_timeout, dict(action="queue_card", path=str(path), duration=cfg.duration, type="recipe")))
        if not isinstance(result, dict) or result.get("ok") is not True or result.get("action") != "queue_card":
            raise ValueError("Unexpected queue response; submission is uncertain")
        record["accepted"] = True
        state["last_accepted"] = dict(record)
        save(cfg.state / "queue.json", state)
        return {"queued": True, "path": str(path), "recipe": recipe.url, "reason": "HTTP accepted; display remains asynchronous"}
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        LOG.warning("Queue deferred/failed: %s", exc)
        return {"queued": False, "reason": str(exc)}


def tick(cfg, now, dry_run=False):
    state = schedule(cfg)
    refresh_due = due(now.timestamp(), state.get("refresh_attempt"), cfg.refresh_hours * 3600)
    if dry_run:
        return {"dry_run": True, "refresh_due": refresh_due, "queue": queue(cfg, now, True)}
    refreshed = refresh(cfg, now) if refresh_due else None
    # Re-read clock after network work; do not queue across the closing boundary.
    result = queue(cfg, utcnow())
    return {"refreshed": refreshed, "queue": result}


def status(cfg, now):
    cache, state = cached(cfg), schedule(cfg)
    try:
        q = journal(cfg)
        error = None
    except ValueError as exc:
        q, error = {}, str(exc)
    queue_at = max(now.timestamp(), q.get("last_attempt", 0) + cfg.queue_minutes * 60)
    refresh_at = max(now.timestamp(), state.get("refresh_attempt", 0) + cfg.refresh_hours * 3600)
    records = q.get("records", [])
    accepted = [r for r in records if r.get("accepted")]
    return dict(source_mode=cfg.source_mode, queue_mode=cfg.queue_mode, queue_enabled=cfg.queue_enabled, recipe_count=len(cache["recipes"]),
                last_successful_refresh=cache["retrieved"],
                cache_age_hours=(now.timestamp() - cache["retrieved"]) / 3600 if cache["retrieved"] else None,
                next_refresh=datetime.fromtimestamp(refresh_at, UTC).isoformat(),
                last_selected=q.get("last_selected"),
                last_queued=q.get("last_accepted", accepted[-1] if accepted else None),
                unresolved=sum(not r["released"] for r in records),
                next_queue_eligible=next_window(cfg, queue_at) if cfg.queue_enabled and cfg.queue_mode == "independent" else None,
                queue_policy="Frame controls alternating turns; check once per minute" if cfg.queue_mode == "frame_sequence" else "Independent interval",
                queue_error=error, delivery="HTTP acceptance is not a display receipt; offline status makes no API call")
