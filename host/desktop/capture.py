"""Capture the laid-out panel from its own WebView2 frame."""

import hashlib
import io
import json
import time
from pathlib import Path
from uuid import uuid4

from PIL import Image


class PanelNotCapturable(Exception):
    """The panel is not on screen as it is, or the window changed while it was captured: the user can fix it."""


def panel_png(state, workspace, panel):
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    from System import Action
    from System.IO import MemoryStream

    target = json.dumps([workspace, panel])
    script = """(([workspace, panel]) => {
      const frame = [...document.querySelectorAll('iframe[data-workspace-id]')]
        .find(f => f.dataset.workspaceId === workspace);
      const doc = frame ? frame.contentDocument : document;
      if (!doc || (!frame && new URL(location.href).searchParams.get('workspaceId') !== workspace)) return null;
      const element = [...doc.querySelectorAll('[data-talyx-panel]')].find(e => e.dataset.talyxPanel === panel);
      if (!element || !element.checkVisibility() || (frame && !frame.checkVisibility())) return null;
      const rect = element.getBoundingClientRect(), parent = frame?.getBoundingClientRect();
      const x = (parent?.x ?? 0) + rect.x, y = (parent?.y ?? 0) + rect.y;
      if (rect.width <= 0 || rect.height <= 0 || x < -0.5 || y < -0.5 ||
          x + rect.width > innerWidth + 0.5 || y + rect.height > innerHeight + 0.5) return null;
      return [innerWidth, innerHeight, x, y, x + rect.width, y + rect.height];
    })(""" + target + ")"
    rect = state.js(script)
    if not rect:
        raise PanelNotCapturable("Open the panel in the workspace window to capture it")
    stream = MemoryStream()
    tasks = []
    def start():
        core = state.window.native.browser.webview.CoreWebView2
        tasks.append(core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream))

    try:
        state.window.native.Invoke(Action(start))
        tasks[0].GetAwaiter().GetResult()
        if state.js(script) != rect:
            raise PanelNotCapturable("The panel moved while the window was being captured")
        with Image.open(io.BytesIO(bytes(stream.ToArray()))) as source:
            width, height, left, top, right, bottom = rect
            sx, sy = source.width / width, source.height / height
            if abs(sx - sy) > 0.03:
                raise PanelNotCapturable("The window changed size during capture")
            box = (max(0, round(left * sx)), max(0, round(top * sy)),
                   min(source.width, round(right * sx)), min(source.height, round(bottom * sy)))
            image = source.crop(box)
            result = io.BytesIO()
            image.save(result, format="PNG")
            return result.getvalue()
    finally:
        stream.Dispose()


def save_png(directory: Path, pixels: bytes) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    for old in directory.glob("*.png"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    path = directory / (uuid4().hex + ".png")
    with path.open("xb") as output:
        output.write(pixels)
    return {"path": str(path.resolve()), "bytes": len(pixels), "sha256": hashlib.sha256(pixels).hexdigest()}
