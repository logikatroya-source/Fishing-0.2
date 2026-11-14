from pathlib import Path
from zipfile import ZipFile
import pytest
from fishing.model import Settings,Rect,save_settings,load_settings
from fishing.profiles import export_profile,import_profile


def test_portable_profile_roundtrip(tmp_path):
    original=tmp_path/'original';original.mkdir()
    config=Settings(cast_xy=[600,340],client_size=[1280,720],marker_template='marker.png',bar_roi=Rect(100,200,300,40))
    save_settings(config,original/'profile.json')
    (original/'marker.png').write_bytes(b'placeholder')
    out=tmp_path/'profile.zip';export_profile(out,original)
    target=tmp_path/'imported'
    imported=import_profile(out,target)
    assert imported.cast_xy==[600,340]
    assert (target/'marker.png').read_bytes()==b'placeholder'
    assert load_settings(target/'profile.json').marker_template=='marker.png'


def test_import_rejects_untrusted_filename(tmp_path):
    p=tmp_path/'bad.zip'
    with ZipFile(p,'w') as z:
        z.writestr('profile.json','{}')
        z.writestr('../outside.txt','x')
    with pytest.raises(ValueError):import_profile(p,tmp_path/'target')
    assert not (tmp_path/'outside.txt').exists()


def test_import_requires_image(tmp_path):
    p=tmp_path/'bad.zip'
    with ZipFile(p,'w') as z:
        z.writestr('profile.json','{"marker_template":"marker.png"}')
    with pytest.raises(ValueError):import_profile(p,tmp_path/'target')


def test_import_rejects_oversize(tmp_path):
    p=tmp_path/'big.zip'
    with ZipFile(p,'w') as z:
        z.writestr('profile.json',' '*2_000_100)
    with pytest.raises(ValueError):import_profile(p,tmp_path/'target')
