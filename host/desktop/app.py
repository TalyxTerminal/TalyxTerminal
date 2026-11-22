"""WebView2 windows, a Windows tray loop and the owned backend process."""

import ctypes
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pystray
import webview
from PIL import Image
from talyx_sdk import i18n
from talyx_sdk.jobs import Job

from host.files import default_data_dir, platform_locale_files
from host.updates import update_folder

from . import install
from .bridge import Bridge
from .frame import ICON, Frame
from .instance import DesktopActivation

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = Path(__file__).with_name("adapter.js").read_text(encoding="utf8")
# Below this the top bar and a workspace of a few panels no longer fit.
MIN_SIZE = (960, 600)
BACKGROUND = "#0b0b0c"


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port


class WindowAPI:
    def __init__(self, desktop, state):
        self._desktop, self._state = desktop, state

    def _trusted(self):
        if origin(self._state.window.get_current_url()) != origin(self._desktop.ui_url):
            raise PermissionError("Untrusted desktop page")

    def open_workspace(self, workspace_id):
        self._trusted()
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ValueError("workspaceId is required")
        self._desktop.open_window(workspace_id)

    def _on_ui(self, work):
        from System import Func, Object

        return self._state.window.native.Invoke(Func[Object](work))

    def minimize_window(self):
        self._trusted()
        self._state.window.minimize()

    def capture_panel(self, workspace, panel):
        from .capture import panel_png, save_png

        self._trusted()
        pixels = panel_png(self._state, workspace, panel)
        return save_png(self._desktop.data_root / "platform" / "captures", pixels)

    def toggle_maximize_window(self):
        self._trusted()
        if self._on_ui(self._state.frame.maximized):
            self._state.window.restore()
        else:
            self._state.window.maximize()

    def close_window(self):
        """The window's own close: the same as the system's, the window goes to the tray."""
        self._trusted()
        self._desktop.hide(self._state)

    def window_maximized(self):
        self._trusted()
        return bool(self._on_ui(self._state.frame.maximized))

    def drag_window(self):
        self._trusted()
        self._on_ui(self._state.frame.drag)

    def resize_window(self, edge):
        self._trusted()
        if edge not in ("top", "top-left", "top-right"):
            raise ValueError("Unknown window edge")
        self._on_ui(lambda: self._state.frame.resize(edge))

    def open_folder(self, path):
        """Opens a folder in File Explorer."""
        self._trusted()
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_dir():
            raise ValueError("The folder does not exist")
        os.startfile(path)

    def listen_exit(self, listening):
        self._trusted()
        with self._desktop.lock:
            self._state.listening = listening
            if listening:
                return self._desktop.delivery(self._state)

    def request_exit(self):
        self._trusted()
        self._desktop.request_exit()

    def cancel_exit(self, request_id):
        self._trusted()
        with self._desktop.lock:
            self._desktop.check_confirmation(self._state, request_id)
            self._desktop.pending_exit = None
            self._desktop.pending_update = None

    def install_update(self):
        """Quit (the same exit, previewing what stops), then install the downloaded update and open TALYX again."""
        self._trusted()
        self._desktop.install_update()

    def confirm_exit(self, request_id):
        self._trusted()
        with self._desktop.lock:
            self._desktop.check_confirmation(self._state, request_id)
            self._desktop.pending_exit = None
            self._desktop.quitting = True
        threading.Thread(target=self._desktop.confirmed_exit, name="desktop-exit").start()


class WindowState:
    def __init__(self):
        self.id = uuid4().hex
        self.ready = False
        self.initialized = False
        self.frame = None
        self.listening = False
        self.js_lock = threading.Lock()

    def js(self, script):
        with self.js_lock:
            return self.window.evaluate_js(script)


