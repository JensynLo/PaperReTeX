"""Local model pipeline. Only the input PDF is passed to the recognizer."""
import os
import shutil
import subprocess
from pathlib import Path
from .importer import import_content
from .config import cache_root


def recognize(pdf, out, pages=None, executable=None, timeout=3600):
    local = cache_root()/'ocr-runtime'/('Scripts/mineru.exe' if os.name=='nt' else 'bin/mineru')
    command = executable or shutil.which('mineru') or (str(local) if local.exists() else None)
    if not command:
        raise ValueError('Local OCR is not installed. Run paperretex install-runtimes, or explicitly use --mode reflow for a native draft.')
    if pages and pages != list(range(pages[0],pages[-1]+1)):
        raise ValueError('The OCR backend requires a contiguous --pages range.')
    raw = out/'recognition'; raw.mkdir()
    args = [command,'-p',str(pdf),'-o',str(raw),'-b','pipeline','-m','auto','-l','en']
    if pages: args += ['-s',str(pages[0]),'-e',str(pages[-1])]
    env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK='1', TOKENIZERS_PARALLELISM='false')
    env.setdefault('HF_HUB_DISABLE_XET','1')
    env.setdefault('MINERU_MODEL_SOURCE','modelscope')
    # Retain progress on stderr and diagnostics on disk; model output is never executed.
    with (out/'recognition.log').open('w') as log:
        proc = subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=timeout)
    if proc.returncode:
        tail=(out/'recognition.log').read_text(errors='replace')[-2500:]
        raise RuntimeError('Local OCR failed:\n'+tail)
    files=list(raw.rglob('*_content_list.json'))
    if len(files)!=1: raise RuntimeError('Expected one MinerU Content List V1 output; found '+str(len(files)))
    blocks, report=import_content(files[0],out)
    if pages:
        for b in blocks:b['page']+=pages[0]
    report.update(backend='mineru_pipeline',input_policy='pdf_only',source_lookup=False,recognizer_command=args)
    return blocks,report
