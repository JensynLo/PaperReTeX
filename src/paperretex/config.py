"""Project-wide paths independent of a developer checkout."""

import os
from pathlib import Path


def cache_root():
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'paperretex'
