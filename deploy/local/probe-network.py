#!/usr/bin/env python3
"""Exercise real ingress and egress deny/allow in the named local kind cluster."""
import json
import os
import subprocess
import time

NAMESPACE = 'ow-network-probe'
IMAGE = 'busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e'

def kubectl(*args, data=None, check=True):
    return subprocess.run(['kubectl', *args], input=json.dumps(data) if data else None,
                          text=True, capture_output=True, check=check, timeout=60)

def apply(obj):
    kubectl('apply', '-f', '-', data=obj)

def policy(name, role, direction, allow):
    spec = {'podSelector': {'matchLabels': {'role': role}}, 'policyTypes': [direction]}
    rules = []
    if allow:
        rules = [{'ports': [{'protocol': 'TCP', 'port': 8080}],
                  'to' if direction == 'Egress' else 'from': [
                      {'podSelector': {'matchLabels': {'role': 'server' if direction == 'Egress' else 'client'}}}]}]
    spec[direction.lower()] = rules
    apply({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
           'metadata': {'name': name, 'namespace': NAMESPACE}, 'spec': spec})

def observe(label, expected):
    # Calico applies policy asynchronously; require three consecutive matching
    # observations, including real marker content for success, within 45 seconds.
    deadline = time.monotonic() + 45
    matched = 0
    while time.monotonic() < deadline:
        r = kubectl('-n', NAMESPACE, 'exec', 'client', '--', 'wget', '-q', '-T', '2', '-O', '-', f'http://{server_ip}:8080', check=False)
        allowed = r.returncode == 0 and r.stdout.strip() == 'openworker-network-proof'
        denied = r.returncode == 1 and ('timed out' in r.stderr or "can't connect" in r.stderr)
        matched = matched + 1 if (allowed if expected else denied) else 0
        if matched == 3:
            print(f'PASS {label}: {"allowed" if expected else "denied"}', flush=True)
            return
        time.sleep(1)
    raise RuntimeError(f'{label} did not converge: rc={r.returncode}, stdout={r.stdout!r}, stderr={r.stderr!r}')

if not os.environ.get('KUBECONFIG'):
    raise SystemExit('Set a dedicated KUBECONFIG; default context is not permitted')
expected_context = os.environ.get('OW_PROBE_CONTEXT', 'kind-openworker-poc')
if not expected_context.startswith('kind-') or kubectl('config', 'current-context').stdout.strip() != expected_context:
    raise SystemExit('Refusing to modify a cluster other than the explicitly named kind test context')
if kubectl('get', 'namespace', NAMESPACE, check=False).returncode == 0:
    raise SystemExit(f'{NAMESPACE} already exists; inspect/remove that previous probe before rerunning')
apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}})
try:
    for role in ['server', 'client']:
        command = ['sh', '-c', 'mkdir -p /tmp/www; echo openworker-network-proof >/tmp/www/index.html; exec httpd -f -p 8080 -h /tmp/www'] if role == 'server' else ['sleep', '3600']
        apply({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': role, 'namespace': NAMESPACE, 'labels': {'role': role}},
               'spec': {'automountServiceAccountToken': False, 'containers': [{'name': role, 'image': IMAGE, 'command': command,
                         'resources': {'requests': {'cpu': '10m', 'memory': '8Mi'}, 'limits': {'cpu': '100m', 'memory': '32Mi'}}}]}})
    # Bounded individual waits let callers inspect progress during image pulls.
    for _ in range(6):
        if kubectl('-n', NAMESPACE, 'wait', '--for=condition=Ready', 'pod', '--all', '--timeout=30s', check=False).returncode == 0:
            break
    else:
        raise RuntimeError('Probe pods did not become ready')
    server_ip = json.loads(kubectl('-n', NAMESPACE, 'get', 'pod', 'server', '-o', 'json').stdout)['status']['podIP']
    observe('baseline', True)
    policy('egress', 'client', 'Egress', False); observe('egress deny', False)
    policy('egress', 'client', 'Egress', True); observe('egress allow', True)
    kubectl('-n', NAMESPACE, 'delete', 'networkpolicy', 'egress')
    policy('ingress', 'server', 'Ingress', False); observe('ingress deny', False)
    policy('ingress', 'server', 'Ingress', True); observe('ingress allow', True)
finally:
    kubectl('delete', 'namespace', NAMESPACE, '--wait=false', check=False)
