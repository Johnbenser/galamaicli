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
    cleaned = re.sub(
        r"\b(?:do not|don't|never)\s+(?:modify|edit|change|write|overwrite|create|delete|remove|run|execute)\b"
        r"(?:(?!\b(?:and|but|then)\b)[^.;,\n])*", "", str(text), flags=re.I)
    explanation = re.match(r"\s*(?:(?:please|kindly)\s+)?(?:explain|describe|what\b|why\b|how\b|compare|suggest)\b", cleaned, re.I)
    explicit_next_action = re.search(r"\b(?:and|then)\s+(?:run|execute|fix|edit|modify|create)\b", cleaned, re.I)
    if explanation and not explicit_next_action:
        return False, False
    action = bool(re.search(r"\b(?:create|write|make|build|implement|generate|edit|modify|update|change|fix|refactor|replace|delete|remove|rename|copy|move|touch|mkdir|execute|run|start|open|inspect|read|list|search|audit)\b|go to", cleaned, re.I))
    mutation = bool(re.search(r"\b(?:edit|modify|update|change|fix|refactor|rewrite)\b", cleaned, re.I)) or requires_content(cleaned)
    code = bool(re.search(r"\b(?:code|file|script|app|function|class|page|site|css|html|py|js|json)\b|\.(?:py|jsx?|tsx?|json|css|html)\b", cleaned, re.I))
    navigation = bool(re.search(r"\b(?:directory|folder|mode|model|endpoint|settings|working directory)\b", cleaned, re.I))
    return action, mutation and (code or not navigation)


def requires_content(text):
    """Distinguish requested implementations from deliberately empty files/folders."""
    text = str(text)
    if re.search(r"\b(?:empty|blank|placeholder)\s+(?:\w+\s+)?file\b|\btouch\s+", text, re.I):
        return False
    creation = re.search(r"\b(?:create|write|make|build|implement|generate)\b", text, re.I)
    content = re.search(r"\b(?:code|script|app|website|webpage|page|site|html|css|python|javascript|program|application|function|component)\b|\.(?:py|jsx?|tsx?|json|css|html|sh)\b|\b(?:about|containing|contents?|that|which)\b", text, re.I)
    implementation = re.search(r"\b(?:build|implement)\b", text, re.I) and not re.search(r"\b(?:directory|folder)\b", text, re.I)
    return bool(creation and (content or implementation))
