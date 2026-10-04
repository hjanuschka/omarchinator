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

    def test_browser_without_url_opens_blank_window_not_saved_startup_url(self):
        win = saved_window("google-chrome", cmd="/usr/bin/google-chrome https://unrelated.example --restore-last-session")
        win["title"] = "Project browser"
        self._write_setup("Work", [win])
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(launch, "launch_saved_windows") as launched,
            mock.patch.object(sweep, "sweep") as swept,
        ):
            swept.return_value = ([], [])
            blank = setups.start("Work")
            self.assertEqual(blank["blank_chrome"], ["Project browser"])
            self.assertEqual(blank["launched"], 1)
            self.assertEqual(launched.call_args.args[0][0]["cmd"],
                             "/usr/bin/google-chrome --new-window about:blank")
            self.assertEqual(launched.call_args.args[0][0]["workspace"]["name"], "2")
            swept.assert_called_once()
            setups.set_url("Work", 0, "https://work.example")
            self.assertEqual(setups.read("Work")["windows"][0]["url"], "https://work.example")
            with self.assertRaises(ValueError):
                setups.set_url("Work", 0, "javascript:alert(1)")
            swept.return_value = ([], [])
            result = setups.start("Work")
            self.assertEqual(result["blank_chrome"], [])
            self.assertEqual(result["launched"], 1)
            command = launched.call_args.args[0][0]["cmd"]
            self.assertEqual(command, "/usr/bin/google-chrome --new-window https://work.example")
            self.assertEqual(launched.call_args.kwargs["clean_browser_exit"], False)

    def test_browser_notification_count_does_not_duplicate_saved_page(self):
        saved = saved_window("google-chrome", ws=2)
        saved["title"] = "Startseite / X - Google Chrome"
        saved["url"] = "https://x.com/home"
        live = client("google-chrome", title="(2) Startseite / X - Google Chrome",
                      workspace={"id": 2, "name": "2"})
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={live["address"]: live}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(launch, "launch_saved_windows") as launched,
        ):
            result = setups.apply_windows([saved])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["launched"], 0)
        launched.assert_not_called()

    def test_matching_title_prefers_saved_workspace(self):
        saved = saved_window("google-chrome", ws=2)
        saved["title"] = "Startseite / X - Google Chrome"
        elsewhere = client("google-chrome", title=saved["title"],
                           workspace={"id": 1, "name": "1"})
        on_workspace = client("google-chrome", title="(2) " + saved["title"],
                              workspace={"id": 2, "name": "2"})
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={
                elsewhere["address"]: elsewhere, on_workspace["address"]: on_workspace}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(placement, "place_window") as moved,
            mock.patch.object(launch, "launch_saved_windows") as launched,
        ):
            result = setups.apply_windows([saved])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["launched"], 0)
        moved.assert_not_called()
        launched.assert_not_called()

    def test_force_apply_rejects_empty_setup(self):
        self._write_setup("Work", [])
        with mock.patch.object(hypr, "dispatch") as dispatched:
            with self.assertRaisesRegex(ValueError, "empty setup"):
                setups.force_start("Work")
        dispatched.assert_not_called()

    def test_force_apply_keeps_saved_windows_and_closes_only_old_extras(self):
        saved = saved_window("foot", ws=2)
        saved["title"] = "Shell"
        self._write_setup("Work", [saved])
        wanted = client("foot", title="Shell")
        extra = client("google-chrome", title="Other")
        live = {c["address"]: c for c in (wanted, extra)}
        def close(command):
            self.assertIn("address:" + extra["address"], command)
            live.pop(extra["address"])
            return True
        with (
            mock.patch.object(hypr, "get_managed_clients", side_effect=lambda: dict(live)),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(hypr, "dispatch", side_effect=close) as dispatched,
            mock.patch.object(session, "save_session") as save_last,
        ):
            result = setups.force_start("Work")
        self.assertEqual(result["closed"], 1)
        self.assertFalse(result["aborted"])
        self.assertEqual(result["remaining"], [])
        self.assertIn(wanted["address"], live)
        dispatched.assert_called_once()
        save_last.assert_not_called()

    def test_force_apply_launches_and_verifies_before_closing(self):
        saved = saved_window("google-chrome", ws=2,
                             cmd="/usr/bin/google-chrome --profile-directory=Default")
        saved["url"] = "https://x.com/home"
        self._write_setup("Work", [saved])
        extra = client("foot")
        opened = client("google-chrome")
        live = {extra["address"]: extra}
        events = []
        def spawn(windows, *args, **kwargs):
            events.append("launch")
        def arrived(windows, *args):
            live[opened["address"]] = opened
            return ([(windows[0], opened["address"])], [])
        def close(command):
            events.append("close")
            live.pop(extra["address"])
            return True
        with (
            mock.patch.object(hypr, "get_managed_clients", side_effect=lambda: dict(live)),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(launch, "launch_saved_windows", side_effect=spawn),
            mock.patch.object(sweep, "sweep", side_effect=arrived),
            mock.patch.object(hypr, "dispatch", side_effect=close),
        ):
            result = setups.force_start("Work")
        self.assertEqual(events, ["launch", "close"])
        self.assertEqual(result["closed"], 1)
        self.assertEqual(result["launched"], 1)
        self.assertIn(opened["address"], live)

    def test_force_apply_reports_a_window_that_refuses_to_close(self):
        saved = saved_window("foot", ws=2)
        self._write_setup("Work", [saved])
        wanted = client("foot")
        extra = client("google-chrome", title="Unsaved form")
        live = {c["address"]: c for c in (wanted, extra)}
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value=live),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(hypr, "dispatch", return_value=True) as dispatched,
            mock.patch.object(setups.time, "monotonic", side_effect=[0, 9]),
        ):
            result = setups.force_start("Work")
        self.assertEqual(result["closed"], 0)
        self.assertEqual(result["remaining"], ["Unsaved form"])
        dispatched.assert_called_once()

    def test_force_apply_does_not_close_old_windows_if_saved_window_missing(self):
        saved = saved_window("foot", ws=2, spawn=False)
        saved["title"] = "Missing"
        self._write_setup("Work", [saved])
        extra = client("google-chrome", title="Other")
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={extra["address"]: extra}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(hypr, "dispatch") as dispatched,
        ):
            result = setups.force_start("Work")
        self.assertTrue(result["aborted"])
        self.assertEqual(result["closed"], 0)
        self.assertEqual(result["missing"], ["Missing"])
        dispatched.assert_not_called()

    def test_force_apply_verifies_each_saved_window_separately(self):
        windows = [saved_window("foot", ws=2) for _ in range(2)]
        for win in windows:
            win["title"] = "Shell"
        self._write_setup("Work", windows)
        live = client("foot")
        extra = client("google-chrome")
        clients = {c["address"]: c for c in (live, extra)}
        def partial_apply(saved, kept):
            kept[0] = live["address"]
            return {"matched": 1, "launched": 1, "blank_chrome": [], "missing": []}
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value=clients),
            mock.patch.object(setups, "apply_windows", side_effect=partial_apply),
            mock.patch.object(hypr, "dispatch") as dispatched,
        ):
            result = setups.force_start("Work")
        self.assertTrue(result["aborted"])
        self.assertEqual(result["missing"], ["Shell"])
        dispatched.assert_not_called()

    def test_force_apply_leaves_windows_opened_during_restore_alone(self):
        saved = saved_window("foot", ws=2)
        self._write_setup("Work", [saved])
        wanted = client("foot")
        late = client("google-chrome")
        def applied(windows, kept):
            kept[0] = wanted["address"]
            return {"matched": 1, "launched": 0, "blank_chrome": [], "missing": []}
        snapshots = [{wanted["address"]: wanted},
                     {wanted["address"]: wanted, late["address"]: late},
                     {wanted["address"]: wanted, late["address"]: late}]
        with (
            mock.patch.object(hypr, "get_managed_clients", side_effect=snapshots),
            mock.patch.object(setups, "apply_windows", side_effect=applied),
            mock.patch.object(hypr, "dispatch") as dispatched,
        ):
            result = setups.force_start("Work")
        self.assertEqual(result["closed"], 0)
        dispatched.assert_not_called()

    def test_blank_chrome_is_not_duplicated_when_workspace_already_has_one(self):
        saved = saved_window("google-chrome", ws=2)
        saved["title"] = "Saved page"
        live = client("google-chrome", title="about:blank - Google Chrome",
                      workspace={"id": 2, "name": "2"})
        with (
            mock.patch.object(hypr, "get_managed_clients", return_value={live["address"]: live}),
            mock.patch.object(hypr, "get_monitor_origins", return_value={}),
            mock.patch.object(launch, "launch_saved_windows") as launched,
        ):
            result = setups.apply_windows([saved])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["launched"], 0)
        launched.assert_not_called()

    def test_apply_last_opens_blank_chrome_and_other_apps(self):
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
        self.assertEqual(result["blank_chrome"], ["Work browser"])
        self.assertEqual(result["launched"], 2)
        self.assertEqual([w["class"] for w in launched.call_args.args[0]], ["google-chrome", "foot"])

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
