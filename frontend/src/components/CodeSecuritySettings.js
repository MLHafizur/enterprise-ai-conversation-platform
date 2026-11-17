import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  CpuChipIcon,
  LockClosedIcon,
  ShieldCheckIcon,
  XCircleIcon
} from '@heroicons/react/24/outline';
import { toast } from 'react-toastify';

import { getApiErrorMessage } from '../api';
import {
  SECURITY_SEVERITIES,
  fetchCodeSecuritySettings,
  updateCodeSecuritySettings
} from '../codeSecurity';
import { formatBytes } from './SecurityPresentation';

const megabytesToBytes = (value) => Math.round(Number(value) * 1024 * 1024);
const bytesToMegabytes = (value) => value / 1024 / 1024;

const Toggle = ({ checked, onChange, disabled, label, description }) => (
  <label className={`flex items-start justify-between gap-4 rounded-lg border border-gray-200 p-4 ${disabled ? 'cursor-not-allowed bg-gray-50' : 'cursor-pointer bg-white'}`}>
    <span><span className="block text-sm font-medium text-gray-900">{label}</span><span className="mt-1 block text-xs text-gray-500">{description}</span></span>
    <span className="relative mt-0.5 inline-flex h-6 w-11 flex-none">
      <input type="checkbox" className="peer sr-only" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} />
      <span className="absolute inset-0 rounded-full bg-gray-300 transition peer-checked:bg-indigo-600 peer-focus:ring-2 peer-focus:ring-indigo-500 peer-focus:ring-offset-2 peer-disabled:opacity-50" />
      <span className="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5" />
    </span>
  </label>
);

export const SecuritySettingsForm = ({
  settings,
  canManage = false,
  saving = false,
  onSave = () => {}
}) => {
  const [draft, setDraft] = useState(() => ({
    ...settings,
    maxFileMegabytes: bytesToMegabytes(settings.maxFileBytes),
    maxRepositoryMegabytes: bytesToMegabytes(settings.maxRepositoryBytes)
  }));

  useEffect(() => {
    setDraft({
      ...settings,
      maxFileMegabytes: bytesToMegabytes(settings.maxFileBytes),
      maxRepositoryMegabytes: bytesToMegabytes(settings.maxRepositoryBytes)
    });
  }, [settings]);

  const update = (key, value) => setDraft((current) => ({ ...current, [key]: value }));
  const submit = (event) => {
    event.preventDefault();
    onSave({
      revision: draft.revision,
      blockSeverity: draft.blockSeverity,
      reviewSeverity: draft.reviewSeverity,
      maxFileBytes: megabytesToBytes(draft.maxFileMegabytes),
      maxRepositoryFiles: Number(draft.maxRepositoryFiles),
      maxRepositoryBytes: megabytesToBytes(draft.maxRepositoryMegabytes),
      autoScanPush: draft.autoScanPush,
      autoScanPullRequest: draft.autoScanPullRequest,
      aiSummaryEnabled: draft.aiSummaryEnabled
    });
  };

  return (
    <form onSubmit={submit} className="space-y-6">
      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm" aria-labelledby="gate-policy-heading">
        <div className="flex items-center gap-3"><div className="rounded-lg bg-red-50 p-2"><ShieldCheckIcon className="h-5 w-5 text-red-600" /></div><div><h2 id="gate-policy-heading" className="font-semibold text-gray-900">Security gate policy</h2><p className="text-sm text-gray-500">Deterministic thresholds control merge approval.</p></div></div>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <label className="text-sm font-medium text-gray-700">Block at or above<select disabled={!canManage} value={draft.blockSeverity} onChange={(event) => update('blockSeverity', event.target.value)} className="mt-1 block w-full rounded-md border-gray-300 px-3 py-2 disabled:bg-gray-100">{SECURITY_SEVERITIES.map((severity) => <option key={severity} value={severity}>{severity}</option>)}</select><span className="mt-1 block text-xs font-normal text-gray-500">Critical findings block by default.</span></label>
          <label className="text-sm font-medium text-gray-700">Review at or above<select disabled={!canManage} value={draft.reviewSeverity} onChange={(event) => update('reviewSeverity', event.target.value)} className="mt-1 block w-full rounded-md border-gray-300 px-3 py-2 capitalize disabled:bg-gray-100">{SECURITY_SEVERITIES.map((severity) => <option key={severity} value={severity}>{severity}</option>)}</select><span className="mt-1 block text-xs font-normal text-gray-500">Must be weaker than the block threshold.</span></label>
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm" aria-labelledby="scan-limits-heading">
        <div className="flex items-center gap-3"><div className="rounded-lg bg-sky-50 p-2"><CpuChipIcon className="h-5 w-5 text-sky-600" /></div><div><h2 id="scan-limits-heading" className="font-semibold text-gray-900">Resource limits</h2><p className="text-sm text-gray-500">Bound work before cloning or parsing untrusted source.</p></div></div>
        <div className="mt-5 grid gap-4 md:grid-cols-3">
          <label className="text-sm font-medium text-gray-700">Maximum file size (MB)<input disabled={!canManage} required type="number" min="0.001" max="10" step="0.001" value={draft.maxFileMegabytes} onChange={(event) => update('maxFileMegabytes', event.target.value)} className="mt-1 block w-full rounded-md border-gray-300 px-3 py-2 disabled:bg-gray-100" /><span className="mt-1 block text-xs font-normal text-gray-500">Current: {formatBytes(settings.maxFileBytes)}</span></label>
          <label className="text-sm font-medium text-gray-700">Maximum repository files<input disabled={!canManage} required type="number" min="1" max="100000" step="1" value={draft.maxRepositoryFiles} onChange={(event) => update('maxRepositoryFiles', event.target.value)} className="mt-1 block w-full rounded-md border-gray-300 px-3 py-2 disabled:bg-gray-100" /></label>
          <label className="text-sm font-medium text-gray-700">Maximum repository size (MB)<input disabled={!canManage} required type="number" min="0.001" max="1024" step="0.001" value={draft.maxRepositoryMegabytes} onChange={(event) => update('maxRepositoryMegabytes', event.target.value)} className="mt-1 block w-full rounded-md border-gray-300 px-3 py-2 disabled:bg-gray-100" /><span className="mt-1 block text-xs font-normal text-gray-500">Current: {formatBytes(settings.maxRepositoryBytes)}</span></label>
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm" aria-labelledby="automation-heading">
        <h2 id="automation-heading" className="font-semibold text-gray-900">Automation and advisory AI</h2>
        <p className="mt-1 text-sm text-gray-500">Webhook automation remains bounded by the same deterministic policy.</p>
        <div className="mt-5 grid gap-4 lg:grid-cols-3">
          <Toggle checked={draft.autoScanPush} onChange={(value) => update('autoScanPush', value)} disabled={!canManage} label="Auto-scan pushes" description="Queue a scan for signed push webhooks." />
          <Toggle checked={draft.autoScanPullRequest} onChange={(value) => update('autoScanPullRequest', value)} disabled={!canManage} label="Auto-scan pull requests" description="Compare trusted base and head refs." />
          <Toggle checked={draft.aiSummaryEnabled} onChange={(value) => update('aiSummaryEnabled', value)} disabled={!canManage} label="Advisory AI summaries" description="Send only bounded, redacted evidence." />
        </div>
      </section>

      <section className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div><p className="text-sm font-medium text-gray-900">Optimistic revision {settings.revision}</p><p className="mt-1 text-xs text-gray-500">A stale save is rejected instead of overwriting another administrator's policy.</p></div>
        {canManage ? <button type="submit" disabled={saving} className="inline-flex items-center rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"><CheckCircleIcon className="mr-2 h-5 w-5" />{saving ? 'Saving…' : 'Save policy'}</button> : <span className="inline-flex items-center rounded-full bg-gray-100 px-3 py-2 text-xs font-semibold text-gray-700"><LockClosedIcon className="mr-2 h-4 w-4" />Read only</span>}
      </section>
    </form>
  );
};

