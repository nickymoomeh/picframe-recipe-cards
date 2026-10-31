from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from recipe_cards.config import in_window, due, load
from recipe_cards import service, source
from recipe_cards.storage import read, save
from recipe_cards.cli import main


@pytest.mark.parametrize('instant,expected', [('2026-10-31T06:59:00+00:00', False), ('2026-10-31T07:00:00+00:00', True), ('2026-10-31T20:29:59+00:00', True), ('2026-10-31T20:30:00+00:00', False), ('2026-07-01T06:00:00+00:00', True), ('2026-07-01T19:30:00+00:00', False)])
def test_window(cfg, instant, expected):
    assert in_window(cfg, datetime.fromisoformat(instant)) is expected


def test_dst_and_overnight_windows(cfg):
    cfg = replace(cfg, window_start='01:15', window_end='01:45')
    # Both occurrences of 01:30 on autumn transition are eligible.
    assert in_window(cfg, datetime(2026, 10, 25, 0, 30, tzinfo=UTC))
    assert in_window(cfg, datetime(2026, 10, 25, 1, 30, tzinfo=UTC))
    # Spring clock skips local 01:30 entirely.
    assert not in_window(cfg, datetime(2026, 3, 29, 1, 30, tzinfo=UTC))
    cfg = replace(cfg, window_start='22:00', window_end='06:00')
    assert in_window(cfg, datetime(2026, 10, 31, 23, tzinfo=UTC))
    assert in_window(cfg, datetime(2026, 10, 31, 5, tzinfo=UTC))
    assert not in_window(cfg, datetime(2026, 10, 31, 12, tzinfo=UTC))
    assert in_window(replace(cfg, window_start='00:00', window_end='00:00'), datetime.now(UTC))
    assert due(100, 90, 10) and not due(100, 95, 10) and due(80, 95, 10)


def test_no_repeated_refresh(populated, now, monkeypatch, fake_frame):
    cfg = populated
    monkeypatch.setattr(source, 'retrieve', lambda _: pytest.fail('Should use cache'))
    assert service.tick(cfg, now)['refreshed'] is None


def test_queue_payload_rotation_and_restart(populated, fake_frame, now):
    cfg = populated
    result = service.queue(cfg, now)
    assert result['queued']
    assert fake_frame.calls[0] == dict(action='queue_card', path=result['path'], duration=45, type='recipe')
    original = Path(result['path']).read_bytes()
    assert not service.queue(cfg, now + timedelta(minutes=1))['queued']
    assert not service.queue(cfg, now + timedelta(minutes=16))['queued']  # Still pending.
    fake_frame.card = dict(active=False, type=None, pending=0, photos_until_next=8)
    second = service.queue(cfg, now + timedelta(minutes=17))
    assert second['queued'] and second['recipe'] != result['recipe']
    assert Path(result['path']).read_bytes() == original
    assert len(service.journal(cfg)['records']) == 2


def test_unresolved_snapshot_survives_indefinite_pause(populated, fake_frame, now):
    cfg = populated
    first = service.queue(cfg, now)
    assert first['queued']
    for day in range(1, 30):
        assert not service.queue(cfg, now + timedelta(days=day))['queued']
    assert len(fake_frame.calls) == 1
    assert Path(first['path']).is_file()


def test_weather_fifo_no_replacement_or_reset(populated, fake_frame, now):
    cfg = populated
    fake_frame.card = dict(active=True, type='weather', pending=1, photos_until_next=6)
    assert service.queue(cfg, now)['queued']
    assert fake_frame.calls[0]['type'] == 'recipe'
    # Weather producer stays independent: the service sends no cancel, navigation,
    # weather replacement, or photo countdown/configuration command.
    assert fake_frame.calls[0]['action'] == 'queue_card' and len(fake_frame.calls) == 1
    fake_frame.card = dict(active=True, type='weather', pending=2, photos_until_next=8)
    assert not service.queue(cfg, now + timedelta(minutes=16))['queued']


