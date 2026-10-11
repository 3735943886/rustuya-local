"""A cover's settings written through IL are saved by the host (`SaveSettings` -> `OverrideWatcher.save_settings` ->
`zz_settings.json`), applied at once and kept across restarts."""
import asyncio
import json
import threading

import pytest
from tuya2ildevice import Command, Connected, Message

from rustuya_local.host import (
    SETTINGS_FILE,
    Hub,
    IlTopics,
    InProcessTransport,
    OverrideWatcher,
    Runner,
    load_overrides,
)
from tests.unit.tuya_samples import curtain


def sent(outs):
    (out,) = outs
    return out.json["dps"]


async def start(conv, **kw):
    il = InProcessTransport()
    watcher = OverrideWatcher(conv, None, interval=60)
    loaded = await watcher.load_initial()
    hub = Hub([curtain()], il=IlTopics("il", "tuya"), overrides=loaded.overrides, device_settings=True)
    commands = []
    runner = Runner(hub, il, on_bridge_command=commands.append, on_settings=watcher.save_settings, **kw)
    watcher.runner = runner
    await runner.start()
    runner.on_bridge_message("cur1", Connected())
    runner.on_bridge_message("cur1", Message("state", {"3": 30}))
    await runner.drain()
    return il, runner, commands


async def test_a_switch_written_through_il_is_saved_applied_and_kept(tmp_path):
    conv = tmp_path / "conv"
    conv.mkdir()
    (conv / "10_mine.json").write_text(json.dumps({"overrides": {"other": {"device": {"label": "x"}}}}))
    il, runner, commands = await start(conv)
    assert il.retained["il/cur1/position"].payload == "30"
    commands.clear()
    await il.publish("il/cur1/cover_invert_position/set", "true", 1, False)
    await runner.drain()
    assert json.loads((conv / SETTINGS_FILE).read_text())["overrides"] == {"cur1": {"cover": {"invert_position": True}}}
    assert not [c for c in commands if c.action == "set"]                     # nothing to the device
    assert il.retained["il/cur1/position"].payload == "70"                    # at once, from the last state
    assert il.retained["il/cur1/cover_invert_position"].payload == "true"
    runner.on_bridge_message("cur1", Message("state", {"3": 20}))
    await runner.drain()
    assert il.retained["il/cur1/position"].payload == "80"
    await runner.stop()

    il, runner, _ = await start(conv)                                          # a restart: still inverted
    assert il.retained["il/cur1/position"].payload == "70" and il.retained["il/cur1/cover_invert_position"].payload == "true"
    await il.publish("il/cur1/cover_invert_position/set", "false", 1, False)
    await runner.drain()
    assert json.loads((conv / SETTINGS_FILE).read_text())["overrides"] == {"cur1": {"cover": {"invert_position": False}}}
    assert il.retained["il/cur1/position"].payload == "30"
    await runner.stop()


async def test_a_sync_callback_and_a_failing_one(tmp_path, caplog):
    saved = []
    il = InProcessTransport()
    runner = Runner(Hub([curtain()], device_settings=True), il, on_settings=lambda i, b: saved.append((i, b)))
    await runner.start()
    await il.publish("il/cur1/cover_state_source/set", "none", 1, False)
    await runner.drain()
    assert saved == [("cur1", {"cover": {"state_source": "none"}})]
    await runner.stop()

    async def broken(i, b):
        raise OSError("disk full")
    runner = Runner(Hub([curtain()], device_settings=True), il, on_settings=broken)
    await runner.start()
    await il.publish("il/cur1/cover_state_source/set", "none", 1, False)
    await runner.drain()
    assert "could not save the settings of cur1" in caplog.text
    await runner.stop()


