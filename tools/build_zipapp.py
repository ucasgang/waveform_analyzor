"""Build a single-file Python application. No compiler or third-party package."""
from pathlib import Path
import shutil
import tempfile
import zipapp
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temp:
    stage=Path(temp)
    shutil.copytree(ROOT/'esswave',stage/'esswave',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (stage/'__main__.py').write_text('from esswave.cli import main\nraise SystemExit(main())\n',encoding='utf-8')
    target=ROOT/'ESS_WaveTest.pyz'
    zipapp.create_archive(stage,target=target,interpreter='/usr/bin/env python3',compressed=True)
    print(target)
