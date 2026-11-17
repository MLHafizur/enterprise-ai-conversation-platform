import api from './api';
import {
  analyzeCodeFile,
  analyzeCodeSnippet,
  canManageCodeSecurity,
  canReadCodeSecurity,
  createCodeRepository,
  explainSecurityFinding,
  fetchSecurityDashboard,
  fetchSecurityRules,
  generateSecurityReport,
  normalizeSecurityDashboard,
  normalizeSecurityScan,
  normalizeSecuritySettings,
  queuePullRequestScan,
  reviewSecurityScan,
  suggestSecurityFix,
  updateCodeSecuritySettings,
  validateCodeSecurityFile
} from './codeSecurity';

jest.mock('./api', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn(), put: jest.fn() }
}));

const scanId = '11111111-1111-4111-8111-111111111111';
const findingId = '22222222-2222-4222-8222-222222222222';
const repositoryId = '33333333-3333-4333-8333-333333333333';
const actorId = '44444444-4444-4444-8444-444444444444';

const finding = () => ({
  id: findingId,
  fingerprint: 'a'.repeat(64),
  rule_id: 'python.hardcoded_secret',
  severity: 'critical',
  detector: 'pattern',
  cwe_id: 'CWE-798',
  owasp_category: 'A02:2021',
  safe_message: 'A hardcoded credential-like value was detected.',
  file_path: 'src/app.py',
  line_number: 7,
  column_number: 0,
  redacted_code_snippet: 'API_KEY = "[REDACTED]"',
  recommendation: 'Load credentials from a managed secret store.',
  created_at: '2026-11-16T18:00:00Z'
});

const scan = ({ includeFindings = true, overrides = {} } = {}) => ({
  id: scanId,
  repository_id: repositoryId,
  source: 'repository',
  status: 'succeeded',
  phase: 'completed',
  gate_decision: 'block',
  source_label: 'acme/payments@main',
  branch: 'main',
  base_branch: null,
  head_branch: null,
  total_findings: 1,
  critical_findings: 1,
  high_findings: 0,
  medium_findings: 0,
  low_findings: 0,
  info_findings: 0,
  files_scanned: 12,
  bytes_scanned: 4096,
  ruleset_version: 'enterprise-security.v1',
  safe_ai_summary: null,
  safe_error: null,
  created_at: '2026-11-16T18:00:00Z',
  updated_at: '2026-11-16T18:00:02Z',
  started_at: '2026-11-16T18:00:01Z',
  finished_at: '2026-11-16T18:00:02Z',
  ...(includeFindings ? { findings: [finding()] } : {}),
  ...overrides
});

const dashboard = () => ({
  counts: {
    total_scans: 4,
    vulnerabilities_found: 7,
    files_scanned: 32,
    repositories: 2,
    approved: 2,
    review_required: 1,
    blocked: 1,
    failed: 0,
    critical: 1,
    high: 2,
    medium: 3,
    low: 1,
    info: 0
  },
  recent_scans: [scan({ includeFindings: false })],
  top_vulnerabilities: [
    {
      rule_id: 'python.sql_injection',
      title: 'SQL injection risk',
      count: 3,
      severity: 'high',
      cwe_id: 'CWE-89'
    }
  ],
  trend: [
    {
      date: '2026-11-16',
      total: 7,
      critical: 1,
      high: 2,
      medium: 3,
      low: 1,
      info: 0
    }
  ]
});

const settings = () => ({
  revision: 3,
  block_severity: 'critical',
  review_severity: 'high',
  max_file_bytes: 1048576,
  max_repository_files: 2000,
  max_repository_bytes: 52428800,
  auto_scan_push: true,
  auto_scan_pull_request: true,
  ai_summary_enabled: false
});

