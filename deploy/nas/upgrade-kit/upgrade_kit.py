#!/usr/bin/env python3
"""Trusted operator CLI. No activate/restart/pull/prune or model/browser calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'manifest.json'
RECIPES = HERE / 'recipes.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, timeout=30):
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    if result.returncode:
        # Raw Docker errors can contain configuration values: keep output private.
        raise RuntimeError(f'{argv[0]} command failed (exit {result.returncode})')
    return result.stdout


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def load():
    manifest = json.loads(MANIFEST.read_text())
    if sha(RECIPES) != manifest['recipes_sha256']:
        raise RuntimeError('Recipe checksum mismatch; do not use this kit.')
    return manifest, json.loads(RECIPES.read_text())


def status(manifest):
    checks = []
    warnings = []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})

    gateway = json.loads(run(['docker', 'inspect', manifest['gateway']]))[0]
    check('gateway_running_healthy', gateway['State']['Running'] and gateway['State'].get('Health', {}).get('Status') == 'healthy')
    check('gateway_image_pinned', gateway['Image'] == manifest['image_id'])
    mounts = {m['Destination']: m for m in gateway['Mounts'] if m['Destination'].startswith('/app/dist/')}
    check('exact_overlay_set', set(mounts) == {o['target'] for o in manifest['overlays']})
    for overlay in manifest['overlays']:
        name, mount = overlay['module'], mounts.get(overlay['target'], {})
        check(name + ':readonly_source_mount', mount.get('Source') == overlay['source'] and mount.get('RW') is False)
        source = Path(overlay['source'])
        check(name + ':source_checksum', source.is_file() and sha(source) == overlay['sha256'])
    # Hash bytes at the actual mounted targets; importing modules could provision tools.
    script = '''const fs=require('fs'),crypto=require('crypto');
const paths=JSON.parse(process.argv[1]);
console.log(JSON.stringify(Object.fromEntries(paths.map(p=>[p,crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')]))));'''
    mounted = json.loads(run(['docker', 'exec', manifest['gateway'], 'node', '-e', script, json.dumps([o['target'] for o in manifest['overlays']])]))
    for overlay in manifest['overlays']:
        check(overlay['module'] + ':mounted_checksum', mounted.get(overlay['target']) == overlay['sha256'])
    for dep in manifest['dependencies']:
        path = Path(dep['path'])
        check('dependency:' + path.name, path.is_file() and sha(path) == dep['sha256'])
    for baseline in manifest['baselines']:
        path = Path(baseline['path'])
        check('baseline_exists:' + path.name, path.is_file())
        if path.is_file() and sha(path) != baseline['sha256']:
            warnings.append(path.name + ': changed since reviewed baseline; review before upgrade')
    config_path = next(b['path'] for b in manifest['baselines'] if Path(b['path']).name == 'openclaw.json')
    config = json.loads(Path(config_path).read_text())
    policy = manifest['policy']
    for key, expected in policy['tools'].items():
        check('tool_policy:' + key, config.get('tools', {}).get(key) == expected)
    defaults = config.get('agents', {}).get('defaults', {}).get('sandbox', {})
    for key, expected in policy['sandbox_defaults'].items():
        check('sandbox_default:' + key, defaults.get(key) == expected)
    check('shared_bind_policy', defaults.get('docker', {}).get('binds') == policy['shared_binds'])
    entries = config.get('agents', {}).get('entries', {})
    for aid, expected in policy['agents'].items():
        agent = entries.get(aid, {})
        sandbox = agent.get('sandbox', {})
        check('agent:' + aid + ':workspace', agent.get('workspace') == expected['workspace'])
        check('agent:' + aid + ':sandbox', all(sandbox.get(k) == v for k, v in expected['sandbox'].items()))
        for kind in ['docker', 'browser']:
            check('agent:' + aid + ':' + kind + '_binds', sandbox.get(kind, {}).get('binds') == expected[kind + '_binds'])
        # Do not follow untrusted docs symlinks or inspect source contents.
        root_bind = next(b for b in expected['docker_binds'] if b.rsplit(':', 2)[1] == expected['workspace'])
        docs = Path(root_bind.rsplit(':', 2)[0]) / 'docs'
        check('agent:' + aid + ':real_docs_directory', not docs.is_symlink() and docs.is_dir())
    extra = sorted(set(entries) - set(policy['agents']))
    if extra:
        warnings.append('New agent configuration needs baseline review: ' + ', '.join(extra))
    browser_ids = run(['docker', 'ps', '-aq', '--filter', 'label=openclaw.sandboxBrowser=1']).decode().split()
    browser = json.loads(run(['docker', 'inspect', *browser_ids])) if browser_ids else []
    # Browser state is informational; existing timer/path state is not changed.
    return {
        'ok': all(c['passed'] for c in checks),
        'baseline_ready_for_upgrade_planning': all(c['passed'] for c in checks) and not warnings,
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'gateway_id': gateway['Id'], 'started_at': gateway['State']['StartedAt'],
        'checks': checks, 'warnings': warnings,
        'browsers': [{'name': b['Name'].lstrip('/'), 'running': b['State']['Running']} for b in browser],
        'model_calls': 0, 'browser_calls': 0, 'production_writes': 0,
    }


def build(manifest, recipes, stock_dir, output):
    stock_dir, output = Path(stock_dir).resolve(), Path(output).resolve()
    # Never write inside a runtime source, the kit, or the stock input.
    protected = [HERE, stock_dir] + [Path(o['source']).parent for o in manifest['overlays']]
    if any(output == p or p in output.parents for p in protected):
        raise RuntimeError('Output must be a new separate candidate directory.')
    if output.exists():
        raise RuntimeError('Output already exists; candidates are never overwritten.')
    result = {}
    files = {}
    for overlay in manifest['overlays']:
        name = overlay['module']
        source = (stock_dir / name).read_bytes()
        if digest(source) != overlay['stock_sha256']:
            raise RuntimeError(name + ': unreviewed stock version; adapt and review first')
        text = source.decode('utf-8')
        for number, hunk in enumerate(recipes[name], 1):
            if text.count(hunk['before']) != 1:
                raise RuntimeError(f'{name}: anchor {number} is not unique')
            text = text.replace(hunk['before'], hunk['after'], 1)
        patched = text.encode('utf-8')
        if digest(patched) != overlay['sha256']:
            raise RuntimeError(name + ': reconstructed hash mismatch')
        files[name] = patched
        result[name] = {'stock_sha256': digest(source), 'patched_sha256': digest(patched), 'anchors': len(recipes[name])}
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.upgrade-kit-', dir=output.parent))
    try:
        for name, content in files.items():
            (temp / name).write_bytes(content)
        report = {'ok': True, 'reproduced_modules': len(files), 'from_pristine_stock': True, 'modules': result, 'activated': False}
        (temp / 'BUILD.json').write_text(json.dumps(report, indent=2) + '\n')
        # Atomic publication without overwriting a race-created target.
        output.mkdir()
        try:
            for file in temp.iterdir():
                file.rename(output / file.name)
        except Exception:
            shutil.rmtree(output)
            raise
    finally:
        shutil.rmtree(temp)
    return report


def image_inventory(manifest, image, output=None):
    metadata = json.loads(run(['docker', 'image', 'inspect', image]))[0]
    pinned = metadata['Id']
    prefixes = ['context-', 'plugin-service-', 'workspace-mounts-', 'tool-policy-diagnostic-', 'core-coding-tools-', 'openclaw-tools-']
    script = '''const fs=require('fs'),crypto=require('crypto'),path=require('path');
const prefixes=JSON.parse(process.argv[1]),out={};
for(const prefix of prefixes){const names=fs.readdirSync('/app/dist').filter(n=>n.startsWith(prefix)&&n.endsWith('.mjs'));
out[prefix]=names.map(name=>{const bytes=fs.readFileSync(path.join('/app/dist',name));return {name,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),...(process.argv[2]==='extract'?{content:bytes.toString('base64')}:{})};});}
console.log(JSON.stringify(out));'''
    mode = 'extract' if output else 'inventory'
    modules = json.loads(run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '64', '--entrypoint', 'node', pinned, '-e', script, json.dumps(prefixes), mode], timeout=60))
    ready = pinned == manifest['image_id'] and all(any(x['name'] == o['module'] and x['sha256'] == o['stock_sha256'] for x in modules[o['role_prefix']]) for o in manifest['overlays'])
    if output:
        # Unreviewed versions may be inventoried, never staged as compatible stock.
        if not ready:
            raise RuntimeError('Unreviewed image: inventory only; no stock extraction accepted')
        target = Path(output).resolve()
        if target.exists():
            raise RuntimeError('Stock output already exists; will not overwrite')
        if HERE == target or HERE in target.parents:
            raise RuntimeError('Stock output must be separate from the installed kit')
        import base64
        payloads = {o['module']: next(x for x in modules[o['role_prefix']] if x['name'] == o['module']) for o in manifest['overlays']}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        for name, data in payloads.items():
            value = base64.b64decode(data['content'], validate=True)
            if digest(value) != data['sha256']:
                raise RuntimeError('Image extraction checksum mismatch')
            (target / name).write_bytes(value)
    clean = {key: [{k: v for k, v in x.items() if k != 'content'} for x in values] for key, values in modules.items()}
    return {'image_id': pinned, 'reviewed_image': ready, 'requires_adaptation_review': not ready, 'modules': clean, 'stock_extracted': bool(output), 'activated': False, 'production_mounts': 0, 'network': 'none'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Read-only production metadata, checksums and reviewed policy')
    inventory = sub.add_parser('inspect-image', help='Inventory an already installed image without production mounts')
    inventory.add_argument('--image', required=True)
    extract = sub.add_parser('extract-stock', help='Extract reviewed pristine modules; no production data mount')
    extract.add_argument('--image', required=True)
    extract.add_argument('--output', required=True)
    rebuild = sub.add_parser('rebuild', help='Reproduce all six overlays into a new candidate directory; never activate')
    rebuild.add_argument('--stock-dir', required=True)
    rebuild.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        manifest, recipes = load()
        if args.command == 'status':
            result = status(manifest)
        elif args.command == 'rebuild':
            result = build(manifest, recipes, args.stock_dir, args.output)
        else:
            result = image_inventory(manifest, args.image, getattr(args, 'output', None))
        emit(result)
        return 0 if result.get('ok', True) else 1
    except Exception as error:
        emit({'ok': False, 'error': str(error), 'activated': False})
        return 1


if __name__ == '__main__':
    sys.exit(main())
