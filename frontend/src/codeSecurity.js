import api from './api';

export const CODE_SECURITY_READER_ROLES = Object.freeze(['admin', 'agent']);
export const CODE_SECURITY_ADMIN_ROLES = Object.freeze(['admin']);
export const SECURITY_SEVERITIES = Object.freeze([
  'info',
  'low',
  'medium',
  'high',
  'critical'
]);
export const SECURITY_SCAN_STATUSES = Object.freeze([
  'queued',
  'running',
  'succeeded',
  'failed',
  'cancelled'
]);
export const SECURITY_GATE_DECISIONS = Object.freeze([
  'approve',
  'review_required',
  'block'
]);

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const RULE_ID_PATTERN = /^[a-z][a-z0-9_.-]{0,63}$/;
const CWE_PATTERN = /^CWE-[1-9][0-9]{0,4}$/;
const OWASP_PATTERN = /^A[0-9]{2}(?::[0-9]{4})?$/;
const RULESET_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const GIT_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$/;
const SOURCES = new Set([
  'snippet',
  'file_upload',
  'repository',
  'pull_request',
  'webhook',
  'ci'
]);
const PHASES = new Set([
  'received',
  'validating',
  'parsing',
  'detecting',
  'deduplicating',
  'redacting',
  'gating',
  'completed'
]);
const DETECTORS = new Set(['ast', 'pattern']);
const PROVIDERS = new Set(['github', 'gitlab', 'generic_git']);
const REVIEW_DECISIONS = new Set(['approved', 'blocked']);
const OWASP_BY_CWE = Object.freeze({
  'CWE-22': 'A01:2021',
  'CWE-78': 'A03:2021',
  'CWE-79': 'A03:2021',
  'CWE-89': 'A03:2021',
  'CWE-95': 'A03:2021',
  'CWE-798': 'A07:2021'
});
const MAX_LIST_ITEMS = 1000;

const invalidResponse = () => new Error('Code security response was invalid');
const invalidRequest = (message) => new Error(message);

const requireObject = (value, errorFactory = invalidResponse) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw errorFactory();
  }
  return value;
};

const requireAllowedKeys = (
  value,
  requiredKeys,
  optionalKeys = [],
  errorFactory = invalidResponse
) => {
  const object = requireObject(value, errorFactory);
  const allowed = new Set([...requiredKeys, ...optionalKeys]);
  const keys = Object.keys(object);
  if (
    requiredKeys.some((key) => !Object.prototype.hasOwnProperty.call(object, key)) ||
    keys.some((key) => !allowed.has(key))
  ) {
    throw errorFactory();
  }
  return object;
};

const requireString = (
  value,
  { maximum = 2048, allowNewlines = false, errorFactory = invalidResponse } = {}
) => {
  if (
    typeof value !== 'string' ||
    !value ||
    value !== value.trim() ||
    value.length > maximum ||
    value.includes('\0') ||
    (!allowNewlines && /[\r\n]/.test(value))
  ) {
    throw errorFactory();
  }
  return value;
};

const requireOptionalString = (value, options) =>
  value === null ? null : requireString(value, options);

const requireEnum = (value, allowed, errorFactory = invalidResponse) => {
  if (!allowed.has(value)) {
    throw errorFactory();
  }
  return value;
};

const requireUuid = (value, errorFactory = invalidResponse) => {
  if (typeof value !== 'string' || !UUID_PATTERN.test(value)) {
    throw errorFactory();
  }
  return value;
};

const requireOptionalUuid = (value) =>
  value === null ? null : requireUuid(value);

const requireInteger = (
  value,
  { minimum = 0, maximum = Number.MAX_SAFE_INTEGER, errorFactory = invalidResponse } = {}
) => {
  if (
    !Number.isSafeInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw errorFactory();
  }
  return value;
};

const requireTimestamp = (value) => {
  if (typeof value !== 'string' || Number.isNaN(new Date(value).getTime())) {
    throw invalidResponse();
  }
  return value;
};

const requireOptionalTimestamp = (value) =>
  value === null ? null : requireTimestamp(value);

const requireBoolean = (value, errorFactory = invalidResponse) => {
  if (typeof value !== 'boolean') {
    throw errorFactory();
  }
  return value;
};

