from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from recipe_cards import service, source
from recipe_cards.sequence import validate_state
from recipe_cards.config import load


@pytest.fixture
def seq_cfg(populated):
    return replace(populated, queue_mode='frame_sequence', queue_enabled=True)


@pytest.fixture
def ordered(monkeypatch):
    class Frame:
        def __init__(self):
            self.next = 'weather'
            self.pending = {}
            self.shown = {}
            self.session = 'first-session'
            self.active = None
            self.posts = []
            self.fail = None
        def card(self):
            return dict(sequence=['weather', 'recipe'], next_type=self.next,
                        session=self.session, last_shown=dict(self.shown), sequence_error=None,
                        active=self.active is not None, type=self.active,
                        pending=len(self.pending), pending_types=list(self.pending), photos_until_next=8)
        def weather(self):
            self.pending['weather'] = dict(type='weather', path='/weather.png', duration=30)
        def show(self):
            if self.next not in self.pending:
                return None
            payload = self.pending.pop(self.next)
            self.active = self.next
            self.shown[self.next] = payload['path']
            self.next = 'recipe' if self.next == 'weather' else 'weather'
            return payload
        def http(self, url, timeout, payload=None):
            if payload is None:
                return json.dumps({'card': self.card()})
            self.posts.append(payload)
            # Simulate acceptance before an ambiguous response if requested.
            if isinstance(self.fail, HTTPError):
                raise self.fail
            self.pending[payload['type']] = dict(payload)
            if self.fail:
                raise self.fail
            return '{"ok":true,"action":"queue_card"}'
    f = Frame()
    monkeypatch.setattr(source, 'request', f.http)
    return f


def test_weather_turn_never_queues_recipe(seq_cfg, ordered, now):
    assert not service.queue(seq_cfg, now)['queued']
    assert not ordered.posts


def test_replenish_one_recipe_and_use_receipt_not_elapsed_time(seq_cfg, ordered, now):
    ordered.weather()
    ordered.show()
    assert service.queue(seq_cfg, now)['queued']
    first = ordered.posts[-1]
    assert first['duration'] == 45
    for i in (1, 3, 16, 60):
        assert not service.queue(seq_cfg, now + timedelta(minutes=i))['queued']
    assert len(ordered.posts) == 1  # Paused pending recipe cannot be replaced.
    ordered.show()
    assert not service.queue(seq_cfg, now + timedelta(minutes=61))['queued']
    assert service.journal(seq_cfg)['records'][0]['released']
    assert service.journal(seq_cfg)['records'][0]['displayed']


def test_frame_restart_abandons_lost_pending_but_keeps_turn(seq_cfg, ordered, now):
    ordered.weather()
    ordered.show()
    assert service.queue(seq_cfg, now)['queued']
    first = ordered.posts[-1]
    ordered.session = 'restarted'
    ordered.pending.clear()
    ordered.active = None
    second = service.queue(seq_cfg, now + timedelta(minutes=1))
    assert second['queued'] and second['path'] != first['path']
    assert second['recipe'] != service.journal(seq_cfg)['records'][0]['url']
    assert service.journal(seq_cfg)['records'][0]['abandoned_session']
    assert Path(first['path']).is_file()  # Retention protects already decoded textures too.


def test_receipt_survives_poll_missed_active_card(seq_cfg, ordered, now):
    ordered.weather()
    ordered.show()
    assert service.queue(seq_cfg, now)['queued']
    ordered.show()  # Recipe is displayed between polling ticks.
    ordered.active = None
    ordered.weather()
    ordered.show()  # Weather has also displayed by next poll.
    assert service.queue(seq_cfg, now + timedelta(minutes=8))['queued']
    assert service.journal(seq_cfg)['records'][0]['displayed']


def test_unknown_recipe_pending_is_never_overwritten(seq_cfg, ordered, now):
    ordered.weather()
    ordered.show()
    ordered.pending['recipe'] = dict(type='recipe', path='/unknown.png', duration=45)
    assert not service.queue(seq_cfg, now)['queued']
    assert not ordered.posts


def test_uncertain_post_waits_for_matching_receipt(seq_cfg, ordered, now):
    ordered.weather()
    ordered.show()
    ordered.fail = TimeoutError('reply lost')
    assert not service.queue(seq_cfg, now)['queued']
    ordered.fail = None
    assert not service.queue(seq_cfg, now + timedelta(minutes=1))['queued']
    assert len(ordered.posts) == 1
    ordered.show()
    service.queue(seq_cfg, now + timedelta(minutes=2))
    assert service.journal(seq_cfg)['records'][0]['displayed']


def test_definitive_http_rejection_can_retry_next_check(seq_cfg, ordered, now):
    ordered.weather()
    ordered.show()
    ordered.fail = HTTPError(seq_cfg.api_url, 400, 'bad request', {}, None)
    assert not service.queue(seq_cfg, now)['queued']
    assert service.journal(seq_cfg)['records'][0]['rejected']
    ordered.fail = None
    assert service.queue(seq_cfg, now + timedelta(minutes=1))['queued']


@pytest.mark.parametrize('change', [dict(sequence=[]), dict(next_type=None), dict(sequence_error='disk full'), dict(pending_types=['weather', 'weather']), dict(last_shown={'recipe': 3}), dict(session='')])
def test_unknown_sequence_contract_fails_closed(ordered, change):
    state = ordered.card() | change
    with pytest.raises(ValueError):
        validate_state(state)


def test_dry_run_has_no_frame_calls(seq_cfg, now, monkeypatch):
    monkeypatch.setattr(source, 'request', lambda *a: pytest.fail('dry run contacted frame'))
    assert service.queue(seq_cfg, now, True)['dry_run']
    assert not (seq_cfg.state / 'queue.json').exists()


def test_defaults_leave_scheduling_disabled(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('')
    cfg = load(path)
    assert cfg.queue_mode == 'frame_sequence' and cfg.queue_enabled is False


def test_three_hour_cycle_with_three_minute_weather_producer(seq_cfg, ordered, now):
    """One display countdown; independent checks only refill their type slots."""
    active_until, next_photo, photos = 0, 20, 0
    shown = []
    for second in range(3 * 3600):
        moment = now + timedelta(seconds=second)
        if second % 180 == 0:
            ordered.weather()
        if second % 60 == 0:
            service.queue(seq_cfg, moment)
        if ordered.active is not None:
            if second < active_until:
                continue
            ordered.active = None
            next_photo = second + 20
            continue
        if second < next_photo:
            continue
        photos += 1
        next_photo = second + 20
        if photos >= 8:
            payload = ordered.show()
            if payload:
                shown.append((second, payload['type']))
                active_until = second + payload['duration']
                photos = 0
    assert len(shown) > 40
    assert [t for _, t in shown] == ['weather' if i % 2 == 0 else 'recipe' for i in range(len(shown))]
    gaps = [b[0] - a[0] for a, b in zip(shown, shown[1:])]
    assert set(gaps) <= {190, 205}
    assert len([r for r in service.journal(seq_cfg)['records'] if not r['released']]) <= 1
    assert len(ordered.posts) <= len([x for x in shown if x[1] == 'recipe']) + 1