const CodeSecuritySettings = ({ canManage = false }) => {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMessage('');
    try {
      setSettings(await fetchCodeSecuritySettings());
    } catch (error) {
      setErrorMessage(getApiErrorMessage(error, 'Could not load security settings'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const save = async (draft) => {
    setSaving(true);
    try {
      const updated = await updateCodeSecuritySettings(draft);
      setSettings(updated);
      toast.success(`Security policy revision ${updated.revision} saved`);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not save the security policy. Refresh if another administrator updated it.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-8 flex items-start justify-between gap-4">
          <div><h1 className="text-2xl font-semibold text-gray-900">Security settings</h1><p className="mt-1 text-sm text-gray-600">Tenant policy for deterministic gates, bounded scans, webhooks, and advisory AI.</p></div>
          <button type="button" onClick={load} disabled={loading} className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 disabled:opacity-50"><ArrowPathIcon className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />Refresh</button>
        </div>
        {!canManage && <div className="mb-6 rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-800"><LockClosedIcon className="mr-2 inline h-5 w-5" />You can inspect this policy. Only tenant administrators can change it.</div>}
        {loading ? <div className="flex min-h-[320px] items-center justify-center" role="status"><div className="h-8 w-8 animate-spin rounded-full border-b-2 border-indigo-600" /></div> : errorMessage ? <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center" role="alert"><XCircleIcon className="mx-auto h-8 w-8 text-red-500" /><p className="mt-3 text-sm text-red-700">{errorMessage}</p><button type="button" onClick={load} className="mt-4 rounded-md bg-red-700 px-3 py-2 text-sm font-semibold text-white">Try again</button></div> : <SecuritySettingsForm settings={settings} canManage={canManage} saving={saving} onSave={save} />}
      </div>
    </div>
  );
};

export default CodeSecuritySettings;
