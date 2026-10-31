"""Replenish the frame's recipe turn; the frame owns order and photo spacing."""
import json
import logging
from pathlib import Path
from urllib.error import HTTPError
import uuid

from PIL import Image

from . import service, source
from .config import due, in_window
from .render import render
from .storage import save

LOG = logging.getLogger(__name__)


def validate_state(card):
    if (card.get('sequence') != ['weather', 'recipe'] or card.get('sequence_error')
            or card.get('next_type') not in ('weather', 'recipe')
            or not isinstance(card.get('session'), str) or not card['session']
            or not isinstance(card.get('last_shown'), dict)
            or any(k not in ('weather', 'recipe') or not isinstance(v, str) for k, v in card['last_shown'].items())
            or not isinstance(card.get('pending_types'), list)
            or any(t not in ('weather', 'recipe') for t in card['pending_types'])
            or len(set(card['pending_types'])) != len(card['pending_types'])
            or len(card['pending_types']) != card['pending']):
        raise ValueError('Frame must run the optional weather/recipe sequence patch with a healthy cursor')
    return card


def queue(cfg, now, dry_run=False):
    stamp = now.timestamp()
    history = service.journal(cfg)
    if dry_run:
        r = service.choose(cfg, history)
        return dict(queued=False, dry_run=True, recipe=r.url if r else None,
                    reason='Frame selects recipe turns; would verify sequence/receipts and replenish once. No HTTP or writes')
    try:
        card = validate_state(service.frame_state(cfg))
        for record in history['records']:
            if record['released']:
                continue
            if not record.get('frame_session'):
                raise ValueError('Unresolved legacy request: drain recipes before changing queue mode')
            if card['last_shown'].get('recipe') == record['path']:
                record.update(released=True, displayed=True, accepted=True)
                history['last_accepted'] = dict(record)
            elif record['frame_session'] != card['session']:
                # Slideshow recreation loses its in-memory queue. Its persisted
                # cursor and last_shown receipt still preserve the display order.
                record.update(released=True, abandoned_session=True)
        save(cfg.state / 'queue.json', history)
        service.cleanup(cfg, history, stamp)
        if any(not r['released'] for r in history['records']):
            return dict(queued=False, reason='Waiting for the previous recipe display receipt')
        if card['next_type'] != 'recipe':
            return dict(queued=False, reason='Frame is waiting for weather')
        if 'recipe' in card['pending_types'] or card['active'] and card['type'] == 'recipe':
            return dict(queued=False, reason='Recipe already pending/active; no replacement')
        # Only a submission safety throttle, not a display timer. The old
        # fifteen-minute queue interval is deliberately unused in this mode.
        if not due(stamp, history.get('last_attempt'), 60):
            return dict(queued=False, reason='Submission safety throttle')
        recipe = service.choose(cfg, history)
        if recipe is None:
            return dict(queued=False, reason='No usable non-repeated recipe; photos continue while its turn waits')
        cfg.output.mkdir(parents=True, exist_ok=True)
        if len(history['records']) >= cfg.max_snapshots or len(list(cfg.output.glob('recipe-queued-*.png'))) >= cfg.max_snapshots:
            return dict(queued=False, reason='Snapshot cap reached; unresolved/unknown files retained')
        path = cfg.output / f'recipe-queued-{uuid.uuid4().hex}.png'
        if path.exists() or path.is_symlink():
            raise ValueError('Refusing to overwrite an existing snapshot')
        render(cfg, recipe, path)
        if not in_window(cfg, service.utcnow()):
            path.unlink(missing_ok=True)
            return dict(queued=False, reason='Display window closed during generation')
        record = dict(path=str(path), url=recipe.url, attempt=stamp, released=False,
                      accepted=False, frame_session=card['session'])
        history['records'].append(record)
        history['last_attempt'], history['last_selected'] = stamp, recipe.url
        rotation = history.get('rotation', [])
        if recipe.url in rotation:
            rotation = []
        urls = [r.url for r in service.cached(cfg)['recipes']]
        history['rotation'] = [u for u in rotation if u in urls] + [recipe.url]
        save(cfg.state / 'queue.json', history)
        try:
            response = json.loads(source.request(cfg.api_url, cfg.request_timeout,
                dict(action='queue_card', path=str(path), duration=cfg.duration, type='recipe')))
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                record.update(released=True, rejected=True)  # The HTTP validator did not enqueue.
                save(cfg.state / 'queue.json', history)
            raise
        if not isinstance(response, dict) or response.get('ok') is not True or response.get('action') != 'queue_card':
            raise ValueError('Uncertain queue response; retaining snapshot until receipt/restart')
        record['accepted'] = True
        history['last_accepted'] = dict(record)
        save(cfg.state / 'queue.json', history)
        return dict(queued=True, recipe=recipe.url, path=str(path), reason='Recipe turn replenished; frame controls display')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        LOG.warning('Recipe sequence deferred: %s', exc)
        return dict(queued=False, reason=str(exc))