def test_busy_weather_defers_before_render(populated, fake_frame, now):
    fake_frame.card = dict(active=False, type=None, pending=2, photos_until_next=3)
    result = service.queue(populated, now)
    assert not result['queued'] and 'busy' in result['reason']
    assert not list(populated.output.glob('recipe-queued-*.png'))
    fake_frame.card['pending'] = 1
    assert service.queue(populated, now + timedelta(minutes=1))['queued']


def test_api_timeout_at_most_once_and_recovery(populated, fake_frame, now):
    cfg = populated
    fake_frame.error = TimeoutError('uncertain response')
    assert not service.queue(cfg, now)['queued']
    saved = service.journal(cfg)
    assert saved['records'][0]['accepted'] is False
    path = Path(saved['records'][0]['path'])
    assert path.is_file()
    assert not service.queue(cfg, now + timedelta(minutes=1))['queued']
    assert len(fake_frame.calls) == 1
    fake_frame.error = None
    fake_frame.card = dict(active=True, type='recipe', pending=0, photos_until_next=8)
    assert not service.queue(cfg, now + timedelta(minutes=16))['queued']
    assert service.journal(cfg)['records'][0]['released']
    fake_frame.card = dict(active=False, type=None, pending=0, photos_until_next=7)
    assert service.queue(cfg, now + timedelta(minutes=17))['queued']


def test_unavailable_state_never_posts(populated, now, monkeypatch):
    calls = []
    def fail(url, *args):
        calls.append(url)
        raise OSError('frame offline')
    monkeypatch.setattr(source, 'request', fail)
    assert not service.queue(populated, now)['queued']
    assert len(calls) == 1 and calls[0].endswith('/api/state')
    assert len(service.cached(populated)['recipes']) == 3
    assert not list(populated.output.glob('recipe-queued-*.png'))


def test_unknown_contract_fails_closed(populated, now, monkeypatch):
    monkeypatch.setattr(source, 'request', lambda *a: '{"card":{"pending":0}}')
    result = service.queue(populated, now)
    assert not result['queued'] and 'contract' in result['reason']


def test_one_recipe_never_immediately_repeats(cfg, entries, fake_frame, now):
    save(cfg.manifest, {'schema': 1, 'recipes': entries[:1]})
    assert service.refresh(cfg, now)
    assert service.queue(cfg, now)['queued']
    fake_frame.card['pending'] = 0
    assert not service.queue(cfg, now + timedelta(minutes=16))['queued']


def test_snapshot_cleanup_only_released_owned_files(populated, fake_frame, now):
    cfg = populated
    result = service.queue(cfg, now)
    snapshot = Path(result['path'])
    unrelated = cfg.output / 'weather-queued-abc.png'
    unrelated.write_text('weather')
    unknown = cfg.output / 'recipe-queued-unknown.png'
    unknown.write_text('unknown')
    state = service.journal(cfg)
    service.cleanup(cfg, state, now.timestamp() + 10 * 86400)
    assert snapshot.exists() and unrelated.exists() and unknown.exists()
    state['records'][0]['released'] = True
    service.cleanup(cfg, state, now.timestamp() + 10 * 86400)
    assert not snapshot.exists() and unrelated.exists() and unknown.exists()


def test_snapshot_cap_preserves_unknown(populated, fake_frame, now):
    cfg = replace(populated, max_snapshots=1)
    cfg.output.mkdir()
    (cfg.output / 'recipe-queued-unknown.png').write_bytes(b'old')
    assert 'cap' in service.queue(cfg, now)['reason']
    assert not fake_frame.calls


def test_corrupt_queue_state_fails_closed_but_refresh_works(populated, now):
    cfg = populated
    (cfg.state / 'queue.json').write_text('broken')
    with pytest.raises(ValueError, match='corrupt'):
        service.queue(cfg, now)
    assert service.refresh(cfg, now)
    assert service.status(cfg, now)['queue_error']


def test_dry_run_no_network_or_writes(cfg, now):
    before = list(cfg.state.parent.iterdir())
    assert service.tick(cfg, now, True)['dry_run']
    assert list(cfg.state.parent.iterdir()) == before


