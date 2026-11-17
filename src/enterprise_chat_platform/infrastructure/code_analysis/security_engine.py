"""Bounded, deterministic source-code vulnerability analysis.

The engine parses source text but never imports or executes it. Its output is
redacted and immutable so callers can safely persist or display findings.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import PurePosixPath
from threading import RLock
from uuid import NAMESPACE_URL, uuid5

from enterprise_chat_platform.domain.code_security import (
    MAX_FILE_PATH_LENGTH,
    MAX_SECURITY_RULES,
    RuleSetVersion,
    SecurityAnalysisSettings,
    SecurityDetector,
    SecurityFinding,
    SecurityRule,
    SecuritySeverity,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.identifiers import SecurityFindingId

DEFAULT_RULE_SET_VERSION = RuleSetVersion("day10.v1")

DEFAULT_SECURITY_RULES: tuple[SecurityRule, ...] = (
    SecurityRule(
        rule_id="sql_injection",
        title="Dynamic SQL execution",
        severity=SecuritySeverity.HIGH,
        detectors=(SecurityDetector.AST, SecurityDetector.PATTERN),
        safe_message="SQL query may be constructed from untrusted input",
        recommendation="Use parameterized queries or prepared statements",
        cwe_id="CWE-89",
        pattern=r"\b(?:execute|executemany)\s*\([^\n)]*(?:\+|%|\.format\s*\(|f[\"'])",
    ),
    SecurityRule(
        rule_id="xss",
        title="Unsafe HTML assignment",
        severity=SecuritySeverity.MEDIUM,
        detectors=(SecurityDetector.PATTERN,),
        safe_message="Dynamic content may be assigned to an HTML execution sink",
        recommendation="Use safe DOM APIs and context-aware output encoding",
        cwe_id="CWE-79",
        pattern=r"\.innerHTML\s*=\s*(?:[^\n]*\+|`[^\n`]*\$\{)",
    ),
    SecurityRule(
        rule_id="path_traversal",
        title="Path traversal",
        severity=SecuritySeverity.HIGH,
        detectors=(SecurityDetector.AST, SecurityDetector.PATTERN),
        safe_message="File access may traverse outside the intended directory",
        recommendation="Resolve paths against an allowlisted root and reject traversal",
        cwe_id="CWE-22",
        pattern=r"\b(?:open|Path)\s*\([^\n)]*(?:\.\.[/\\])",
    ),
    SecurityRule(
        rule_id="hardcoded_secret",
        title="Hardcoded secret",
        severity=SecuritySeverity.CRITICAL,
        detectors=(SecurityDetector.AST, SecurityDetector.PATTERN),
        safe_message="A credential-like value is hardcoded in source",
        recommendation="Load secrets from an approved secret manager",
        cwe_id="CWE-798",
        pattern=(
            r"\b(?:[A-Za-z_][A-Za-z0-9_]*_)?"
            r"(?:password|passwd|secret|token|api_?key|private_?key)"
            r"(?:_[A-Za-z0-9_]+)*\s*=\s*['\"][^'\"]{8,}['\"]"
        ),
    ),
    SecurityRule(
        rule_id="unsafe_eval_exec",
        title="Dynamic code execution",
        severity=SecuritySeverity.HIGH,
        detectors=(SecurityDetector.AST, SecurityDetector.PATTERN),
        safe_message="Dynamic code execution can run untrusted input",
        recommendation="Remove eval or exec and use a constrained parser",
        cwe_id="CWE-95",
        pattern=r"\b(?:eval|exec)\s*\(",
    ),
    SecurityRule(
        rule_id="command_injection",
        title="Command injection",
        severity=SecuritySeverity.HIGH,
        detectors=(SecurityDetector.AST, SecurityDetector.PATTERN),
        safe_message="A shell command may be constructed from untrusted input",
        recommendation="Use subprocess argument lists with shell disabled and validate input",
        cwe_id="CWE-78",
        pattern=(
            r"\b(?:os\.system|subprocess\.(?:run|call|Popen))\s*"
            r"\([^\n)]*(?:\+|%|\.format\s*\(|f[\"'])"
        ),
    ),
)

_SECRET_NAME_PARTS = frozenset(
    {"password", "passwd", "pwd", "secret", "token", "credential", "credentials"}
)
_PLACEHOLDER_SECRET_VALUES = frozenset(
    {
        "changeme",
        "example",
        "placeholder",
        "redacted",
        "replace_me",
        "your_secret_here",
    }
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?P<prefix>[A-Za-z_][A-Za-z0-9_]*(?:password|passwd|secret|token|api_?key|"
    r"private_?key)[A-Za-z0-9_]*\s*[:=]\s*)(?P<quote>['\"])(?P<value>[^'\"]*)"
    r"(?P=quote)"
)
_QUOTED_VALUE_PATTERN = re.compile(r"(?P<quote>['\"])(?P<value>[^'\"]{4,})(?P=quote)")


@dataclass(frozen=True, slots=True)
class _Candidate:
    rule: SecurityRule
    detector: SecurityDetector
    line_number: int
    column_number: int
    snippet: str


class SecurityEngine:
    """Analyze code with deterministic AST and pattern rules only."""

    def __init__(
        self,
        *,
        rule_set_version: RuleSetVersion = DEFAULT_RULE_SET_VERSION,
        rules: tuple[SecurityRule, ...] = DEFAULT_SECURITY_RULES,
        settings: SecurityAnalysisSettings | None = None,
    ) -> None:
        if not isinstance(rule_set_version, RuleSetVersion):
            raise DomainValidationError("Security engine rule-set version is invalid")
        try:
            normalized_rules = tuple(rules)
        except TypeError:
            raise DomainValidationError("Security engine rules are invalid") from None
        if not normalized_rules or len(normalized_rules) > MAX_SECURITY_RULES:
            raise DomainValidationError("Security engine requires a bounded nonempty rule set")
        if any(not isinstance(rule, SecurityRule) for rule in normalized_rules):
            raise DomainValidationError("Security engine rules are invalid")
        rule_ids = [rule.rule_id for rule in normalized_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise DomainValidationError("Security engine rule IDs must be unique")

        resolved_settings = settings or SecurityAnalysisSettings()
        if not isinstance(resolved_settings, SecurityAnalysisSettings):
            raise DomainValidationError("Security engine settings are invalid")

        self._rule_set_version = rule_set_version
        self._rules = tuple(rule for rule in normalized_rules if rule.enabled)
        if not self._rules:
            raise DomainValidationError("Security engine requires at least one enabled rule")
        self._rules_by_id = {rule.rule_id: rule for rule in self._rules}
        self._settings = resolved_settings
        self._compiled_patterns = {
            rule.rule_id: re.compile(rule.pattern, re.IGNORECASE)
            for rule in self._rules
            if rule.pattern is not None
        }
        self._cache: OrderedDict[str, tuple[SecurityFinding, ...]] = OrderedDict()
        self._cache_lock = RLock()

    @property
    def cache_size(self) -> int:
        """Expose cache occupancy for health metrics and deterministic tests."""
        with self._cache_lock:
            return len(self._cache)

    def clear_cache(self) -> None:
        """Discard cached redacted findings."""
        with self._cache_lock:
            self._cache.clear()

    def analyze_code(self, code: str, file_path: str) -> tuple[SecurityFinding, ...]:
        """Return stable, deduplicated, redacted findings without executing code."""
        self._validate_input(code=code, file_path=file_path)
        cache_key = self._cache_key(code=code, file_path=file_path)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached

        candidates: list[_Candidate] = []
        if self._settings.ast_enabled and PurePosixPath(file_path).suffix.lower() == ".py":
            candidates.extend(self._analyze_python_ast(code=code))
        if self._settings.pattern_enabled:
            candidates.extend(self._analyze_patterns(code=code))

        findings = self._build_findings(candidates=candidates, file_path=file_path)
        with self._cache_lock:
            self._cache[cache_key] = findings
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._settings.cache_entries:
                self._cache.popitem(last=False)
        return findings

    def _validate_input(self, *, code: str, file_path: str) -> None:
        if not isinstance(code, str):
            raise DomainValidationError("Code analysis input must be text")
        if not code:
            raise DomainValidationError("Code analysis input cannot be empty")
        if len(code.encode("utf-8")) > self._settings.max_code_bytes:
            raise DomainValidationError("Code analysis input exceeds the configured byte limit")
        if (
            not isinstance(file_path, str)
            or not file_path
            or file_path != file_path.strip()
            or len(file_path) > MAX_FILE_PATH_LENGTH
            or not file_path.isprintable()
            or "\\" in file_path
            or file_path.startswith("/")
            or "//" in file_path
        ):
            raise DomainValidationError("Code analysis file path is invalid")
        path = PurePosixPath(file_path)
        if (
            file_path in {".", ".."}
            or path.as_posix() != file_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise DomainValidationError("Code analysis file path is invalid")

    def _cache_key(self, *, code: str, file_path: str) -> str:
        digest = hashlib.sha256()
        for component in (self._rule_set_version.value, file_path, code):
            encoded = component.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    def _analyze_python_ast(self, *, code: str) -> list[_Candidate]:
        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError):
            # Incomplete editor buffers and generated Python can be invalid but
            # still contain pattern-detectable vulnerabilities. Keep the AST
            # detector fail-safe and let the bounded pattern pass run next.
            return []

        source_lines = code.splitlines()
        dynamic_names = self._collect_dynamic_names(tree)
        candidates: list[_Candidate] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                candidates.extend(
                    self._check_secret_assignment(node=node, source_lines=source_lines)
                )
            if not isinstance(node, ast.Call):
                continue

            function_name = _call_name(node.func)
            first_argument = node.args[0] if node.args else None

            if (
                function_name.rsplit(".", maxsplit=1)[-1] in {"execute", "executemany"}
                and first_argument is not None
                and _is_dynamic_expression(first_argument, dynamic_names=dynamic_names)
            ):
                self._append_candidate(
                    candidates,
                    rule_id="sql_injection",
                    detector=SecurityDetector.AST,
                    node=node,
                    source_lines=source_lines,
                )

            if function_name in {"eval", "exec"}:
                self._append_candidate(
                    candidates,
                    rule_id="unsafe_eval_exec",
                    detector=SecurityDetector.AST,
                    node=node,
                    source_lines=source_lines,
                )

            if function_name in {
                "os.system",
                "subprocess.call",
                "subprocess.Popen",
                "subprocess.run",
            } and (
                (
                    first_argument is not None
                    and _is_dynamic_expression(first_argument, dynamic_names=dynamic_names)
                )
                or _has_true_shell_keyword(node)
            ):
                self._append_candidate(
                    candidates,
                    rule_id="command_injection",
                    detector=SecurityDetector.AST,
                    node=node,
                    source_lines=source_lines,
                )

            if function_name in {"open", "pathlib.Path", "Path"} and first_argument is not None:
                literal_path = _literal_string(first_argument)
                if literal_path is not None and _contains_parent_traversal(literal_path):
                    self._append_candidate(
                        candidates,
                        rule_id="path_traversal",
                        detector=SecurityDetector.AST,
                        node=node,
                        source_lines=source_lines,
                    )

        return candidates

    def _collect_dynamic_names(self, tree: ast.AST) -> frozenset[str]:
        dynamic_names: set[str] = set()
        assignments: list[tuple[tuple[str, ...], ast.expr]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                dynamic_names.update(argument.arg for argument in node.args.posonlyargs)
                dynamic_names.update(argument.arg for argument in node.args.args)
                dynamic_names.update(argument.arg for argument in node.args.kwonlyargs)
                if node.args.vararg is not None:
                    dynamic_names.add(node.args.vararg.arg)
                if node.args.kwarg is not None:
                    dynamic_names.add(node.args.kwarg.arg)
            if isinstance(node, ast.Assign):
                names = tuple(target.id for target in node.targets if isinstance(target, ast.Name))
                assignments.append((names, node.value))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    assignments.append(((node.target.id,), node.value))

        # A small fixed-point pass handles simple chains such as query -> command.
        for _ in range(len(assignments) + 1):
            changed = False
            for names, expression in assignments:
                if _is_dynamic_expression(expression, dynamic_names=frozenset(dynamic_names)):
                    previous_size = len(dynamic_names)
                    dynamic_names.update(names)
                    changed = changed or len(dynamic_names) != previous_size
            if not changed:
                break
        return frozenset(dynamic_names)

    def _check_secret_assignment(
        self,
        *,
        node: ast.Assign | ast.AnnAssign,
        source_lines: list[str],
    ) -> list[_Candidate]:
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return []
        if len(value.value) < 8 or value.value.strip().lower() in _PLACEHOLDER_SECRET_VALUES:
            return []

        targets: tuple[ast.expr, ...]
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
        else:
            targets = (node.target,)
        if not any(_is_sensitive_target(target) for target in targets):
            return []
        candidate = self._candidate(
            rule_id="hardcoded_secret",
            detector=SecurityDetector.AST,
            node=node,
            source_lines=source_lines,
        )
        return [candidate] if candidate is not None else []

    def _append_candidate(
        self,
        candidates: list[_Candidate],
        *,
        rule_id: str,
        detector: SecurityDetector,
        node: ast.AST,
        source_lines: list[str],
    ) -> None:
        candidate = self._candidate(
            rule_id=rule_id,
            detector=detector,
            node=node,
            source_lines=source_lines,
        )
        if candidate is not None:
            candidates.append(candidate)

    def _candidate(
        self,
        *,
        rule_id: str,
        detector: SecurityDetector,
        node: ast.AST,
        source_lines: list[str],
    ) -> _Candidate | None:
        rule = self._rules_by_id.get(rule_id)
        if rule is None or detector not in rule.detectors:
            return None
        line_number = getattr(node, "lineno", 1)
        column_number = getattr(node, "col_offset", 0)
        return _Candidate(
            rule=rule,
            detector=detector,
            line_number=line_number,
            column_number=column_number,
            snippet=_source_line(source_lines=source_lines, line_number=line_number),
        )

    def _analyze_patterns(self, *, code: str) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for line_number, line in enumerate(code.splitlines(), start=1):
            for rule_id, pattern in self._compiled_patterns.items():
                rule = self._rules_by_id[rule_id]
                for match in pattern.finditer(line):
                    candidates.append(
                        _Candidate(
                            rule=rule,
                            detector=SecurityDetector.PATTERN,
                            line_number=line_number,
                            column_number=match.start(),
                            snippet=line,
                        )
                    )
        return candidates

    def _build_findings(
        self,
        *,
        candidates: list[_Candidate],
        file_path: str,
    ) -> tuple[SecurityFinding, ...]:
        detector_order = {SecurityDetector.AST: 0, SecurityDetector.PATTERN: 1}
        ordered_candidates = sorted(
            candidates,
            key=lambda candidate: (
                candidate.line_number,
                candidate.column_number,
                candidate.rule.rule_id,
                detector_order[candidate.detector],
            ),
        )
        findings: list[SecurityFinding] = []
        unique_candidates: list[tuple[_Candidate, str, str]] = []
        seen_locations: set[tuple[str, int, str]] = set()

        for candidate in ordered_candidates:
            snippet = _redact_snippet(candidate.snippet)
            normalized_snippet = " ".join(snippet.split())
            location_key = (
                candidate.rule.rule_id,
                candidate.line_number,
                normalized_snippet,
            )
            if location_key in seen_locations:
                continue
            seen_locations.add(location_key)
            unique_candidates.append((candidate, snippet, normalized_snippet))

        if len(unique_candidates) > self._settings.max_findings:
            raise DomainValidationError("Security analysis exceeded the configured finding limit")

        occurrence_counts: dict[tuple[str, str], int] = {}
        for candidate, snippet, normalized_snippet in unique_candidates:
            occurrence_key = (candidate.rule.rule_id, normalized_snippet)
            occurrence = occurrence_counts.get(occurrence_key, 0) + 1
            occurrence_counts[occurrence_key] = occurrence
            fingerprint = _finding_fingerprint(
                rule_id=candidate.rule.rule_id,
                file_path=file_path,
                normalized_snippet=normalized_snippet,
                occurrence=occurrence,
            )
            findings.append(
                SecurityFinding(
                    finding_id=SecurityFindingId(
                        uuid5(NAMESPACE_URL, f"security-finding:{fingerprint}")
                    ),
                    fingerprint=fingerprint,
                    rule_id=candidate.rule.rule_id,
                    rule_set_version=self._rule_set_version,
                    severity=candidate.rule.severity,
                    detector=candidate.detector,
                    safe_message=candidate.rule.safe_message,
                    recommendation=candidate.rule.recommendation,
                    file_path=file_path,
                    line_number=candidate.line_number,
                    column_number=candidate.column_number,
                    code_snippet=snippet,
                    cwe_id=candidate.rule.cwe_id,
                )
            )
        findings.sort(
            key=lambda finding: (
                -finding.severity.rank,
                finding.file_path,
                finding.line_number,
                finding.column_number,
                finding.rule_id,
            )
        )
        return tuple(findings)


def _call_name(function: ast.expr) -> str:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        prefix = _call_name(function.value)
        return f"{prefix}.{function.attr}" if prefix else function.attr
    return ""


def _is_dynamic_expression(expression: ast.expr, *, dynamic_names: frozenset[str]) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in dynamic_names
    if isinstance(expression, ast.JoinedStr):
        return any(isinstance(value, ast.FormattedValue) for value in expression.values)
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, (ast.Add, ast.Mod)):
        return not _is_literal_expression(expression)
    if isinstance(expression, ast.Call):
        return True
    if isinstance(expression, (ast.Attribute, ast.Subscript)):
        return True
    return False


def _is_literal_expression(expression: ast.expr) -> bool:
    if isinstance(expression, ast.Constant):
        return True
    if isinstance(expression, ast.BinOp):
        return _is_literal_expression(expression.left) and _is_literal_expression(expression.right)
    return False


def _is_sensitive_target(target: ast.expr) -> bool:
    name = ""
    if isinstance(target, ast.Name):
        name = target.id
    elif isinstance(target, ast.Attribute):
        name = target.attr
    if not name:
        return False
    parts = {
        part.lower()
        for part in re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|[0-9]|_|$)|[A-Z]?[a-z]+|[0-9]+",
            name,
        )
    }
    return (
        bool(parts.intersection(_SECRET_NAME_PARTS))
        or {"api", "key"}.issubset(parts)
        or {
            "private",
            "key",
        }.issubset(parts)
    )


def _has_true_shell_keyword(node: ast.Call) -> bool:
    return any(
        keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )


def _literal_string(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    return None


def _contains_parent_traversal(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return ".." in PurePosixPath(normalized).parts


def _source_line(*, source_lines: list[str], line_number: int) -> str:
    if 1 <= line_number <= len(source_lines):
        return source_lines[line_number - 1]
    return "source unavailable"


def _redact_snippet(snippet: str) -> str:
    safe = snippet.replace("\t", "    ").strip()
    safe = _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}"
        ),
        safe,
    )
    safe = _QUOTED_VALUE_PATTERN.sub(
        lambda match: f"{match.group('quote')}[REDACTED]{match.group('quote')}",
        safe,
    )
    safe = "".join(character if character.isprintable() else "?" for character in safe)
    return safe[:512] or "source unavailable"


def _finding_fingerprint(
    *,
    rule_id: str,
    file_path: str,
    normalized_snippet: str,
    occurrence: int,
) -> str:
    digest = hashlib.sha256()
    for component in (
        rule_id,
        file_path,
        normalized_snippet,
        str(occurrence),
    ):
        encoded = component.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()
