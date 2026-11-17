import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ArrowPathIcon,
  ArrowsRightLeftIcon,
  CodeBracketSquareIcon,
  FolderPlusIcon,
  PlayIcon,
  ShieldCheckIcon,
  XCircleIcon
} from '@heroicons/react/24/outline';
import { format } from 'date-fns';
import { toast } from 'react-toastify';

import { getApiErrorMessage } from '../api';
import {
  createCodeRepository,
  fetchCodeRepositories,
  fetchSecurityScan,
  fetchSecurityScans,
  queuePullRequestScan,
  queueRepositoryScan
} from '../codeSecurity';
import { useWebSocket } from '../hooks/useWebSocket';
import { SecurityAnalysisResults } from './CodeSecurityAnalysis';
import { SecurityBadge } from './SecurityPresentation';

const latestScansByRepository = (scans) => {
  const latest = new Map();
  scans.forEach((scan) => {
    if (!scan.repositoryId) return;
    const current = latest.get(scan.repositoryId);
    if (!current || new Date(scan.createdAt) > new Date(current.createdAt)) {
      latest.set(scan.repositoryId, scan);
    }
  });
  return latest;
};

const AddRepositoryForm = ({ onCreated, onCancel }) => {
  const [displayName, setDisplayName] = useState('');
  const [cloneUrl, setCloneUrl] = useState('');
  const [defaultBranch, setDefaultBranch] = useState('main');
  const [working, setWorking] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const submit = async (event) => {
    event.preventDefault();
    setWorking(true);
    setErrorMessage('');
    try {
      const repository = await createCodeRepository({
        displayName,
        cloneUrl,
        defaultBranch
      });
      toast.success('Repository added');
      onCreated(repository);
    } catch (error) {
      setErrorMessage(getApiErrorMessage(error, 'Could not add the repository'));
    } finally {
      setWorking(false);
    }
  };

  return (
    <form onSubmit={submit} className="mb-6 rounded-xl border border-indigo-200 bg-indigo-50 p-5">
      <div className="flex items-start justify-between gap-4">
        <div><h2 className="font-semibold text-indigo-950">Add an approved repository</h2><p className="mt-1 text-sm text-indigo-800">Only HTTPS repositories on the server allowlist can be registered.</p></div>
        <button type="button" onClick={onCancel} className="text-sm font-medium text-indigo-700">Cancel</button>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <label className="text-sm font-medium text-indigo-950">Display name<input required value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={128} className="mt-1 block w-full rounded-md border-indigo-300 px-3 py-2" placeholder="Payments API" /></label>
        <label className="text-sm font-medium text-indigo-950 md:col-span-2">HTTPS clone URL<input required type="url" value={cloneUrl} onChange={(event) => setCloneUrl(event.target.value)} maxLength={2048} className="mt-1 block w-full rounded-md border-indigo-300 px-3 py-2" placeholder="https://github.com/company/payments-api.git" /></label>
        <label className="text-sm font-medium text-indigo-950">Default branch<input required value={defaultBranch} onChange={(event) => setDefaultBranch(event.target.value)} maxLength={255} className="mt-1 block w-full rounded-md border-indigo-300 px-3 py-2" /></label>
      </div>
      {errorMessage && <p className="mt-3 text-sm text-red-700" role="alert">{errorMessage}</p>}
      <button type="submit" disabled={working} className="mt-4 rounded-md bg-indigo-700 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-800 disabled:opacity-50">{working ? 'Adding…' : 'Add repository'}</button>
    </form>
  );
};

