#!/usr/bin/env python3
"""Compatibility wrapper; installed packages can use paperretex install-runtimes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from paperretex.runtime_install import install_runtimes

print('Hybrid runtimes installed in '+str(install_runtimes()))
