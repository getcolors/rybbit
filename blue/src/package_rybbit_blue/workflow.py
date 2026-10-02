"""Rybbit v2 lifecycle, with profile serialization and scoped SSH access."""
from blue import dry_run, progress, tofu
from .reauth import read_login_pars
from blue.lifecycle import preflight
from blue.workflow import advice_add, failed, workflow
from . import access, compute, ssh_config, tools, validate

DEFAULTS = {'provider-compute': validate.default_compute_provider,
            'provider-dns': 'cloudflare', 'provider-backend': 'r2',
            'compute-prevent-destroy': True, 'workdir': '.colors'}


async def start_step(original, env=None):
    async def after(opts, environment, ctx):
        event = ctx['event']
        real = not compute.planning(opts)
        if event == 'build':
            opts = {**opts, 'workdir': str(opts['workdir']) + '/build'}
        if not opts.get("blue/dry-run"):
            access.lock(opts)
        if real and event == 'create':
            opts = ssh_config.preflight(opts)
            if failed(opts):
                return opts
        for step in ((access.resource_step,) if opts.get('blue/dry-run') else (access.resource_step, access.registration_step)):
            opts = await step(opts)
            if failed(opts):
                return opts
        if real and (event in ('delete', 'ssh') or opts.get('compute-require-existing-state')):
            override_ip = opts.get('ip')
            opts = await compute.load(opts, environment)
            if failed(opts) or opts.get('colors-compute/already-destroyed'):
                return opts
            if opts.get('rybbit/registration-destroyed'):
                return {**opts, 'blue/exit': 1, 'blue/err': 'SSH registration is destroyed while compute is still present'}
            if event == 'delete' and override_ip:
                opts = {**opts, 'ip': override_ip}
        return await access.agent_step(opts)
    return await preflight(original, defaults=DEFAULTS, overlay=read_login_pars, env=env,
        validators=[lambda _o,e,_c: validate.env_errors(e), lambda o,_e,_c: validate.state_errors(o),
                    lambda o,_e,c: validate.secret_errors(o) if c['real'] and c['event'] in ('create','delete') else [],
                    lambda o,_e,c: ['compute destruction is protected; set COLORS_PAR_COMPUTE_PREVENT_DESTROY=false to delete'] if c['real'] and c['event']=='delete' and o.get('compute-prevent-destroy') else []], after_validate=after)


def wire_fn(step, run_opts):
    if run_opts.get('blue/event') == 'ssh':
        return {'rybbit/start': (start_step, 'rybbit/ssh'), 'rybbit/ssh': (access.ssh_step,)}.get(step)
    if run_opts.get('blue/event') == 'delete':
        return {
            'rybbit/start': (start_step, 'rybbit/ssh-config'),
            'rybbit/ssh-config': (tools.ansible_local_step, 'rybbit/ansible'),
            'rybbit/ansible': (tools.ansible_step, 'rybbit/dns'),
            'rybbit/dns': (tools.dns_step, 'rybbit/infrastructure'),
            'rybbit/infrastructure': (tools.infrastructure_step, 'rybbit/registration-delete'),
            'rybbit/registration-delete': (access.registration_delete,),
        }.get(step)
    return {
        'rybbit/start': (start_step, 'rybbit/infrastructure'),
        'rybbit/infrastructure': (tools.infrastructure_step, 'rybbit/ssh-config'),
        'rybbit/ssh-config': (tools.ansible_local_step, 'rybbit/dns'),
        'rybbit/dns': (tools.dns_step, 'rybbit/ansible'),
        'rybbit/ansible': (tools.ansible_step, 'rybbit/acceptance'),
        'rybbit/acceptance': (tools.acceptance_step,),
    }.get(step)


def backend_advice(tool):
    return tofu.conventional_backend_advice(dir=lambda o: tools.tool_dir(o, tool), key=lambda o: f"{o.get('profile') or ''}/{tool}.tfstate")


def next_steps(step, successors, opts):
    if failed(opts):
        return []
    return [(successor, opts) for successor in (successors or [])]


side_effecting = ['rybbit/infrastructure', 'rybbit/dns', 'rybbit/ssh-config', 'rybbit/ansible', 'rybbit/acceptance', 'rybbit/registration-delete', 'rybbit/ssh']


def create_workflow():
    wf = workflow(start='rybbit/start', wire_fn=wire_fn, next_fn=next_steps)
    wf = advice_add(wf, 'rybbit/dns', 'before', 'rybbit.workflow/backend', backend_advice(tools.dns_tool))
    return dry_run.advise(progress.advise(wf), side_effecting)


rybbit_workflow = create_workflow()
