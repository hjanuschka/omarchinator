import json
import os
import unittest
from unittest import mock

from omarchy_last_session import config, hypr, proc, session, setups
from omarchy_last_session.restore import launch, placement, sweep
from tests.helpers import StateDirCase, client, saved_window


class NamedSetups(StateDirCase):
    def test_save_does_not_replace_last_and_can_list_and_inspect(self):
        self.write_session([{"class": "old"}])
        live = client("foot", title="Shell")
        with (
            mock.patch.object(hypr, "query", return_value=[live]),
            mock.patch.object(proc, "read_cmdline", return_value=["/bin/sh"]),
        ):
            setups.save("Company A")
        self.assertEqual(self.read_session(), [{"class": "old"}])
        self.assertEqual([s["name"] for s in setups.list_setups()], ["Company A"])
        self.assertEqual(setups.describe("Company A")["entries"][0]["title"], "Shell")
        self.assertEqual(os.stat(setups.setup_dir("Company A")).st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            setups.save("Company A")

    def test_screenshot_is_local_and_private(self):
        monitor = {"name": "DP-1", "focused": True}
        def fake_run(argv, **kwargs):
            with open(argv[-1], "wb") as f:
                f.write(b"PNG")
            return mock.Mock(returncode=0)
        with (
            mock.patch.object(session, "snapshot_windows", return_value=[]),
            mock.patch.object(hypr, "query", return_value=[monitor]),
            mock.patch.object(setups.subprocess, "run", side_effect=fake_run) as process,
        ):
            setups.save("Company A", screenshot=True)
        screenshot = os.path.join(setups.setup_dir("Company A"), "preview.png")
        self.assertEqual(os.stat(screenshot).st_mode & 0o777, 0o600)
        self.assertEqual(process.call_args_list[0].args[0][:3], ["grim", "-o", "DP-1"])
        self.assertNotIn("preview.png", os.listdir(self.dir.name))

    def test_each_window_gets_a_private_preview_even_on_another_workspace(self):
        windows = [client("foot", stableId=18000007, workspace={"id": 2, "name": "2"}),
                   client("foot", stableId=18000009, workspace={"id": 4, "name": "4"})]
        def fake_run(argv, **kwargs):
            with open(argv[-1], "wb") as f:
                f.write(b"PNG")
            return mock.Mock(returncode=0)
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={w["address"]: w for w in windows}),
            mock.patch.object(hypr, "query", return_value=[{"name": "DP-1", "focused": True}]),
            mock.patch.object(proc, "read_cmdline", return_value=["/bin/sh"]),
            mock.patch.object(setups.subprocess, "run", side_effect=fake_run) as process,
        ):
            data = setups.save("Company B", screenshot=True)
        captures = [call.args[0][:3] for call in process.call_args_list if call.args[0][0] == "grim"]
        self.assertEqual(captures, [["grim", "-o", "DP-1"], ["grim", "-T", "18000007"],
                                    ["grim", "-T", "18000009"]])
        for index, win in enumerate(data["windows"]):
            self.assertEqual(win["preview"], f"window-{index}.png")
            self.assertEqual(os.stat(setups.describe("Company B")["entries"][index]["preview"]).st_mode & 0o777, 0o600)

    def test_live_previews_use_runtime_cache_and_remove_previous_captures(self):
        runtime = os.path.join(self.dir.name, "runtime")
        os.mkdir(runtime, 0o700)
        cached = os.path.join(runtime, "omarchinator-previews")
        os.mkdir(cached, 0o700)
        stale = os.path.join(cached, "old.png")
        with open(stale, "wb") as f:
            f.write(b"old")
        live = client("kitty", stableId=45)
        def capture(argv, path):
            with open(path, "wb") as f:
                f.write(b"PNG")
            os.chmod(path, 0o600)
            return True
        with (
            mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}),
            mock.patch.object(setups, "capture_image", side_effect=capture) as captured,
        ):
            result = setups.live_previews([live])
        self.assertEqual(captured.call_args.args[0], ["grim", "-T", "45"])
        self.assertEqual(os.stat(result[live["address"]]).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(stale))
        self.assertEqual(os.stat(cached).st_mode & 0o777, 0o700)

    def test_names_cannot_escape_private_storage(self):
        for name in ("../secret", "", "a/b", ".hidden", "a\nfoo"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                setups.setup_dir(name)

    def test_workspace_layout_preserves_window_geometry_and_groups(self):
        one = saved_window("foot", ws=2, at=(100, 200), size=(500, 400))
        two = saved_window("google-chrome", ws=2, at=(600, 200), size=(1000, 400))
        three = saved_window("foot", ws=4, at=(3000, 0), size=(1200, 900))
        self._write_setup("Work", [one, two, three])
        detail = setups.describe("Work")
        self.assertEqual(detail["workspaces"], [
            {"name": "2", "indexes": [0, 1], "bounds": [100, 200, 1500, 400]},
            {"name": "4", "indexes": [2], "bounds": [3000, 0, 1200, 900]},
        ])
        self.assertEqual(detail["entries"][1]["at"], [600, 200])
        self.assertEqual([w["name"] for w in setups.workspace_layout([three, one, two])], ["2", "4"])

    def test_browser_requires_explicit_url_and_never_uses_saved_startup_url(self):
        win = saved_window("google-chrome", cmd="/usr/bin/google-chrome https://unrelated.example --restore-last-session")
        win["title"] = "Project browser"
        self._write_setup("Work", [win])
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(launch, "launch_saved_windows") as launched,
            mock.patch.object(sweep, "sweep") as swept,
        ):
            self.assertEqual(setups.start("Work")["skipped_chrome"], ["Project browser"])
            launched.assert_not_called()
            swept.assert_not_called()
            setups.set_url("Work", 0, "https://work.example")
            self.assertEqual(setups.read("Work")["windows"][0]["url"], "https://work.example")
            with self.assertRaises(ValueError):
                setups.set_url("Work", 0, "javascript:alert(1)")
            swept.return_value = ([], [])
            result = setups.start("Work")
            self.assertEqual(result["launched"], 1)
            command = launched.call_args.args[0][0]["cmd"]
            self.assertEqual(command, "/usr/bin/google-chrome --new-window https://work.example")
            self.assertEqual(launched.call_args.kwargs["clean_browser_exit"], False)

    def test_apply_last_skips_chrome_without_a_url_but_launches_other_apps(self):
        browser = saved_window("google-chrome", cmd="chrome --restore-last-session")
        browser["title"] = "Work browser"
        terminal = saved_window("foot", cmd="foot")
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(launch, "launch_saved_windows") as launched,
            mock.patch.object(sweep, "sweep", return_value=([], [])),
        ):
            result = setups.apply_windows([browser, terminal])
        self.assertEqual(result["skipped_chrome"], ["Work browser"])
        self.assertEqual(result["launched"], 1)
        self.assertEqual([w["class"] for w in launched.call_args.args[0]], ["foot"])

    def test_matching_window_moves_without_relaunching_or_touching_last(self):
        saved = saved_window("foot", ws=3)
        saved["title"] = "Shell"
        self.write_session([{"class": "old"}])
        self._write_setup("Work", [saved])
        existing = client("foot", title="Shell", workspace={"id": 2, "name": "2"})
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={existing["address"]: existing}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(placement, "place_window") as moved,
            mock.patch.object(launch, "launch_saved_windows") as launched,
        ):
            self.assertEqual(setups.start("Work")["matched"], 1)
        moved.assert_called_once()
        launched.assert_not_called()
        self.assertEqual(self.read_session(), [{"class": "old"}])

    def test_save_keeps_kitty_session_when_autosave_replaces_it(self):
        kitty = client("kitty", pid=999, title="Term")
        with (
            mock.patch.object(hypr, "query", return_value=[kitty]),
            mock.patch.object(proc, "read_cmdline", return_value=["kitty"]),
            mock.patch.object(session.kitty, "build_session_text", return_value="new_os_window\n"),
        ):
            data = setups.save("Work")
        command = data["windows"][0]["cmd"]
        self.assertIn("/setups/work/kitty-999.session", command)
        with open(os.path.join(setups.setup_dir("Work"), "kitty-999.session")) as f:
            self.assertEqual(f.read(), "new_os_window\n")

    def _write_setup(self, name, windows):
        path = setups.setup_dir(name)
        setups.ensure_dir(os.path.dirname(path))
        setups.ensure_dir(path)
        session.write_private(os.path.join(path, "session.json"), json.dumps({
            "name": name, "saved_at": 1, "windows": windows,
        }))


if __name__ == "__main__":
    unittest.main()
