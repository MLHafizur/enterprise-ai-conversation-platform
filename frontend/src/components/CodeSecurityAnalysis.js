import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowUpTrayIcon,
  BeakerIcon,
  CheckCircleIcon,
  CodeBracketIcon,
  DocumentArrowDownIcon,
  LightBulbIcon,
  ShieldExclamationIcon,
  SparklesIcon
} from '@heroicons/react/24/outline';
import { toast } from 'react-toastify';

import { getApiErrorMessage } from '../api';
import {
  analyzeCodeFile,
  analyzeCodeSnippet,
  explainSecurityFinding,
  fetchCodeSecuritySettings,
  fetchSecurityRules,
  generateSecurityReport,
  reviewSecurityScan,
  suggestSecurityFix
} from '../codeSecurity';
import { SecurityBadge, formatBytes } from './SecurityPresentation';

const EXAMPLE_CODE = `import os
import sqlite3

API_SECRET = "replace-me-with-a-secret-manager"

def find_user(user_id):
    connection = sqlite3.connect("users.db")
    query = "SELECT * FROM users WHERE id = " + user_id
    return connection.execute(query).fetchone()

def run_user_expression(user_expression):
    return eval(user_expression)

def open_report(file_name):
    return open("/srv/reports/" + file_name).read()
`;

const severityOrder = ['critical', 'high', 'medium', 'low', 'info'];