@pytest.mark.parametrize('text', ['queue_minutes = 0', 'duration = true', 'typo = 1', 'source_mode = "goodfood"\nautomated_metadata_permission = false', 'window_start = "25:00"', 'api_url = "https://example.com/api/command"', 'width = 800', 'include_trending = 1'])
def test_bad_config_rejected(tmp_path, text):
    path = tmp_path / 'config.toml'
    path.write_text(text)
    with pytest.raises((ValueError, KeyError)):
        load(path)


def test_cli_preview_without_state(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / 'examples/recipes.json'
    out = tmp_path / 'preview.png'
    assert main(['preview', '--fixture', str(fixture), '--output', str(out)]) == 0
    assert out.is_file()


def test_simulated_weather_replacements_cannot_starve_recipe(populated, now, monkeypatch):
    """Contract simulator: shared countdown plus FIFO dictionary replacement.

    This exercises repeated three-minute weather submissions while the recipe
    service checks every minute; no frame implementation is imported.
    """
    pending = {}
    seen = []
    countdown = 8
    active = None
    active_until = -1
    def http(url, timeout, payload=None):
        if payload is not None:
            pending[payload['type']] = payload
            return '{"ok":true,"action":"queue_card"}'
        return json.dumps({'card': dict(active=active is not None, type=active,
                          pending=len(pending), photos_until_next=countdown)})
    monkeypatch.setattr(source, 'request', http)
    for minute in range(60):
        if active_until <= minute:
            active = None
        if minute % 3 == 0:
            pending['weather'] = {'type': 'weather', 'path': 'weather.png'}
        service.queue(populated, now + timedelta(minutes=minute))
        # One normal photo each simulated minute. Every card resets the SAME
        # eight-photo gap, matching the inspected default frame contract.
        if active is None:
            if countdown <= 0 and pending:
                active = next(iter(pending))
                del pending[active]
                seen.append(active)
                countdown = 8
                active_until = minute + 1
            else:
                countdown = max(0, countdown - 1)
    assert 'recipe' in seen and 'weather' in seen
    assert seen[:2] == ['weather', 'recipe']


def test_post_response_rejected_and_no_immediate_retry(populated, now, monkeypatch):
    calls = []
    def http(url, timeout, payload=None):
        if payload is None:
            return '{"card":{"active":false,"type":null,"pending":0,"photos_until_next":8}}'
        calls.append(payload)
        return '{"ok":false}'
    monkeypatch.setattr(source, 'request', http)
    assert not service.queue(populated, now)['queued']
    assert not service.queue(populated, now + timedelta(seconds=5))['queued']
    assert len(calls) == 1 and not service.journal(populated)['records'][0]['released']


def test_closing_window_during_generation_does_not_submit(populated, fake_frame, now, monkeypatch):
    monkeypatch.setattr(service, 'utcnow', lambda: now.replace(hour=20, minute=30))
    result = service.queue(populated, now.replace(hour=20, minute=29, second=59))
    assert not result['queued'] and 'closed' in result['reason']
    assert not fake_frame.calls and not list(populated.output.glob('recipe-queued-*.png'))


def test_rotation_cycles_all_recipes_before_reuse(populated, fake_frame, now):
    selected = []
    for i in range(7):
        fake_frame.card = dict(active=False, type=None, pending=0, photos_until_next=8)
        result = service.queue(populated, now + timedelta(minutes=16 * i))
        assert result['queued']
        selected.append(result['recipe'])
    assert len(set(selected[:3])) == 3
    assert all(a != b for a, b in zip(selected, selected[1:]))


def test_snapshot_name_collision_never_overwrites(populated, fake_frame, now, monkeypatch):
    cfg = populated
    cfg.output.mkdir()
    existing = cfg.output / 'recipe-queued-collision.png'
    existing.write_bytes(b'never change me')
    class Identity:
        hex = 'collision'
    monkeypatch.setattr(service.uuid, 'uuid4', Identity)
    assert not service.queue(cfg, now)['queued']
    assert existing.read_bytes() == b'never change me' and not fake_frame.calls
