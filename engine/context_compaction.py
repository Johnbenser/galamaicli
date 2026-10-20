"""Recoverable active-task compaction; never rewrite saved conversations or user requests."""
import copy
import json

from engine.result_store import archive_context, compact_text


def fit_task(rows, originals, mandatory, available, estimate, is_request):
    rows = copy.deepcopy(rows)
    mandatory = set(mandatory)
    changed = set()

    def cost():
        return sum(estimate(rows[i]) + 8 for i in mandatory)

    def preview(text, limit):
        reference = archive_context(text)
        return (compact_text(text, limit) +
                f'\n[Partial historical context; exact text: read_action_output(action_id="{reference}"). '
                'Retrieve the needed lines before editing; omitted source is not inspected evidence.]')

    # Keep the latest request and every real constraint verbatim. Compact the largest
    # machine/generated bodies first, including huge historical write arguments.
    for limit in (1800, 800, 320):
        if cost() <= available:
            break
        candidates = sorted(mandatory, key=lambda i: estimate(rows[i]), reverse=True)
        for i in candidates:
            if cost() <= available:
                break
            if is_request(originals[i]):
                continue
            row = rows[i]
            original = originals[i]
            text = str(original.get('content', ''))
            if len(text) > limit + 300:
                row['content'] = preview(text, limit)
                changed.add(i)
            calls = original.get('tool_calls')
            if calls and len(json.dumps(calls)) > limit + 300:
                reference = archive_context(json.dumps(calls, ensure_ascii=False))
                reduced = copy.deepcopy(calls)
                for call in reduced:
                    function = call.get('function', {})
                    arguments = function.get('arguments', '{}')
                    try:
                        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(arguments, dict):
                        continue
                    for key, value in arguments.items():
                        if isinstance(value, str) and len(value) > limit:
                            arguments[key] = compact_text(value, limit)
                    function['arguments'] = json.dumps(arguments, ensure_ascii=False)
                row['tool_calls'] = reduced
                row['content'] += (f'\n[Historical tool arguments abbreviated; not a new call. '
                                   f'Exact original: read_action_output(action_id="{reference}").]')
                changed.add(i)

    # A long task can contain hundreds of small exchanges. Archive whole old native
    # call/result groups together; never strand tool results or touch real requests.
    groups = []
    index = 0
    while index < len(rows):
        end = index + 1
        if rows[index].get('tool_calls'):
            while end < len(rows) and rows[end].get('role') == 'tool':
                end += 1
        groups.append(set(range(index, end)))
        index = end
    latest = groups[-1] if groups else set()
    if groups and rows[min(latest)].get('role') != 'tool':
        latest |= next((g for g in reversed(groups) if rows[min(g)].get('tool_calls')), set())
    removed = []
    for group in groups:
        if cost() <= available:
            break
        if group & latest or not group <= mandatory or any(is_request(originals[i]) for i in group):
            continue
        removed.extend(sorted(group))
        mandatory.difference_update(group)
    if removed:
        record = '\n\n'.join(json.dumps(originals[i], ensure_ascii=False, default=str) for i in removed)
        reference = archive_context(record)
        # Reserve one explicit, recoverable task-log marker in the chronological slot.
        first = removed[0]
        rows[first] = {'role': 'user', 'content':
            f'[Earlier task exchanges archived ({len(removed)} messages). '
            f'For prior commands, paths, failures and results use read_action_output(action_id="{reference}"). '
            'This archive does not prove task completion. Original user requirements still apply.]'}
        mandatory.add(first)
        changed.update(removed)
        # If the marker tips a very tight budget over, the caller reports a real limit.
    return rows, mandatory, len(changed)