describe('code security API adapter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('separates reader and administrator capabilities', () => {
    expect(canReadCodeSecurity(['agent'])).toBe(true);
    expect(canReadCodeSecurity(['user', 'admin'])).toBe(true);
    expect(canReadCodeSecurity(['user'])).toBe(false);
    expect(canManageCodeSecurity(['admin'])).toBe(true);
    expect(canManageCodeSecurity(['agent'])).toBe(false);
    expect(canReadCodeSecurity(['agent', 42])).toBe(false);
    expect(canManageCodeSecurity([' admin'])).toBe(false);
  });

  test('normalizes and freezes a redacted scan result', () => {
    const normalized = normalizeSecurityScan(scan());

    expect(normalized).toMatchObject({
      id: scanId,
      repositoryId,
      gateDecision: 'block',
      totalFindings: 1,
      counts: { critical: 1 },
      findings: [
        {
          id: findingId,
          cweId: 'CWE-798',
          owaspCategory: 'A02:2021',
          redactedCodeSnippet: 'API_KEY = "[REDACTED]"'
        }
      ]
    });
    expect(Object.isFrozen(normalized)).toBe(true);
    expect(Object.isFrozen(normalized.findings)).toBe(true);
  });

  test('normalizes an existing immutable human review in scan detail', () => {
    const normalized = normalizeSecurityScan(scan({
      overrides: {
        review: {
          id: findingId,
          scan_id: scanId,
          decision: 'approved',
          safe_note: 'Compensating controls verified.',
          reviewed_by: actorId,
          reviewed_at: '2026-11-16T18:02:00Z'
        }
      }
    }));

    expect(normalized.review).toMatchObject({
      decision: 'approved',
      safeNote: 'Compensating controls verified.'
    });
    expect(Object.isFrozen(normalized.review)).toBe(true);
  });

  test.each([
    ['unexpected raw source', (value) => { value.raw_code = 'API_KEY="secret"'; }],
    ['unknown severity', (value) => { value.findings[0].severity = 'urgent'; }],
    ['bad severity total', (value) => { value.high_findings = 1; }],
    ['absolute file path', (value) => { value.findings[0].file_path = '/etc/passwd'; }],
    ['unredacted extension field', (value) => { value.findings[0].matched_secret = 'secret'; }]
  ])('rejects %s', (_label, mutate) => {
    const value = scan();
    mutate(value);
    expect(() => normalizeSecurityScan(value)).toThrow(
      'Code security response was invalid'
    );
  });

  test('normalizes dashboard aggregates, recent scans, top rules, and trends', () => {
    expect(normalizeSecurityDashboard(dashboard())).toMatchObject({
      counts: {
        totalScans: 4,
        vulnerabilitiesFound: 7,
        reviewRequired: 1
      },
      recentScans: [{ id: scanId }],
      topVulnerabilities: [{ ruleId: 'python.sql_injection', count: 3 }],
      trend: [{ date: '2026-11-16', total: 7 }]
    });
  });

  test('loads the tenant security dashboard endpoint', async () => {
    api.get.mockResolvedValue({ data: dashboard() });

    await expect(fetchSecurityDashboard()).resolves.toMatchObject({
      counts: { totalScans: 4 }
    });
    expect(api.get).toHaveBeenCalledWith('/v1/security/dashboard');
  });

  test('loads versioned rules and derives their OWASP mapping from CWE', async () => {
    api.get.mockResolvedValue({
      data: {
        ruleset_version: 'enterprise-security.v1',
        rules: [
          {
            rule_id: 'python.sql_injection',
            title: 'SQL injection risk',
            severity: 'high',
            detectors: ['ast', 'pattern'],
            safe_message: 'Dynamic SQL construction was detected.',
            recommendation: 'Use parameterized queries.',
            cwe_id: 'CWE-89',
            enabled: true
          }
        ]
      }
    });

    await expect(fetchSecurityRules()).resolves.toMatchObject({
      rulesetVersion: 'enterprise-security.v1',
      rules: [{ owaspCategory: 'A03:2021' }]
    });
    expect(api.get).toHaveBeenCalledWith('/v1/security/rules');
  });

  test('submits snippets with a safe source label and no client identity', async () => {
    api.post.mockResolvedValue({
      data: scan({
        overrides: {
          repository_id: null,
          source: 'snippet',
          source_label: 'example.py',
          branch: null
        }
      })
    });

    await analyzeCodeSnippet({ code: 'print("hello")', filePath: 'example.py' });

    expect(api.post).toHaveBeenCalledWith('/v1/security/scans/snippet', {
      code: 'print("hello")',
      source_label: 'example.py'
    });
  });

  test('rejects path traversal before submitting a snippet', async () => {
    await expect(
      analyzeCodeSnippet({ code: 'print("hello")', filePath: '../secret.py' })
    ).rejects.toThrow('File path must be a safe relative path');
    expect(api.post).not.toHaveBeenCalled();
  });

  test('rejects repository credentials before the request', async () => {
    await expect(
      createCodeRepository({
        displayName: 'Payments',
        cloneUrl: 'https://token@github.com/acme/payments.git',
        defaultBranch: 'main'
      })
    ).rejects.toThrow('Repository details are invalid');
    expect(api.post).not.toHaveBeenCalled();
  });

  test('rejects a pull request with identical refs', async () => {
    await expect(
      queuePullRequestScan(repositoryId, {
        baseBranch: 'main',
        headBranch: 'main'
      })
    ).rejects.toThrow('Base and head branches must be different');
    expect(api.post).not.toHaveBeenCalled();
  });

  test('rejects an uploaded file that exceeds the tenant limit before sending', async () => {
    const oversized = new File(['a'.repeat(1025)], 'service.py', {
      type: 'text/x-python'
    });

    expect(() => validateCodeSecurityFile(oversized, 1024)).toThrow(
      'Selected source file exceeds the tenant file-size limit'
    );
    await expect(analyzeCodeFile(oversized, 1024)).rejects.toThrow(
      'Selected source file exceeds the tenant file-size limit'
    );
    expect(api.post).not.toHaveBeenCalled();
  });

  test('normalizes settings and sends their optimistic revision on update', async () => {
    const normalized = normalizeSecuritySettings(settings());
    api.put.mockResolvedValue({
      data: { ...settings(), revision: 4 }
    });

    await expect(
      updateCodeSecuritySettings({
        revision: normalized.revision,
        blockSeverity: normalized.blockSeverity,
        reviewSeverity: normalized.reviewSeverity,
        maxFileBytes: normalized.maxFileBytes,
        maxRepositoryFiles: normalized.maxRepositoryFiles,
        maxRepositoryBytes: normalized.maxRepositoryBytes,
        autoScanPush: normalized.autoScanPush,
        autoScanPullRequest: normalized.autoScanPullRequest,
        aiSummaryEnabled: normalized.aiSummaryEnabled
      })
    ).resolves.toMatchObject({ revision: 4 });
    expect(api.put).toHaveBeenCalledWith(
      '/v1/security/settings',
      expect.objectContaining({
        expected_revision: 3,
        block_severity: 'critical'
      })
    );
  });

  test('rejects a gate policy where block is not stronger than review', async () => {
    await expect(
      updateCodeSecuritySettings({
        revision: 3,
        blockSeverity: 'high',
        reviewSeverity: 'critical',
        maxFileBytes: 1048576,
        maxRepositoryFiles: 2000,
        maxRepositoryBytes: 52428800,
        autoScanPush: true,
        autoScanPullRequest: true,
        aiSummaryEnabled: false
      })
    ).rejects.toThrow('Block severity must be stronger than review severity');
  });

  test('submits an admin review with only a bounded safe note', async () => {
    api.post.mockResolvedValue({
      data: {
        id: '55555555-5555-4555-8555-555555555555',
        scan_id: scanId,
        decision: 'approved',
        safe_note: 'Risk accepted after framework-level regression testing.',
        reviewed_by: actorId,
        reviewed_at: '2026-11-16T18:02:00Z'
      }
    });

    await reviewSecurityScan(scanId, {
      decision: 'approved',
      safeNote: 'Risk accepted after framework-level regression testing.'
    });
    expect(api.post).toHaveBeenCalledWith(
      `/v1/security/scans/${scanId}/reviews`,
      {
        decision: 'approved',
        safe_note: 'Risk accepted after framework-level regression testing.'
      }
    );
  });

  test('validates advisory AI explanation, fix, and report envelopes', async () => {
    api.post
      .mockResolvedValueOnce({
        data: {
          finding_id: findingId,
          advisory: true,
          meaning: 'Untrusted data may alter a SQL statement.',
          exploitation: 'An attacker could change query semantics.',
          remediation_steps: ['Use a bound parameter.', 'Add a regression test.'],
          secure_example: 'cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))'
        }
      })
      .mockResolvedValueOnce({
        data: {
          finding_id: findingId,
          advisory: true,
          suggestion: 'Use a parameterized query and test hostile input.'
        }
      })
      .mockResolvedValueOnce({
        data: {
          scan_id: scanId,
          advisory: true,
          report: '# Security report\n\nOne high-risk finding requires review.'
        }
      });

    await expect(explainSecurityFinding(findingId)).resolves.toMatchObject({
      advisory: true,
      remediationSteps: ['Use a bound parameter.', 'Add a regression test.']
    });
    await expect(suggestSecurityFix(findingId)).resolves.toContain(
      'parameterized query'
    );
    await expect(generateSecurityReport(scanId)).resolves.toContain(
      'Security report'
    );
  });

  test('rejects advisory output whose finding identity does not match', async () => {
    api.post.mockResolvedValue({
      data: {
        finding_id: '99999999-9999-4999-8999-999999999999',
        advisory: true,
        meaning: 'Meaning',
        exploitation: 'Exploitation',
        remediation_steps: ['Remediate'],
        secure_example: 'Safe example'
      }
    });

    await expect(explainSecurityFinding(findingId)).rejects.toThrow(
      'Code security response was invalid'
    );
  });
});
