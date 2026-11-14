"""Portable profile for a fleet of machines running the same game resolution."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED,BadZipFile
from .model import settings_path,Settings,load_settings,save_settings

ALLOWED={'profile.json','marker.png','success.png'}


def export_profile(destination:Path,root:Path|None=None) -> None:
    root=root or settings_path().parent
    settings=load_settings(root/'profile.json')
    if settings.marker_template and Path(settings.marker_template).name!='marker.png':
        raise ValueError('Для экспорта сохраните новый образец маркера')
    with ZipFile(destination,'w',compression=ZIP_DEFLATED) as z:
        for name in sorted(ALLOWED):
            p=root/name
            if p.exists():z.write(p,arcname=name)


def import_profile(source:Path,root:Path|None=None)->Settings:
    root=root or settings_path().parent
    with ZipFile(source,'r') as z:
        names=z.namelist()
        if 'profile.json' not in names or any(name not in ALLOWED for name in names):
            raise ValueError('Неверный формат профиля')
        if any(z.getinfo(n).file_size>2_000_000 for n in names):
            raise ValueError('Слишком большой файл в профиле')
        import json
        config=Settings.from_dict(json.loads(z.read('profile.json').decode('utf-8')))
        # Only local relative images may be referenced by imported configuration.
        for key in ('marker_template','result_template'):
            val=getattr(config,key)
            if val and val not in ('marker.png','success.png'):
                raise ValueError('Некорректный путь к изображению в профиле')
            if val and val not in names:raise ValueError('В профиле нет образца: '+val)
        root.mkdir(parents=True,exist_ok=True)
        for name in names:
            if name!='profile.json':(root/name).write_bytes(z.read(name))
        save_settings(config,root/'profile.json')
        return config
