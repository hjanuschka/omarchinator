"""Named, reusable layouts. Unlike the login snapshot, starting one never closes apps."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
import time
from typing import Any

from omarchy_last_session import config, hypr, session, warn
from omarchy_last_session.restore import launch, placement, sweep

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,47}\Z")
URL = re.compile(r"https?://\S+\Z")


def setup_dir(name: str) -> str:
    if not NAME.fullmatch(name):
        raise ValueError("setup name must be 1-48 letters, digits, spaces, _ or -")
    return os.path.join(config.STATE_DIR, "setups", re.sub(r"[ _]+", "-", name.lower()))


def ensure_dir(path: str) -> None:
    session.ensure_private_state_dir()
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise PermissionError(f"not a private directory: {path}")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)


def read(name: str) -> dict[str, Any]:
    with session.open_private(os.path.join(setup_dir(name), "session.json")) as f:
        data = json.load(f)
    if data.get("name") != name or not isinstance(data.get("windows"), list):
        raise ValueError(f"invalid setup: {name}")
    return data


def save(name: str, screenshot: bool = False) -> dict[str, Any]:
    directory = setup_dir(name)
    ensure_dir(os.path.dirname(directory))
    if os.path.lexists(directory):
        raise FileExistsError(f"setup '{name}' exists; choose a new name")
    ensure_dir(directory)
    clients = list(hypr.get_managed_clients().values())
    windows = session.snapshot_windows(clients)
    for win in windows:
        if win["class"] in config.CHROMIUM_BROWSERS:
            win["url"] = ""
        argv = shlex.split(win["cmd"])
        if win["class"] == config.KITTY_CLASS and len(argv) >= 3 and argv[1] == config.KITTY_SESSION_FLAG:
            source = argv[2]
            dest = os.path.join(directory, os.path.basename(source))
            with session.open_private(source) as f:
                session.write_private(dest, f.read())
            win["cmd"] = shlex.join([argv[0], argv[1], dest])
    data: dict[str, Any] = {"name": name, "saved_at": time.time(), "windows": windows}
    session.write_private(os.path.join(directory, "session.json"), json.dumps(data, indent=2))
    if screenshot:
        capture(directory)
        for index, (win, client) in enumerate(zip(windows, clients)):
            if client.get("stableId") is not None:
                filename = f"window-{index}.png"
                if capture_image(["grim", "-T", str(client["stableId"])], os.path.join(directory, filename)):
                    win["preview"] = filename
        session.write_private(os.path.join(directory, "session.json"), json.dumps(data, indent=2))
    return data


def capture(directory: str) -> None:
    monitors = hypr.query("monitors")
    focused = next((m for m in monitors if m.get("focused")), None)
    if not focused:
        warn("no focused monitor for setup screenshot")
        return
    capture_image(["grim", "-o", focused["name"]], os.path.join(directory, "preview.png"))


def capture_image(command: list[str], destination: str) -> bool:
    directory = os.path.dirname(destination)
    fd, path = tempfile.mkstemp(dir=directory, suffix=".png")
    os.close(fd)
    thumb = path + ".small.png"
    try:
        result = subprocess.run([*command, path], capture_output=True, timeout=15)
        if result.returncode:
            warn(f"setup screenshot failed: {result.stderr.decode(errors='replace').strip()}")
            return False
        result = subprocess.run(["magick", path, "-resize", "800x450>", thumb], capture_output=True, timeout=15)
        if result.returncode:
            warn(f"screenshot resize failed: {result.stderr.decode(errors='replace').strip()}")
            return False
        os.chmod(thumb, 0o600)
        os.replace(thumb, destination)
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        warn(f"setup screenshot failed: {e}")
        return False
    finally:
        os.unlink(path)
        if os.path.exists(thumb):
            os.unlink(thumb)


def live_previews(clients: list[hypr.Client]) -> dict[str, str]:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        warn("XDG_RUNTIME_DIR is unavailable; live previews disabled")
        return {}
    parent = os.lstat(runtime)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise PermissionError(f"not a private runtime directory: {runtime}")
    directory = os.path.join(runtime, "omarchinator-previews")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise PermissionError(f"not a private preview directory: {directory}")
    if st.st_mode & 0o077:
        os.chmod(directory, 0o700)
    previews: dict[str, str] = {}
    stamp = time.time_ns()
    for client in clients:
        if client.get("stableId") is None:
            continue
        filename = f"{client['stableId']}-{stamp}.png"
        path = os.path.join(directory, filename)
        if capture_image(["grim", "-T", str(client["stableId"])], path):
            previews[client["address"]] = path
    for filename in os.listdir(directory):
        if filename.endswith(".png") and not filename.endswith(f"-{stamp}.png"):
            os.unlink(os.path.join(directory, filename))
    return previews


def list_setups() -> list[dict[str, Any]]:
    base = os.path.join(config.STATE_DIR, "setups")
    if not os.path.isdir(base):
        return []
    results = []
    for item in sorted(os.listdir(base)):
        try:
            with session.open_private(os.path.join(base, item, "session.json")) as f:
                data = json.load(f)
            name = data["name"]
            if setup_dir(name) != os.path.join(base, item):
                continue
            results.append(summary(data))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return results


def summary(data: dict[str, Any]) -> dict[str, Any]:
    name = data["name"]
    preview = os.path.join(setup_dir(name), "preview.png")
    return {
        "name": name,
        "saved_at": data["saved_at"],
        "windows": len(data["windows"]),
        "preview": preview if os.path.isfile(preview) else "",
    }


def describe(name: str) -> dict[str, Any]:
    data = read(name)
    return {**summary(data), "entries": [
        {"class": w["class"], "title": w["title"], "workspace": w["workspace"]["name"],
         "at": w["at"], "size": w["size"], "url": w.get("url", ""),
         "browser": w["class"] in config.CHROMIUM_BROWSERS,
         "preview": os.path.join(setup_dir(name), w["preview"]) if w.get("preview") else ""}
        for w in data["windows"]
    ], "workspaces": workspace_layout(data["windows"])}


def workspace_layout(windows: list[session.SavedWindow]) -> list[dict[str, Any]]:
    grouped: dict[str, list[int]] = {}
    for index, win in enumerate(windows):
        grouped.setdefault(win["workspace"]["name"], []).append(index)
    layouts = []
    for name in sorted(grouped, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else n)):
        indexes = grouped[name]
        left = min(windows[i]["at"][0] for i in indexes)
        top = min(windows[i]["at"][1] for i in indexes)
        right = max(windows[i]["at"][0] + windows[i]["size"][0] for i in indexes)
        bottom = max(windows[i]["at"][1] + windows[i]["size"][1] for i in indexes)
        layouts.append({
            "name": name, "indexes": indexes,
            "bounds": [left, top, max(1, right - left), max(1, bottom - top)],
        })
    return layouts


def set_url(name: str, index: int, url: str) -> None:
    data = read(name)
    windows = data["windows"]
    if index < 0 or index >= len(windows) or windows[index]["class"] not in config.CHROMIUM_BROWSERS:
        raise ValueError("index must refer to a Chrome window in this setup")
    if url and not URL.fullmatch(url):
        raise ValueError("URL must start with http:// or https://")
    windows[index]["url"] = url
    session.write_private(os.path.join(setup_dir(name), "session.json"), json.dumps(data, indent=2))


def start(name: str) -> dict[str, Any]:
    return apply_windows(read(name)["windows"])


def apply_windows(windows: list[session.SavedWindow]) -> dict[str, Any]:
    existing = hypr.get_managed_clients()
    origins = hypr.get_monitor_origins()
    used: set[str] = set()
    to_launch = []
    skipped = []
    for win in windows:
        match = next((addr for addr, client in existing.items()
                      if addr not in used and client["class"] == win["class"]
                      and client.get("title") == win["title"]), None)
        if match:
            used.add(match)
            if placement.is_out_of_place(win, existing[match], origins):
                placement.place_window(win, match, origins, existing[match].get("floating", False))
            continue
        if win["class"] in config.CHROMIUM_BROWSERS:
            url = win.get("url", "")
            if not URL.fullmatch(url):
                skipped.append(win["title"])
                continue
            argv = shlex.split(win["cmd"])
            profile = []
            for index, arg in enumerate(argv[1:], 1):
                if arg.startswith(("--user-data-dir=", "--profile-directory=")):
                    profile.append(arg)
                elif arg in ("--user-data-dir", "--profile-directory") and index + 1 < len(argv):
                    profile.extend((arg, argv[index + 1]))
            win = {**win, "cmd": shlex.join([argv[0], *profile, "--new-window", url]), "spawn": True}
        to_launch.append(win)
    spawning = {w["class"] for w in to_launch if w["spawn"]}
    unavailable = [w["title"] for w in to_launch if not w["spawn"] and w["class"] not in spawning]
    to_launch = [w for w in to_launch if w["spawn"] or w["class"] in spawning]
    if to_launch:
        ordered = launch.sort_for_launch(to_launch)
        launch.launch_saved_windows(
            ordered, origins, {c["class"] for c in existing.values()}, clean_browser_exit=False
        )
        _, missing = sweep.sweep(ordered, set(existing), origins)
    else:
        missing = []
    return {"launched": len(to_launch), "matched": len(used),
            "skipped_chrome": skipped, "missing": unavailable + [w["title"] for w in missing]}
