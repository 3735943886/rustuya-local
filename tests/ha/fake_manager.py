"""A stand-in for `rustuya_manager.Manager`, so the config/options flow tests exercise the flow's own state machine
(the steps, the polling, error/skip paths) without a real Tuya Cloud login, broker, or bridge."""

from __future__ import annotations

from dataclasses import dataclass, field


class WizardState:
    IDLE, REQUESTING_QR, AWAITING_SCAN, LOGGED_IN, FETCHING, DONE, ERROR = (
        "idle", "requesting_qr", "awaiting_scan", "logged_in", "fetching", "done", "error")


@dataclass
class Session:
    state: str = WizardState.IDLE
    qr_url: str | None = None
    qr_image_data_url: str | None = None
    message: str = ""
    error: str | None = None


class FakeWizard:
    """No background task, no wall-clock timing: `session.state` only changes when a test calls `advance()` (or the
    convenience `autoplay`, for a test that just wants a normal login to finish). `async_step_cloud_wizard_progress`
    polls with a real `asyncio.sleep`-based task (`_async_wait_wizard_tick`), so driving state changes from a task
    the flow's own machinery can observe — not a task of the fake's own that Home Assistant doesn't track — is what
    keeps `hass.async_block_till_done()` able to settle deterministically instead of racing it."""

    def __init__(self, script: list[str] | None = None, autoplay: bool = True) -> None:
        self.session = Session()
        self._script = script or [WizardState.REQUESTING_QR, WizardState.AWAITING_SCAN, WizardState.LOGGED_IN,
                                  WizardState.FETCHING, WizardState.DONE]
        self._i = 0
        self.autoplay = autoplay
        self.started_with: str | None = None

    async def start(self, user_code=None, scan=False):
        self.started_with = user_code
        self._advance()
        return self.session

    async def read_saved_user_code(self):
        return None

    def advance(self) -> None:
        """Move to the next state in the script (a no-op once the last one is reached)."""
        self._i = min(self._i + 1, len(self._script) - 1)
        self._advance()

    def _advance(self) -> None:
        self.session.state = self._script[self._i]
        if self.session.state == WizardState.AWAITING_SCAN:
            self.session.qr_url = "tuyaSmart--qrLogin?token=fake"
            self.session.qr_image_data_url = "data:image/png;base64,Zm9v"
        else:
            # mirrors the real wizard's qr_callback(None), fired once scanning moves the state on
            self.session.qr_url = None
            self.session.qr_image_data_url = None
        if self.session.state == WizardState.ERROR:
            self.session.error = "login failed"


@dataclass
class FakeDevice:
    id: str
    name: str = "Device"
    type: str = "WiFi"
    key: str | None = None
    ip: str = "Auto"
    version: str = "Auto"
    cid: str | None = None
    parent_id: str | None = None


@dataclass
class FakeDiff:
    synced: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    orphaned: list = field(default_factory=list)
    mismatched: list = field(default_factory=list)      # [(FakeDevice, ["IP: 1.1.1.1 -> 2.2.2.2"])]


class FakeManager:
    """`sync_result`, `wizard_script` and `scan_result` let a test shape one manager's behaviour."""

    def __init__(self, sync_result: FakeDiff | None = None, wizard_script: list[str] | None = None,
                 scan_result: dict | None = None, **kw) -> None:
        self.kwargs = kw
        self.wizard = FakeWizard(wizard_script)
        self._sync_result = sync_result or FakeDiff()
        self._scan_result = scan_result or {}
        self.added: list[str] = []
        self.removed: list[str] = []
        self.commands: list[tuple] = []     # every publish_command: (action, target_id, target_name, extra)
        self.publish_error: Exception | None = None
        self.closed = False

        class _ScanCoordinator:
            async def run(_self):
                return self._scan_result

        self.scan_coordinator = _ScanCoordinator()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def sync(self):
        return self._sync_result

    async def publish_command(self, action, *, target_id=None, target_name=None, extra=None):
        if self.publish_error is not None:
            raise self.publish_error
        self.commands.append((action, target_id, target_name, extra))
        if action == "add":
            self.added.append(target_id)

    async def add_device(self, target_id, **extra):
        self.added.append(target_id)

    async def remove_device(self, target_id, **extra):
        self.removed.append(target_id)


def install(monkeypatch, manager: FakeManager) -> None:
    """Point `manager_session.open_manager`/`close_manager` at `manager` for the duration of a test."""
    from custom_components.rustuya import manager_session

    async def open_manager(**kw):
        manager.kwargs = kw
        await manager.__aenter__()
        return manager

    async def close_manager(m):
        await m.__aexit__(None, None, None)

    monkeypatch.setattr(manager_session, "open_manager", open_manager)
    monkeypatch.setattr(manager_session, "close_manager", close_manager)
    monkeypatch.setattr(manager_session, "available", lambda: True)

    import sys
    import types

    fake_module = types.ModuleType("rustuya_manager.wizard")
    fake_module.WizardState = WizardState
    monkeypatch.setitem(sys.modules, "rustuya_manager.wizard", fake_module)
