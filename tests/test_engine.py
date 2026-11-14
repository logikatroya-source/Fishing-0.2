from pathlib import Path
import pytest
from fishing.engine import FishingEngine,Stage
from fishing.model import Settings,Rect,save_settings,load_settings
from fishing.vision import View


def cfg():
    return Settings(cast_xy=[260,200],bite_roi=Rect(180,120,50,50),bar_roi=Rect(80,300,260,45),
                    safe_roi=Rect(120,302,200,24),marker_template='marker.png',
                    between_cast_s=.2,cast_hold_ms=500,bite_timeout_s=3,min_bite_wait_s=.7,
                    hook_delay_ms=100,verify_timeout_s=.5, max_attempts=2)


def tick(e,now,**kw):return e.step(View(**kw),now)


def test_observe_no_inputs():
    e=FishingEngine(cfg());e.start('observe',0)
    assert not tick(e,1,bite=True,bar=True,marker_x=200)


def test_requires_calibration():
    with pytest.raises(ValueError):FishingEngine(Settings()).start('auto',0)
    # Built-in gauge reader no longer requires hand-drawn gauge and marker.
    FishingEngine(Settings()).start('assist',0)
    with pytest.raises(ValueError):FishingEngine(Settings(auto_gauge=False)).start('assist',0)


def test_cast_wait_and_hook():
    e=FishingEngine(cfg());e.start('auto',0)
    assert tick(e,0)[0].type=='down';assert e.attempts==1
    assert not tick(e,.1)
    assert tick(e,.51)[0].type=='up';assert e.stage==Stage.BITE
    assert not tick(e,.81,bite=True)
    actions=tick(e,1.3,bite=True)
    assert actions[0].type=='down' and e.stage==Stage.HOOK
    assert tick(e,1.45)[0].type=='up'
    assert e.stage==Stage.REEL_WAIT
    tick(e,1.55,bar=True,marker_x=200)
    assert e.stage==Stage.REEL


def test_reel_hysteresis_hold_and_release():
    e=FishingEngine(cfg());e.start('assist',0)
    tick(e,.1,bar=True,marker_x=200)
    assert e.stage==Stage.REEL
    assert tick(e,.2,bar=True,marker_x=125)[0].type=='down'
    assert e.button_down
    assert tick(e,.5,bar=True,marker_x=290)[0].type=='up'
    assert not e.button_down


def test_reel_force_release_after_hold_limit():
    e=FishingEngine(cfg());e.start('assist',0)
    tick(e,.1,bar=True,marker_x=200)
    tick(e,.2,bar=True,marker_x=125)
    assert tick(e,.8,bar=True,marker_x=125)[0].type=='up'


def test_lost_marker_releases_and_pauses():
    e=FishingEngine(cfg());e.start('assist',0)
    tick(e,.1,bar=True,marker_x=200)
    tick(e,.2,bar=True,marker_x=125)
    a=tick(e,.3,bar=True)
    assert a and a[0].type=='up'
    tick(e,1.4,bar=True)
    assert e.stage==Stage.ERROR


def test_reel_bar_disappearance_not_verification():
    e=FishingEngine(cfg());e.start('assist',0)
    tick(e,.1,bar=True,marker_x=200)
    tick(e,.3,bar=True,marker_x=160)
    tick(e,.45,bar=False)
    tick(e,1.15,bar=False)
    assert e.stage==Stage.VERIFY
    tick(e,1.7,success=False)
    assert e.verified==0 and e.uncertain==1
    assert e.stage==Stage.ASSIST


def test_verified_catch_and_stop_limit():
    c=cfg();c.max_attempts=1;c.result_template='yes.png'
    e=FishingEngine(c);e.start('auto',0)
    tick(e,0);tick(e,.6);tick(e,1.5,bar=True,marker_x=250)
    assert e.stage==Stage.REEL
    tick(e,1.7,bar=True,marker_x=260)
    tick(e,2.35,bar=False)
    assert e.stage==Stage.VERIFY
    tick(e,2.4,success=True)
    assert e.verified==1
    tick(e,2.7)
    assert e.stage==Stage.NEED_MOVE


def test_timeout_recast():
    e=FishingEngine(cfg());e.start('auto',0)
    tick(e,0);tick(e,.6)
    tick(e,4.0)
    assert e.stage==Stage.COOLDOWN and e.uncertain==1
    tick(e,4.3)
    assert e.attempts==2


def test_focus_loss_releases_and_stops():
    e=FishingEngine(cfg());e.start('auto',0)
    tick(e,0)
    a=e.step(View(),.1,foreground=False)
    assert a[0].type=='up'
    assert e.stage==Stage.PAUSE
    assert not tick(e,.3)


def test_emergency_while_holding():
    e=FishingEngine(cfg());e.start('auto',0)
    tick(e,0)
    assert e.emergency(.1)[0].type=='up'
    assert e.stage==Stage.ERROR
    assert tick(e,.7)==[]


def test_new_spot_requires_recalibration():
    e=FishingEngine(cfg());e.start('auto',0)
    tick(e,0)
    a=e.new_spot(.1)
    assert a[0].type=='up'
    assert e.cfg.cast_xy is None
    assert e.attempts==0
    with pytest.raises(ValueError):e.start('auto',2)


def test_persist_roundtrip(tmp_path):
    c=cfg();p=tmp_path/'profile.json'
    save_settings(c,p)
    d=load_settings(p)
    assert d.bite_roi==c.bite_roi and d.max_attempts==c.max_attempts
    assert d.cast_xy==c.cast_xy


def test_bad_config_rejected():
    with pytest.raises(ValueError):Settings(max_attempts=101).validate()
    with pytest.raises(ValueError):Settings(fps=0).validate()
    with pytest.raises(ValueError):Settings(bite_roi=Rect(1,1,0,2)).validate()


def test_assist_no_cast():
    e=FishingEngine(cfg());e.start('assist',0)
    assert not tick(e,.2,bar=False,bite=True)
    assert e.attempts==0


def test_unknown_outcome_not_fish():
    e=FishingEngine(cfg());e.start('assist',0)
    tick(e,.1,bar=True,marker_x=200);tick(e,.6,bar=True,marker_x=250)
    tick(e,1.3,bar=False);tick(e,1.85)
    assert e.verified==0


def test_false_bar_without_marker_ignored():
    e=FishingEngine(cfg());e.start('auto',0)
    tick(e,0);tick(e,.6)
    tick(e,.8,bar=True,marker_x=None)
    assert e.stage==Stage.BITE


def test_assist_returns_to_assist_after_success():
    c=cfg();c.result_template='success.png'
    e=FishingEngine(c);e.start('assist',0)
    tick(e,.1,bar=True,marker_x=150)
    tick(e,.2,bar=True,marker_x=150,success=False)
    tick(e,1.,bar=False)
    assert e.stage==Stage.VERIFY
    tick(e,1.1,success=True)
    assert e.stage==Stage.ASSIST and e.verified==1
    assert e.attempts==0


def test_marker_not_found_bar_does_not_start_assist():
    e=FishingEngine(cfg());e.start('assist',0)
    tick(e,.15,bar=True,marker_x=None)
    assert e.stage==Stage.ASSIST
