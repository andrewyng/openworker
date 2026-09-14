"""Exercise release integration with real local Git repositories, no GitHub writes."""
import importlib.util
import json
import subprocess
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'packaging' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = load('sync_upstream_release')
policy = load('check_fork_policy')


@pytest.mark.parametrize('release', [
    {'tag_name': 'v1.2.3', 'draft': True},
    {'tag_name': 'v1.2.3', 'prerelease': True},
    {'tag_name': 'v1.2.3-rc.1'}, {'tag_name': '--upload-pack=evil'},
])
def test_reject_nonstable_release(release):
    with pytest.raises(ValueError):
        sync.release_tag(release)


@pytest.fixture
def integration(tmp_path, monkeypatch):
    def git(path, *args):
        return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.email', 'test@example.com')
    git(source, 'config', 'user.name', 'Test')
    (source / 'shared').write_text('base\n')
    (source / '.github').mkdir()
    (source / '.github/keep').write_text('keep')
    (source / 'packaging').mkdir()
    (source / 'packaging/check_fork_policy.py').write_text('print("test fixture policy")\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'base')
    git(source, 'checkout', '-b', sync.BASE)
    (source / 'nexus').write_text('preserved\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'Nexus')
    fork = tmp_path / 'fork.git'
    subprocess.run(['git', 'clone', '--bare', str(source), str(fork)], check=True, capture_output=True)
    git(source, 'checkout', 'main')
    (source / 'shared').write_text('upstream release\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'release')
    git(source, 'tag', 'v1.2.3')
    sha = git(source, 'rev-parse', 'HEAD')
    work = tmp_path / 'work'
    subprocess.run(['git', 'clone', str(fork), str(work)], check=True, capture_output=True)
    git(work, 'config', 'user.email', 'test@example.com')
    git(work, 'config', 'user.name', 'Test')
    monkeypatch.chdir(work)
    calls = []
    original = sync.run
    def run(*args):
        calls.append(args)
        if args[:4] == ('git', 'remote', 'get-url', 'origin'):
            return f'https://github.com/{sync.FORK}.git'
        if args[0] == 'gh':
            if args[1] == 'api':
                return json.dumps({'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False})
            if args[1:3] == ('pr', 'list'):
                return '[]'
            return ''
        args = tuple(str(source) if a == f'https://github.com/{sync.UPSTREAM}.git' else a for a in args)
        return original(*args)
    monkeypatch.setattr(sync, 'run', run)
    return work, fork, source, sha, calls, git


def test_integration_preserves_fork_and_pins_release(integration):
    work, fork, _, sha, calls, git = integration
    sync.main()
    assert (work / 'nexus').read_text() == 'preserved\n'
    assert (work / 'shared').read_text() == 'upstream release\n'
    assert json.loads((work / '.github/nexus-upstream.json').read_text())['sha'] == sha
    assert any(c[:3] == ('gh', 'pr', 'create') for c in calls)
    assert any(c[:4] == ('gh', 'workflow', 'run', 'ci.yml') for c in calls)
    branch = f'codex/upstream-v1.2.3-{sha[:12]}'
    assert git(fork, 'rev-parse', branch) == git(work, 'rev-parse', 'HEAD')


def test_dirty_checkout_is_untouched(integration):
    work, _, _, _, calls, _ = integration
    (work / 'nexus').write_text('local changes\n')
    with pytest.raises(RuntimeError, match='clean disposable'):
        sync.main()
    assert (work / 'nexus').read_text() == 'local changes\n'
    assert not any(c[0] == 'gh' for c in calls)


def test_conflict_never_pushes_partial_result(integration):
    work, _, _, _, calls, git = integration
    (work / 'shared').write_text('fork conflicting edit\n')
    git(work, 'add', '.')
    git(work, 'commit', '-m', 'fork conflict')
    git(work, 'push', 'origin', sync.BASE)
    with pytest.raises(RuntimeError, match='Merge conflict'):
        sync.main()
    assert not any(c[:2] == ('git', 'push') for c in calls)
    assert not any(c[:3] == ('gh', 'pr', 'create') for c in calls)
    assert not (work / '.git/MERGE_HEAD').exists()


def test_already_integrated_is_noop(integration):
    work, _, source, sha, calls, git = integration
    git(work, 'fetch', str(source), 'refs/tags/v1.2.3')
    git(work, 'merge', '--no-edit', sha)
    git(work, 'push', 'origin', sync.BASE)
    sync.main()
    assert not any(c[:2] == ('git', 'push') for c in calls)
    assert not any(c[:3] == ('gh', 'pr', 'create') for c in calls)


def test_checked_in_policy_and_keyless_build(monkeypatch):
    monkeypatch.delenv('TAURI_SIGNING_PRIVATE_KEY', raising=False)
    policy.check()


@pytest.fixture
def policy_root(tmp_path):
    for relative in ['surfaces/gui/src-tauri/tauri.conf.json', 'coworker/providers/registry.py',
                     'surfaces/gui/.npmrc', '.github/workflows/ci.yml',
                     '.github/workflows/release.yml', 'packaging/build_dmg.sh']:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return tmp_path


def test_signing_requires_configured_public_key(monkeypatch, policy_root):
    config_file = policy_root / 'surfaces/gui/src-tauri/tauri.conf.json'
    config = json.loads(config_file.read_text())
    config['plugins']['updater']['pubkey'] = ''
    config_file.write_text(json.dumps(config))
    monkeypatch.setenv('TAURI_SIGNING_PRIVATE_KEY', 'test-only')
    with pytest.raises(AssertionError, match='public key'):
        policy.check(policy_root)


def test_upstream_endpoint_reintroduction_fails(monkeypatch, policy_root):
    monkeypatch.delenv('TAURI_SIGNING_PRIVATE_KEY', raising=False)
    config_file = policy_root / 'surfaces/gui/src-tauri/tauri.conf.json'
    config = json.loads(config_file.read_text())
    config['plugins']['updater']['endpoints'].append('https://download.openworker.com/latest.json')
    config_file.write_text(json.dumps(config))
    with pytest.raises(AssertionError, match='fork channel'):
        policy.check(policy_root)


def test_rerun_reuses_existing_branch_and_pr(integration, monkeypatch):
    work, fork, _, sha, calls, git = integration
    sync.main()
    branch = f'codex/upstream-v1.2.3-{sha[:12]}'
    first = git(fork, 'rev-parse', branch)
    git(work, 'checkout', sync.BASE)
    git(work, 'branch', '-D', branch)  # discard only this test checkout's local branch
    previous = sync.run
    def existing_pr(*args):
        if args[:3] == ('gh', 'pr', 'list'):
            return '[{"number": 42}]'
        return previous(*args)
    monkeypatch.setattr(sync, 'run', existing_pr)
    calls.clear()
    sync.main()
    assert git(fork, 'rev-parse', branch) == first
    assert any(c[:3] == ('gh', 'pr', 'edit') for c in calls)
    assert not any(c[:3] == ('gh', 'pr', 'create') for c in calls)