async def test_save_settings_removes_an_empty_block_and_leaves_a_single_file_alone(tmp_path, caplog):
    watcher = OverrideWatcher(tmp_path, None)
    await watcher.save_settings("a", {"cover": {"invert_control": True}})
    await watcher.save_settings("b", {"cover": {"settle": 2}})
    await watcher.save_settings("a", {})
    assert json.loads((tmp_path / SETTINGS_FILE).read_text())["overrides"] == {"b": {"cover": {"settle": 2}}}
    loaded = load_overrides(tmp_path).overrides
    assert loaded["b"] == {"cover": {"settle": 2}} and "a" not in loaded
    assert [p.name for p in tmp_path.iterdir()] == [SETTINGS_FILE]            # no temporary file left
    (tmp_path / SETTINGS_FILE).write_text("{broken")
    await watcher.save_settings("a", {"cover": {"invert_control": True}})       # logged, the file left to its owner
    assert (tmp_path / SETTINGS_FILE).read_text() == "{broken" and "could not save" in caplog.text
    one = tmp_path / "one.json"
    one.write_text("{}")
    await OverrideWatcher(one, None).save_settings("a", {"cover": {"invert_control": True}})
    assert one.read_text() == "{}" and not (tmp_path / "one.json" / SETTINGS_FILE).exists()




async def test_source_settings_roundtrip_saved_and_restarted(tmp_path):
    il, runner, commands = await start(tmp_path)
    for key, value in [("invert_set_position", "true"), ("position_from_target", "true"),
                       ("invert_position", "true"), ("state_source", "control"), ("invert_reported_motion", "true")]:
        await il.publish(f"il/cur1/cover_{key}/set", value, 1, False)
        await runner.drain()
    d = runner.hub.drivers["cur1"]
    assert "cover_position_from_target" in d.descriptor["props"]
    assert "cover_invert_set_position" not in d.descriptor["props"]
    assert sent(d.handle(2, Command("position", 70))) == {"2": 30}
    assert not [c for c in commands if c.action == "set"]
    await runner.stop()
    il, runner, commands = await start(tmp_path)
    assert il.retained["il/cur1/cover_position_from_target"].payload == "true"
    assert il.retained["il/cur1/cover_state_source"].payload == "control"
    assert il.retained["il/cur1/cover_invert_reported_motion"].payload == "true"
    for mode in ("none", "inferred", "control"):
        await il.publish("il/cur1/cover_state_source/set", mode, 1, False)
        await runner.drain()
    assert il.retained["il/cur1/cover_invert_reported_motion"].payload == "true"
    await il.publish("il/cur1/cover_position_from_target/set", "false", 1, False)
    await runner.drain()
    d = runner.hub.drivers["cur1"]
    assert d.state["covers"][0]["cur"]["code"] == "percent_state"
    assert il.retained["il/cur1/cover_invert_set_position"].payload == "true"
    assert il.retained["il/cur1/position"].payload == "70"
    assert not [c for c in commands if c.action == "set"]
    await runner.stop()




@pytest.mark.parametrize('cancel_first', [False, True])
async def test_concurrent_saves_keep_both_devices_even_when_one_is_cancelled(tmp_path, monkeypatch, cancel_first):
    watcher = OverrideWatcher(tmp_path, None)
    entered = asyncio.Event()
    release = threading.Event()
    second_entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    write = watcher._write_settings

    def blocked(device_id, block):
        if device_id == 'a':
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError('test did not release the first save')
        else:
            loop.call_soon_threadsafe(second_entered.set)
        write(device_id, block)

    monkeypatch.setattr(watcher, '_write_settings', blocked)
    first = asyncio.create_task(watcher.save_settings('a', {'cover': {'invert_position': True}}))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if cancel_first:
            first.cancel()
        second = asyncio.create_task(watcher.save_settings('b', {'cover': {'invert_control': True}}))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_entered.wait(), 0.05)
    finally:
        release.set()
        results = await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError) if cancel_first else results[0] is None
    assert results[1] is None
    assert json.loads((tmp_path / SETTINGS_FILE).read_text())['overrides'] == {
        'a': {'cover': {'invert_position': True}},
        'b': {'cover': {'invert_control': True}},
    }
    assert list(tmp_path.iterdir()) == [tmp_path / SETTINGS_FILE]

