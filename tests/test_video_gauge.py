"""Fixture frames cropped from the user's supplied capture (no full game video distributed)."""
from pathlib import Path
import cv2
import numpy as np
from fishing.hud import detect_gauge
from fishing.vision import Vision
from fishing.model import Settings
from fishing.engine import FishingEngine,Stage

SAMPLES=Path(__file__).parent/'fixtures'

def whole_hud(name):
    f=np.zeros((1080,1920,3),dtype=np.uint8)
    crop=cv2.imread(str(SAMPLES/name))
    assert crop is not None
    f[465:620,785:1195]=crop
    return f

def whole_reward(name):
    f=np.zeros((1080,1920,3),dtype=np.uint8)
    crop=cv2.imread(str(SAMPLES/name))
    assert crop is not None
    f[60:195,690:1230]=crop
    return f

def test_no_gauge_when_idle():
    assert not detect_gauge(whole_hud('idle.png')).visible

def test_all_recorded_gauges_have_correct_safe_zone():
    for name in ['start.png','middle.png','progress.png','near_finish.png','second.png','second_end.png']:
        g=detect_gauge(whole_hud(name))
        assert g.visible,name
        assert 850<=g.safe_left<=930,name
        assert 1000<=g.safe_right<=1090,name
        assert g.buoy_x is None or (g.safe_left-30<=g.buoy_x<=g.safe_right+30),name

def test_recorded_fish_progress_increases():
    g=[detect_gauge(whole_hud(name)) for name in ['start.png','middle.png','progress.png','near_finish.png']]
    progress=[x.progress for x in g]
    assert all(x is not None for x in progress)
    assert progress == sorted(progress) and progress[-1] > .80

def test_reward_banner_recognized_for_two_different_fish():
    vision=Vision(Settings())
    assert not vision.inspect(whole_reward('no_reward.png')).success
    assert vision.inspect(whole_reward('reward_one.png')).success
    assert vision.inspect(whole_reward('reward_other.png')).success

def test_engine_uses_dynamic_green_edges():
    e=FishingEngine(Settings())
    e.start('assist',0.0)
    e.step(vision_view(True,960,870,1050),.1)
    assert e.stage==Stage.REEL
    actions=e.step(vision_view(True,884,870,1050),.2)
    assert any(a.type=='down' for a in actions)
    actions=e.step(vision_view(True,1032,870,1050),.5)
    assert any(a.type=='up' for a in actions)

def vision_view(bar, marker, left, right):
    from fishing.vision import View
    return View(bar=bar, marker_x=marker, safe_left=left,safe_right=right)

def test_assist_without_manual_template_is_allowed():
    e=FishingEngine(Settings(auto_gauge=True))
    e.start('assist',0)
    assert e.stage==Stage.ASSIST
