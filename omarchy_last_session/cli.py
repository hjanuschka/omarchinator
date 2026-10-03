"""Omarchinator: recover Last on login and launch named workday setups.

save     - snapshot every mapped window (workspace, geometry, relaunch command)
shutdown - save, then let session-keeping apps exit cleanly; optional, for a
           power menu action wired to run it first
restore  - relaunch every saved window silently on its original workspace
daemon   - save when Hyprland reports a window moving, so a power button, a
           crash or the power menu costs a few seconds of changes at most
config   - open the config file in your editor; the daemon picks an edit up
           within a minute
preview  - show restorable windows now and in the saved snapshot as JSON
setup    - list | save NAME [--screenshot] | show NAME | start NAME | url NAME INDEX URL
menu     - print the rows for ~/.config/omarchy/extensions/omarchy-menu.jsonc:
           the preview and config under Setup, and Omarchy's own Logout,
           Reboot and Shutdown rows, each running shutdown first

Config lives in $XDG_CONFIG_HOME/omarchy/last-session.ini, written with every default
and a comment on each the first time the plugin runs.
State lives in $XDG_STATE_HOME/omarchy-last-session, or the state_dir set there.
Skip the next restore:   touch <state dir>/disabled
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from typing import cast

from omarchy_last_session import config, hypr, log, restore, session, setups, warn

# Omarchy's own: opens the user's editor and shows a toast, as the Config menu entries do.
CONFIG_EDITOR = "omarchy-launch-config-editor"
MENU_ICON = "󰁯"
# Omarchy's own menu, under OMARCHY_PATH or the path Omarchy itself falls back to.
OMARCHY_MENU = "default/omarchy/omarchy-menu.jsonc"
OMARCHY_DEFAULT_PATH = "/usr/share/omarchy"
# What Omarchy's Logout, Reboot and Shutdown rows run. The rows are found by these,
# since their ids, icons and labels are Omarchy's to change.
POWER_COMMANDS = ("omarchy-system-logout", "omarchy-system-reboot", "omarchy-system-shutdown")
# The checkout: where the launcher, the manifest and this package live.
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_save() -> None:
    print(f"saved {session.save_session()} windows to {config.SESSION_FILE}")


def run_preview(captures: bool = False) -> None:
    clients = list(hypr.get_managed_clients().values())
    current = session.snapshot_windows(clients)
    try:
        with session.open_private(config.SESSION_FILE) as f:
            saved = json.load(f)
    except FileNotFoundError:
        saved = {"saved_at": None, "windows": []}
    images = setups.live_previews(clients) if captures else {}
    unmatched = list(clients)
    live = []
    for win in current:
        client = next((c for c in unmatched if c["class"] == win["class"]
                       and c.get("title") == win["title"] and c["workspace"] == win["workspace"]), None)
        if client:
            unmatched.remove(client)
        live.append(preview_window(win, images.get(client["address"], "") if client else ""))
    saved_windows = [preview_window(w) for w in saved["windows"]]
    remaining = list(live)
    for win in saved_windows:
        match = next(
            (candidate for candidate in remaining
             if candidate["class"] == win["class"] and candidate["title"] == win["title"]
             and candidate["workspace"] == win["workspace"]),
            None,
        )
        if match:
            win["preview"] = match["preview"]
            remaining.remove(match)
    print(json.dumps({
        "current": live,
        "current_workspaces": setups.workspace_layout(current),
        "saved": saved_windows,
        "saved_workspaces": setups.workspace_layout(saved["windows"]),
        "saved_at": saved.get("saved_at"),
    }))


def preview_window(win: session.SavedWindow, image: str = "") -> dict[str, object]:
    return {
        "class": win["class"],
        "title": win["title"],
        "workspace": win["workspace"]["name"],
        "monitor": win.get("monitor_name", ""),
        "at": win["at"],
        "size": win["size"],
        "preview": image,
    }


def run_setup(args: list[str]) -> int:
    if len(args) < 2:
        warn("usage: setup list | save NAME [--screenshot] | show NAME | start NAME | url NAME INDEX URL")
        return 1
    action = args[1]
    try:
        if action == "list" and len(args) == 2:
            result = setups.list_setups()
        elif action == "save" and len(args) in (3, 4) and (len(args) == 3 or args[3] == "--screenshot"):
            result = setups.summary(setups.save(args[2], screenshot=len(args) == 4))
        elif action == "show" and len(args) == 3:
            result = setups.describe(args[2])
        elif action == "start" and len(args) == 3:
            result = setups.start(args[2])
        elif action == "url" and len(args) == 5:
            setups.set_url(args[2], int(args[3]), args[4])
            result = setups.describe(args[2])
        else:
            raise ValueError("invalid setup command or arguments")
    except (OSError, ValueError, KeyError, IndexError) as e:
        warn(str(e))
        return 1
    print(json.dumps(result))
    return 0


def run_shutdown() -> None:
    print(f"saved {session.save_session()} windows")
    try:
        session.copy_session_to(config.LAST_SHUTDOWN_FILE)
    except OSError as e:
        warn(f"could not keep a copy: {e}")
    stubborn = session.quit_session_keeping_apps()
    if stubborn:
        print(f"{len(stubborn)} app(s) did not exit in time")
    else:
        print("session-keeping apps exited cleanly")


def run_daemon() -> None:
    time.sleep(config.DAEMON_INITIAL_DELAY)
    scheduler = session.SaveScheduler()
    events = hypr.open_event_stream()
    while hypr.wait_for_placement_change(events, save_if_due(scheduler)):
        pass


def run_config() -> int:
    try:
        os.execvp(CONFIG_EDITOR, [CONFIG_EDITOR, config.CONFIG_FILE])
    except FileNotFoundError:
        warn(f"{CONFIG_EDITOR} is not on PATH, so open the file yourself: {config.CONFIG_FILE}")
        return 1


def run_menu() -> int:
    path = os.path.join(os.environ.get("OMARCHY_PATH") or OMARCHY_DEFAULT_PATH, OMARCHY_MENU)
    try:
        power_rows = read_power_rows(path)
    except (OSError, ValueError) as e:
        warn(f"could not read Omarchy's menu {path}: {e}")
        return 1
    if not power_rows:
        warn(f"no row in Omarchy's menu {path} runs {', '.join(POWER_COMMANDS)}")
        return 1
    print(render_menu_rows(tilde(PLUGIN_DIR), power_rows), end="")
    return 0


def read_power_rows(path: str) -> dict[str, dict[str, object]]:
    """Omarchy's rows that run a power command, by id, read as its menu reads
    them: whole-line comments and trailing commas dropped."""
    with open(path, encoding="utf-8") as f:
        text = re.sub(r"^\s*//[^\n]*(\n|$)", "", f.read(), flags=re.MULTILINE)
    rows = as_json_object(json.loads(re.sub(r",(\s*[}\]])", r"\1", text)))
    if rows is None:
        raise ValueError("not a JSON object")
    power_rows: dict[str, dict[str, object]] = {}
    for row_id, value in rows.items():
        row = as_json_object(value)
        if row is not None and row.get("action") in POWER_COMMANDS:
            power_rows[row_id] = row
    return power_rows


def as_json_object(value: object) -> dict[str, object] | None:
    """A decoded JSON object, whose keys are always strings; None for any other value."""
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def render_menu_rows(root: str, power_rows: dict[str, dict[str, object]]) -> str:
    """JSONC rows for the user's menu file. The setup rows hide while the
    plugin directory is gone; power rows replace Omarchy's own and keep
    working after the plugin is removed."""
    launcher = f"{root}/bin/omarchinator"
    rows: dict[str, dict[str, object]] = {
        "setup.omarchinator": {
            "icon": MENU_ICON,
            "label": "Omarchinator",
            "when": f"[[ -d {root} ]]",
            "action": "omarchy-shell shell summon io.github.hjanuschka.omarchinator",
        },
        "setup.config.last-session": {
            "icon": MENU_ICON,
            "label": "Last Session",
            "when": f"[[ -d {root} ]]",
            "action": f"{launcher} config",
        },
    }
    for row_id, row in power_rows.items():
        # Every other field is copied: Omarchy's menu fills in whatever a row leaves
        # out, with no icon and the row's id as its label.
        rows[row_id] = {**row, "action": f"[[ -x {launcher} ]] && {launcher} shutdown; {row['action']}"}
    return "".join(f"  {format_json(row_id)}: {format_json(row)},\n" for row_id, row in rows.items())


def format_json(value: object) -> str:
    """On one line and with the icons as they are, as Omarchy writes its menu."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def tilde(path: str) -> str:
    """~ for the home directory, which the menu's bash expands, so the rows read
    the same on every machine."""
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path == home or path.startswith(home + os.sep) else path


def save_if_due(scheduler: session.SaveScheduler) -> float:
    """Returns how long the daemon may sleep before looking again."""
    try:
        if config.reload_if_changed():
            log(f"reloaded {config.CONFIG_FILE}")
        now = time.monotonic()
        if scheduler.is_due(hypr.get_layout(), now):
            session.save_session()
            scheduler.mark_saved(now)
        return scheduler.seconds_until_recheck(time.monotonic())
    except Exception as e:
        warn(f"save failed: {e}")
        return config.SAVE_INTERVAL


COMMANDS: dict[str, Callable[[], int | None]] = {
    "save": run_save,
    "preview": run_preview,
    "shutdown": run_shutdown,
    "restore": restore.restore_session,
    "daemon": run_daemon,
    "config": run_config,
    "menu": run_menu,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "setup":
        config.ensure_file()
        return run_setup(args)
    if args == ["preview", "--screenshots"]:
        config.ensure_file()
        run_preview(captures=True)
        return 0
    command = COMMANDS.get(args[0]) if args else None
    if command is None:
        print((__doc__ or "").strip(), file=sys.stderr)
        return 1
    config.ensure_file()
    return command() or 0
