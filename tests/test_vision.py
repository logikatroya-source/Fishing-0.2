import cv2
import numpy as np
from fishing.model import Rect
from fishing.vision import green_bar_present,template_match,MotionBite,write_image,read_image,crop


def test_green_bar():
    frame=np.zeros((200,400,3),dtype=np.uint8)
    roi=Rect(30,65,300,32)
    assert not green_bar_present(frame,roi)
    frame[75:89,60:290]=(0,215,20)
    assert green_bar_present(frame,roi)


def test_template_localization():
    rng=np.random.default_rng(23)
    templ=rng.integers(0,255,(15,13,3),dtype=np.uint8)
    frame=np.zeros((120,160,3),dtype=np.uint8)
    frame[65:80,93:106]=templ
    c,center=template_match(frame,Rect(60,40,90,70),templ)
    assert c>.99
    assert center==(99,72)


def test_template_uniform_rejected():
    t=np.ones((10,12,3),dtype=np.uint8)*200
    c,pos=template_match(np.ones((80,80,3),dtype=np.uint8)*200,None,t)
    assert c==0 and pos is None


def test_bite_motion_requires_consecutive():
    rng=np.random.default_rng(3)
    base=rng.integers(10,150,(80,100,3),dtype=np.uint8)
    det=MotionBite()
    roi=Rect(10,10,50,45)
    for i in range(9):
        assert not det.observe(base,roi,threshold=12,consecutive=3)
    strong=np.zeros_like(base)
    assert not det.observe(strong,roi,threshold=12,consecutive=3)
    assert not det.observe(strong,roi,threshold=12,consecutive=3)
    assert det.observe(strong,roi,threshold=12,consecutive=3)


def test_bite_no_motion():
    f=np.zeros((100,100,3),dtype=np.uint8)
    d=MotionBite()
    for i in range(100):assert not d.observe(f,Rect(0,0,50,50),6)


def test_unicode_image_io(tmp_path):
    p=tmp_path/'Шкала рыбка.png'
    f=np.zeros((25,40,3),dtype=np.uint8);f[:,:]=[5,40,150]
    assert write_image(str(p),f)
    r=read_image(str(p));assert np.array_equal(r,f)


def test_rect_clipping():
    r=Rect(85,90,40,30)
    assert r.clamp(100,100)==Rect(85,90,15,10)
    assert Rect(-10,4,5,6).clamp(100,100) is None


def test_no_result_template():
    c,pos=template_match(None,None,None)
    assert c==0 and pos is None