const RepositoryActions = ({ repository, onQueued }) => {
  const [branch, setBranch] = useState(repository.defaultBranch);
  const [baseBranch, setBaseBranch] = useState(repository.defaultBranch);
  const [headBranch, setHeadBranch] = useState('feature/security-review');
  const [showPullRequest, setShowPullRequest] = useState(false);
  const [working, setWorking] = useState('');

  const scanBranch = async () => {
    setWorking('branch');
    try {
      const scan = await queueRepositoryScan(repository.id, branch);
      toast.success('Repository scan queued');
      onQueued(scan);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not queue the repository scan'));
    } finally {
      setWorking('');
    }
  };

  const scanPullRequest = async (event) => {
    event.preventDefault();
    setWorking('pull-request');
    try {
      const scan = await queuePullRequestScan(repository.id, {
        baseBranch,
        headBranch
      });
      toast.success('Pull-request scan queued');
      setShowPullRequest(false);
      onQueued(scan);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not queue the pull-request scan'));
    } finally {
      setWorking('');
    }
  };

  return (
    <div className="mt-4 border-t border-gray-100 pt-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs font-medium text-gray-600">Branch<input value={branch} onChange={(event) => setBranch(event.target.value)} className="mt-1 block w-48 rounded-md border-gray-300 px-3 py-2 text-sm" /></label>
        <button type="button" onClick={scanBranch} disabled={Boolean(working)} className="inline-flex items-center rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"><PlayIcon className="mr-2 h-4 w-4" />{working === 'branch' ? 'Queuing…' : 'Scan branch'}</button>
        <button type="button" onClick={() => setShowPullRequest((current) => !current)} disabled={Boolean(working)} className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50"><ArrowsRightLeftIcon className="mr-2 h-4 w-4" />Scan pull request</button>
      </div>
      {showPullRequest && (
        <form onSubmit={scanPullRequest} className="mt-4 grid gap-3 rounded-lg bg-gray-50 p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label className="text-xs font-medium text-gray-600">Base branch<input required value={baseBranch} onChange={(event) => setBaseBranch(event.target.value)} className="mt-1 block w-full rounded-md border-gray-300 px-3 py-2 text-sm" /></label>
          <label className="text-xs font-medium text-gray-600">Head branch<input required value={headBranch} onChange={(event) => setHeadBranch(event.target.value)} className="mt-1 block w-full rounded-md border-gray-300 px-3 py-2 text-sm" /></label>
          <button type="submit" disabled={Boolean(working)} className="rounded-md bg-gray-900 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{working === 'pull-request' ? 'Queuing…' : 'Queue PR scan'}</button>
        </form>
      )}
    </div>
  );
};