const requirePattern = (value, pattern, errorFactory = invalidResponse) => {
  if (typeof value !== 'string' || !pattern.test(value)) {
    throw errorFactory();
  }
  return value;
};

const requireSafeRelativePath = (value, errorFactory = invalidResponse) => {
  const normalized = requireString(value, { maximum: 512, errorFactory });
  if (
    normalized.startsWith('/') ||
    normalized.includes('\\') ||
    normalized.split('/').some((segment) => segment === '..' || segment === '')
  ) {
    throw errorFactory();
  }
  return normalized;
};

const owaspForCwe = (cweId) => OWASP_BY_CWE[cweId] || null;

const normalizeRoles = (roles) =>
  Array.isArray(roles) &&
  roles.length > 0 &&
  roles.every(
    (role) =>
      typeof role === 'string' && role.length > 0 && role === role.trim()
  )
    ? roles
    : [];

export const canReadCodeSecurity = (roles) =>
  normalizeRoles(roles).some((role) =>
    CODE_SECURITY_READER_ROLES.includes(role)
  );

export const canManageCodeSecurity = (roles) =>
  normalizeRoles(roles).some((role) =>
    CODE_SECURITY_ADMIN_ROLES.includes(role)
  );

const normalizeFinding = (value) => {
  const finding = requireAllowedKeys(
    value,
    [
      'id',
      'fingerprint',
      'rule_id',
      'severity',
      'detector',
      'cwe_id',
      'owasp_category',
      'safe_message',
      'file_path',
      'line_number',
      'column_number',
      'redacted_code_snippet',
      'recommendation'
    ],
    ['created_at']
  );

  return Object.freeze({
    id: requireUuid(finding.id),
    fingerprint: requirePattern(finding.fingerprint, SHA256_PATTERN),
    ruleId: requirePattern(finding.rule_id, RULE_ID_PATTERN),
    severity: requireEnum(finding.severity, new Set(SECURITY_SEVERITIES)),
    detector: requireEnum(finding.detector, DETECTORS),
    cweId:
      finding.cwe_id === null
        ? null
        : requirePattern(finding.cwe_id, CWE_PATTERN),
    owaspCategory:
      finding.owasp_category === null
        ? null
        : requirePattern(finding.owasp_category, OWASP_PATTERN),
    safeMessage: requireString(finding.safe_message, { maximum: 512 }),
    filePath: requireSafeRelativePath(finding.file_path),
    lineNumber: requireInteger(finding.line_number, { minimum: 1 }),
    columnNumber: requireInteger(finding.column_number),
    redactedCodeSnippet: requireString(finding.redacted_code_snippet, {
      maximum: 512
    }),
    recommendation: requireString(finding.recommendation, { maximum: 1024 }),
    createdAt:
      finding.created_at === undefined
        ? null
        : requireTimestamp(finding.created_at)
  });
};

const normalizeSecurityReview = (value) => {
  const review = requireAllowedKeys(value, [
    'id',
    'scan_id',
    'decision',
    'safe_note',
    'reviewed_by',
    'reviewed_at'
  ]);
  return Object.freeze({
    id: requireUuid(review.id),
    scanId: requireUuid(review.scan_id),
    decision: requireEnum(review.decision, REVIEW_DECISIONS),
    safeNote: requireOptionalString(review.safe_note, { maximum: 512 }),
    reviewedBy: requireUuid(review.reviewed_by),
    reviewedAt: requireTimestamp(review.reviewed_at)
  });
};

