"""Windows client HWND handling. Fail-closed focus check on every input."""
from __future__ import annotations
from dataclasses import dataclass
import os
import sys

IS_WIN=sys.platform=='win32'

@dataclass(frozen=True)
class Window:
    hwnd:int
    pid:int
    title:str

if IS_WIN:
    import ctypes
    from ctypes import wintypes
    user32=ctypes.windll.user32
    class POINT(ctypes.Structure):
        _fields_=[('x',ctypes.c_long),('y',ctypes.c_long)]
    class RECT(ctypes.Structure):
        _fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:pass


def box(hwnd:int):
    if not IS_WIN:return None
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):return None
    rect=RECT();pt=POINT(0,0)
    if not user32.GetClientRect(hwnd,ctypes.byref(rect)):return None
    if not user32.ClientToScreen(hwnd,ctypes.byref(pt)):return None
    w=rect.right-rect.left;h=rect.bottom-rect.top
    if w<400 or h<300:return None
    return (pt.x,pt.y,w,h)


def windows()->list[Window]:
    if not IS_WIN:return []
    found=[]
    cbtype=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def cb(hwnd,lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):return True
        n=user32.GetWindowTextLengthW(hwnd)
        if n<3:return True
        s=ctypes.create_unicode_buffer(n+1)
        user32.GetWindowTextW(hwnd,s,n+1)
        pid=wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value==os.getpid():return True
        if box(hwnd):found.append(Window(int(hwnd),int(pid.value),s.value))
        return True
    cbptr=cbtype(cb)
    user32.EnumWindows(cbptr,0)
    return found


def focused(hwnd:int,pid:int|None=None)->bool:
    if not IS_WIN or not hwnd:return False
    fg=user32.GetForegroundWindow()
    if int(fg)!=int(hwnd) or not user32.IsWindow(hwnd):return False
    actual=wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd,ctypes.byref(actual))
    return actual.value!=os.getpid() and (pid is None or actual.value==pid)
