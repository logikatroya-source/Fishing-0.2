"""Screenshot-only vision; no network, memory reading or injected code."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import cv2
import numpy as np
from .model import Rect, Settings, settings_path
from .hud import detect_gauge, Gauge


def crop(frame: np.ndarray, roi: Rect | None) -> np.ndarray | None:
    if frame is None or roi is None:return None
    found=roi.clamp(frame.shape[1],frame.shape[0])
    if found is None:return None
    return frame[found.y:found.y+found.h,found.x:found.x+found.w]


def read_image(path: str) -> np.ndarray | None:
    try:
        b=np.fromfile(str(path),dtype=np.uint8)
        im=cv2.imdecode(b,cv2.IMREAD_COLOR)
        return im
    except Exception:return None


def write_image(path: str,frame: np.ndarray) -> bool:
    try:
        p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
        ok,data=cv2.imencode('.png',frame)
        if ok:data.tofile(str(p))
        return bool(ok)
    except Exception:return False


def template_match(frame: np.ndarray | None, roi: Rect | None, template: np.ndarray | None) -> tuple[float,tuple[int,int] | None]:
    part=crop(frame,roi) if roi else frame
    if part is None or template is None:return 0.0,None
    if part.shape[0] < template.shape[0] or part.shape[1] < template.shape[1]:return 0.0,None
    g=cv2.cvtColor(part,cv2.COLOR_BGR2GRAY)
    t=cv2.cvtColor(template,cv2.COLOR_BGR2GRAY)
    # CCORR_NORMED is stable for near-uniform icons; CCOEFF requires variance.
    if t.std()<4.0:
        return 0.0,None
    response=cv2.matchTemplate(g,t,cv2.TM_CCOEFF_NORMED)
    _,maxv,_,maxloc=cv2.minMaxLoc(response)
    ox=roi.x if roi else 0
    oy=roi.y if roi else 0
    x=int(maxloc[0]+ox+template.shape[1]//2)
    y=int(maxloc[1]+oy+template.shape[0]//2)
    return float(maxv),(x,y)


def green_bar_present(frame: np.ndarray,roi: Rect | None,ratio_threshold: float=.018) -> bool:
    part=crop(frame,roi)
    if part is None:return False
    hsv=cv2.cvtColor(part,cv2.COLOR_BGR2HSV)
    # Albion green strip can be bright or muted; use width as well as density to
    # reject tiny isolated green particles in the scene.
    mask=cv2.inRange(hsv,np.array([35,48,42],dtype=np.uint8),np.array([95,255,255],dtype=np.uint8))
    if np.count_nonzero(mask)/mask.size < ratio_threshold:return False
    occupied=np.count_nonzero(np.count_nonzero(mask,axis=0)>0)
    return occupied >= max(8,round(part.shape[1]*.14))


class MotionBite:
    """Motion detector with warm-up, EMA background, and consecutive confirmation."""
    def __init__(self):
        self.background: np.ndarray | None=None
        self.positives=0
        self.score=0.0
        self.frames=0

    def reset(self):
        self.background=None;self.positives=0;self.score=0.0;self.frames=0

    def observe(self,frame:np.ndarray,roi:Rect,threshold:float,consecutive:int=3) -> bool:
        part=crop(frame,roi)
        if part is None:return False
        gray=cv2.GaussianBlur(cv2.cvtColor(part,cv2.COLOR_BGR2GRAY),(5,5),0).astype(np.float32)
        small=cv2.resize(gray,(max(8,min(90,gray.shape[1])),max(8,min(65,gray.shape[0]))))
        if self.background is None or self.background.shape!=small.shape:
            self.background=small.copy();self.positives=0;self.frames=0;return False
        self.frames+=1
        self.score=float(np.mean(cv2.absdiff(small,self.background)))
        # slow adaptation to ordinary water animation; do not absorb strong hits
        if self.score < threshold*1.6:
            cv2.accumulateWeighted(small,self.background,0.08)
        if self.frames<6:
            self.positives=0
            return False
        self.positives=self.positives+1 if self.score>=threshold else 0
        return self.positives>=max(2,consecutive)


@dataclass(frozen=True)
class View:
    bar:bool=False
    marker_x:int|None=None
    marker_conf:float=0.0
    bite:bool=False
    bite_score:float=0.0
    success:bool=False
    safe_left:int|None=None
    safe_right:int|None=None
    fish_progress:float|None=None
    gauge_confidence:float=0.0
    gauge_rect:tuple[int,int,int,int]|None=None


class Vision:
    def __init__(self,cfg:Settings):
        self.cfg=cfg
        self.bite=MotionBite()
        self.marker=None
        self.result=None
        self.builtin_reward=read_image(str(Path(__file__).resolve().parent/'assets'/'reward_text.png'))
        self.load_templates()

    def load_templates(self):
        def resolve(path: str):
            if not path:return None
            candidate=Path(path)
            if not candidate.is_absolute():candidate=settings_path().parent/candidate
            return read_image(str(candidate))
        self.marker=resolve(self.cfg.marker_template)
        self.result=resolve(self.cfg.result_template)

    def inspect(self,frame:np.ndarray,look_for_bite:bool=False)->View:
        gauge = detect_gauge(frame) if self.cfg.auto_gauge else Gauge()
        if self.cfg.auto_gauge:
            bar = gauge.visible
            marker_x = gauge.buoy_x
            c = gauge.quality
        else:
            bar = green_bar_present(frame,self.cfg.bar_roi,self.cfg.bar_green_ratio_threshold)
            c,pos = template_match(frame,self.cfg.bar_roi,self.marker)
            marker_x = pos[0] if pos is not None and c >= self.cfg.marker_confidence else None
        success_prob,_=template_match(frame,self.cfg.result_roi,self.result)
        is_success=bool(self.result is not None and success_prob>.80)
        if self.cfg.built_in_reward and self.builtin_reward is not None:
            h,w=frame.shape[:2]
            reward_roi=Rect(round(w*.31),round(h*.045),round(w*.38),round(h*.16))
            reward_score,_=template_match(frame,reward_roi,self.builtin_reward)
            if reward_score >= .84:is_success=True
        bite=False
        if look_for_bite and self.cfg.bite_roi:
            bite=self.bite.observe(frame,self.cfg.bite_roi,self.cfg.bite_motion_threshold,self.cfg.bite_consecutive_frames)
        return View(bar=bar,marker_x=marker_x,marker_conf=c,bite=bite,
                    bite_score=self.bite.score,success=is_success,
                    safe_left=gauge.safe_left,safe_right=gauge.safe_right,
                    fish_progress=gauge.progress,gauge_confidence=gauge.quality,
                    gauge_rect=gauge.rect)
