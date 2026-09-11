"""Account-scoped quota reads and validated model recommendations. No credential copying."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import time

from .control import GateError


def codex_account(config, *, timeout=12):
    home = config.get('shadowHomePath') or config.get('homePath') or str(Path.home()/'.codex')
    home = str(Path(home).expanduser().resolve())
    binary = config.get('binaryPath') or 'codex'
    args = [binary, *shlex.split(config.get('launchArgs') or ''), 'app-server']
    process = subprocess.Popen(args, env={**os.environ, 'CODEX_HOME': home},
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b''
    deadline = time.monotonic() + timeout
    def send(method, identifier=None, params=None):
        message = {'method': method}
        if identifier is not None: message['id'] = identifier
        if params is not None: message['params'] = params
        process.stdin.write((json.dumps(message)+'\n').encode()); process.stdin.flush()
    results = {}
    try:
        send('initialize', 1, {'clientInfo': {'name': 'phonebridge_quota_reader', 'version': '1'}})
        while time.monotonic() < deadline:
            if not selector.select(max(.01, deadline-time.monotonic())): break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk: break
            buffer += chunk
            if len(buffer) > 1024*1024: raise GateError('account_response_too_large')
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try: event = json.loads(line)
                except ValueError: continue
                if event.get('id') == 1:
                    if 'error' in event: raise GateError('account_protocol_failed')
                    send('initialized')
                    send('account/read', 2, {'refreshToken': False})
                    send('account/rateLimits/read', 3)
                elif event.get('id') in (2, 3):
                    if 'error' in event: raise GateError('account_quota_unavailable')
                    results[event['id']] = event['result']
            if len(results) == 2:
                account = results[2].get('account') or {}
                identity = account.get('email')
                limits = results[3]
                buckets = limits.get('rateLimitsByLimitId') or {'codex': limits.get('rateLimits') or {}}
                windows = []
                for bucket_id, bucket in buckets.items():
                    for name in ('primary', 'secondary'):
                        w = bucket.get(name)
                        if isinstance(w, dict) and type(w.get('usedPercent')) in (int, float):
                            reset = w.get('resetsAt')
                            windows.append({'id': bucket_id+':'+name, 'bucket': bucket_id,
                                'usedPercent': w['usedPercent'], 'windowDurationMins': w.get('windowDurationMins'),
                                'resetsAt': datetime.fromtimestamp(reset, timezone.utc).isoformat() if type(reset) in (int,float) else None})
                return {'checkedAt': datetime.now(timezone.utc).isoformat(), 'windows': windows,
                        'account_group': hashlib.sha256(identity.casefold().encode()).hexdigest() if identity else None,
                        'source': 'codex-account-api'}
        raise GateError('account_quota_timeout')
    finally:
        selector.close()
        process.stdin.close()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        process.stdout.close()


def remaining_capacity(limits, now=None):
    now = time.time() if now is None else now
    if not isinstance(limits, dict) or limits.get('error'): return None
    try:
        checked = datetime.fromisoformat(limits['checkedAt'].replace('Z', '+00:00')).timestamp()
        if now-checked > 300 or checked > now+30: return None
    except (KeyError, TypeError, ValueError): return None
    remaining = []
    for window in limits.get('windows', []):
        used = window.get('usedPercent')
        if type(used) not in (int, float) or not 0 <= used <= 100: continue
        reset = window.get('resetsAt')
        if reset:
            try:
                if datetime.fromisoformat(reset.replace('Z', '+00:00')).timestamp() <= now:
                    continue  # A past reset isn't proof of a fresh quota.
            except (TypeError, ValueError): continue
        remaining.append(100-used)
    return min(remaining) if remaining else None


class ProviderAdvisor:
    def __init__(self, client, account_reader=codex_account):
        self.client, self.account_reader = client, account_reader
        self.catalog = []
        self.last_refresh = 0

    def refresh(self, force=False):
        if self.catalog and not force and time.monotonic()-self.last_refresh < 60: return self.catalog
        try: self.client.rpc('server.refreshProviders', {'refreshModels': False})
        except GateError: pass
        config = self.client.rpc('server.getConfig')
        instances = config.get('settings', {}).get('providerInstances', {})
        providers = [p for p in config.get('providers', []) if p.get('enabled') and p.get('installed')
                     and p.get('status') in ('ready', 'warning') and p.get('auth', {}).get('status') == 'authenticated']
        reads = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            for p in providers:
                if p.get('driver') == 'codex':
                    reads[p['instanceId']] = pool.submit(self.account_reader, instances.get(p['instanceId'], {}).get('config', {}))
            catalog = []
            for p in providers:
                limits = p.get('usageLimits')
                if p['instanceId'] in reads:
                    try: limits = reads[p['instanceId']].result()
                    except Exception: limits = {'source': 'unavailable', 'windows': []}
                elif isinstance(limits, dict):
                    limits = {**limits, 'source': 't3-provider-snapshot'}
                catalog.append({'instanceId': p['instanceId'], 'driver': p['driver'],
                    'name': p.get('displayName', p['instanceId']), 'models': p.get('models', []),
                    'limits': limits, 'remaining_percent': remaining_capacity(limits)})
        self.catalog, self.last_refresh = catalog, time.monotonic()
        return catalog

    def coordinator_selection(self):
        providers = sorted(self.refresh(), key=lambda p: p['remaining_percent'] if p['remaining_percent'] is not None else -1, reverse=True)
        for preferred in ('gpt-5.6-sol', 'gpt-5.6-terra', 'claude-sonnet-5'):
            for provider in providers:
                if provider['remaining_percent'] == 0: continue
                model = next((m for m in provider['models'] if m['slug'] == preferred), None)
                if model:
                    options = []
                    for d in (model.get('capabilities') or {}).get('optionDescriptors', []):
                        if d['id'] in ('reasoningEffort', 'effort') and any(x['id'] == 'low' for x in d.get('options', [])):
                            options.append({'id': d['id'], 'value': 'low'})
                    return {'instanceId': provider['instanceId'], 'model': preferred, 'options': options}
        for p in providers:
            if p['remaining_percent'] != 0 and p['models']:
                return {'instanceId': p['instanceId'], 'model': p['models'][0]['slug'], 'options': []}
        raise GateError('no_available_provider_account')

    def options(self):
        return [{'instanceId': p['instanceId'], 'provider': p['driver'], 'account': p['name'],
                 'remaining_percent': p['remaining_percent'], 'limits': p['limits'],
                 'models': [{'model': m['slug'], 'name': m['name'],
                             'options': (m.get('capabilities') or {}).get('optionDescriptors', [])} for m in p['models']]}
                for p in self.refresh()]

    def validate(self, selection):
        if not isinstance(selection, dict): raise GateError('model_recommendation_required')
        provider = next((p for p in self.refresh() if p['instanceId'] == selection.get('instanceId')), None)
        if provider is None: raise GateError('provider_unavailable')
        model = next((m for m in provider['models'] if m['slug'] == selection.get('model')), None)
        if model is None: raise GateError('model_unavailable')
        descriptors = {d['id']:d for d in (model.get('capabilities') or {}).get('optionDescriptors', [])}
        options = selection.get('options', [])
        if not isinstance(options,list) or len({o.get('id') for o in options if isinstance(o,dict)}) != len(options):
            raise GateError('invalid_model_options')
        for option in options:
            d = descriptors.get(option.get('id'))
            if not d: raise GateError('unsupported_model_option')
            if d['type'] == 'select' and option.get('value') not in {x['id'] for x in d.get('options', [])}:
                raise GateError('unsupported_reasoning_effort')
            if d['type'] == 'boolean' and type(option.get('value')) is not bool:
                raise GateError('invalid_model_option_type')
        if provider['remaining_percent'] == 0: raise GateError('selected_account_exhausted')
        return provider, model

    def describe(self, selection):
        provider, model = self.validate(selection)
        effort = next((o['value'] for o in selection.get('options', []) if o['id'] in ('reasoningEffort','effort')), None)
        if effort is None:
            for d in (model.get('capabilities')or{}).get('optionDescriptors',[]):
                if d['id'] in ('reasoningEffort','effort'):
                    effort = d.get('currentValue') or next((o['id'] for o in d.get('options',[]) if o.get('isDefault')), None)
        capacity = provider['remaining_percent']
        quota = 'Die verbleibenden Limits sind derzeit nicht verlässlich verfügbar.' if capacity is None else f'Im knappsten gemeldeten Limitfenster sind {capacity:g} Prozent frei.'
        return f"{provider['name']}, {model['name']}, Reasoning {effort or 'Standard'}. {quota}"

    def normalize(self, selection):
        _, model = self.validate(selection)
        result = {**selection, 'options': [dict(o) for o in selection.get('options', [])]}
        for descriptor in (model.get('capabilities') or {}).get('optionDescriptors', []):
            if descriptor['id'] not in ('reasoningEffort', 'effort', 'serviceTier'): continue
            if any(o['id'] == descriptor['id'] for o in result['options']): continue
            value = next((o['id'] for o in descriptor.get('options', []) if o.get('isDefault')), descriptor.get('currentValue'))
            if descriptor['id'] == 'serviceTier' and any(o['id'] == 'default' for o in descriptor.get('options', [])): value = 'default'
            if value is not None: result['options'].append({'id': descriptor['id'], 'value': value})
        self.validate(result)
        return result
