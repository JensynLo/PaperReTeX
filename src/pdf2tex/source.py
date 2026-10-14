"""Recover version-matched arXiv LaTeX, retaining semantic source structure."""
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path,PurePosixPath
import fitz

ARXIV_ID=re.compile(r'arXiv\s*:\s*(\d{4}\.\d{4,5}v\d+)',re.I)


def detect_id(pdf):
    with fitz.open(pdf) as doc:
        if doc.needs_pass: raise ValueError('Encrypted PDF.')
        for page in list(doc)[:2]:
            match=ARXIV_ID.search(page.get_text())
            if match:return match.group(1)
    raise ValueError('No versioned arXiv identifier found in PDF. Use --mode reflow or --content-list for reconstruction.')


def safe_extract(data,dest):
    if data.startswith(b'%PDF'):raise ValueError('arXiv provides a PDF, not LaTeX source, for this submission.')
    try:
        archive=tarfile.open(fileobj=io.BytesIO(data),mode='r:*')
    except tarfile.ReadError:
        raw=gzip.decompress(data) if data.startswith(b'\x1f\x8b') else data
        if b'\\documentclass' not in raw and b'\\documentstyle' not in raw:raise ValueError('Response is not a LaTeX source archive.')
        (dest/'main.tex').write_bytes(raw);return
    with archive:
        members=archive.getmembers()
        if len(members)>10000 or sum(m.size for m in members)>1024**3:raise ValueError('Source archive exceeds extraction limits.')
        for m in members:
            rel=PurePosixPath(m.name)
            if rel.is_absolute() or '..' in rel.parts or m.issym() or m.islnk():raise ValueError('Unsafe path or link in source archive: '+m.name)
            target=dest.joinpath(*rel.parts)
            if m.isdir():target.mkdir(parents=True,exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(m) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
            else:raise ValueError('Unsupported archive member: '+m.name)


def source_structure(dest):
    counts={x:0 for x in ['section','subsection','equation','align','figure','table','citations','labels']}
    for p in dest.rglob('*.tex'):
        text=p.read_text(errors='replace')
        text=re.sub(r'(?<!\\)%[^\n]*','',text)
        for name in ['section','subsection']:
            counts[name]+=len(re.findall(r'\\'+name+r'\*?\s*[\[{]',text))
        for name in ['equation','align','figure','table']:
            counts[name]+=len(re.findall(r'\\begin\s*\{'+name+r'\*?\}',text))
        counts['citations']+=len(re.findall(r'\\cite\w*\*?\s*[\[{]',text))
        counts['labels']+=len(re.findall(r'\\label\s*\{',text))
    return counts


def entrypoints(dest):
    metadata={}
    if (dest/'00README.json').exists():metadata=json.loads((dest/'00README.json').read_text())
    entries=[x['filename'] for x in metadata.get('sources',[]) if x.get('usage')=='toplevel']
    if not entries:
        for p in sorted(dest.rglob('*.tex')):
            if re.search(r'(?m)^\s*\\document(?:class|style)',p.read_text(errors='replace')):entries.append(str(p.relative_to(dest)))
    if not entries:raise ValueError('No compilable LaTeX entrypoint in source archive.')
    for name in entries:
        if not (dest/name).is_file() or dest.resolve() not in (dest/name).resolve().parents:raise ValueError('Invalid source entrypoint.')
    entries.sort(key=lambda n:('supp' in n.lower(),n!='main.tex',n))
    engine=metadata.get('process',{}).get('compiler','pdflatex')
    if engine not in ('pdflatex','xelatex','lualatex'):engine='pdflatex'
    return entries,engine,metadata


def recover(pdf,dest,archive_path=None):
    identifier=detect_id(pdf);url='https://arxiv.org/src/'+identifier
    headers={'User-Agent':'PaperReTeX/0.1 (version-matched source recovery)'}
    if archive_path:
        data=Path(archive_path).read_bytes()
        # A caller-supplied archive has its own provenance; an HTTP ETag is opaque,
        # even when its string resembles a content hash. Never treat it as SHA256.
    else:
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=120) as response:
            parts=[];size=0
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>256*1024*1024:raise ValueError('Source download exceeds 256 MiB.')
                parts.append(chunk)
            data=b''.join(parts)
    safe_extract(data,dest)
    entries,engine,metadata=entrypoints(dest)
    return {'backend':'arxiv_source','quality':'original_semantic_source','arxiv_id':identifier,'source_url':url,
            'archive_sha256':hashlib.sha256(data).hexdigest(),'archive_provenance':'local_archive' if archive_path else 'downloaded_versioned_url','entrypoints':entries,'engine':engine,
            'counts':source_structure(dest),'review_blocks':[], 'texlive_requested':metadata.get('texlive_version'),
            'limitations':['This route recovers published source; it is not OCR or a generic PDF decompiler.',
                           'TeX/package version differences can affect line breaks and page layout.']}


def compile_source(dest,report,timeout=180):
    engine=report['engine']
    if not shutil.which(engine):return {'status':'unavailable','message':'Missing compiler: '+engine}
    env=dict(os.environ,openout_any='p',openin_any='p')
    logs=[];pdfs=[]
    for entry in report['entrypoints']:
        work=dest/Path(entry).parent;stem=Path(entry).stem
        command=[engine,'-no-shell-escape','-interaction=nonstopmode','-halt-on-error',Path(entry).name]
        converged=False
        def aux_state():
            return {suffix:hashlib.sha256((work/(stem+suffix)).read_bytes()).hexdigest() for suffix in ['.aux','.brf','.out','.toc'] if (work/(stem+suffix)).exists()}
        for run in range(8):
            before=aux_state()
            try:result=subprocess.run(command,cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
            except subprocess.TimeoutExpired:return {'status':'failed','message':'Source compilation timed out.'}
            logs.append(f'ENTRY {entry} PASS {run+1}\n'+result.stdout)
            (dest/'source-compile.log').write_text('\n'.join(logs),encoding='utf-8')
            if result.returncode:return {'status':'failed','entrypoint':entry,'log':'source-compile.log'}
            aux=work/(stem+'.aux')
            if run==0 and not (work/(stem+'.bbl')).exists() and aux.exists() and r'\bibdata' in aux.read_text(errors='replace'):
                result=subprocess.run(['bibtex',stem],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
                logs.append(result.stdout)
                if result.returncode:return {'status':'failed','message':'BibTeX failed.','log':'source-compile.log'}
            if run>=1 and before==aux_state():
                converged=True;break
        if not converged:
            return {'status':'failed','message':'Cross-references did not converge after 8 LaTeX passes.','log':'source-compile.log'}
        pdfs.append(work/(stem+'.pdf'))
    # Preserve every original entrypoint PDF and provide a convenient combined artifact.
    if len(pdfs)==1:
        if pdfs[0] != dest/'main.pdf':shutil.copyfile(pdfs[0],dest/'main.pdf')
    else:
        with fitz.open() as merged:
            for pdf in pdfs:
                with fitz.open(pdf) as part:merged.insert_pdf(part)
            merged.save(dest/'combined.pdf')
        # main.pdf may itself be an entrypoint; do not overwrite it.
    return {'status':'passed','engine':engine,'pdf':'combined.pdf' if len(pdfs)>1 else 'main.pdf','log':'source-compile.log'}