export const RepositorySecurityList = ({
  repositories,
  scans,
  canScan = false,
  canManage = false,
  onQueued = () => {},
  onOpenScan = () => {}
}) => {
  const latestByRepository = useMemo(() => latestScansByRepository(scans), [scans]);

  if (repositories.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white px-6 py-14 text-center">
        <FolderPlusIcon className="mx-auto h-10 w-10 text-gray-300" />
        <h2 className="mt-3 font-semibold text-gray-900">No repositories registered</h2>
        <p className="mt-1 text-sm text-gray-500">An administrator can add an approved HTTPS repository.</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      {repositories.map((repository) => {
        const latestScan = latestByRepository.get(repository.id);
        return (
          <article key={repository.id} className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2"><CodeBracketSquareIcon className="h-5 w-5 text-indigo-600" /><h2 className="truncate font-semibold text-gray-900">{repository.displayName}</h2></div>
                <p className="mt-2 truncate font-mono text-xs text-gray-500" title={repository.cloneUrl}>{repository.cloneUrl}</p>
                <p className="mt-1 text-xs text-gray-500">{repository.provider} · default {repository.defaultBranch}</p>
              </div>
              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${repository.enabled ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-100 text-gray-700'}`}>{repository.enabled ? 'Enabled' : 'Disabled'}</span>
            </div>

            <div className="mt-4 rounded-lg bg-gray-50 p-4">
              {latestScan ? (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><p className="text-xs font-medium text-gray-500">Latest scan</p><p className="mt-1 text-sm text-gray-800">{format(new Date(latestScan.createdAt), 'MMM d, yyyy p')} · {latestScan.totalFindings} findings</p><button type="button" onClick={() => onOpenScan(latestScan.id)} className="mt-2 text-xs font-semibold text-indigo-700 hover:text-indigo-900">View findings and review</button></div>
                  <div className="flex items-center gap-2"><SecurityBadge value={latestScan.status} kind="status" />{latestScan.gateDecision && <SecurityBadge value={latestScan.gateDecision} kind="gate" />}</div>
                </div>
              ) : (
                <p className="text-sm text-gray-500">This repository has not been scanned yet.</p>
              )}
            </div>
            {(canScan || canManage) && repository.enabled && <RepositoryActions repository={repository} onQueued={onQueued} />}
          </article>
        );
      })}
    </div>
  );
};

const SecurityRepositories = ({ canScan = false, canManage = false }) => {
  const { subscribe } = useWebSocket();
  const [repositories, setRepositories] = useState([]);
  const [scans, setScans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [selectedScan, setSelectedScan] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const openScan = useCallback(async (scanId) => {
    setDetailLoading(true);
    try {
      setSelectedScan(await fetchSecurityScan(scanId));
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not load the scan details'));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    setErrorMessage('');
    try {
      const [repositoryItems, scanItems] = await Promise.all([
        fetchCodeRepositories(),
        fetchSecurityScans()
      ]);
      setRepositories(repositoryItems);
      setScans(scanItems);
    } catch (error) {
      setErrorMessage(getApiErrorMessage(error, 'Could not load repositories'));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(
    () => subscribe((message) => {
      if (message?.type === 'code_scan.status.v1') void load({ quiet: true });
    }),
    [load, subscribe]
  );

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8 flex items-start justify-between gap-4">
          <div><h1 className="text-2xl font-semibold text-gray-900">Repositories</h1><p className="mt-1 text-sm text-gray-600">Bounded, isolated scans for approved tenant repositories and pull requests.</p></div>
          <div className="flex gap-2">
            <button type="button" onClick={() => load()} disabled={loading} aria-label="Refresh repositories" className="rounded-md border border-gray-300 bg-white p-2 text-gray-600 hover:bg-gray-50 disabled:opacity-50"><ArrowPathIcon className={`h-5 w-5 ${loading ? 'animate-spin' : ''}`} /></button>
            {canManage && <button type="button" onClick={() => setShowAddForm(true)} className="inline-flex items-center rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700"><FolderPlusIcon className="mr-2 h-5 w-5" />Add repository</button>}
          </div>
        </div>
        {!canManage && <div className="mb-6 rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-800"><ShieldCheckIcon className="mr-2 inline h-5 w-5" />You can scan approved repositories and pull requests. Repository registration requires the admin role.</div>}
        {showAddForm && canManage && <AddRepositoryForm onCancel={() => setShowAddForm(false)} onCreated={(repository) => { setRepositories((current) => [...current, repository]); setShowAddForm(false); }} />}
        {errorMessage && <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert"><XCircleIcon className="mr-2 inline h-5 w-5" />{errorMessage}</div>}
        {detailLoading && <p className="mb-6 text-sm text-gray-500" role="status">Loading scan details…</p>}
        {selectedScan && (
          <section className="mb-8 rounded-xl border border-indigo-200 bg-indigo-50 p-5">
            <div className="mb-5 flex justify-end"><button type="button" onClick={() => setSelectedScan(null)} className="text-sm font-semibold text-indigo-700">Close scan details</button></div>
            <SecurityAnalysisResults scan={selectedScan} canManage={canManage} />
          </section>
        )}
        {loading ? <div className="flex min-h-[320px] items-center justify-center" role="status"><div className="h-8 w-8 animate-spin rounded-full border-b-2 border-indigo-600" /></div> : <RepositorySecurityList repositories={repositories} scans={scans} canScan={canScan} canManage={canManage} onQueued={(scan) => setScans((current) => [scan, ...current])} onOpenScan={openScan} />}
      </div>
    </div>
  );
};

export default SecurityRepositories;
