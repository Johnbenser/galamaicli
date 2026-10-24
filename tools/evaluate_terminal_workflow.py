"""Offline Propose–Probe–Compose checks for terminal guidance, using disposable projects.

No API calls or user projects. Proposals are deterministic test cases, not model
output. This evaluates command checks, not general model intelligence or safety.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def evaluate():
    from config import AGENT_STATE
    from engine.command_reference import assess
    from engine.tool_parser import validate_shell_command
    from engine.task_runner import execute_shell_action
    from engine.context_manager import estimate_tokens
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='galam-terminal-probe-') as tmp:
        root = Path(tmp)
        site = root / 'web project'
        site.mkdir()
        (site / 'package.json').write_text(json.dumps({'scripts': {'develop': 'custom-service', 'diagnose': 'custom-check'}}))
        (root / 'worker.py').write_text('print("PROBE_OK")\n')
        proposals = []
        for manager in ('npm', 'pnpm', 'yarn', 'bun'):
            proposals.extend([
                (f'cd {shlex.quote(str(site))} && {manager} run develop', '', True),
                (f'cd {shlex.quote(str(site))} && {manager} run invented', '', False),
            ])
        proposals.extend([
            ('python worker.py', '', True),
            ('python absent.py', '', False),
            ('ls -la', 'display my folder', True),
            ('mkdir unnecessary', 'display my folder', False),
            ('npm install', 'display my folder', False),
            ('cd missing-folder && pwd', '', False),
        ])
        probes = []
        for command, request, expected in proposals:
            before = validate_shell_command(command)[0]
            result = assess(command, root, request)
            probes.append({'proposal': command.replace(tmp, '<fixture>'), 'expected_allowed': expected,
                           'baseline_shape_check_allowed': before, 'candidate_allowed': result.allowed,
                           'candidate_correct': result.allowed == expected,
                           'correction_estimated_tokens': estimate_tokens(result.reason + '\n'.join(result.suggestions))
                               if not result.allowed else 0})
        executions = []
        with patch.dict(AGENT_STATE, {'working_directory': str(root), 'abort': False}), patch.dict(
                os.environ, {'GALAM_STATE_DIR': str(root / 'runtime')}):
            for command in ('ls -la', f'{shlex.quote(sys.executable)} worker.py',
                            f'cd -- {shlex.quote(str(site))} && pwd'):
                result = execute_shell_action(command)
                executions.append({'command': command.replace(tmp, '<fixture>'), 'success': result.success,
                                   'exit_code': result.exit_code})
        correct = sum(row['candidate_correct'] for row in probes)
        return {'mode': 'offline deterministic fixture evaluation', 'model_requests': 0,
                'proposal_count': len(probes), 'probes': probes, 'executions': executions,
                'baseline_correct': sum(row['baseline_shape_check_allowed'] == row['expected_allowed'] for row in probes),
                'candidate_correct': correct,
                'false_rejections': sum(not row['candidate_allowed'] and row['expected_allowed'] for row in probes),
                'compose': {'adopt': correct == len(probes) and all(row['success'] for row in executions),
                            'retained': 'Live project command references before execution; existing review stays in control.',
                            'limits': 'Known fixture outcomes only. Complex shell programs require runtime diagnosis; these results do not establish performance across real models.'},
                'elapsed_seconds': round(time.monotonic() - started, 3)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('proposal_count', 'baseline_correct', 'candidate_correct',
                                                   'false_rejections', 'model_requests', 'elapsed_seconds')}))
    raise SystemExit(0 if result['compose']['adopt'] else 1)
