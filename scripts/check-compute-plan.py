#!/usr/bin/env python3
"""Check the v2 singleton's independent compute and SSH registration ownership."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
profile = root.name
assert (root / '.rybbit.lock').is_file(), 'build must serialize its profile root'
provider = sys.argv[2]
node = root / 'rybbit-compute'
registration = root / 'registration-machine-access'
stages = [(node, f'{profile}/rybbit-node-0.tfstate')]
needs_registration = provider in {'aws', 'digitalocean', 'hcloud', 'vultr'}
assert registration.is_dir() == needs_registration, 'separate registration follows provider contract'
if needs_registration:
    stages.append((registration, f'{profile}/rybbit-ssh-registration.tfstate'))
for stage, key in stages:
    documents = [json.loads(path.read_text()) for path in stage.glob('*.tf.json')]
    backend = json.loads((stage / 'backend.tf.json').read_text())['terraform']['backend']['s3']
    assert backend['key'] == key
    assert not {'access_key', 'secret_key', 'token'} & backend.keys()
    assert documents
    assert all('provisioner' not in json.dumps(document) for document in documents)
    assert all('PRIVATE KEY' not in json.dumps(document) for document in documents)
resources = json.loads((node / 'compute.tf.json').read_text())['resource']
assert not {'tls_private_key', 'local_sensitive_file', 'aws_key_pair', 'digitalocean_ssh_key', 'hcloud_ssh_key', 'vultr_ssh_key'} & resources.keys()
assert not (root / 'compute').exists(), 'v1 shared/node tree must not be rendered'
assert not (root / 'ssh/machine-access/resource.json').exists(), 'build must not create SSH authority'
assert not (root / 'rybbit-infrastructure').exists(), 'old compute stage must not be applied'
assert (root / 'rybbit-dns/backend.tf.json').is_file(), 'application DNS remains separate'