export const normalizeSecurityScan = (value) => {
  const scan = requireAllowedKeys(
    value,
    [
      'id',
      'repository_id',
      'source',
      'status',
      'phase',
      'gate_decision',
      'source_label',
      'branch',
      'base_branch',
      'head_branch',
      'total_findings',
      'critical_findings',
      'high_findings',
      'medium_findings',
      'low_findings',
      'info_findings',
      'files_scanned',
      'bytes_scanned',
      'ruleset_version',
      'safe_ai_summary',
      'safe_error',
      'created_at',
      'updated_at',
      'started_at',
      'finished_at'
    ],
    ['findings', 'review']
  );
  const findings = scan.findings === undefined ? [] : scan.findings;
  if (!Array.isArray(findings) || findings.length > 10000) {
    throw invalidResponse();
  }

  const counts = {
    critical: requireInteger(scan.critical_findings),
    high: requireInteger(scan.high_findings),
    medium: requireInteger(scan.medium_findings),
    low: requireInteger(scan.low_findings),
    info: requireInteger(scan.info_findings)
  };
  const totalFindings = requireInteger(scan.total_findings);
  if (Object.values(counts).reduce((sum, count) => sum + count, 0) !== totalFindings) {
    throw invalidResponse();
  }
  if (findings.length > 0 && findings.length !== totalFindings) {
    throw invalidResponse();
  }

  return Object.freeze({
    id: requireUuid(scan.id),
    repositoryId: requireOptionalUuid(scan.repository_id),
    source: requireEnum(scan.source, SOURCES),
    status: requireEnum(scan.status, new Set(SECURITY_SCAN_STATUSES)),
    phase: requireEnum(scan.phase, PHASES),
    gateDecision:
      scan.gate_decision === null
        ? null
        : requireEnum(scan.gate_decision, new Set(SECURITY_GATE_DECISIONS)),
    sourceLabel: requireString(scan.source_label, { maximum: 256 }),
    branch: requireOptionalString(scan.branch, { maximum: 255 }),
    baseBranch: requireOptionalString(scan.base_branch, { maximum: 255 }),
    headBranch: requireOptionalString(scan.head_branch, { maximum: 255 }),
    totalFindings,
    counts: Object.freeze(counts),
    filesScanned: requireInteger(scan.files_scanned),
    bytesScanned: requireInteger(scan.bytes_scanned),
    rulesetVersion: requirePattern(scan.ruleset_version, RULESET_PATTERN),
    safeAiSummary: requireOptionalString(scan.safe_ai_summary, {
      maximum: 2000,
      allowNewlines: true
    }),
    safeError: requireOptionalString(scan.safe_error, { maximum: 512 }),
    createdAt: requireTimestamp(scan.created_at),
    updatedAt: requireTimestamp(scan.updated_at),
    startedAt: requireOptionalTimestamp(scan.started_at),
    finishedAt: requireOptionalTimestamp(scan.finished_at),
    findings: Object.freeze(findings.map(normalizeFinding)),
    review:
      scan.review === undefined || scan.review === null
        ? null
        : normalizeSecurityReview(scan.review)
  });
};

const normalizeRepository = (value) => {
  const repository = requireAllowedKeys(value, [
    'id',
    'provider',
    'display_name',
    'clone_url',
    'default_branch',
    'enabled',
    'created_at',
    'updated_at'
  ]);
  return Object.freeze({
    id: requireUuid(repository.id),
    provider: requireEnum(repository.provider, PROVIDERS),
    displayName: requireString(repository.display_name, { maximum: 128 }),
    cloneUrl: requireString(repository.clone_url, { maximum: 2048 }),
    defaultBranch: requirePattern(repository.default_branch, GIT_REF_PATTERN),
    enabled: requireBoolean(repository.enabled),
    createdAt: requireTimestamp(repository.created_at),
    updatedAt: requireTimestamp(repository.updated_at)
  });
};

const normalizeRule = (value) => {
  const rule = requireAllowedKeys(value, [
    'rule_id',
    'title',
    'severity',
    'detectors',
    'safe_message',
    'recommendation',
    'cwe_id',
    'enabled'
  ], ['owasp_category']);
  if (
    !Array.isArray(rule.detectors) ||
    rule.detectors.length < 1 ||
    rule.detectors.length > 2
  ) {
    throw invalidResponse();
  }
  const detectors = rule.detectors.map((detector) =>
    requireEnum(detector, DETECTORS)
  );
  if (new Set(detectors).size !== detectors.length) {
    throw invalidResponse();
  }
  return Object.freeze({
    ruleId: requirePattern(rule.rule_id, RULE_ID_PATTERN),
    title: requireString(rule.title, { maximum: 128 }),
    severity: requireEnum(rule.severity, new Set(SECURITY_SEVERITIES)),
    detectors: Object.freeze(detectors),
    safeMessage: requireString(rule.safe_message, { maximum: 512 }),
    recommendation: requireString(rule.recommendation, { maximum: 1024 }),
    cweId:
      rule.cwe_id === null ? null : requirePattern(rule.cwe_id, CWE_PATTERN),
    owaspCategory:
      rule.owasp_category === undefined || rule.owasp_category === null
        ? owaspForCwe(rule.cwe_id)
        : requirePattern(rule.owasp_category, OWASP_PATTERN),
    enabled: requireBoolean(rule.enabled)
  });
};

