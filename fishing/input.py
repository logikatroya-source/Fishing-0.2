"""Conservative mouse input with independent hold watchdog and emergency release."""
from __future__ import annotations
import threading
import time
from .windows import focused,box
from .engine import Action


class MouseInput:
    def __init__(self):
        from pynput.mouse import Controller,Button
        self.mouse=Controller()
        self.button=Button.left
        self.held=False
        self._lock=threading.RLock()
        self._deadline=0.0
        self.watchdog_fired=False
        self._stop=threading.Event()
        self._watchdog=threading.Thread(target=self._watchdog_loop,name='fishing-input-watchdog',daemon=True)
        self._watchdog.start()

    def _watchdog_loop(self):
        while not self._stop.wait(.045):
            with self._lock:
                if self.held and time.monotonic()>self._deadline:
                    try:self.mouse.release(self.button)
                    finally:
                        self.held=False
                        self.watchdog_fired=True

    def release(self):
        with self._lock:
            if self.held:
                try:self.mouse.release(self.button)
                finally:self.held=False

    def close(self):
        self.release();self._stop.set()
        if self._watchdog.is_alive():self._watchdog.join(timeout=.2)

    def execute(self,action:Action,hwnd:int,pid:int,expected_size:list[int],hold_limit_s:float=.95):
        with self._lock:
            if action.type=='up':
                self.release();return
            if action.type!='down':return
            if not focused(hwnd,pid):
                self.release();raise RuntimeError('Игровое окно не в фокусе')
            r=box(hwnd)
            if not r or (expected_size and list(r[2:])!=list(expected_size)):
                self.release();raise RuntimeError('Размер окна игры изменился, требуется калибровка')
            if action.point is not None:
                x,y=action.point
                if not (r[2]*.08 < x < r[2]*.88 and r[3]*.12 < y < r[3]*.77):
                    raise RuntimeError('Точка заброса близко к интерфейсу или краю окна')
                self.mouse.position=(r[0]+int(x),r[1]+int(y))
            if not self.held:
                self.mouse.press(self.button)
                self.held=True
                self._deadline=time.monotonic()+hold_limit_s
