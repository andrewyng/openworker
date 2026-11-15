#!/usr/bin/env python3
"""Package reviewed local deployment assets. Offline; never uploads or provisions."""
import argparse
import hashlib
import json
import pathlib
import shutil


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pin(source, destination):
    source = pathlib.Path(source)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Expected a bounded regular release file')
    shutil.copyfile(source, destination)
    return {'file': str(destination), 'sha256': sha(destination.read_bytes())}


def tree(root):
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Chart symlinks are not supported')
        if path.is_file():
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('Chart file exceeds 1 MiB')
            files[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    if 'Chart.yaml' not in files or len(files) > 128:
        raise ValueError('Expected a bounded Helm chart')
    return sha(json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('version', 'architecture', 'harness-image', 'runner-image', 'service-chart',
                'openshell-archive', 'openshell-values', 'platform-manifest', 'database-ca', 'out'):
        p.add_argument('--' + arg, required=True)
    p.add_argument('--ui-image')
    a = p.parse_args()
    if a.architecture not in ('arm64', 'amd64'):
        p.error('architecture must be arm64 or amd64')
    for image in (a.harness_image, a.runner_image, a.ui_image):
        if image is not None:
            import re
            if not re.fullmatch(r'[a-zA-Z0-9/._:-]+@sha256:[a-f0-9]{64}', image):
                p.error('images must use immutable SHA-256 digests')
    root = pathlib.Path(a.out).absolute()
    root.mkdir(mode=0o700)  # refuse overwrite of an existing release
    chart = pathlib.Path(a.service_chart)
    tree(chart)  # validate before copying
    shutil.copytree(chart, root / 'chart')
    result = {'schemaVersion': 1, 'version': a.version, 'architecture': a.architecture,
              'images': {'harness': a.harness_image, 'runner': a.runner_image, 'serviceUi': a.ui_image},
              'serviceChart': str(root / 'chart'), 'serviceChartSha256': tree(root / 'chart')}
    for key, source, name in (
        ('openshellArchive', a.openshell_archive, 'openshell.tgz'),
        ('openshellValues', a.openshell_values, 'openshell-values.json'),
        ('platformManifest', a.platform_manifest, 'platform.json'),
        ('databaseCa', a.database_ca, 'database-ca.pem'),
    ):
        result[key] = pin(source, root / name)
    (root / 'release.json').write_text(json.dumps(result, indent=2) + '\n')
    print(root / 'release.json')


if __name__ == '__main__':
    main()
