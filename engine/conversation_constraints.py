"""Carry user restrictions without promoting completed actions to new orders."""
import re

PATTERN = re.compile(r"\b(must|never|preserve|keep|constraint|remember|without|only|always)\b|do not|don't", re.I)
_ACTION = r'(?:create|write|make|build|implement|generate|edit|modify|update|change|fix|refactor|replace|run|execute|analyze|inspect|read|search|start|launch|verify|test|report|explain|return|rebuild)'
_RESTRICTION = r"(?:preserve|keep|do not|don't|never|always|remember|without)"


def prose_mask(text):
    """Blank quoted data without moving offsets used to slice original prose."""
    result = list(text)
    quote = None
    escaped = False
    for i, char in enumerate(text):
        if quote:
            result[i] = ' '
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('`', '"', "'") and (char != "'" or i == 0 or not text[i-1].isalnum()):
            quote = char
            result[i] = ' '
    return ''.join(result)


def sentences(text):
    """Split prose at sentence boundaries while preserving quoted code/paths."""
    text = str(text)
    mask = prose_mask(text)
    start = 0
    for i, char in enumerate(mask):
        if char in '\n;' or char in '.!?' and (i+1 == len(text) or text[i+1].isspace()):
            value = text[start:i+1].strip()
            if value:
                yield value
            start = i+1
    if text[start:].strip():
        yield text[start:].strip()


def constraint_clauses(text):
    result = []
    for sentence in sentences(text):
        if not PATTERN.search(prose_mask(sentence)):
            continue
        clause = re.sub(r'^\s*[-*]\s+', '', sentence).strip()
        # Deictic restrictions belong to their original edit, not every later task.
        if re.fullmatch(r"(?:do not|don't)\s+(?:change|modify|edit|touch)\s+(?:any\s+)?other\s+files?[.!;]?|preserve\s+(?:every|all)\s+other\s+bytes?[.!;]?", clause, re.I):
            continue
        standing = re.match(r'^(?:always|never|remember|from now on)\b', clause, re.I)
        if re.match(r'^(?:(?:please|kindly)\s+)?'+_ACTION+r'\b', clause, re.I) and not standing:
            # "Run tests and preserve data" carries the restriction, not the run.
            restriction = re.search(r'(?:\band\b|\bbut\b|,)\s*('+_RESTRICTION+r')\b', prose_mask(clause), re.I)
            if not restriction:
                restriction = re.search(r'\b(without)\b', prose_mask(clause), re.I)
            if not restriction:
                continue  # "Change only X" is a task, not a standing instruction.
            clause = clause[restriction.start(1):]
        if not standing and not re.match(r"^(?:do not|don't|never)\b", clause, re.I):
            action = re.search(r'(?:\s+(?:and|then|but)\s+|,\s*(?:then\s+)?)(?='+_ACTION+r'\b)', prose_mask(clause), re.I)
            if action:
                clause = clause[:action.start()].rstrip()
        if clause and clause not in result:
            result.append(clause)
    return result
