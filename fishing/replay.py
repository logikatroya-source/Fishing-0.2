"""Offline replay of fishing video: inspect gauge & catch events without clicks.

Command: python -m fishing.replay recording.mp4 --out report_folder
"""
from __future__ import annotations
import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path
import cv2
from .model import Settings
from .vision import Vision


def analyze(path: str | Path, sample_fps: float=5, on_progress=None, cancelled=None) -> tuple[dict,list[dict]]:
    cap=cv2.VideoCapture(str(path))
    if not cap.isOpened():raise ValueError('Не удалось открыть видео')
    fps=cap.get(cv2.CAP_PROP_FPS) or 30.0
    total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_size=[int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]
    if not 0<fps<=240 or total<1:raise ValueError('Некорректный видеопоток')
    sample_fps=max(.5,min(30.0,sample_fps))
    next_sample_t=-1e-6
    vision=Vision(Settings(auto_gauge=True,built_in_reward=True))
    frames=[]; active=[]; rewards=[]; start=None; peak=0.
    last_reward=False;last_visible=False;read_count=0;idx=-1
    while True:
        if cancelled is not None and cancelled():
            cap.release()
            raise RuntimeError('Анализ отменён')
        ok,frame=cap.read()
        if not ok:break
        idx+=1
        # Screen recordings are often variable-frame-rate. Always use presentation
        # timestamps instead of estimating seconds from frame_number/avg_fps.
        t_ms=cap.get(cv2.CAP_PROP_POS_MSEC)
        t=round((t_ms/1000 if t_ms>=0 else idx/fps),3)
        if t<next_sample_t:continue
        next_sample_t=t+1.0/sample_fps
        v=vision.inspect(frame,look_for_bite=False)
        frames.append({'sec':t,'gauge':bool(v.bar),'marker_x':v.marker_x,
                       'safe_left':v.safe_left,'safe_right':v.safe_right,
                       'fish_progress':round(v.fish_progress,4) if v.fish_progress is not None else None,
                       'gauge_quality':round(v.gauge_confidence,3),
                       'reward_visible':bool(v.success)})
        if v.bar:
            if not last_visible:
                start=t;peak=0.
            if v.fish_progress is not None:peak=max(peak,v.fish_progress)
        if not v.bar and last_visible and start is not None:
            active.append({'start_s':start,'end_s':t,'peak_progress':round(peak,3), 'reward_confirmed':False})
        if v.success and not last_reward:
            rewards.append(t)
        last_reward=bool(v.success);last_visible=bool(v.bar)
        read_count+=1
        if on_progress and read_count%15==0:on_progress(min(100,round(idx/max(1,total)*100)))
    cap.release()
    if last_visible and start is not None:
        active.append({'start_s':start,'end_s':round(t,3),'peak_progress':round(peak,3),'reward_confirmed':False})
    for reward in rewards:
        possible=[a for a in active if -0.5<=reward-a['end_s']<=4.0 and not a['reward_confirmed']]
        if possible:possible[-1]['reward_confirmed']=True
    report={'video':Path(path).name,'size':video_size,
            'video_fps':round(fps,2),'sample_fps':round(sample_fps,2),
            'frames_analyzed':len(frames),'minigames':active,
            'reward_events_sec':rewards,
            'confirmed_cycles':sum(x['reward_confirmed'] for x in active),
            'caution':'Offline visual analysis. It does not test mouse control or the live client.'}
    if on_progress:on_progress(100)
    return report,frames


def write_report(report:dict,frames:list[dict],out: str | Path):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    with (out/'report.json').open('w',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    with (out/'timeline.csv').open('w',newline='',encoding='utf-8-sig') as f:
        if frames:
            w=csv.DictWriter(f,fieldnames=list(frames[0]));w.writeheader();w.writerows(frames)
    return out/'report.json'


def main():
    parser=argparse.ArgumentParser(description='Анализ видео рыбалки без управления игрой')
    parser.add_argument('video',help='Путь к MP4')
    parser.add_argument('--out',default='fishing_replay_report',help='Папка для отчёта')
    parser.add_argument('--fps',type=float,default=5.0)
    args=parser.parse_args()
    report,frames=analyze(args.video,args.fps)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print('Сохранено:',write_report(report,frames,args.out))

if __name__=='__main__':main()
