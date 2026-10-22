"""Internal action outcomes, with the original (text, background) interface."""

from dataclasses import dataclass, field
from typing import Optional
import re
import uuid


@dataclass
class ActionResult:
    output: str = ""
    success: bool = True
    exit_code: Optional[int] = 0
    is_background: bool = False
    status: str = "completed"
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    output_path: str = ""
    checks: list = field(default_factory=list)
    verified: bool = False
    metadata: dict = field(default_factory=dict)

    def __iter__(self):
        yield self.output
        yield self.is_background

    def __getitem__(self, index):
        return (self.output, self.is_background)[index]

    @classmethod
    def legacy(cls, output, background=False):
        if isinstance(output, cls):
            return output
        text = str(output or "")
        failure = text.startswith(("[Error:", "[Execution Error:", "[Failed", "[Exit status ",
                                   "Error:", "Error ", "Execution exception:", "Unknown manage_tasks",
                                   "[Safety guard:")) or (text.startswith("Task/PID ") and "not found" in text)
        code = re.match(r"\[Exit status (-?\d+)\]", text)
        status = "failed" if failure else "running" if background else "completed"
        if "timed out" in text[:100].lower():
            status = "timed_out"
        return cls(text, not failure, int(code.group(1)) if code else 1 if failure else 0,
                   background, status)


def as_result(value):
    if isinstance(value, ActionResult):
        return value
    if isinstance(value, tuple):
        return ActionResult.legacy(*value)
    return ActionResult.legacy(value)


def request_intent(text):
    """Negated constraints and explanations must not become mutation requests."""
    from engine.conversation_constraints import sentences, prose_mask
    clauses = []
    for sentence in sentences(str(text)):
        # A prohibition can coordinate several verbs across commas/"or". Its
        # scope ends at a sentence or an explicit positive transition.
        mask = prose_mask(sentence)
        spans = list(re.finditer(r"\b(?:do not|don't|never)\b.*?(?=\b(?:but|then)\b|$)", mask, re.I))
        for span in reversed(spans):
            sentence = sentence[:span.start()] + ' ' + sentence[span.end():]
        clauses.append(sentence)
    cleaned = ' '.join(clauses)
    prose = prose_mask(cleaned)
    prose = re.sub(r'(?<![\w])(?:[~./\w-]*/)?[\w-]+\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*', ' ', prose)
    prose = re.sub(r'^\s*(?:but|then|and)\s+', '', prose, flags=re.I)
    explanation = re.match(r"\s*(?:(?:please|kindly)\s+)?(?:explain|describe|what\b|why\b|how\b|compare|suggest)\b", prose, re.I)
    explicit_next_action = re.search(r"\b(?:and|then)\s+(?:run|execute|fix|edit|modify|create)\b", prose, re.I)
    if explanation and not explicit_next_action:
        return False, False
    action = bool(re.search(r"\b(?:create|write|make|build|implement|generate|edit|modify|update|change|fix|refactor|replace|delete|remove|rename|copy|move|touch|mkdir|execute|run|start|launch|restart|serve|open|inspect|read|list|search|audit|analyze)\b|go to|bring\s+up", prose, re.I))
    creation = bool(re.search(r"\b(?:create|write|make|build|implement|generate)\b", prose, re.I))
    implementation = creation and requires_content(cleaned)
    mutation = bool(re.search(r"\b(?:edit|modify|update|change|fix|refactor|rewrite)\b", prose, re.I)) or implementation
    code = bool(re.search(r"\b(?:code|file|script|app|application|function|class|page|site|website|webpage|component|program|css|html|py|js|json)\b|\.(?:py|jsx?|tsx?|json|css|html)\b", cleaned, re.I))
    navigation = bool(re.search(r"\b(?:directory|folder|mode|model|endpoint|settings|working directory)\b", prose, re.I))
    return action, mutation and (implementation or code or not navigation)


def requires_content(text):
    """Distinguish requested implementations from deliberately empty files/folders."""
    text = str(text)
    if re.search(r"\b(?:empty|blank|placeholder)\s+(?:\w+\s+)?file\b|\btouch\s+", text, re.I):
        return False
    creation = re.search(r"\b(?:create|write|make|build|implement|generate)\b", text, re.I)
    content = re.search(r"\b(?:code|script|app|website|webpage|page|site|html|css|python|javascript|program|application|function|component)\b|\.(?:py|jsx?|tsx?|json|css|html|sh)\b|\b(?:about|containing|contents?|that|which)\b", text, re.I)
    implementation = re.search(r"\b(?:build|implement)\b", text, re.I) and not re.search(r"\b(?:directory|folder)\b", text, re.I)
    return bool(creation and (content or implementation))