class Desktop:
    def __init__(self, ui_url, backend_port=8000, profile=None, *, start_in_tray=False, attach=False,
                 data_root=None):
        parsed = urlsplit(ui_url)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("--ui-url must be loopback HTTP")
        self.ui_url = ui_url
        self.backend_port = backend_port
        self.data_root = Path(data_root) if data_root else default_data_dir()
        self.profile = Path(profile) if profile else self.data_root / "desktop"
        self.start_in_tray, self.attach = start_in_tray, attach
        self.activation = None
        self.windows = []
        self.lock = threading.RLock()
        self.last_active = None
        self.hiding = False
        self.pending_exit = None
        self.pending_update = None   # (installer, version) to run once quit
        self.previewing = False
        self.quitting = False
        self.exit_code = 0
        self.backend = self.bridge = self.job = self.tray = None

    def start_backend(self):
        if self.attach:
            deadline = time.monotonic() + 120
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", self.backend_port), timeout=1):
                        break
                except ConnectionRefusedError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("The existing backend did not finish starting") from None
                    time.sleep(0.1)
            self.bridge = Bridge(f"ws://127.0.0.1:{self.backend_port}/ws/bridge", self.data_root)
            return
        # --desktop: the Host applies the desktop's General settings and stops when its standard input closes.
        args = ["-B", "-X", "utf8", "-m", "host", "--desktop", "--data-dir", str(self.data_root),
                "--port", str(self.backend_port), "--ui-url", self.ui_url]
        env = {
            **os.environ,
            "UV_PROJECT_ENVIRONMENT": str(Path(sys.executable).resolve().parents[1]),
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TALYX_UI_URL": self.ui_url,
        }
        self.backend = subprocess.Popen(
            [sys.executable, *args],   # this environment's Python (the repository's .venv)
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.job = Job(self.backend)
        launches = queue.Queue()

        def stdout():
            for line in self.backend.stdout:
                if line.startswith("TALYX UI: "):
                    launches.put(line[10:].strip())
                else:
                    print(line, end="", flush=True)
            launches.put(None)

        self.stdout_thread = threading.Thread(target=stdout, name="desktop-backend-output")
        self.stdout_thread.start()
        if launches.get(timeout=600) is None:
            raise RuntimeError(f"Backend exited before launch handoff: {self.backend.wait()}")
        self.bridge = Bridge(f"ws://127.0.0.1:{self.backend_port}/ws/bridge", self.data_root)

    def read_language(self):
        """The tray's words, in the language set when the desktop starts (the platform's dictionary)."""
        read = self.bridge.call("platform.settings.read", {"module": "platform", "workspaceId": "global", "keys": ["language"]})
        i18n.configure(platform_locale_files(), read["values"]["language"]["value"])

    def _activated(self, state):
        if not self.hiding:
            self.last_active = state.id

    def open_window(self, workspace_id=None):
        for state in self.windows.copy():
            if workspace_id is not None and state.ready:
                identities = state.js(
                    "[new URL(location.href).searchParams.get('workspaceId'),"
                    "...Array.from(document.querySelectorAll('iframe[data-workspace-id]'),"
                    " f=>f.dataset.workspaceId)]"
                )
                if workspace_id in identities:
                    state.js(
                        "window.dispatchEvent(new CustomEvent('talyx-desktop-workspace',{detail:"
                        + json.dumps(workspace_id)
                        + "}))"
                    )
                    self.bring_to_front(state)
                    return state
        parsed = urlsplit(self.ui_url)
        query = parse_qs(parsed.query)
        if workspace_id is not None:
            query["workspaceId"] = [workspace_id]
        target = urlunsplit(parsed._replace(query=urlencode(query, doseq=True), fragment=""))
        state = WindowState()
        state.window = webview.create_window(
            "TALYX",
            html="<!doctype html><title>TALYX</title>",
            js_api=WindowAPI(self, state),
            width=1280,
            height=800,
            min_size=MIN_SIZE,
            background_color=BACKGROUND,
            hidden=self.start_in_tray and not self.windows,
        )
        self.windows.append(state)

        def before_load():
            state.ready, state.listening = False, False
            with self.lock:
                if self.pending_exit and self.pending_exit.get("deliveredTo") == state.id:
                    self.pending_exit["deliveredTo"] = None

        def loaded():
            if not state.initialized:
                from System import Func, Object

                def navigating(sender, event):
                    if origin(str(event.Uri)) != origin(self.ui_url):
                        event.Cancel = True

                def activated(sender, event):
                    self._activated(state)

                def setup():
                    core = state.window.native.browser.webview.CoreWebView2
                    core.NavigationStarting += navigating
                    state.window.native.Activated += activated
                    state.frame = Frame(state.window.native.Handle.ToInt64())

                state.window.native.Invoke(Func[Object](setup))
                state.initialized = True
                state.window.load_url(target)
            else:
                if origin(state.window.get_current_url()) != origin(self.ui_url):
                    raise PermissionError("Unexpected desktop navigation")
                state.ready = True
                state.js(ADAPTER)

        def closing():
            if not self.quitting:
                self.hide(state)
                return False

        def maximized_changed(value):
            if state.ready:
                state.js(
                    "window.dispatchEvent(new CustomEvent('talyx-desktop-window',{detail:{maximized:"
                    + json.dumps(value)
                    + "}}))"
                )

        def closed():
            self.windows.remove(state)

        state.window.events.before_load += before_load
        state.window.events.loaded += loaded
        state.window.events.closing += closing
        state.window.events.closed += closed
        state.window.events.maximized += lambda: maximized_changed(True)
        state.window.events.restored += lambda: maximized_changed(False)
        state.window.events.maximized += lambda: self.page_shown(state, True)
        state.window.events.restored += lambda: self.page_shown(state, True)
        state.window.events.minimized += lambda: self.page_shown(state, False)
        return state

    def page_shown(self, state, shown):
        """Minimised or in the tray, WebView2 still reports the page visible and keeps rendering it; hiding the control
        hides the page (visibilityState), so its animations and timers rest until the window is back."""
        from System import Action

        def apply():
            control = state.window.native.browser.webview
            control.Visible = shown
            if shown:
                control.Focus()  # back in front, the page has the keyboard (and Home's stone moves) at once

        if state.frame is not None:  # the control exists once the page has first loaded
            state.window.native.Invoke(Action(apply))

    def hide(self, state):
        self.hiding = True
        try:
            self.page_shown(state, False)
            state.window.hide()
        finally:
            self.hiding = False

    def bring_to_front(self, state):
        """Shows a window in front; a minimized one comes back as it was (a maximized one stays maximized)."""
        from System import Action

        state.window.show()
        if state.frame is not None:  # None until the window's page first loads (it may start in the tray)
            state.window.native.Invoke(Action(state.frame.bring_to_front))
        self.page_shown(state, True)

    def open_interface(self, icon=None, item=None):
        self.start_in_tray = False
        states = self.windows.copy()
        target = next((s for s in states if s.id == self.last_active), states[-1])
        for state in [*(s for s in states if s is not target), target]:
            self.bring_to_front(state)
        return target

    def delivery(self, state):
        if self.pending_exit and state.ready:
            self.pending_exit["deliveredTo"] = state.id
            return {k: self.pending_exit[k] for k in ("requestId", "preview")}

    def request_exit(self, icon=None, item=None):
        with self.lock:
            if self.quitting or self.previewing:
                return
            self.previewing = True
        try:
            if not self.pending_exit:
                self.pending_exit = {"requestId": uuid4().hex, "preview": self.exit_preview()}
            state = self.open_interface()
            with self.lock:
                payload = self.delivery(state) if state.listening else None
            if payload:
                state.js(
                    "window.dispatchEvent(new CustomEvent('talyx-desktop-exit',{detail:"
                    + json.dumps(payload)
                    + "}))"
                )
        finally:
            self.previewing = False

    def install_update(self):
        download = self.bridge.call("platform.updates.read").get("download") or {}
        folder = update_folder(self.data_root).resolve()
        installer = Path(download.get("path") or "").resolve()
        if download.get("state") != "ready" or installer.parent != folder or not installer.is_file():
            raise ValueError(i18n.t("No downloaded update is ready to install."))
        with self.lock:
            self.pending_update = (installer, download["version"])
        self.request_exit()

    def exit_preview(self):
        """What quitting stops: each open workspace with the work running for it (the same the platform lists when
        one workspace closes)."""
        workspaces = []
        for workspace in self.bridge.call("platform.workspace.list_open"):
            activities = self.bridge.call("platform.activity.list", {"workspaceId": workspace["workspaceId"]})
            workspaces.append({"workspace": workspace, "activities": activities["activities"]})
        return {"workspaces": workspaces}

    def check_confirmation(self, state, request_id):
        if (
            not self.pending_exit
            or self.pending_exit["requestId"] != request_id
            or self.pending_exit.get("deliveredTo") != state.id
        ):
            raise PermissionError("No matching delivered exit request")

    def confirmed_exit(self):
        """Quit: the Host this desktop started stops (closing its standard input asks it to, as Ctrl+C would); a Host
        it only attached to keeps running."""
        try:
            if self.backend is not None:
                self.backend.stdin.close()
                code = self.backend.wait(timeout=90)
                if code != 0:
                    raise RuntimeError(f"Backend exit code: {code}")
        except Exception:
            self.exit_code = 1
            traceback.print_exc()
        self.stop_interface()

    def stop_interface(self):
        self.tray.stop()
        for state in self.windows.copy():
            state.window.destroy()

    def run(self, workspace_id=None, started=None):
        # Its own taskbar group, so Windows shows TALYX's icon rather than the Python interpreter's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TALYX.Desktop")
        # Held while the desktop runs, so the installer can tell TALYX is open and ask to quit it before an upgrade
        # (installer/setup.iss, AppMutex). Windows releases it when the process ends.
        self.running_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "TALYX.Desktop")
        # Native menus (the tray's) in the dark, like the app: uxtheme's SetPreferredAppMode(ForceDark), ordinal 135, and
        # FlushMenuThemes, ordinal 136 (Windows 10 1903 and later; not in the headers).
        uxtheme = ctypes.WinDLL("uxtheme")
        uxtheme[135](2)
        uxtheme[136]()
        try:
            self.start_backend()
            self.read_language()
            self.open_window(workspace_id)
            self.activation = DesktopActivation(self.data_root, self.open_interface)
            self.tray = pystray.Icon(
                "TALYX",
                Image.open(ICON),
                "TALYX",
                menu=pystray.Menu(
                    pystray.MenuItem(i18n.t("Open TALYX"), self.open_interface, default=True),
                    pystray.MenuItem(i18n.t("Quit"), self.request_exit),
                ),
            )
            tray_thread = threading.Thread(target=self.tray.run, name="TALYX-tray")
            tray_thread.start()
            webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
            webview.start(
                started, gui="edgechromium", private_mode=False, storage_path=str(self.profile)
            )
            tray_thread.join(10)
        finally:
            if self.activation:
                self.activation.close()
            if self.tray:
                self.tray.stop()
            if self.bridge:
                self.bridge.close()
            if self.job:
                self.job.close()
            if self.backend:
                self.backend.wait(timeout=30)
                self.stdout_thread.join(10)
        if self.quitting and self.pending_update is not None:
            installer, version = self.pending_update
            install.start(installer, version, update_folder(self.data_root))
        return self.exit_code