export const normalizeSecurityRules = (value) => {
  const response = requireAllowedKeys(value, ['ruleset_version', 'rules']);
  if (
    !Array.isArray(response.rules) ||
    response.rules.length < 1 ||
    response.rules.length > 128
  ) {
    throw invalidResponse();
  }
  return Object.freeze({
    rulesetVersion: requirePattern(response.ruleset_version, RULESET_PATTERN),
    rules: Object.freeze(response.rules.map(normalizeRule))
  });
};

const normalizeTrendPoint = (value) => {
  const point = requireAllowedKeys(value, [
    'date',
    'total',
    'critical',
    'high',
    'medium',
    'low',
    'info'
  ]);
  if (
    typeof point.date !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}$/.test(point.date)
  ) {
    throw invalidResponse();
  }
  const counts = {
    critical: requireInteger(point.critical),
    high: requireInteger(point.high),
    medium: requireInteger(point.medium),
    low: requireInteger(point.low),
    info: requireInteger(point.info)
  };
  return Object.freeze({
    date: point.date,
    total: requireInteger(point.total),
    ...counts
  });
};

const normalizeTopVulnerability = (value) => {
  const item = requireAllowedKeys(value, [
    'rule_id',
    'title',
    'count',
    'severity',
    'cwe_id'
  ]);
  return Object.freeze({
    ruleId: requirePattern(item.rule_id, RULE_ID_PATTERN),
    title: requireString(item.title, { maximum: 128 }),
    count: requireInteger(item.count, { minimum: 1 }),
    severity: requireEnum(item.severity, new Set(SECURITY_SEVERITIES)),
    cweId: item.cwe_id === null ? null : requirePattern(item.cwe_id, CWE_PATTERN)
  });
};

export const normalizeSecurityDashboard = (value) => {
  const response = requireAllowedKeys(value, [
    'counts',
    'recent_scans',
    'top_vulnerabilities',
    'trend'
  ]);
  const counts = requireAllowedKeys(response.counts, [
    'total_scans',
    'approved',
    'review_required',
    'blocked',
    'failed',
    'critical',
    'high',
    'medium',
    'low',
    'info'
  ], ['vulnerabilities_found', 'files_scanned', 'repositories']);
  const requiredCounts = Object.fromEntries(
    Object.entries(counts).map(([key, count]) => [
      key.replace(/_([a-z])/g, (_, character) => character.toUpperCase()),
      requireInteger(count)
    ])
  );
  const normalizedCounts = Object.freeze({
    ...requiredCounts,
    vulnerabilitiesFound:
      requiredCounts.vulnerabilitiesFound ??
      requiredCounts.critical +
        requiredCounts.high +
        requiredCounts.medium +
        requiredCounts.low +
        requiredCounts.info,
    filesScanned: requiredCounts.filesScanned ?? 0,
    repositories: requiredCounts.repositories ?? 0
  });
  if (
    !Array.isArray(response.recent_scans) ||
    response.recent_scans.length > 100 ||
    !Array.isArray(response.top_vulnerabilities) ||
    response.top_vulnerabilities.length > 100 ||
    !Array.isArray(response.trend) ||
    response.trend.length > 366
  ) {
    throw invalidResponse();
  }
  return Object.freeze({
    counts: normalizedCounts,
    recentScans: Object.freeze(response.recent_scans.map(normalizeSecurityScan)),
    topVulnerabilities: Object.freeze(
      response.top_vulnerabilities.map(normalizeTopVulnerability)
    ),
    trend: Object.freeze(response.trend.map(normalizeTrendPoint))
  });
};

