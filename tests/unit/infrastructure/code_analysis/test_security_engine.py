import pytest

from enterprise_chat_platform.domain.code_security import (
    RuleSetVersion,
    SecurityAnalysisSettings,
    SecurityDetector,
    SecurityRule,
    SecuritySeverity,
)
from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import SecurityEngine


def _rule_ids(code: str, *, file_path: str = "src/app.py") -> set[str]:
    return {finding.rule_id for finding in SecurityEngine().analyze_code(code, file_path)}


def test_detects_assigned_dynamic_sql_passed_to_execute() -> None:
    findings = SecurityEngine().analyze_code(
        """def get_user(cursor, user_id):
    query = "SELECT * FROM users WHERE id = " + user_id
    cursor.execute(query)
""",
        "src/repository.py",
    )

    assert [(finding.rule_id, finding.detector) for finding in findings] == [
        ("sql_injection", SecurityDetector.AST)
    ]


def test_parameterized_sql_is_clean() -> None:
    code = """def get_user(cursor, user_id):
    query = "SELECT * FROM users WHERE id = ?"
    cursor.execute(query, (user_id,))
"""

    assert SecurityEngine().analyze_code(code, "src/repository.py") == ()


def test_detects_supported_rule_families() -> None:
    assert "xss" in _rule_ids('element.innerHTML = "<b>" + userInput', file_path="ui.js")
    assert "path_traversal" in _rule_ids('open("../../etc/passwd")')
    assert "unsafe_eval_exec" in _rule_ids("eval(user_input)")
    assert "hardcoded_secret" in _rule_ids('API_KEY = "sk-live-1234567890"')


def test_detects_command_injection_from_assigned_and_direct_parameters() -> None:
    code = """import os
def run(user_input):
    command = "ls " + user_input
    os.system(command)

def run_direct(command):
    os.system(command)
"""

    findings = SecurityEngine().analyze_code(code, "src/commands.py")

    assert [finding.rule_id for finding in findings] == [
        "command_injection",
        "command_injection",
    ]


def test_constant_command_does_not_create_injection_finding() -> None:
    assert SecurityEngine().analyze_code('import os\nos.system("ls -la")', "tool.py") == ()


def test_duplicate_ast_and_pattern_matches_are_suppressed() -> None:
    findings = SecurityEngine().analyze_code(
        'cursor.execute("SELECT * FROM users WHERE id=" + user_id)',
        "repository.py",
    )

    assert len(findings) == 1
    assert findings[0].detector is SecurityDetector.AST


def test_secret_values_are_redacted_before_findings_and_cache() -> None:
    secret = "sk-live-super-secret-value"
    engine = SecurityEngine()

    findings = engine.analyze_code(f'API_SECRET = "{secret}"\neval("{secret}")', "app.py")

    assert findings
    assert all(secret not in repr(finding) for finding in findings)
    assert all("[REDACTED]" in finding.code_snippet for finding in findings)
    assert engine.analyze_code(f'API_SECRET = "{secret}"\neval("{secret}")', "app.py") == findings


def test_fingerprint_is_stable_across_runs_line_insertion_and_secret_rotation() -> None:
    first = SecurityEngine().analyze_code('API_KEY = "first-secret-value"', "app.py")[0]
    second = SecurityEngine().analyze_code(
        '# harmless line\nAPI_KEY = "different-secret-value"',
        "app.py",
    )[0]

    assert first.fingerprint == second.fingerprint
    assert first.finding_id == second.finding_id


def test_cache_key_includes_path_and_rule_set_version() -> None:
    code = "eval(user_input)"
    first_engine = SecurityEngine(rule_set_version=RuleSetVersion("day10.v1"))
    first = first_engine.analyze_code(code, "a.py")
    second = first_engine.analyze_code(code, "b.py")
    versioned = SecurityEngine(rule_set_version=RuleSetVersion("day10.v2")).analyze_code(
        code,
        "a.py",
    )

    assert first[0].file_path == "a.py"
    assert second[0].file_path == "b.py"
    assert first_engine.cache_size == 2
    assert first[0].rule_set_version != versioned[0].rule_set_version


def test_cache_is_bounded() -> None:
    engine = SecurityEngine(settings=SecurityAnalysisSettings(cache_entries=2))

    engine.analyze_code("eval(first)", "one.py")
    engine.analyze_code("eval(second)", "two.py")
    engine.analyze_code("eval(third)", "three.py")

    assert engine.cache_size == 2


def test_syntax_errors_fall_back_to_redacted_pattern_analysis() -> None:
    secret = "sentinel-secret-value"

    findings = SecurityEngine().analyze_code(
        f'API_KEY = "{secret}"\nif (',
        "broken.py",
    )

    assert {finding.rule_id for finding in findings} == {"hardcoded_secret"}
    assert all(secret not in repr(finding) for finding in findings)


@pytest.mark.parametrize("file_path", ["/app.py", "../app.py", "src\\app.py", "src/./app.py"])
def test_rejects_noncanonical_or_unsafe_paths(file_path: str) -> None:
    with pytest.raises(DomainValidationError, match="path"):
        SecurityEngine().analyze_code("print('safe')", file_path)


def test_finding_overflow_fails_closed_instead_of_hiding_critical_issue() -> None:
    engine = SecurityEngine(settings=SecurityAnalysisSettings(max_findings=1))
    code = 'element.innerHTML = "<b>" + userInput\nAPI_KEY = "critical-secret-value"'

    with pytest.raises(DomainValidationError, match="finding limit"):
        engine.analyze_code(code, "ui.js")


def test_subset_rule_catalog_does_not_raise_for_unconfigured_ast_rules() -> None:
    xss_rule = SecurityRule(
        rule_id="xss",
        title="Unsafe HTML assignment",
        severity=SecuritySeverity.MEDIUM,
        detectors=(SecurityDetector.PATTERN,),
        safe_message="Dynamic HTML may be unsafe",
        recommendation="Use safe DOM APIs",
        cwe_id="CWE-79",
        pattern=r"\.innerHTML\s*=\s*[^\n]*\+",
    )
    engine = SecurityEngine(rules=(xss_rule,))

    assert engine.analyze_code("eval(user_input)", "app.py") == ()
