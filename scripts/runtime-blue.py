"""Pure v2 behavior parity: no provider calls, state or operator files."""
import json
import sys
import yaml
from package_rybbit_blue import compute, access, workflow
with open(sys.argv[1]) as handle:
    opts = {**yaml.safe_load(handle), 'workdir': '/tmp/rybbit-runtime-parity', 'blue/event': 'build'}

def graph(event):
    result, step = [], 'rybbit/start'
    while step:
        result.append(step)
        edge = workflow.wire_fn(step, {**opts, 'blue/event': event})
        step = edge[1] if edge and len(edge) > 1 else None
    return result

def source_errors(suffix, value, missing=False):
    state = {**opts}
    for prefix in ('compute', 'rybbit', opts['provider-compute']):
        state.pop(prefix+'-'+suffix, None)
    if not missing:
        state['compute-'+suffix] = value
    return compute.errors(state)

req = compute.request(opts)
output = {
    'legacyReferences': [compute.errors({**opts, key: None}) for key in ('digitalocean-ssh-keys','vultr-ssh-keys','ssh-key-id')],
    'failures': [compute.failure(opts, result)['blue/err'] for result in [{}, {'error': None}, {'error': {'message': None}}, {'error': {'message': 'refused', 'stderr': 'provider detail'}}]],
    'sourceErrors': {suffix: {name: source_errors(suffix, value, name == 'missing') for name, value in [('missing', None), ('false', False), ('map', {}), ('null', None)]} for suffix in ('ssh-sources', 'http-sources')},
    'errors': {'missingV2': compute.errors({**opts, 'compute-api-version': None}), 'legacyKey': compute.errors({**opts, 'ssh-private-key-path': '/tmp/operator-key'})},
    'requirements': compute.requirements(opts),
    'closedHttp': compute.requirements({**opts, 'compute-http-sources': []}),
    'sourcePrecedence': compute.requirements({**opts, 'compute-ssh-sources': ['192.0.2.1/32'], 'rybbit-ssh-sources': ['192.0.2.2/32'], opts['provider-compute']+'-ssh-sources': ['192.0.2.3/32']})['ingress'][0]['sources'],
    'identity': {'node': req['node_id'], 'state': req['state_filename'], 'passphrase': compute.ssh_request(opts)['passphrase_env'], 'registration': compute.registration(opts)},
    'ssh': access.ssh_args({**opts, 'ip': '203.0.113.1', 'user': 'ubuntu', 'ssh-private-key-path': '/tmp/public.pub', 'rybbit/agent-socket': '/tmp/agent.sock'}),
    'graphs': {event: graph(event) for event in ('create', 'delete', 'ssh')},
}
print(json.dumps(output, sort_keys=True, separators=(',', ':')))