export const normalizeSecuritySettings = (value) => {
  const settings = requireAllowedKeys(value, [
    'revision',
    'block_severity',
    'review_severity',
    'max_file_bytes',
    'max_repository_files',
    'max_repository_bytes',
    'auto_scan_push',
    'auto_scan_pull_request',
    'ai_summary_enabled'
  ]);
  const blockSeverity = requireEnum(
    settings.block_severity,
    new Set(SECURITY_SEVERITIES)
  );
  const reviewSeverity = requireEnum(
    settings.review_severity,
    new Set(SECURITY_SEVERITIES)
  );
  if (
    SECURITY_SEVERITIES.indexOf(blockSeverity) <=
    SECURITY_SEVERITIES.indexOf(reviewSeverity)
  ) {
    throw invalidResponse();
  }
  return Object.freeze({
    revision: requireInteger(settings.revision),
    blockSeverity,
    reviewSeverity,
    maxFileBytes: requireInteger(settings.max_file_bytes, {
      minimum: 1024,
      maximum: 10485760
    }),
    maxRepositoryFiles: requireInteger(settings.max_repository_files, {
      minimum: 1,
      maximum: 100000
    }),
    maxRepositoryBytes: requireInteger(settings.max_repository_bytes, {
      minimum: 1024,
      maximum: 1073741824
    }),
    autoScanPush: requireBoolean(settings.auto_scan_push),
    autoScanPullRequest: requireBoolean(settings.auto_scan_pull_request),
    aiSummaryEnabled: requireBoolean(settings.ai_summary_enabled)
  });
};

const normalizeScanCollection = (value) => {
  const response = requireAllowedKeys(value, ['scans']);
  if (!Array.isArray(response.scans) || response.scans.length > MAX_LIST_ITEMS) {
    throw invalidResponse();
  }
  return Object.freeze(response.scans.map(normalizeSecurityScan));
};

const normalizeRepositoryCollection = (value) => {
  const response = requireAllowedKeys(value, ['repositories']);
  if (
    !Array.isArray(response.repositories) ||
    response.repositories.length > MAX_LIST_ITEMS
  ) {
    throw invalidResponse();
  }
  return Object.freeze(response.repositories.map(normalizeRepository));
};

const validateSourceCode = (code) => {
  if (typeof code !== 'string' || !code.trim() || code.length > 10485760) {
    throw invalidRequest('Source code must contain between 1 and 10,485,760 characters');
  }
  return code;
};

const validateFilePath = (filePath) => {
  const errorFactory = () => invalidRequest('File path must be a safe relative path');
  return requireSafeRelativePath(filePath, errorFactory);
};

const validateGitRef = (value, label = 'Git reference') => {
  const errorFactory = () => invalidRequest(`${label} is invalid`);
  const ref = requirePattern(value, GIT_REF_PATTERN, errorFactory);
  if (
    ref.startsWith('-') ||
    ref.includes('..') ||
    ref.includes('//') ||
    ref.includes('@{') ||
    ref.endsWith('.') ||
    ref.endsWith('/')
  ) {
    throw errorFactory();
  }
  return ref;
};

export const fetchSecurityDashboard = async () => {
  const response = await api.get('/v1/security/dashboard');
  return normalizeSecurityDashboard(response.data);
};

export const fetchSecurityRules = async () => {
  const response = await api.get('/v1/security/rules');
  return normalizeSecurityRules(response.data);
};

export const fetchSecurityScans = async ({ limit = 50 } = {}) => {
  const boundedLimit = requireInteger(limit, {
    minimum: 1,
    maximum: 100,
    errorFactory: () => invalidRequest('Scan activity limit is invalid')
  });
  const response = await api.get('/v1/security/scans', {
    params: { limit: boundedLimit }
  });
  return normalizeScanCollection(response.data);
};

export const fetchSecurityScan = async (scanId) => {
  requireUuid(scanId, () => invalidRequest('Scan ID is invalid'));
  const response = await api.get(
    `/v1/security/scans/${encodeURIComponent(scanId)}`
  );
  return normalizeSecurityScan(response.data);
};

export const analyzeCodeSnippet = async ({ code, filePath }) => {
  const response = await api.post('/v1/security/scans/snippet', {
    code: validateSourceCode(code),
    source_label: validateFilePath(filePath)
  });
  return normalizeSecurityScan(response.data);
};

export const validateCodeSecurityFile = (
  file,
  maximumBytes = 10485760
) => {
  const limit = requireInteger(maximumBytes, {
    minimum: 1024,
    maximum: 10485760,
    errorFactory: () => invalidRequest('Tenant file-size limit is invalid')
  });
  if (!(file instanceof File) || file.size < 1) {
    throw invalidRequest('Select a non-empty source file');
  }
  if (file.size > limit) {
    throw invalidRequest('Selected source file exceeds the tenant file-size limit');
  }
  validateFilePath(file.name);
  return file;
};

