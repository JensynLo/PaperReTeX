"""Bounded, batched CLI review. At most two invocations per review folder."""
import json
import subprocess
from pathlib import Path
from .llm_cli import command_for, prompt_with_images, save_response

SCHEMA={
 'type':'object','additionalProperties':False,
 'properties':{
  'summary':{'type':'string'},
  'findings':{'type':'array','items':{'type':'object','additionalProperties':False,'properties':{
   'document':{'type':'string'},'page':{'type':'integer'},'issue':{'type':'string'},'suggested_fix':{'type':'string'}},
   'required':['document','page','issue','suggested_fix']}},
  'equations':{'type':'array','items':{'type':'object','additionalProperties':False,'properties':{
   'document':{'type':'string'},'page':{'type':'integer'},'latex':{'type':'string'},'confidence':{'type':'string'}},
   'required':['document','page','latex','confidence']}}},
 'required':['summary','findings','equations']}


def run_round(folder,prompt,images,timeout=180,retry_failed_startup=False,provider='codex',command=None):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    ledger_path=folder/'calls.json'
    ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    if ledger and any(row.get('provider','codex')!=provider for row in ledger):
        raise ValueError('Review folder belongs to another CLI provider.')
    retry_parent=None
    if retry_failed_startup:
        for old in ledger:
            log_path=folder/f"round-{old['round']}.log"
            if old.get('status')=='failed' and not old.get('retry') and log_path.exists() and 'failed to initialize in-process app-server client' in log_path.read_text():
                retry_parent=old;break
        if retry_parent is None:raise ValueError('No proven pre-model startup failure eligible for one retry.')
        number=retry_parent['round']
    else:
        if len(ledger)>=2: raise ValueError('Two-round CLI review budget exhausted for this folder.')
        number=len(ledger)+1
    stem=f'round-{number}'+('-retry-1' if retry_parent is not None else '')
    schema=folder/'schema.json';schema.write_text(json.dumps(SCHEMA,indent=2))
    (folder/f'{stem}-prompt.txt').write_text(prompt,encoding='utf-8')
    result_path=folder/f'{stem}-result.json'
    command=command_for(provider,command or provider,schema,result_path,images,folder)
    prompt=prompt_with_images(provider,prompt,images)
    entry={'round':number,'status':'started','provider':provider,'image_count':len(images),'command':command}
    if retry_parent is None:ledger.append(entry)
    else:retry_parent['retry']=entry
    ledger_path.write_text(json.dumps(ledger,indent=2))
    try:
        proc=subprocess.run(command,input=prompt,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
        (folder/f'{stem}.log').write_text(proc.stdout,encoding='utf-8')
        complete=proc.returncode==0 and save_response(provider,proc.stdout,result_path)
        entry.update(status='completed' if complete else 'failed',returncode=proc.returncode)
    except subprocess.TimeoutExpired as exc:
        value=exc.stdout or b''
        (folder/f'{stem}.log').write_text(value.decode(errors='replace') if isinstance(value,bytes) else value,encoding='utf-8')
        entry.update(status='timeout')
    except OSError as exc:
        (folder/f'{stem}.log').write_text(str(exc),encoding='utf-8')
        entry.update(status='failed',error=str(exc))
    ledger_path.write_text(json.dumps(ledger,indent=2))
    return entry
