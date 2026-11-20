"""The desktop window without the system title bar: the shell's top bar is the title bar.

The window keeps its standard resizable frame, so Windows still gives it a shadow, edge resizing, snapping and the
maximize animation; only the caption is taken out of the frame (WM_NCCALCSIZE). The top bar moves the window and
resizes it from the top edge by handing the press to Windows as a press on the caption or top border, which keeps
snapping and the system's own drag.
"""

import ctypes
from ctypes import wintypes
from pathlib import Path

ICON = Path(__file__).with_name("talyx.ico")

user32 = ctypes.windll.user32
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.CallWindowProcW.restype = LRESULT
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]

GWLP_WNDPROC = -4
WM_NCCALCSIZE = 0x0083
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION, HTTOP, HTTOPLEFT, HTTOPRIGHT = 2, 12, 13, 14
SM_CYFRAME, SM_CXPADDEDBORDER = 33, 92
SWP_FRAMECHANGED, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x20, 0x2, 0x1, 0x4, 0x10
SW_RESTORE = 9
WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXICON, SM_CXSMICON = 11, 49
EDGES = {"top": HTTOP, "top-left": HTTOPLEFT, "top-right": HTTOPRIGHT}


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class Frame:
    """One window's frame. Every call runs on the window's own thread (the caller marshals)."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.proc = WNDPROC(self._proc)  # kept alive as long as the window
        self.previous = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(self.proc, ctypes.c_void_p))
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        # The taskbar and Alt+Tab show the TALYX mark, not the Python interpreter's icon.
        dpi = user32.GetDpiForWindow(hwnd)
        for kind, metric in ((ICON_BIG, SM_CXICON), (ICON_SMALL, SM_CXSMICON)):
            size = user32.GetSystemMetricsForDpi(metric, dpi)
            icon = user32.LoadImageW(None, str(ICON), IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if icon:
                user32.SendMessageW(hwnd, WM_SETICON, kind, icon)

    def _proc(self, hwnd, message, wparam, lparam):
        if message == WM_NCCALCSIZE and wparam:
            params = ctypes.cast(lparam, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
            top = params.rgrc[0].top
            result = user32.CallWindowProcW(self.previous, hwnd, message, wparam, lparam)
            # The client area starts at the window's top: no caption. Maximized, the frame hangs off the screen, so
            # the client starts where the screen does.
            dpi = user32.GetDpiForWindow(hwnd)
            params.rgrc[0].top = top + (user32.GetSystemMetricsForDpi(SM_CYFRAME, dpi)
                                        + user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi) if user32.IsZoomed(hwnd) else 0)
            return result
        return user32.CallWindowProcW(self.previous, hwnd, message, wparam, lparam)

    def _press(self, hit):
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.ReleaseCapture()
        user32.SendMessageW(self.hwnd, WM_NCLBUTTONDOWN, hit, (point.y & 0xFFFF) << 16 | point.x & 0xFFFF)

    def drag(self):
        """Moves the window with the pressed mouse, as from the caption (snapping included)."""
        self._press(HTCAPTION)

    def resize(self, edge):
        """Resizes the window from its top edge or a top corner with the pressed mouse."""
        if not user32.IsZoomed(self.hwnd):
            self._press(EDGES[edge])

    def maximized(self):
        return bool(user32.IsZoomed(self.hwnd))

    def bring_to_front(self):
        """Shows the window in front, restored to how it was if minimized (maximized stays maximized)."""
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_RESTORE)
        user32.SetForegroundWindow(self.hwnd)