export const analyzeCodeFile = async (file, maximumBytes = 10485760) => {
  const validatedFile = validateCodeSecurityFile(file, maximumBytes);
  const body = new FormData();
  body.append('file', validatedFile);
  const response = await api.post('/v1/security/scans/file', body, {
    headers: { 'Content-Type': 'multipart/form-data' }
  });
  return normalizeSecurityScan(response.data);
};

export const fetchCodeRepositories = async () => {
  const response = await api.get('/v1/security/repositories');
  return normalizeRepositoryCollection(response.data);
};

export const createCodeRepository = async ({
  displayName,
  cloneUrl,
  defaultBranch
}) => {
  const requestError = () => invalidRequest('Repository details are invalid');
  const name = requireString(displayName, {
    maximum: 128,
    errorFactory: requestError
  });
  let parsedUrl;
  try {
    parsedUrl = new URL(cloneUrl);
  } catch (_error) {
    throw requestError();
  }
  if (
    parsedUrl.protocol !== 'https:' ||
    parsedUrl.username ||
    parsedUrl.password ||
    parsedUrl.search ||
    parsedUrl.hash
  ) {
    throw requestError();
  }
  const response = await api.post('/v1/security/repositories', {
    display_name: name,
    clone_url: parsedUrl.toString(),
    default_branch: validateGitRef(defaultBranch, 'Default branch')
  });
  return normalizeRepository(response.data);
};

export const queueRepositoryScan = async (repositoryId, branch) => {
  requireUuid(repositoryId, () => invalidRequest('Repository ID is invalid'));
  const response = await api.post(
    `/v1/security/repositories/${encodeURIComponent(repositoryId)}/scans`,
    { branch: validateGitRef(branch, 'Branch') }
  );
  return normalizeSecurityScan(response.data);
};

export const queuePullRequestScan = async (
  repositoryId,
  { baseBranch, headBranch }
) => {
  requireUuid(repositoryId, () => invalidRequest('Repository ID is invalid'));
  const base = validateGitRef(baseBranch, 'Base branch');
  const head = validateGitRef(headBranch, 'Head branch');
  if (base === head) {
    throw invalidRequest('Base and head branches must be different');
  }
  const response = await api.post(
    `/v1/security/repositories/${encodeURIComponent(
      repositoryId
    )}/pull-request-scans`,
    { base_branch: base, head_branch: head }
  );
  return normalizeSecurityScan(response.data);
};

const normalizeExplanation = (value) => {
  const response = requireAllowedKeys(value, [
    'finding_id',
    'advisory',
    'meaning',
    'exploitation',
    'remediation_steps',
    'secure_example'
  ]);
  if (
    !Array.isArray(response.remediation_steps) ||
    response.remediation_steps.length < 1 ||
    response.remediation_steps.length > 10 ||
    response.advisory !== true
  ) {
    throw invalidResponse();
  }
  return Object.freeze({
    findingId: requireUuid(response.finding_id),
    advisory: true,
    meaning: requireString(response.meaning, {
      maximum: 2000,
      allowNewlines: true
    }),
    exploitation: requireString(response.exploitation, {
      maximum: 2000,
      allowNewlines: true
    }),
    remediationSteps: Object.freeze(
      response.remediation_steps.map((step) =>
        requireString(step, { maximum: 1000, allowNewlines: true })
      )
    ),
    secureExample: requireString(response.secure_example, {
      maximum: 4000,
      allowNewlines: true
    })
  });
};

export const explainSecurityFinding = async (findingId) => {
  requireUuid(findingId, () => invalidRequest('Finding ID is invalid'));
  const response = await api.post(
    `/v1/security/findings/${encodeURIComponent(findingId)}/explanation`
  );
  const explanation = normalizeExplanation(response.data);
  if (explanation.findingId !== findingId) throw invalidResponse();
  return explanation;
};

