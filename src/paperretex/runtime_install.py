"""Install the optional BabelDOC and formula OCR runtimes in user cache."""

import os
import shutil
import subprocess
from importlib.resources import as_file, files

from .config import cache_root


RUNTIMES = (('babeldoc', '3.12'), ('ocr', '3.11'))


def install_runtimes():
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv first: https://docs.astral.sh/uv/')
    cache = cache_root()
    for name, version in RUNTIMES:
        folder = cache/(name+'-runtime')
        python = folder/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.is_file():
            subprocess.run([uv, 'venv', '--python', version, str(folder)], check=True)
        resource = files('paperretex').joinpath('data', name+'-requirements.lock')
        with as_file(resource) as requirements:
            subprocess.run([uv, 'pip', 'install', '--python', str(python),
                            '-r', str(requirements)], check=True)
    return cache
