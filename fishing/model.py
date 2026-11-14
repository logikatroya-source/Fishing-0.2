"""Typed and safely persisted configuration for one fishing station."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
import json
from json import JSONDecodeError
import os


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def valid(self) -> bool:
        return self.w >= 4 and self.h >= 4 and self.x >= 0 and self.y >= 0

    def inside(self, px: int, py: int) -> bool:
        return self.x <= px < self.x+self.w and self.y <= py < self.y+self.h

    def clamp(self, width: int, height: int) -> Optional['Rect']:
        x0=max(0,min(self.x,width)); y0=max(0,min(self.y,height))
        x1=max(0,min(self.x+self.w,width)); y1=max(0,min(self.y+self.h,height))
        if x1-x0<4 or y1-y0<4:return None
        return Rect(x0,y0,x1-x0,y1-y0)


@dataclass
class Settings:
    window_title: str = ''
    client_size: list[int] = field(default_factory=list)
    cast_xy: Optional[list[int]] = None
    bite_roi: Optional[Rect] = None
    bar_roi: Optional[Rect] = None
    safe_roi: Optional[Rect] = None
    marker_template: str = ''
    result_template: str = ''
    result_roi: Optional[Rect] = None
    cast_hold_ms: int = 650
    hook_delay_ms: int = 150
    reel_timeout_s: float = 16.0
    bite_timeout_s: float = 30.0
    verify_timeout_s: float = 1.8
    between_cast_s: float = 1.25
    max_attempts: int = 10
    max_verified: int = 10
    bite_motion_threshold: float = 7.0
    bite_consecutive_frames: int = 3
    min_bite_wait_s: float = 1.2
    marker_confidence: float = 0.65
    marker_deadband_px: int = 9
    marker_margin_px: int = 11
    predictive_s: float = 0.105
    max_hold_s: float = 0.48
    max_lost_marker_s: float = 1.1
    min_reel_visible_s: float = 0.35
    bar_green_ratio_threshold: float = 0.018
    auto_gauge: bool = True
    built_in_reward: bool = True
    min_progress_for_likely: float = 0.78
    fps: int = 15

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> 'Settings':
        known=set(cls.__dataclass_fields__)
        kwargs={k:v for k,v in payload.items() if k in known}
        for k in ('bite_roi','bar_roi','safe_roi','result_roi'):
            if isinstance(kwargs.get(k),dict):
                r=kwargs[k]
                kwargs[k]=Rect(*(int(r[n]) for n in ('x','y','w','h')))
        settings=cls(**kwargs)
        settings.validate()
        return settings

    def validate(self) -> None:
        if not 1 <= int(self.max_attempts) <= 100:raise ValueError('Число попыток вне диапазона 1..100')
        if not 1 <= int(self.max_verified) <= 100:raise ValueError('Лимит рыб вне диапазона 1..100')
        if not 100 <= int(self.cast_hold_ms) <= 2500:raise ValueError('Время заброса вне диапазона')
        if not 1 <= int(self.fps) <= 30:raise ValueError('Неверная частота кадров')
        for key in ('bite_roi','bar_roi','safe_roi','result_roi'):
            roi=getattr(self,key)
            if roi is not None and not roi.valid:raise ValueError('Некорректная область '+key)

    def ready_to_reel(self) -> bool:
        if self.auto_gauge:
            return True
        return (self.bar_roi is not None and self.bar_roi.valid
                and self.safe_roi is not None and self.safe_roi.valid
                and bool(self.marker_template))

    def ready_to_auto(self) -> bool:
        return (self.cast_xy is not None and len(self.cast_xy)==2
                and self.bite_roi is not None and self.bite_roi.valid and self.ready_to_reel())


def settings_path() -> Path:
    base=os.environ.get('LOCALAPPDATA') or str(Path.home()/'.local'/'share')
    return Path(base)/'GamePilotFishing'/'profile.json'


def save_settings(cfg: Settings, path: Path | None = None) -> Path:
    cfg.validate()
    path=path or settings_path(); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg.to_dict(),indent=2,ensure_ascii=False),encoding='utf-8')
    tmp.replace(path)
    return path


def load_settings(path: Path | None=None) -> Settings:
    path=path or settings_path()
    if not path.exists():return Settings()
    try:return Settings.from_dict(json.loads(path.read_text(encoding='utf-8')))
    except (ValueError,TypeError,KeyError,UnicodeError,JSONDecodeError) as e: # type: ignore[name-defined]
        raise ValueError('Некорректные настройки: '+str(e)) from e