export const suggestSecurityFix = async (findingId) => {
  requireUuid(findingId, () => invalidRequest('Finding ID is invalid'));
  const response = await api.post(
    `/v1/security/findings/${encodeURIComponent(findingId)}/fix`
  );
  const body = requireAllowedKeys(response.data, [
    'finding_id',
    'advisory',
    'suggestion'
  ]);
  if (body.finding_id !== findingId || body.advisory !== true) {
    throw invalidResponse();
  }
  return requireString(body.suggestion, { maximum: 8000, allowNewlines: true });
};

export const generateSecurityReport = async (scanId) => {
  requireUuid(scanId, () => invalidRequest('Scan ID is invalid'));
  const response = await api.post(
    `/v1/security/scans/${encodeURIComponent(scanId)}/report`
  );
  const body = requireAllowedKeys(response.data, ['scan_id', 'advisory', 'report']);
  if (body.scan_id !== scanId || body.advisory !== true) {
    throw invalidResponse();
  }
  return requireString(body.report, { maximum: 16000, allowNewlines: true });
};

export const fetchCodeSecuritySettings = async () => {
  const response = await api.get('/v1/security/settings');
  return normalizeSecuritySettings(response.data);
};

export const updateCodeSecuritySettings = async (settings) => {
  const normalized = requireAllowedKeys(
    settings,
    [
      'revision',
      'blockSeverity',
      'reviewSeverity',
      'maxFileBytes',
      'maxRepositoryFiles',
      'maxRepositoryBytes',
      'autoScanPush',
      'autoScanPullRequest',
      'aiSummaryEnabled'
    ],
    [],
    () => invalidRequest('Security settings are invalid')
  );
  const severitySet = new Set(SECURITY_SEVERITIES);
  const block = requireEnum(
    normalized.blockSeverity,
    severitySet,
    () => invalidRequest('Block severity is invalid')
  );
  const review = requireEnum(
    normalized.reviewSeverity,
    severitySet,
    () => invalidRequest('Review severity is invalid')
  );
  if (SECURITY_SEVERITIES.indexOf(block) <= SECURITY_SEVERITIES.indexOf(review)) {
    throw invalidRequest('Block severity must be stronger than review severity');
  }
  const payload = {
    expected_revision: requireInteger(normalized.revision, {
      errorFactory: () => invalidRequest('Settings revision is invalid')
    }),
    block_severity: block,
    review_severity: review,
    max_file_bytes: requireInteger(normalized.maxFileBytes, {
      minimum: 1024,
      maximum: 10485760,
      errorFactory: () => invalidRequest('Maximum file size is invalid')
    }),
    max_repository_files: requireInteger(normalized.maxRepositoryFiles, {
      minimum: 1,
      maximum: 100000,
      errorFactory: () => invalidRequest('Maximum repository files is invalid')
    }),
    max_repository_bytes: requireInteger(normalized.maxRepositoryBytes, {
      minimum: 1024,
      maximum: 1073741824,
      errorFactory: () => invalidRequest('Maximum repository size is invalid')
    }),
    auto_scan_push: requireBoolean(
      normalized.autoScanPush,
      () => invalidRequest('Push scanning setting is invalid')
    ),
    auto_scan_pull_request: requireBoolean(
      normalized.autoScanPullRequest,
      () => invalidRequest('Pull-request scanning setting is invalid')
    ),
    ai_summary_enabled: requireBoolean(
      normalized.aiSummaryEnabled,
      () => invalidRequest('AI summary setting is invalid')
    )
  };
  if (payload.max_repository_bytes < payload.max_file_bytes) {
    throw invalidRequest('Repository byte limit cannot be smaller than file limit');
  }
  const response = await api.put('/v1/security/settings', payload);
  return normalizeSecuritySettings(response.data);
};

export const reviewSecurityScan = async (scanId, { decision, safeNote }) => {
  requireUuid(scanId, () => invalidRequest('Scan ID is invalid'));
  const normalizedDecision = requireEnum(
    decision,
    REVIEW_DECISIONS,
    () => invalidRequest('Review decision is invalid')
  );
  const note =
    safeNote === '' || safeNote === null
      ? null
      : requireString(safeNote, {
          maximum: 512,
          errorFactory: () => invalidRequest('Review note is invalid')
        });
  const response = await api.post(
    `/v1/security/scans/${encodeURIComponent(scanId)}/reviews`,
    { decision: normalizedDecision, safe_note: note }
  );
  return normalizeSecurityReview(response.data);
};