export const SecurityRuleCatalog = ({ catalog }) => (
  <section
    className="mt-8 rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
    aria-labelledby="deterministic-rule-catalog-heading"
  >
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h2
          id="deterministic-rule-catalog-heading"
          className="font-semibold text-gray-900"
        >
          Deterministic rule catalog
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Read-only, safe rule metadata used by the authoritative security gate.
        </p>
      </div>
      <div className="text-right text-xs text-gray-500">
        <p>{catalog.rules.filter((rule) => rule.enabled).length} active rules</p>
        <p className="mt-1 font-mono">{catalog.rulesetVersion}</p>
      </div>
    </div>

    <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
      {catalog.rules.map((rule) => (
        <article
          key={rule.ruleId}
          className="rounded-lg border border-gray-200 bg-gray-50 p-4"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold text-gray-900">{rule.title}</h3>
              <p className="mt-1 font-mono text-xs text-gray-500">{rule.ruleId}</p>
            </div>
            <div className="flex items-center gap-2">
              <SecurityBadge value={rule.severity} />
              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${rule.enabled ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200 text-gray-700'}`}>
                {rule.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {rule.detectors.map((detector) => (
              <span key={detector} className="rounded bg-slate-200 px-2 py-1 font-medium uppercase text-slate-700">
                {detector.toUpperCase()}
              </span>
            ))}
            {rule.cweId && <span className="rounded bg-gray-200 px-2 py-1 font-medium text-gray-700">{rule.cweId}</span>}
            {rule.owaspCategory && <span className="rounded bg-indigo-100 px-2 py-1 font-medium text-indigo-700">OWASP {rule.owaspCategory}</span>}
          </div>

          <p className="mt-3 text-sm text-gray-800">{rule.safeMessage}</p>
          <div className="mt-3 rounded-md border border-indigo-100 bg-indigo-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-indigo-700">
              Recommendation
            </p>
            <p className="mt-1 text-sm text-indigo-950">{rule.recommendation}</p>
          </div>
        </article>
      ))}
    </div>
  </section>
);

const FindingCard = ({ finding }) => {
  const [explanation, setExplanation] = useState(null);
  const [fix, setFix] = useState('');
  const [working, setWorking] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  const explain = async () => {
    setWorking('explanation');
    setErrorMessage('');
    try {
      setExplanation(await explainSecurityFinding(finding.id));
    } catch (error) {
      setErrorMessage(getApiErrorMessage(error, 'Could not generate an explanation'));
    } finally {
      setWorking('');
    }
  };

  const suggestFix = async () => {
    setWorking('fix');
    setErrorMessage('');
    try {
      setFix(await suggestSecurityFix(finding.id));
    } catch (error) {
      setErrorMessage(getApiErrorMessage(error, 'Could not generate a fix suggestion'));
    } finally {
      setWorking('');
    }
  };

  return (
    <article className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <SecurityBadge value={finding.severity} />
            <span className="font-mono text-xs text-gray-500">{finding.ruleId}</span>
            {finding.cweId && <span className="rounded bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700">{finding.cweId}</span>}
            {finding.owaspCategory && <span className="rounded bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-700">OWASP {finding.owaspCategory}</span>}
          </div>
          <h3 className="mt-3 font-semibold text-gray-900">{finding.safeMessage}</h3>
          <p className="mt-1 text-xs text-gray-500">
            {finding.filePath}:{finding.lineNumber}:{finding.columnNumber} · {finding.detector.toUpperCase()}
          </p>
        </div>
      </div>

      <div className="mt-4 rounded-lg bg-slate-950 p-4">
        <div className="mb-2 flex items-center justify-between text-xs text-slate-400">
          <span>Redacted evidence</span>
          <span>Secrets never leave the scanner</span>
        </div>
        <code className="block whitespace-pre-wrap break-all font-mono text-sm text-slate-100">{finding.redactedCodeSnippet}</code>
      </div>

      <div className="mt-4 rounded-lg border border-indigo-100 bg-indigo-50 p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-indigo-700">Recommendation</p>
        <p className="mt-1 text-sm text-indigo-950">{finding.recommendation}</p>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" onClick={explain} disabled={Boolean(working)} className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
          <SparklesIcon className="mr-2 h-4 w-4 text-indigo-600" aria-hidden="true" />
          {working === 'explanation' ? 'Explaining…' : 'Explain finding'}
        </button>
        <button type="button" onClick={suggestFix} disabled={Boolean(working)} className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
          <LightBulbIcon className="mr-2 h-4 w-4 text-amber-600" aria-hidden="true" />
          {working === 'fix' ? 'Generating…' : 'Suggest secure fix'}
        </button>
      </div>

      {errorMessage && <p className="mt-3 text-sm text-red-700" role="alert">{errorMessage}</p>}

      {explanation && (
        <section className="mt-4 rounded-lg border border-purple-200 bg-purple-50 p-4" aria-label="Advisory AI explanation">
          <div className="flex items-center gap-2"><SparklesIcon className="h-5 w-5 text-purple-600" /><h4 className="font-semibold text-purple-950">Advisory explanation</h4></div>
          <p className="mt-3 text-sm text-purple-950"><strong>What it means:</strong> {explanation.meaning}</p>
          <p className="mt-2 text-sm text-purple-950"><strong>How it may be exploited:</strong> {explanation.exploitation}</p>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-purple-950">
            {explanation.remediationSteps.map((step) => <li key={step}>{step}</li>)}
          </ol>
          <pre className="mt-3 whitespace-pre-wrap rounded bg-purple-950 p-3 text-xs text-purple-50">{explanation.secureExample}</pre>
          <p className="mt-3 text-xs text-purple-700">AI output is advisory. The deterministic gate remains authoritative.</p>
        </section>
      )}

      {fix && (
        <section className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4" aria-label="Advisory fix suggestion">
          <h4 className="font-semibold text-amber-950">Secure alternative</h4>
          <pre className="mt-2 whitespace-pre-wrap text-sm text-amber-950">{fix}</pre>
          <p className="mt-2 text-xs text-amber-700">Review, test, and re-scan this suggestion before merging.</p>
        </section>
      )}
    </article>
  );
};

const ReviewPanel = ({ scan, onReviewed }) => {
  const [decision, setDecision] = useState('approved');
  const [safeNote, setSafeNote] = useState('');
  const [working, setWorking] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setWorking(true);
    try {
      const review = await reviewSecurityScan(scan.id, { decision, safeNote });
      toast.success(`Scan ${review.decision.replace('_', ' ')}`);
      onReviewed(review);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not record the review'));
    } finally {
      setWorking(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-6 rounded-xl border border-amber-300 bg-amber-50 p-5">
      <h3 className="font-semibold text-amber-950">Human security review required</h3>
      <p className="mt-1 text-sm text-amber-800">A deterministic high-risk finding paused this gate. Record an auditable decision.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <label className="text-sm font-medium text-amber-950">Decision
          <select value={decision} onChange={(event) => setDecision(event.target.value)} className="mt-1 block w-full rounded-md border-amber-300 bg-white px-3 py-2">
            <option value="approved">Approve with review</option>
            <option value="blocked">Block</option>
          </select>
        </label>
        <label className="text-sm font-medium text-amber-950">Safe audit note (optional)
          <input value={safeNote} onChange={(event) => setSafeNote(event.target.value)} maxLength={512} className="mt-1 block w-full rounded-md border-amber-300 px-3 py-2" placeholder="No secrets or source code" />
        </label>
      </div>
      <button type="submit" disabled={working} className="mt-4 rounded-md bg-amber-800 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-900 disabled:opacity-50">{working ? 'Recording…' : 'Record review'}</button>
    </form>
  );
};

export const SecurityAnalysisResults = ({ scan, canManage = false, onReviewed = () => {} }) => {
  const [report, setReport] = useState('');
  const [reporting, setReporting] = useState(false);
  const [review, setReview] = useState(scan.review);

  useEffect(() => {
    setReview(scan.review);
  }, [scan.id, scan.review]);

  const generateReport = async () => {
    setReporting(true);
    try {
      setReport(await generateSecurityReport(scan.id));
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not generate the security report'));
    } finally {
      setReporting(false);
    }
  };

  return (
    <section aria-labelledby="analysis-results-heading">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 id="analysis-results-heading" className="text-lg font-semibold text-gray-900">Analysis results</h2>
          <p className="mt-1 text-sm text-gray-500">{scan.sourceLabel} · {formatBytes(scan.bytesScanned)} · ruleset {scan.rulesetVersion}</p>
        </div>
        <div className="flex items-center gap-2"><SecurityBadge value={scan.status} kind="status" />{scan.gateDecision && <SecurityBadge value={scan.gateDecision} kind="gate" />}</div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <div className="rounded-lg border border-gray-200 bg-white p-3"><p className="text-xs text-gray-500">Total</p><p className="mt-1 text-xl font-semibold">{scan.totalFindings}</p></div>
        {severityOrder.map((severity) => <div key={severity} className="rounded-lg border border-gray-200 bg-white p-3"><p className="text-xs capitalize text-gray-500">{severity}</p><p className="mt-1 text-xl font-semibold">{scan.counts[severity]}</p></div>)}
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        <button type="button" onClick={generateReport} disabled={reporting} className="inline-flex items-center rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
          <DocumentArrowDownIcon className="mr-2 h-4 w-4" />{reporting ? 'Generating…' : 'Generate report'}
        </button>
      </div>

      {scan.safeAiSummary && <div className="mt-5 rounded-lg border border-purple-200 bg-purple-50 p-4"><p className="text-xs font-semibold uppercase tracking-wide text-purple-700">Advisory AI summary</p><p className="mt-2 whitespace-pre-wrap text-sm text-purple-950">{scan.safeAiSummary}</p></div>}
      {report && <div className="mt-5 rounded-lg border border-indigo-200 bg-indigo-50 p-4"><h3 className="font-semibold text-indigo-950">Security report</h3><pre className="mt-3 whitespace-pre-wrap text-sm text-indigo-950">{report}</pre></div>}

      {scan.findings.length === 0 ? (
        <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 px-6 py-10 text-center"><CheckCircleIcon className="mx-auto h-10 w-10 text-emerald-600" /><h3 className="mt-3 font-semibold text-emerald-950">No security findings</h3><p className="mt-1 text-sm text-emerald-800">The configured deterministic rules found no issues in this input.</p></div>
      ) : (
        <div className="mt-6 space-y-4">{scan.findings.map((finding) => <FindingCard key={finding.id} finding={finding} />)}</div>
      )}

      {review && (
        <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5" aria-label="Recorded security review">
          <h3 className="font-semibold text-gray-900">Recorded human review</h3>
          <p className="mt-2 text-sm text-gray-700">Decision: <strong>{review.decision}</strong></p>
          <p className="mt-1 text-xs text-gray-500">Recorded {new Date(review.reviewedAt).toLocaleString()}</p>
          {review.safeNote && <p className="mt-3 text-sm text-gray-700">{review.safeNote}</p>}
        </section>
      )}

      {canManage && scan.gateDecision === 'review_required' && !review && (
        <ReviewPanel scan={scan} onReviewed={(recordedReview) => { setReview(recordedReview); onReviewed(recordedReview); }} />
      )}
    </section>
  );
};

const CodeSecurityAnalysis = ({ canManage = false }) => {
  const [mode, setMode] = useState('snippet');
  const [code, setCode] = useState(EXAMPLE_CODE);
  const [filePath, setFilePath] = useState('example.py');
  const [file, setFile] = useState(null);
  const [scan, setScan] = useState(null);
  const [rules, setRules] = useState(null);
  const [settings, setSettings] = useState(null);
  const [rulesError, setRulesError] = useState('');
  const [settingsError, setSettingsError] = useState('');
  const [working, setWorking] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    setRulesError('');
    fetchSecurityRules().then(setRules).catch((error) => {
      console.error('Could not load security rules:', error);
      setRulesError(getApiErrorMessage(error, 'Could not load the deterministic rule catalog'));
    });
    setSettingsError('');
    fetchCodeSecuritySettings().then(setSettings).catch((error) => {
      console.error('Could not load security settings:', error);
      setSettingsError(getApiErrorMessage(error, 'Could not load the tenant file-size limit'));
    });
  }, []);

  const selectFile = (event) => {
    const selected = event.target.files?.[0] || null;
    setErrorMessage('');
    if (!selected) {
      setFile(null);
      return;
    }
    if (!settings) {
      setFile(null);
      setErrorMessage('The tenant file-size limit is not available yet.');
      event.target.value = '';
      return;
    }
    if (selected.size > settings.maxFileBytes) {
      setFile(null);
      setErrorMessage(
        `Selected file exceeds the tenant limit of ${formatBytes(settings.maxFileBytes)}.`
      );
      event.target.value = '';
      return;
    }
    setFile(selected);
  };

  const analyze = useCallback(async () => {
    if (mode === 'file' && !settings) {
      setErrorMessage('The tenant file-size limit is not available.');
      return;
    }
    setWorking(true);
    setErrorMessage('');
    setScan(null);
    try {
      const result = mode === 'snippet'
        ? await analyzeCodeSnippet({ code, filePath })
        : await analyzeCodeFile(file, settings.maxFileBytes);
      setScan(result);
      toast.success('Security analysis completed');
    } catch (error) {
      setErrorMessage(getApiErrorMessage(error, 'Security analysis failed'));
    } finally {
      setWorking(false);
    }
  }, [code, file, filePath, mode, settings]);

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-gray-900">Code analysis</h1>
          <p className="mt-1 text-sm text-gray-600">Scan Python code with versioned AST and pattern rules. Only redacted evidence is persisted.</p>
          {rules && <p className="mt-2 text-xs text-gray-500">{rules.rules.filter((rule) => rule.enabled).length} active rules · ruleset {rules.rulesetVersion}</p>}
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm" aria-labelledby="code-input-heading">
            <h2 id="code-input-heading" className="font-semibold text-gray-900">Code input</h2>
            <div className="mt-4 flex rounded-lg bg-gray-100 p-1" role="tablist" aria-label="Code input type">
              <button type="button" role="tab" aria-selected={mode === 'snippet'} onClick={() => setMode('snippet')} className={`flex-1 rounded-md px-3 py-2 text-sm font-medium ${mode === 'snippet' ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-600'}`}><CodeBracketIcon className="mr-2 inline h-4 w-4" />Paste snippet</button>
              <button type="button" role="tab" aria-selected={mode === 'file'} onClick={() => setMode('file')} className={`flex-1 rounded-md px-3 py-2 text-sm font-medium ${mode === 'file' ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-600'}`}><ArrowUpTrayIcon className="mr-2 inline h-4 w-4" />Upload file</button>
            </div>

            {mode === 'snippet' ? (
              <div className="mt-4 space-y-4">
                <label className="block text-sm font-medium text-gray-700">Relative file path
                  <input value={filePath} onChange={(event) => setFilePath(event.target.value)} maxLength={512} className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 focus:border-indigo-500 focus:ring-indigo-500" />
                </label>
                <label className="block text-sm font-medium text-gray-700">Source code
                  <textarea value={code} onChange={(event) => setCode(event.target.value)} rows={22} spellCheck="false" className="mt-1 block w-full rounded-md border border-gray-300 bg-slate-950 p-4 font-mono text-sm text-slate-100 focus:border-indigo-500 focus:ring-indigo-500" />
                </label>
              </div>
            ) : (
              <div className="mt-4 rounded-xl border-2 border-dashed border-gray-300 px-6 py-12 text-center">
                <ArrowUpTrayIcon className="mx-auto h-10 w-10 text-gray-400" />
                <label className={`mt-3 inline-block rounded-md bg-white font-semibold ${settings ? 'cursor-pointer text-indigo-600 hover:text-indigo-700' : 'cursor-not-allowed text-gray-400'}`}>Choose a source file<input type="file" className="sr-only" accept=".py,.js,.jsx,.ts,.tsx,.java,.go,.rb,.php,.cs,.cpp,.c,.h" disabled={!settings} onChange={selectFile} /></label>
                <p className="mt-2 text-sm text-gray-500">{file ? `${file.name} · ${formatBytes(file.size)}` : settings ? `Maximum ${formatBytes(settings.maxFileBytes)} per tenant policy. Relative file name only.` : 'Loading tenant file-size limit…'}</p>
                {settingsError && <p className="mt-2 text-sm text-red-700" role="alert">{settingsError}</p>}
              </div>
            )}

            {errorMessage && <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">{errorMessage}</div>}
            <button type="button" onClick={analyze} disabled={working || (mode === 'file' && (!file || !settings))} className="mt-5 inline-flex items-center rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"><BeakerIcon className="mr-2 h-5 w-5" />{working ? 'Analyzing…' : 'Analyze securely'}</button>
          </section>

          <div>
            {scan ? (
              <SecurityAnalysisResults scan={scan} canManage={canManage} />
            ) : (
              <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center"><div><ShieldExclamationIcon className="mx-auto h-12 w-12 text-gray-300" /><h2 className="mt-4 font-semibold text-gray-900">Ready to analyze</h2><p className="mt-2 max-w-md text-sm text-gray-500">Results will show deterministic severity, CWE/OWASP mappings, redacted evidence, recommendations, and advisory AI help.</p></div></div>
            )}
          </div>
        </div>
        {rules ? (
          <SecurityRuleCatalog catalog={rules} />
        ) : rulesError ? (
          <div className="mt-8 rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700" role="alert">
            {rulesError}
          </div>
        ) : (
          <div className="mt-8 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-500" role="status">
            Loading deterministic rule catalog…
          </div>
        )}
      </div>
    </div>
  );
};

export default CodeSecurityAnalysis;
