import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  ShieldCheckIcon,
  XCircleIcon
} from '@heroicons/react/24/outline';
import { format } from 'date-fns';

import { getApiErrorMessage } from '../api';
import { fetchInputPolicyGovernance } from '../governance';

const actionPresentation = {
  allow: {
    label: 'Allowed',
    badge: 'bg-emerald-100 text-emerald-800',
    icon: CheckCircleIcon,
    iconColor: 'text-emerald-600'
  },
  block: {
    label: 'Blocked',
    badge: 'bg-red-100 text-red-800',
    icon: XCircleIcon,
    iconColor: 'text-red-600'
  },
  escalate: {
    label: 'Escalated',
    badge: 'bg-amber-100 text-amber-800',
    icon: ExclamationTriangleIcon,
    iconColor: 'text-amber-600'
  }
};

const severityPresentation = {
  info: 'bg-sky-50 text-sky-700 border-sky-200',
  low: 'bg-gray-50 text-gray-700 border-gray-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  high: 'bg-orange-50 text-orange-700 border-orange-200',
  critical: 'bg-red-50 text-red-700 border-red-200'
};

const formatAssessmentTime = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown time' : format(date, 'MMM d, yyyy p');
};

const shortenId = (value) => `${value.slice(0, 8)}…`;

const SummaryCard = ({ label, value, Icon, iconClassName }) => (
  <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm font-medium text-gray-500">{label}</p>
        <p className="mt-2 text-3xl font-semibold text-gray-900">{value}</p>
      </div>
      <div className="rounded-lg bg-gray-50 p-3">
        <Icon className={`h-6 w-6 ${iconClassName}`} aria-hidden="true" />
      </div>
    </div>
  </div>
);

const DecisionCard = ({ assessment }) => {
  const presentation = actionPresentation[assessment.action];
  const Icon = presentation.icon;

  return (
    <article className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <Icon
            className={`mt-0.5 h-6 w-6 flex-none ${presentation.iconColor}`}
            aria-hidden="true"
          />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${presentation.badge}`}
              >
                {presentation.label}
              </span>
              <span className="text-xs text-gray-500">
                Risk {Math.round(assessment.riskScore * 100)}%
              </span>
            </div>
            <p className="mt-2 text-sm text-gray-600">
              Run{' '}
              <span className="font-mono text-gray-800" title={assessment.agentRunId}>
                {shortenId(assessment.agentRunId)}
              </span>
              {' · '}Conversation{' '}
              <span className="font-mono text-gray-800" title={assessment.conversationId}>
                {shortenId(assessment.conversationId)}
              </span>
            </p>
          </div>
        </div>
        <time
          dateTime={assessment.assessedAt}
          className="flex-none text-xs text-gray-500"
        >
          {formatAssessmentTime(assessment.assessedAt)}
        </time>
      </div>

      {assessment.findings.length === 0 ? (
        <p className="mt-4 text-sm text-gray-500">No policy findings.</p>
      ) : (
        <ul className="mt-4 space-y-2" aria-label="Safe policy findings">
          {assessment.findings.map((finding) => (
            <li
              key={finding.code}
              className={`rounded-lg border px-3 py-2 ${severityPresentation[finding.severity]}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide">
                  {finding.severity}
                </span>
                <span className="font-mono text-xs">{finding.code}</span>
              </div>
              <p className="mt-1 text-sm">{finding.safeMessage}</p>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-4 text-xs text-gray-400">
        Policy version {assessment.policyVersion}
      </p>
    </article>
  );
};

export const ComplianceContent = ({ snapshot }) => {
  const { counts, recent } = snapshot;

  return (
    <>
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Policy totals">
        <SummaryCard
          label="Total assessments"
          value={counts.total}
          Icon={ShieldCheckIcon}
          iconClassName="text-indigo-600"
        />
        <SummaryCard
          label="Allowed"
          value={counts.allow}
          Icon={CheckCircleIcon}
          iconClassName="text-emerald-600"
        />
        <SummaryCard
          label="Blocked"
          value={counts.block}
          Icon={XCircleIcon}
          iconClassName="text-red-600"
        />
        <SummaryCard
          label="Escalated"
          value={counts.escalate}
          Icon={ExclamationTriangleIcon}
          iconClassName="text-amber-600"
        />
      </section>

      <section className="mt-8" aria-labelledby="recent-decisions-heading">
        <div className="mb-4">
          <h2 id="recent-decisions-heading" className="text-lg font-semibold text-gray-900">
            Recent decisions
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Safe policy metadata only. User message content is never displayed here.
          </p>
        </div>

        {recent.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-300 bg-white px-6 py-12 text-center">
            <ShieldCheckIcon className="mx-auto h-10 w-10 text-gray-300" aria-hidden="true" />
            <h3 className="mt-3 text-sm font-semibold text-gray-900">No assessments yet</h3>
            <p className="mt-1 text-sm text-gray-500">
              Policy decisions will appear after this tenant submits chat turns.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {recent.map((assessment) => (
              <DecisionCard key={assessment.agentRunId} assessment={assessment} />
            ))}
          </div>
        )}
      </section>
    </>
  );
};

const ComplianceDashboard = () => {
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState('');

  const loadSnapshot = useCallback(async () => {
    setLoading(true);
    setErrorMessage('');

    try {
      setSnapshot(await fetchInputPolicyGovernance());
    } catch (error) {
      setSnapshot(null);
      setErrorMessage(
        getApiErrorMessage(error, 'Could not load governance information')
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Compliance overview</h1>
            <p className="mt-1 text-sm text-gray-600">
              Tenant-scoped input-policy outcomes for authorized reviewers.
            </p>
          </div>
          <button
            type="button"
            onClick={loadSnapshot}
            disabled={loading}
            className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ArrowPathIcon
              className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`}
              aria-hidden="true"
            />
            Refresh
          </button>
        </div>

        {loading ? (
          <div className="flex min-h-[320px] items-center justify-center" role="status">
            <div className="text-center">
              <div
                className="mx-auto h-8 w-8 animate-spin rounded-full border-b-2 border-indigo-600"
                aria-hidden="true"
              />
              <p className="mt-3 text-sm text-gray-500">Loading policy decisions…</p>
            </div>
          </div>
        ) : errorMessage ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-6 py-8 text-center" role="alert">
            <XCircleIcon className="mx-auto h-8 w-8 text-red-500" aria-hidden="true" />
            <h2 className="mt-3 text-sm font-semibold text-red-900">
              Governance data is unavailable
            </h2>
            <p className="mt-1 text-sm text-red-700">{errorMessage}</p>
            <button
              type="button"
              onClick={loadSnapshot}
              className="mt-4 rounded-md bg-red-700 px-3 py-2 text-sm font-medium text-white hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
            >
              Try again
            </button>
          </div>
        ) : (
          <ComplianceContent snapshot={snapshot} />
        )}
      </div>
    </div>
  );
};

export default ComplianceDashboard;
