"""Run desktop checks only; never discover tests in unrelated web projects."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tools' / 'qa-system'


def run():
    OUT.mkdir(exist_ok=True)
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js is required')
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    commands = [('node-check-' + path.stem, [node, '--check', str(path)]) for path in sorted((ROOT/'static').glob('*.js'))]
    commands += [('node-tests', [node, '--test', *map(str, sorted((ROOT/'tests').glob('*.js'))),
                                *map(str, sorted((ROOT/'tests'/'frontend').glob('*.test.mjs')))]),
                 ('python-tests', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])]
    report = []
    for name, command in commands:
        with (OUT/(name+'.log')).open('wb') as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        report.append({'check': name, 'exit_code': result.returncode, 'log': name+'.log'})
        print(name + ': ' + str(result.returncode), flush=True)
    (OUT/'checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return int(any(item['exit_code'] for item in report))


if __name__ == '__main__':
    sys.exit(run())
