import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowPathIcon,
  BugAntIcon,
  CodeBracketSquareIcon,
  ExclamationTriangleIcon,
  FolderOpenIcon,
  ShieldCheckIcon,
  XCircleIcon
} from '@heroicons/react/24/outline';
import { format } from 'date-fns';

import { getApiErrorMessage } from '../api';
import { fetchSecurityDashboard } from '../codeSecurity';
import { useWebSocket } from '../hooks/useWebSocket';
import {
  SecurityBadge,
  severityStyles
} from './SecurityPresentation';

const formatTime = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown time' : format(date, 'MMM d, p');
};

const SummaryCard = ({ label, value, Icon, iconClassName }) => (
  <article className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-sm font-medium text-gray-500">{label}</p>
        <p className="mt-2 text-3xl font-semibold text-gray-900">{value}</p>
      </div>
      <div className="rounded-lg bg-gray-50 p-3">
        <Icon className={`h-6 w-6 ${iconClassName}`} aria-hidden="true" />
      </div>
    </div>
  </article>
);

const severityOrder = ['critical', 'high', 'medium', 'low', 'info'];
const trendBarStyles = Object.freeze({
  critical: 'bg-red-600',
  high: 'bg-orange-500',
  medium: 'bg-amber-400',
  low: 'bg-emerald-500',
  info: 'bg-sky-500'
});

