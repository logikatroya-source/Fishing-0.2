"""Color/geometric Albion fishing HUD reader.

Treats the top green/red tension rail and blue progress rail as two separate
measurements.  No neural net, no OCR, no game memory access.  Assumes one
fishing minigame is visible near the middle of the game viewport.
"""
from __future__ import annotations
from dataclasses import dataclass
import cv2
import numpy as np

@dataclass(frozen=True)
class Gauge:
    visible: bool = False
    buoy_x: int | None = None
    safe_left: int | None = None
    safe_right: int | None = None
    progress: float | None = None
    progress_x: int | None = None
    quality: float = 0.0
    rect: tuple[int,int,int,int] | None = None


def _longest_run(indices: np.ndarray, min_len: int = 1):
    if len(indices)==0:return None
    gaps=np.where(np.diff(indices)>1)[0]
    starts=np.r_[0,gaps+1]
    ends=np.r_[gaps,len(indices)-1]
    ix=np.argmax(indices[ends]-indices[starts])
    start,end=int(indices[starts[ix]]),int(indices[ends[ix]])
    return (start,end) if end-start+1>=min_len else None


def detect_gauge(frame: np.ndarray, search: tuple[int,int,int,int] | None = None) -> Gauge:
    if frame is None or frame.ndim!=3:return Gauge()
    H,W=frame.shape[:2]
    if search is None:
        x0,y0,x1,y1=round(W*.22),round(H*.27),round(W*.78),round(H*.77)
    else:
        x0,y0,x1,y1=search
        x0=max(0,x0);y0=max(0,y0);x1=min(W,x1);y1=min(H,y1)
    if x1-x0<100 or y1-y0<50:return Gauge()
    img=frame[y0:y1,x0:x1]
    hsv=cv2.cvtColor(img,cv2.COLOR_BGR2HSV)
    # Blue lower progress rail: strongly saturated brighter blue, not black water.
    blue=cv2.inRange(hsv,(97,95,93),(125,255,230))
    min_width=max(58,int(W*.075))
    # Bridge bright fish/chevron interruptions before selecting horizontal rails.
    bridge=cv2.getStructuringElement(cv2.MORPH_RECT,(max(13,int(W*.018)),3))
    joined=cv2.morphologyEx(blue,cv2.MORPH_CLOSE,bridge)
    kernel=cv2.getStructuringElement(cv2.MORPH_RECT,(max(50,int(W*.05)),3))
    stripes=cv2.morphologyEx(joined,cv2.MORPH_OPEN,kernel)
    n,_,stats,_=cv2.connectedComponentsWithStats(stripes)
    candidates=[]
    for i in range(1,n):
        xx,yy,ww,hh,area=[int(z) for z in stats[i]]
        if ww<max(80,round(W*.095)) or ww>round(W*.3):continue
        if hh<4 or hh>round(H*.06):continue
        # Top green band must sit 4..30 px above blue.
        gy0=max(0, yy-max(14,round(H*.035)))
        gy1=max(0,yy-3)
        if gy1<=gy0:continue
        top=hsv[gy0:gy1,max(0,xx-5):min(hsv.shape[1],xx+ww+5)]
        mask=cv2.inRange(top,(35,65,55),(88,255,240))
        # Require horizontal long green strip, not scattered grass.
        freq=np.count_nonzero(mask,axis=0)
        valid=np.flatnonzero(freq >= max(3,int(mask.shape[0]*.24)))
        # A bobber splits the green strip into two halves. Fill that
        # small gap without treating distant vegetation as part of the HUD.
        bo=np.zeros((1,mask.shape[1]),np.uint8)
        bo[0,valid]=255
        bo=cv2.morphologyEx(bo,cv2.MORPH_CLOSE,np.ones((1,max(14,int(W*.02))),np.uint8))
        run=_longest_run(np.flatnonzero(bo[0]),max(38,int(ww*.36)))
        if run is None:continue
        safe_l,safe_r=run
        local_blue=blue[yy:yy+hh,xx:xx+ww]
        blue_density=float((local_blue>0).mean())
        green_width=(safe_r-safe_l+1)/ww
        if blue_density<.4 or green_width<.35:continue
        # Score for position and density.
        center=(xx+ww/2+x0)/W
        score=blue_density+.4*green_width-.2*abs(center-.5)
        candidates.append((score,xx,yy,ww,hh,gy0,gy1,safe_l,safe_r))
    if not candidates:return Gauge()
    score,xx,yy,ww,hh,gy0,gy1,sl,sr=max(candidates)
    # Green range comes from top strip whose local origin includes xx-5.
    safe_left=x0+max(0,xx-5)+sl
    safe_right=x0+max(0,xx-5)+sr
    blue_left=x0+xx;blue_right=x0+xx+ww
    # Orange feather appears directly above the green rail. Reject the red ends
    # by restricting to the upper area, and keep only coherent orange clusters.
    fy0=max(0,gy0-max(5,round(H*.028)))
    fy1=min(hsv.shape[0],gy0+3)
    fx0=max(0,xx-10);fx1=min(hsv.shape[1],xx+ww+10)
    feather=hsv[fy0:fy1,fx0:fx1]
    orange=cv2.inRange(feather,(7,130,145),(30,255,255))
    # Limit to the middle 70% of the rail, where the bobber is allowed to move.
    cx0=int((safe_left-x0-fx0)-ww*.06)
    cx1=int((safe_right-x0-fx0)+ww*.06)
    cx0=max(0,cx0);cx1=min(orange.shape[1],cx1)
    filt=np.zeros_like(orange);filt[:,cx0:cx1]=orange[:,cx0:cx1]
    n,lab,st,cen=cv2.connectedComponentsWithStats(filt)
    marker_x=None
    blobs=[]
    for k in range(1,n):
        bx,by,bw,bh,area=st[k]
        marker_world_x=float(cen[k][0])+fx0+x0
        middle=(safe_left+safe_right)/2
        if (max(25,round(W*H/100000)) <= area <= max(1300,int(W*H/1200))
                and bh>=5 and bw<round(W*.035)
                and abs(marker_world_x-middle)<(safe_right-safe_left)*.38):
            blobs.append((int(area),marker_world_x))
    if blobs:
        # Feather above rail has the strongest orange connected area.
        blobs.sort(reverse=True)
        marker_x=round(blobs[0][1])
    # Find white fish-shaped cursor over the blue rail. It travels left→right.
    by0=yy+max(0,hh//5);by1=min(hsv.shape[0],yy+hh+2)
    part=img[by0:by1,xx:xx+ww]
    if part.size:
        hp=cv2.cvtColor(part,cv2.COLOR_BGR2HSV)
        white=cv2.inRange(hp,(0,0,155),(179,100,255))
        white=cv2.morphologyEx(white,cv2.MORPH_OPEN,np.ones((2,2),np.uint8))
        num,_,ws,cent=cv2.connectedComponentsWithStats(white)
        fish=[]
        for k in range(1,num):
            bx,by,bw,bh,area=ws[k]
            if 12<=area<=max(220,ww*2) and 5<=bw<ww*.22 and 2<=bh<=hh+4:
                fish.append((int(area),float(cent[k][0])+blue_left))
        fish.sort(reverse=True)
        fish_x=int(round(fish[0][1])) if fish else None
    else:fish_x=None
    progress= min(1.0,max(0.0,(fish_x-blue_left)/max(1,ww))) if fish_x is not None else None
    # Don't invent marker or fish coordinates if visual evidence is missing.
    confidence=min(1.0,max(0.0,(score-.5)/.75))
    if marker_x is None:confidence=min(confidence,.35)
    return Gauge(True, marker_x,safe_left,safe_right,progress,fish_x,confidence,(blue_left,y0+gy0,ww,y0+yy+hh-(y0+gy0)))
