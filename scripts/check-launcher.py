#!/usr/bin/env python3
"""Run the immutable launcher outside the checkout without provider access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='rybbit-launcher-') as temporary:
    root = Path(temporary)
    command = root/'bin'/'rybbit'
    command.parent.mkdir()
    shutil.copyfile(repo/'skills/rybbit/rybbit', command)
    command.chmod(0o700)
    marker = root/'unexpected-provider'
    for name in ('hcloud','cf','ssh','scp','vaultcontext'):
        stub = command.parent/name
        stub.write_text('#!/bin/sh\ntouch '+str(marker)+'\nexit 99\n')
        stub.chmod(0o700)
    env = {k:v for k,v in os.environ.items() if not k.startswith(('COLORS_PAR_','HCLOUD_'))
           and k not in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','UV_PROJECT','UV_PROJECT_ENVIRONMENT')}
    env['PATH'] = str(command.parent)+os.pathsep+env.get('PATH','')
    def call(*args):
        return subprocess.run([str(command),*args],cwd=root,env=env,capture_output=True,text=True,timeout=300)
    for args in ((),('--help',)):
        r=call(*args)
        assert r.returncode==0 and 'prepare' in r.stdout and 'restore' in r.stdout
    r=call('create','--json')
    assert r.returncode==2 and json.loads(r.stdout)['ok'] is False
    source=(repo/'colors.yml').read_text()
    import re
    source=re.sub(r'^profile:.*$', 'profile: synthetic-launcher',source,flags=re.M)
    source=re.sub(r'^compute-require-existing-state:.*$', 'compute-require-existing-state: true',source,flags=re.M)
    (root/'colors.yml').write_text(source)
    r=call('validate','--json')
    assert r.returncode==0 and json.loads(r.stdout)['result']['profile']=='synthetic-launcher'
    r=call('status','--json')
    assert r.returncode==1 and json.loads(r.stdout)['ok'] is False
    assert not marker.exists(), 'Launcher attempted a provider call'
    assert not (root/'.colors.sqlite').exists(), 'Launcher created state'
print('Copied launcher: help, usage, config and missing-state checks passed; no provider calls.')
