import asyncio
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vice import main as main_mod
from vice.config import Config, DiscordConfig, DiscordCustomGame
from vice.discord_rpc import DiscordRPC
from vice.main import ViceDaemon


OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, dict]:
    header = await reader.readexactly(8)
    op, length = struct.unpack("<II", header)
    body = await reader.readexactly(length) if length else b""
    payload = json.loads(body.decode("utf-8")) if body else {}
    return op, payload


async def _write_frame(writer: asyncio.StreamWriter, op: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    writer.write(struct.pack("<II", op, len(body)) + body)
    await writer.drain()


class DiscordRPCTests(unittest.IsolatedAsyncioTestCase):
    async def _with_server(self, handler, test_body) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            socket_path = Path(tmp) / "discord-ipc-0"
            server = await asyncio.start_unix_server(handler, path=str(socket_path))
            try:
                with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}, clear=False):
                    await test_body()
            finally:
                server.close()
                await server.wait_closed()

    async def test_connect_and_set_activity_success(self) -> None:
        frames: list[tuple[int, dict]] = []
        activity_seen = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                frames.append(await _read_frame(reader))
                await _write_frame(writer, OP_FRAME, {"evt": "READY"})
                frames.append(await _read_frame(reader))
                await _write_frame(writer, OP_FRAME, {"cmd": "SET_ACTIVITY"})
                activity_seen.set()
            finally:
                writer.close()

        async def body() -> None:
            rpc = DiscordRPC("client-id", timeout=0.2)
            self.assertTrue(await rpc.connect())
            activity = {"details": "Clipping with Vice", "state": "Counter-Strike 2"}
            self.assertTrue(await rpc.set_activity(activity))
            await asyncio.wait_for(activity_seen.wait(), timeout=0.5)
            await rpc.close()

        await self._with_server(handler, body)
        self.assertEqual(frames[0][0], OP_HANDSHAKE)
        self.assertEqual(frames[0][1]["client_id"], "client-id")
        self.assertEqual(frames[1][1]["args"]["activity"]["state"], "Counter-Strike 2")

    async def test_connect_rejects_discord_close_frame(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await _read_frame(reader)
                await _write_frame(writer, OP_CLOSE, {"code": 4000, "message": "bad client"})
            finally:
                writer.close()

        async def body() -> None:
            rpc = DiscordRPC("client-id", timeout=0.2)
            self.assertFalse(await rpc.connect())
            self.assertFalse(rpc.is_connected)

        await self._with_server(handler, body)

    async def test_set_activity_times_out_and_resets_socket(self) -> None:
        activity_seen = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await _read_frame(reader)
                await _write_frame(writer, OP_FRAME, {"evt": "READY"})
                await _read_frame(reader)
                activity_seen.set()
                await asyncio.sleep(0.2)
            finally:
                writer.close()

        async def body() -> None:
            rpc = DiscordRPC("client-id", timeout=0.03)
            self.assertTrue(await rpc.connect())
            self.assertFalse(await rpc.set_activity({"state": "Game"}))
            await asyncio.wait_for(activity_seen.wait(), timeout=0.5)
            self.assertFalse(rpc.is_connected)

        await self._with_server(handler, body)

    async def test_set_activity_close_frame_resets_socket(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await _read_frame(reader)
                await _write_frame(writer, OP_FRAME, {"evt": "READY"})
                await _read_frame(reader)
                await _write_frame(writer, OP_CLOSE, {"code": 4000, "message": "closed"})
            finally:
                writer.close()

        async def body() -> None:
            rpc = DiscordRPC("client-id", timeout=0.2)
            self.assertTrue(await rpc.connect())
            self.assertFalse(await rpc.set_activity({"state": "Game"}))
            self.assertFalse(rpc.is_connected)

        await self._with_server(handler, body)


class _FakeDiscordRPC:
    instances: list["_FakeDiscordRPC"] = []

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.connected = False
        self.activities: list[dict | None] = []
        _FakeDiscordRPC.instances.append(self)

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def set_activity(self, activity: dict | None) -> bool:
        self.activities.append(activity)
        return True

    async def close(self) -> None:
        self.connected = False


def _discord_daemon(*, enabled: bool = True, client_id: str | None = None) -> ViceDaemon:
    daemon = ViceDaemon.__new__(ViceDaemon)
    daemon.cfg = Config(discord=DiscordConfig(enabled=enabled, client_id_override=client_id))
    daemon._discord_rpc = None
    daemon._discord_task = None
    daemon._discord_client_id = None
    daemon._discord_current_game = None
    daemon._discord_started_at = 0.0
    daemon._discord_last_activity = None
    daemon._discord_current_pid = 0
    daemon._discord_game_comm = ""
    daemon._discord_scan_tick = 0
    daemon._discord_no_socket_logged = False
    daemon._discord_no_window_adapter_logged = False
    return daemon


class GameMatchSpecificityTests(unittest.TestCase):
    """Needles are substrings, so a short one can swallow a longer, more
    specific one. Matching takes the longest needle rather than the first in
    list order, which is what makes a Steam id safe to add when a longer id
    starts with the same digits."""

    def test_longer_steam_id_wins_over_a_prefix(self) -> None:
        daemon = _discord_daemon()
        # steam_app_400 is a prefix of Garry's Mod's steam_app_4000, and
        # steam_app_220 of Kerbal's steam_app_220200.
        for cls, want in (
            ("steam_app_400", "Portal"),
            ("steam_app_4000", "Garry's Mod"),
            ("steam_app_220", "Half-Life 2"),
            ("steam_app_220200", "Kerbal Space Program"),
        ):
            with self.subTest(cls=cls):
                self.assertEqual(daemon._match_game({"process": "", "class": cls}), want)

    def test_sequel_is_not_swallowed_by_its_predecessor(self) -> None:
        daemon = _discord_daemon()
        for proc, want in (
            ("hades2.exe", "Hades II"),
            ("hades.exe", "Hades"),
            ("slaythespire2.exe", "Slay the Spire 2"),
            ("sonsoftheforest.exe", "Sons of the Forest"),
        ):
            with self.subTest(proc=proc):
                self.assertEqual(daemon._match_game({"process": proc, "class": ""}), want)

    def test_generic_unreal_binary_loses_to_the_prefixed_one(self) -> None:
        # Several games ship a *Client-Win64-Shipping.exe; the one carrying the
        # game's own prefix is the answer.
        daemon = _discord_daemon()
        self.assertEqual(
            daemon._match_game({"process": "deltaforceclient-win64-shipping.exe", "class": ""}),
            "Delta Force",
        )

    def test_custom_games_still_beat_the_bundled_list(self) -> None:
        # Specificity is applied within each tier, never across them: an
        # explicit user entry outranks a longer bundled needle.
        daemon = _discord_daemon()
        daemon.cfg.discord.custom_games = [DiscordCustomGame(name="My Build", matches=["cs2"])]
        self.assertEqual(
            daemon._match_game({"process": "cs2.exe", "class": "cs2"}), "My Build"
        )

    def test_every_bundled_needle_resolves_to_its_own_game(self) -> None:
        daemon = _discord_daemon()
        for game in main_mod._DEFAULT_GAMES:
            for needle in game["matches"]:
                with self.subTest(game=game["name"], needle=needle):
                    self.assertEqual(
                        daemon._match_game({"process": needle.lower(), "class": ""}),
                        game["name"],
                    )

    def test_valve_titles_are_present(self) -> None:
        daemon = _discord_daemon()
        for proc, want in (
            ("hl.exe", "Half-Life"),
            ("hl_linux", "Half-Life"),
            ("hl2.exe", "Half-Life 2"),
            ("bms.exe", "Black Mesa"),
            ("portal.exe", "Portal"),
            ("portal2.exe", "Portal 2"),
        ):
            with self.subTest(proc=proc):
                self.assertEqual(daemon._match_game({"process": proc, "class": ""}), want)


class DiscordGameDatabaseTests(unittest.TestCase):
    def test_bundled_games_json_is_valid_expanded_catalog(self) -> None:
        games = main_mod._DEFAULT_GAMES

        self.assertGreaterEqual(len(games), 100)
        names: list[str] = []
        for idx, game in enumerate(games, start=1):
            with self.subTest(idx=idx, game=game.get("name")):
                name = game.get("name")
                matches = game.get("matches")
                self.assertIsInstance(name, str)
                self.assertTrue(name.strip())
                self.assertIsInstance(matches, list)
                self.assertTrue(matches)
                self.assertTrue(all(isinstance(m, str) and m.strip() for m in matches))
                names.append(name)

        self.assertEqual(len(names), len(set(names)))

    def test_representative_bundled_game_matches(self) -> None:
        daemon = _discord_daemon()
        cases = [
            ({"process": "OuterWilds.exe", "class": "", "pid": 1}, "Outer Wilds"),
            ({"process": "", "class": "steam_app_753640", "pid": 1}, "Outer Wilds"),
            ({"process": "TslGame.exe", "class": "", "pid": 1}, "PUBG: BATTLEGROUNDS"),
            ({"process": "RustClient.exe", "class": "", "pid": 1}, "Rust"),
            ({"process": "Warframe.x64.exe", "class": "", "pid": 1}, "Warframe"),
            ({"process": "GTA5.exe", "class": "", "pid": 1}, "Grand Theft Auto V"),
            ({"process": "FiveM.exe", "class": "", "pid": 1}, "FiveM"),
            ({"process": "Hades2.exe", "class": "", "pid": 1}, "Hades II"),
            ({"process": "SlayTheSpire2.exe", "class": "", "pid": 1}, "Slay the Spire 2"),
            ({"process": "CivilizationVII.exe", "class": "", "pid": 1}, "Sid Meier's Civilization VII"),
            ({"process": "StreetFighter6.exe", "class": "", "pid": 1}, "Street Fighter 6"),
        ]

        for win, expected in cases:
            with self.subTest(expected=expected, win=win):
                self.assertEqual(daemon._match_game(win), expected)


class DiscordPresenceLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeDiscordRPC.instances = []

    def test_activity_details_include_game_name(self) -> None:
        daemon = _discord_daemon()
        daemon._discord_started_at = 1234.0

        activity = daemon._discord_activity("Outer Wilds")

        self.assertEqual(activity["details"], "Clipping Outer Wilds with Vice")
        self.assertEqual(activity["state"], "Outer Wilds")
        self.assertEqual(activity["timestamps"]["start"], 1234)

    async def test_presence_sends_activity_only_when_game_changes(self) -> None:
        daemon = _discord_daemon()
        real_sleep = asyncio.sleep
        sleep_calls = 0

        async def fake_sleep(_: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 2:
                daemon.cfg.discord.enabled = False
            await real_sleep(0)

        with mock.patch("vice.discord_rpc.DiscordRPC", _FakeDiscordRPC), \
             mock.patch("vice.active_window.supported_compositor", return_value=True), \
             mock.patch("vice.active_window.get_active_window", return_value={"process": "cs2", "class": "", "pid": 1}), \
             mock.patch("vice.main.asyncio.sleep", new=fake_sleep), \
             mock.patch("vice.main.time.time", return_value=1234.0):
            await daemon._discord_presence_loop()

        rpc = _FakeDiscordRPC.instances[0]
        non_null = [activity for activity in rpc.activities if activity is not None]
        self.assertEqual(len(non_null), 1)
        self.assertEqual(non_null[0]["state"], "Counter-Strike 2")
        self.assertEqual(non_null[0]["details"], "Clipping Counter-Strike 2 with Vice")

    async def test_presence_clears_when_focus_leaves_game(self) -> None:
        # With persistence off, presence follows focus exactly (old behavior).
        daemon = _discord_daemon()
        daemon.cfg.discord.persist_while_running = False
        real_sleep = asyncio.sleep
        windows = [
            {"process": "cs2", "class": "", "pid": 1},
            {"process": "kitty", "class": "kitty", "pid": 2},
        ]
        sleep_calls = 0

        def fake_window():
            return windows.pop(0) if windows else {"process": "kitty", "class": "kitty", "pid": 2}

        async def fake_sleep(_: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 2:
                daemon.cfg.discord.enabled = False
            await real_sleep(0)

        with mock.patch("vice.discord_rpc.DiscordRPC", _FakeDiscordRPC), \
             mock.patch("vice.active_window.supported_compositor", return_value=True), \
             mock.patch("vice.active_window.get_active_window", side_effect=fake_window), \
             mock.patch("vice.main.asyncio.sleep", new=fake_sleep), \
             mock.patch("vice.main.time.time", return_value=1234.0):
            await daemon._discord_presence_loop()

        rpc = _FakeDiscordRPC.instances[0]
        self.assertEqual([a["state"] if a else None for a in rpc.activities], ["Counter-Strike 2", None])

    async def test_presence_persists_while_game_process_runs(self) -> None:
        # Issue #112: alt-tabbing away must not clear the card while the
        # game's process is still alive.
        daemon = _discord_daemon()
        real_sleep = asyncio.sleep
        windows = [{"process": "cs2", "class": "", "pid": 42}]
        sleep_calls = 0

        def fake_window():
            return windows.pop(0) if windows else {"process": "kitty", "class": "kitty", "pid": 2}

        mid_loop_activities: list = []

        async def fake_sleep(_: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 2:
                # After the unfocused tick, before the shutdown clear.
                mid_loop_activities[:] = _FakeDiscordRPC.instances[0].activities
            if sleep_calls >= 3:
                daemon.cfg.discord.enabled = False
            await real_sleep(0)

        def fake_comm(pid: int) -> str:
            return "cs2" if pid == 42 else "kitty"

        with mock.patch("vice.discord_rpc.DiscordRPC", _FakeDiscordRPC), \
             mock.patch("vice.active_window.supported_compositor", return_value=True), \
             mock.patch("vice.active_window.uses_x11_adapter", return_value=False), \
             mock.patch("vice.active_window.get_active_window", side_effect=fake_window), \
             mock.patch("vice.active_window._read_proc_comm", side_effect=fake_comm), \
             mock.patch("vice.main.asyncio.sleep", new=fake_sleep), \
             mock.patch("vice.main.time.time", return_value=1234.0):
            await daemon._discord_presence_loop()

        # The unfocused tick kept the card up; nothing was cleared until the
        # loop itself shut down.
        self.assertEqual(len(mid_loop_activities), 1)
        self.assertIsNotNone(mid_loop_activities[0])
        self.assertEqual(mid_loop_activities[0]["state"], "Counter-Strike 2")

    async def test_presence_clears_when_game_process_exits(self) -> None:
        daemon = _discord_daemon()
        real_sleep = asyncio.sleep
        windows = [{"process": "cs2", "class": "", "pid": 42}]
        game_alive = True
        sleep_calls = 0

        def fake_window():
            return windows.pop(0) if windows else None

        async def fake_sleep(_: float) -> None:
            nonlocal sleep_calls, game_alive
            sleep_calls += 1
            game_alive = False
            if sleep_calls >= 3:
                daemon.cfg.discord.enabled = False
            await real_sleep(0)

        def fake_comm(pid: int) -> str:
            return "cs2" if (pid == 42 and game_alive) else ""

        with mock.patch("vice.discord_rpc.DiscordRPC", _FakeDiscordRPC), \
             mock.patch("vice.active_window.supported_compositor", return_value=True), \
             mock.patch("vice.active_window.uses_x11_adapter", return_value=False), \
             mock.patch("vice.active_window.get_active_window", side_effect=fake_window), \
             mock.patch("vice.active_window._read_proc_comm", side_effect=fake_comm), \
             mock.patch("vice.active_window.list_candidate_windows", return_value=[]), \
             mock.patch("vice.main.asyncio.sleep", new=fake_sleep), \
             mock.patch("vice.main.time.time", return_value=1234.0):
            await daemon._discord_presence_loop()

        rpc = _FakeDiscordRPC.instances[0]
        states = [a["state"] if a else None for a in rpc.activities]
        self.assertEqual(states, ["Counter-Strike 2", None])

    async def test_candidate_scan_finds_unfocused_game(self) -> None:
        # Issue #102: KWin can't report the focused XWayland window, so a
        # periodic scan of visible windows finds the running game instead.
        daemon = _discord_daemon()
        real_sleep = asyncio.sleep
        sleep_calls = 0

        async def fake_sleep(_: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 5:
                daemon.cfg.discord.enabled = False
            await real_sleep(0)

        with mock.patch("vice.discord_rpc.DiscordRPC", _FakeDiscordRPC), \
             mock.patch("vice.active_window.supported_compositor", return_value=True), \
             mock.patch("vice.active_window.uses_x11_adapter", return_value=False), \
             mock.patch("vice.active_window.get_active_window", return_value=None), \
             mock.patch("vice.active_window._read_proc_comm", return_value="cs2"), \
             mock.patch(
                 "vice.active_window.list_candidate_windows",
                 return_value=[{"process": "cs2", "class": "", "pid": 7}],
             ), \
             mock.patch("vice.main.asyncio.sleep", new=fake_sleep), \
             mock.patch("vice.main.time.time", return_value=1234.0):
            await daemon._discord_presence_loop()

        rpc = _FakeDiscordRPC.instances[0]
        non_null = [a for a in rpc.activities if a is not None]
        self.assertTrue(non_null, rpc.activities)
        self.assertEqual(non_null[0]["state"], "Counter-Strike 2")

    async def test_sync_restarts_presence_task_when_client_id_changes(self) -> None:
        daemon = _discord_daemon(client_id="new-client")
        old_task = asyncio.create_task(asyncio.sleep(100))
        daemon._discord_task = old_task
        daemon._discord_client_id = "old-client"

        async def fake_presence_loop(self):
            await asyncio.Event().wait()

        with mock.patch.object(ViceDaemon, "_discord_presence_loop", new=fake_presence_loop):
            await daemon._sync_discord_presence_task()

        try:
            self.assertTrue(old_task.done())
            self.assertIsNot(daemon._discord_task, old_task)
            self.assertIsNotNone(daemon._discord_task)
        finally:
            if daemon._discord_task:
                daemon._discord_task.cancel()
                await asyncio.gather(daemon._discord_task, return_exceptions=True)


class CompositorAdapterTests(unittest.TestCase):
    """Issue #102: KDE/GNOME Wayland fall back to the X11 (XWayland) adapter."""

    def _detect(self, env):
        from vice import active_window
        with mock.patch.dict(os.environ, env, clear=True):
            return active_window._detect_compositor_adapter()

    def test_hyprland_selected(self):
        from vice import active_window
        adapter = self._detect({"HYPRLAND_INSTANCE_SIGNATURE": "abc"})
        self.assertIs(adapter, active_window._get_active_window_hyprland)

    def test_sway_selected(self):
        from vice import active_window
        adapter = self._detect({"SWAYSOCK": "/run/sway.sock"})
        self.assertIs(adapter, active_window._get_active_window_sway)

    def test_pure_x11_selected(self):
        from vice import active_window
        adapter = self._detect({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
        self.assertIs(adapter, active_window._get_active_window_x11)

    def test_kde_wayland_falls_back_to_xwayland(self):
        from vice import active_window
        adapter = self._detect({
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "KDE",
            "WAYLAND_DISPLAY": "wayland-0",
            "DISPLAY": ":1",  # XWayland present
        })
        self.assertIs(adapter, active_window._get_active_window_x11)

    def test_headless_wayland_unsupported(self):
        # No DISPLAY → no XWayland → no adapter.
        adapter = self._detect({
            "XDG_SESSION_TYPE": "wayland",
            "WAYLAND_DISPLAY": "wayland-0",
        })
        self.assertIsNone(adapter)


if __name__ == "__main__":
    unittest.main()


class CandidateWindowScanTests(unittest.TestCase):
    def test_wmctrl_output_parses_to_windows(self) -> None:
        from vice import active_window as aw

        out = (
            "0x03a00003  0 4242   steam_app_271590.steam_app_271590  host GTA V\n"
            "0x04c00007  0 1337   PrismLauncher.PrismLauncher  host Prism Launcher\n"
            "0x00000001 -1 0      N/A  host Desktop\n"
        )
        with mock.patch.object(aw, "_run", return_value=out):
            with mock.patch.object(aw, "_read_proc_comm", return_value="proc"):
                windows = aw._candidate_windows_wmctrl()

        self.assertEqual(
            [(w["class"], w["pid"]) for w in windows],
            [("steam_app_271590", 4242), ("PrismLauncher", 1337), ("N/A", 0)],
        )

    def test_candidate_windows_empty_on_non_x11_adapters(self) -> None:
        from vice import active_window as aw

        with mock.patch.object(aw, "_ADAPTER", aw._get_active_window_hyprland):
            self.assertEqual(aw.list_candidate_windows(), [])


class PointerDisplayTests(unittest.TestCase):
    """#133: which monitor the pointer is on, named the way the capture
    backends name it."""

    HYPR_MONITORS = json.dumps([
        {"name": "DP-1", "x": 0, "y": 0, "width": 2560, "height": 1440, "scale": 1.0,
         "focused": False},
        {"name": "HDMI-A-1", "x": 2560, "y": 0, "width": 1920, "height": 1080,
         "scale": 1.0, "focused": True},
    ])

    def test_hyprland_maps_the_cursor_to_its_monitor(self) -> None:
        from vice import active_window as aw

        def fake_run(cmd, timeout=1.0):
            if cmd[1] == "monitors":
                return self.HYPR_MONITORS
            return json.dumps({"x": 3000, "y": 500})

        with mock.patch.object(aw, "_run", side_effect=fake_run):
            self.assertEqual(aw._pointer_display_hyprland(), "HDMI-A-1")

    def test_hyprland_divides_by_scale(self) -> None:
        """Monitor width/height are the raw mode; the cursor is in logical
        coordinates, so a 2x monitor is half as wide as it reports."""
        from vice import active_window as aw

        monitors = json.dumps([
            {"name": "DP-1", "x": 0, "y": 0, "width": 3840, "height": 2160,
             "scale": 2.0, "focused": True},
            {"name": "DP-2", "x": 1920, "y": 0, "width": 1920, "height": 1080,
             "scale": 1.0, "focused": False},
        ])

        def fake_run(cmd, timeout=1.0):
            return monitors if cmd[1] == "monitors" else json.dumps({"x": 2500, "y": 100})

        with mock.patch.object(aw, "_run", side_effect=fake_run):
            self.assertEqual(aw._pointer_display_hyprland(), "DP-2")

    def test_hyprland_falls_back_to_the_focused_monitor(self) -> None:
        from vice import active_window as aw

        def fake_run(cmd, timeout=1.0):
            return self.HYPR_MONITORS if cmd[1] == "monitors" else ""

        with mock.patch.object(aw, "_run", side_effect=fake_run):
            self.assertEqual(aw._pointer_display_hyprland(), "HDMI-A-1")

    def test_sway_uses_the_focused_output(self) -> None:
        from vice import active_window as aw

        outputs = json.dumps([
            {"name": "DP-1", "focused": False},
            {"name": "eDP-1", "focused": True},
        ])
        with mock.patch.object(aw, "_run", return_value=outputs):
            self.assertEqual(aw._pointer_display_sway(), "eDP-1")

    def test_x11_maps_the_pointer_through_xrandr_geometry(self) -> None:
        from vice import active_window as aw

        listing = (
            "Monitors: 2\n"
            " 0: +*DP-1 2560/600x1440/340+0+0  DP-1\n"
            " 1: +HDMI-A-1 1920/520x1080/290+2560+0  HDMI-A-1\n"
        )

        def fake_run(cmd, timeout=1.0):
            if cmd[0] == "xdotool":
                return "X=2600\nY=300\nSCREEN=0\nWINDOW=12\n"
            return listing

        with mock.patch.object(aw, "_run", side_effect=fake_run):
            self.assertEqual(aw._pointer_display_x11(), "HDMI-A-1")

    def test_pointer_outside_every_monitor_is_unknown(self) -> None:
        from vice import active_window as aw

        rects = aw._parse_xrandr_monitor_rects(" 0: +*DP-1 2560/600x1440/340+0+0  DP-1\n")
        self.assertEqual(rects, [{"name": "DP-1", "x": 0, "y": 0, "w": 2560, "h": 1440}])
        self.assertIsNone(aw._monitor_at((5000, 100), rects))

    def test_unsupported_session_reports_no_pointer_display(self) -> None:
        from vice import active_window as aw

        with mock.patch.object(aw, "_ADAPTER", None):
            self.assertFalse(aw.pointer_display_supported())
            self.assertIsNone(aw.pointer_display())

    def test_xwayland_does_not_count_as_x11_here(self) -> None:
        # The X pointer only tracks the real one over X surfaces, so KDE/GNOME
        # Wayland must not claim support just because XWayland is up.
        from vice import active_window as aw

        with mock.patch.object(aw, "_ADAPTER", aw._get_active_window_x11), \
             mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            self.assertFalse(aw.pointer_display_supported())