const SeverityOverview = ({ counts }) => {
  const maximum = Math.max(...severityOrder.map((key) => counts[key]), 1);
  return (
    <section
      className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
      aria-labelledby="security-severity-heading"
    >
      <h2 id="security-severity-heading" className="font-semibold text-gray-900">
        Severity distribution
      </h2>
      <p className="mt-1 text-sm text-gray-500">
        Deterministic findings across all tenant scans.
      </p>
      <div className="mt-5 space-y-4">
        {severityOrder.map((severity) => (
          <div key={severity}>
            <div className="mb-1 flex justify-between text-sm">
              <span className="capitalize text-gray-700">{severity}</span>
              <span className="font-semibold text-gray-900">{counts[severity]}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-gray-100">
              <div
                className={`h-full rounded-full border ${severityStyles[severity]}`}
                style={{ width: `${Math.max((counts[severity] / maximum) * 100, counts[severity] ? 3 : 0)}%` }}
                aria-hidden="true"
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};

export const SeverityTrendChart = ({ trend }) => {
  const points = trend.slice(-7);
  const maximum = Math.max(...points.map((point) => point.total), 1);
  return (
    <figure className="mt-5" aria-labelledby="severity-trend-chart-caption">
      <figcaption id="severity-trend-chart-caption" className="sr-only">
        Stacked daily security findings by severity. Exact values follow in the table.
      </figcaption>
      <ul className="mb-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-gray-600" aria-label="Severity legend">
        {severityOrder.map((severity) => (
          <li key={severity} className="inline-flex items-center gap-1.5 capitalize">
            <span className={`h-2.5 w-2.5 rounded-sm ${trendBarStyles[severity]}`} aria-hidden="true" />
            {severity}
          </li>
        ))}
      </ul>
      <div
        className="space-y-3"
        role="img"
        aria-label={`Severity trend chart for ${points.length} ${points.length === 1 ? 'day' : 'days'}`}
      >
        {points.map((point) => (
          <div key={point.date} className="grid grid-cols-[6rem_minmax(12rem,1fr)_2.5rem] items-center gap-3" aria-hidden="true">
            <span className="text-xs font-medium text-gray-600">{point.date}</span>
            <div className="h-5 overflow-hidden rounded bg-gray-100">
              <div
                className="flex h-full overflow-hidden rounded"
                style={{ width: `${(point.total / maximum) * 100}%` }}
              >
                {severityOrder.map((severity) => point[severity] > 0 && (
                  <span
                    key={severity}
                    className={trendBarStyles[severity]}
                    style={{ width: `${(point[severity] / Math.max(point.total, 1)) * 100}%` }}
                    title={`${severity}: ${point[severity]}`}
                  />
                ))}
              </div>
            </div>
            <span className="text-right text-xs font-semibold text-gray-800">{point.total}</span>
          </div>
        ))}
      </div>
    </figure>
  );
};

const TrendOverview = ({ trend }) => (
  <section
    className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
    aria-labelledby="security-trend-heading"
  >
    <h2 id="security-trend-heading" className="font-semibold text-gray-900">
      Finding trend
    </h2>
    <p className="mt-1 text-sm text-gray-500">
      Daily severity totals from durable scan history.
    </p>
    {trend.length === 0 ? (
      <p className="mt-8 text-center text-sm text-gray-500">No trend data yet.</p>
    ) : (
      <>
        <SeverityTrendChart trend={trend} />
        <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[520px] text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="pb-2 font-medium">Date</th>
              <th className="pb-2 text-right font-medium">Critical</th>
              <th className="pb-2 text-right font-medium">High</th>
              <th className="pb-2 text-right font-medium">Medium</th>
              <th className="pb-2 text-right font-medium">Low</th>
              <th className="pb-2 text-right font-medium">Info</th>
              <th className="pb-2 text-right font-medium">Total</th>
            </tr>
          </thead>
          <tbody>
            {trend.slice(-7).map((point) => (
              <tr key={point.date} className="border-b border-gray-100 last:border-0">
                <td className="py-2 text-gray-700">{point.date}</td>
                <td className="py-2 text-right font-medium text-red-700">{point.critical}</td>
                <td className="py-2 text-right font-medium text-orange-700">{point.high}</td>
                <td className="py-2 text-right font-medium text-amber-700">{point.medium}</td>
                <td className="py-2 text-right font-medium text-emerald-700">{point.low}</td>
                <td className="py-2 text-right font-medium text-sky-700">{point.info}</td>
                <td className="py-2 text-right font-semibold text-gray-900">{point.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </>
    )}
  </section>
);

export const SecurityDashboardContent = ({ snapshot }) => {
  const { counts, recentScans, topVulnerabilities, trend } = snapshot;
  return (
    <>
      <section
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
        aria-label="Security scan totals"
      >
        <SummaryCard
          label="Total scans"
          value={counts.totalScans}
          Icon={ShieldCheckIcon}
          iconClassName="text-indigo-600"
        />
        <SummaryCard
          label="Vulnerabilities"
          value={counts.vulnerabilitiesFound}
          Icon={BugAntIcon}
          iconClassName="text-red-600"
        />
        <SummaryCard
          label="Files analyzed"
          value={counts.filesScanned}
          Icon={CodeBracketSquareIcon}
          iconClassName="text-sky-600"
        />
        <SummaryCard
          label="Repositories"
          value={counts.repositories}
          Icon={FolderOpenIcon}
          iconClassName="text-emerald-600"
        />
      </section>

      <section className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Security gate decisions">
        <SummaryCard label="Approved" value={counts.approved} Icon={ShieldCheckIcon} iconClassName="text-emerald-600" />
        <SummaryCard label="Review required" value={counts.reviewRequired} Icon={ExclamationTriangleIcon} iconClassName="text-amber-600" />
        <SummaryCard label="Blocked" value={counts.blocked} Icon={XCircleIcon} iconClassName="text-red-600" />
        <SummaryCard label="Failed" value={counts.failed} Icon={XCircleIcon} iconClassName="text-gray-600" />
      </section>

      <div className="mt-8 grid grid-cols-1 gap-6 xl:grid-cols-2">
        <SeverityOverview counts={counts} />
        <TrendOverview trend={trend} />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 xl:grid-cols-2">
        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm" aria-labelledby="recent-code-scans-heading">
          <h2 id="recent-code-scans-heading" className="font-semibold text-gray-900">Recent scans</h2>
          <p className="mt-1 text-sm text-gray-500">Realtime-safe status and redacted summaries.</p>
          {recentScans.length === 0 ? (
            <p className="py-10 text-center text-sm text-gray-500">No scans have been submitted.</p>
          ) : (
            <ul className="mt-4 divide-y divide-gray-100">
              {recentScans.map((scan) => (
                <li key={scan.id} className="flex items-center justify-between gap-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">{scan.sourceLabel}</p>
                    <p className="mt-1 text-xs text-gray-500">
                      {formatTime(scan.createdAt)} · {scan.totalFindings} findings
                    </p>
                  </div>
                  <div className="flex flex-none items-center gap-2">
                    <SecurityBadge value={scan.status} kind="status" />
                    {scan.gateDecision && <SecurityBadge value={scan.gateDecision} kind="gate" />}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm" aria-labelledby="top-vulnerabilities-heading">
          <h2 id="top-vulnerabilities-heading" className="font-semibold text-gray-900">Top vulnerabilities</h2>
          <p className="mt-1 text-sm text-gray-500">Most frequent deterministic rules.</p>
          {topVulnerabilities.length === 0 ? (
            <p className="py-10 text-center text-sm text-gray-500">No vulnerabilities found.</p>
          ) : (
            <ol className="mt-4 divide-y divide-gray-100">
              {topVulnerabilities.map((item) => (
                <li key={item.ruleId} className="flex items-center justify-between gap-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">{item.title}</p>
                    <p className="mt-1 font-mono text-xs text-gray-500">
                      {item.ruleId}{item.cweId ? ` · ${item.cweId}` : ''}
                    </p>
                  </div>
                  <div className="flex flex-none items-center gap-2">
                    <SecurityBadge value={item.severity} />
                    <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">{item.count}</span>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </>
  );
};

const SecurityDashboard = () => {
  const { subscribe } = useWebSocket();
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState('');

  const loadDashboard = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    setErrorMessage('');
    try {
      setSnapshot(await fetchSecurityDashboard());
    } catch (error) {
      if (!quiet) setSnapshot(null);
      setErrorMessage(getApiErrorMessage(error, 'Could not load code security dashboard'));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  useEffect(
    () =>
      subscribe((message) => {
        if (message?.type === 'code_scan.status.v1') {
          void loadDashboard({ quiet: true });
        }
      }),
    [loadDashboard, subscribe]
  );

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Security dashboard</h1>
            <p className="mt-1 text-sm text-gray-600">Tenant-scoped scan health, gates, and vulnerability trends.</p>
          </div>
          <button type="button" onClick={() => loadDashboard()} disabled={loading} className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50">
            <ArrowPathIcon className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
            Refresh
          </button>
        </div>

        {loading ? (
          <div className="flex min-h-[320px] items-center justify-center" role="status">
            <div className="text-center"><div className="mx-auto h-8 w-8 animate-spin rounded-full border-b-2 border-indigo-600" /><p className="mt-3 text-sm text-gray-500">Loading security posture…</p></div>
          </div>
        ) : errorMessage ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-6 py-8 text-center" role="alert">
            <XCircleIcon className="mx-auto h-8 w-8 text-red-500" aria-hidden="true" />
            <h2 className="mt-3 text-sm font-semibold text-red-900">Security dashboard is unavailable</h2>
            <p className="mt-1 text-sm text-red-700">{errorMessage}</p>
            <button type="button" onClick={() => loadDashboard()} className="mt-4 rounded-md bg-red-700 px-3 py-2 text-sm font-medium text-white">Try again</button>
          </div>
        ) : (
          <SecurityDashboardContent snapshot={snapshot} />
        )}
      </div>
    </div>
  );
};

export default SecurityDashboard;
