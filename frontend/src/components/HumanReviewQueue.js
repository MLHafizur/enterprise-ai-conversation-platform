import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClipboardDocumentCheckIcon,
  ExclamationTriangleIcon,
  XCircleIcon
} from '@heroicons/react/24/outline';
import { format } from 'date-fns';

import { getApiErrorMessage } from '../api';
import {
  fetchHumanReview,
  fetchHumanReviews,
  resolveHumanReview
} from '../reviews';

const severityClasses = {
  info: 'border-sky-200 bg-sky-50 text-sky-800',
  low: 'border-gray-200 bg-gray-50 text-gray-800',
  medium: 'border-amber-200 bg-amber-50 text-amber-900',
  high: 'border-orange-200 bg-orange-50 text-orange-900',
  critical: 'border-red-200 bg-red-50 text-red-900'
};

const formatReviewTime = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown time' : format(date, 'MMM d, yyyy p');
};

const shortenId = (value) => `${value.slice(0, 8)}…`;

const statusLabel = (status) => (status === 'resolved' ? 'Resolved' : 'Pending');

const resolutionLabel = (resolution) =>
  resolution === 'dismissed' ? 'Dismissed as a false positive' : 'Policy decision confirmed';

export const ReviewQueueList = ({
  items,
  loading,
  errorMessage,
  selectedAgentRunId,
  onSelectReview,
  onRetry,
  disabled = false
}) => {
  if (loading) {
    return (
      <div className="flex min-h-[240px] items-center justify-center" role="status">
        <div className="text-center">
          <div
            className="mx-auto h-7 w-7 animate-spin rounded-full border-b-2 border-indigo-600"
            aria-hidden="true"
          />
          <p className="mt-3 text-sm text-gray-500">Loading review queue…</p>
        </div>
      </div>
    );
  }

  if (errorMessage) {
    return (
      <div className="m-4 rounded-lg border border-red-200 bg-red-50 p-5 text-center" role="alert">
        <XCircleIcon className="mx-auto h-7 w-7 text-red-500" aria-hidden="true" />
        <p className="mt-2 text-sm font-semibold text-red-900">Review queue unavailable</p>
        <p className="mt-1 text-sm text-red-700">{errorMessage}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md bg-red-700 px-3 py-2 text-sm font-medium text-white hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
        >
          Try again
        </button>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="px-5 py-12 text-center">
        <ClipboardDocumentCheckIcon
          className="mx-auto h-9 w-9 text-gray-300"
          aria-hidden="true"
        />
        <p className="mt-3 text-sm font-semibold text-gray-900">No reviews in this view</p>
        <p className="mt-1 text-sm text-gray-500">
          New escalations will appear here after policy evaluation.
        </p>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-gray-200" aria-label="Human review cases">
      {items.map((item) => {
        const selected = item.agentRunId === selectedAgentRunId;
        return (
          <li key={item.agentRunId}>
            <button
              type="button"
              onClick={() => onSelectReview(item.agentRunId)}
              disabled={disabled}
              aria-current={selected ? 'true' : undefined}
              className={`w-full px-5 py-4 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 ${
                selected ? 'bg-indigo-50' : 'bg-white hover:bg-gray-50'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <span
                  className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                    item.status === 'resolved'
                      ? 'bg-emerald-100 text-emerald-800'
                      : 'bg-amber-100 text-amber-900'
                  }`}
                >
                  {statusLabel(item.status)}
                </span>
                <span className="text-xs text-gray-500">
                  Risk {Math.round(item.riskScore * 100)}%
                </span>
              </div>
              <p className="mt-3 line-clamp-2 text-sm text-gray-800">
                {item.findings[0].safeMessage}
              </p>
              <div className="mt-3 flex items-center justify-between gap-3 text-xs text-gray-500">
                <span className="font-mono" title={item.agentRunId}>
                  Run {shortenId(item.agentRunId)}
                </span>
                <time dateTime={item.createdAt}>{formatReviewTime(item.createdAt)}</time>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
};

export const ReviewDetail = ({
  review,
  loading,
  errorMessage,
  resolution,
  onResolutionChange,
  onResolve,
  resolving,
  onRetry
}) => {
  const headingRef = useRef(null);

  useEffect(() => {
    if (review) {
      headingRef.current?.focus();
    }
  }, [review]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center" role="status">
        <p className="text-sm text-gray-500">Loading review details…</p>
      </div>
    );
  }

  if (errorMessage) {
    return (
      <div className="m-6 rounded-lg border border-red-200 bg-red-50 p-6 text-center" role="alert">
        <XCircleIcon className="mx-auto h-7 w-7 text-red-500" aria-hidden="true" />
        <p className="mt-2 text-sm font-semibold text-red-900">Review details unavailable</p>
        <p className="mt-1 text-sm text-red-700">{errorMessage}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md bg-red-700 px-3 py-2 text-sm font-medium text-white hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
        >
          Try again
        </button>
      </div>
    );
  }

  if (!review) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <div>
          <ClipboardDocumentCheckIcon
            className="mx-auto h-10 w-10 text-gray-300"
            aria-hidden="true"
          />
          <h3 className="mt-3 text-sm font-semibold text-gray-900">Select a review</h3>
          <p className="mt-1 text-sm text-gray-500">
            Choose a case to inspect its policy findings and original input.
          </p>
        </div>
      </div>
    );
  }

  return (
    <article className="h-full overflow-y-auto p-6" aria-labelledby="review-detail-heading">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3
            id="review-detail-heading"
            ref={headingRef}
            tabIndex="-1"
            className="text-lg font-semibold text-gray-900 focus:outline-none"
          >
            Human review details
          </h3>
          <p className="mt-1 text-sm text-gray-500">
            Exact message association: <span className="font-mono">{shortenId(review.inputMessageId)}</span>
          </p>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
            review.status === 'resolved'
              ? 'bg-emerald-100 text-emerald-800'
              : 'bg-amber-100 text-amber-900'
          }`}
        >
          {statusLabel(review.status)}
        </span>
      </div>

      <dl className="mt-6 grid grid-cols-1 gap-4 rounded-lg border border-gray-200 bg-gray-50 p-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">Risk score</dt>
          <dd className="mt-1 text-sm font-semibold text-gray-900">
            {Math.round(review.riskScore * 100)}%
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">Policy version</dt>
          <dd className="mt-1 font-mono text-sm text-gray-900">{review.policyVersion}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">Conversation</dt>
          <dd className="mt-1 font-mono text-sm text-gray-900" title={review.conversationId}>
            {shortenId(review.conversationId)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">Created</dt>
          <dd className="mt-1 text-sm text-gray-900">
            <time dateTime={review.createdAt}>{formatReviewTime(review.createdAt)}</time>
          </dd>
        </div>
      </dl>

      <section className="mt-6" aria-labelledby="review-findings-heading">
        <h4 id="review-findings-heading" className="text-sm font-semibold text-gray-900">
          Safe policy findings
        </h4>
        <ul className="mt-3 space-y-2">
          {review.findings.map((finding) => (
            <li
              key={finding.code}
              className={`rounded-lg border px-4 py-3 ${severityClasses[finding.severity]}`}
            >
              <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-wide">
                <span>{finding.severity}</span>
                <span className="font-mono normal-case tracking-normal">{finding.code}</span>
              </div>
              <p className="mt-1 text-sm">{finding.safeMessage}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-6" aria-labelledby="review-input-heading">
        <div className="flex items-center gap-2">
          <ExclamationTriangleIcon className="h-5 w-5 text-amber-600" aria-hidden="true" />
          <h4 id="review-input-heading" className="text-sm font-semibold text-gray-900">
            Original user input
          </h4>
        </div>
        <p className="mt-1 text-xs text-gray-500">
          Sensitive content is shown only to authorized tenant reviewers.
        </p>
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="whitespace-pre-wrap break-words text-sm text-gray-900">
            {review.inputContent}
          </p>
        </div>
      </section>

      {review.status === 'resolved' ? (
        <section className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50 p-4" aria-label="Resolution">
          <div className="flex items-start gap-3">
            <CheckCircleIcon className="h-5 w-5 flex-none text-emerald-600" aria-hidden="true" />
            <div>
              <p className="text-sm font-semibold text-emerald-900">
                {resolutionLabel(review.resolution)}
              </p>
              <p className="mt-1 text-sm text-emerald-800">
                Resolved <time dateTime={review.resolvedAt}>{formatReviewTime(review.resolvedAt)}</time>
              </p>
            </div>
          </div>
        </section>
      ) : (
        <form onSubmit={onResolve} className="mt-6 rounded-lg border border-gray-200 p-4">
          <fieldset disabled={resolving}>
            <legend className="text-sm font-semibold text-gray-900">Resolve this review</legend>
            <p className="mt-1 text-sm font-medium text-red-700">
              Resolution records a reviewer decision. It never resends this input to the AI.
            </p>
            <div className="mt-4 space-y-3">
              <label className="flex items-start gap-3">
                <input
                  type="radio"
                  name="review-resolution"
                  value="confirmed"
                  checked={resolution === 'confirmed'}
                  onChange={(event) => onResolutionChange(event.target.value)}
                  className="mt-1 h-4 w-4 border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                <span>
                  <span className="block text-sm font-medium text-gray-900">Confirm policy decision</span>
                  <span className="block text-xs text-gray-500">Keep the escalation as a valid policy outcome.</span>
                </span>
              </label>
              <label className="flex items-start gap-3">
                <input
                  type="radio"
                  name="review-resolution"
                  value="dismissed"
                  checked={resolution === 'dismissed'}
                  onChange={(event) => onResolutionChange(event.target.value)}
                  className="mt-1 h-4 w-4 border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                <span>
                  <span className="block text-sm font-medium text-gray-900">Dismiss as false positive</span>
                  <span className="block text-xs text-gray-500">Record that this escalation did not require intervention.</span>
                </span>
              </label>
            </div>
            <button
              type="submit"
              disabled={!resolution || resolving}
              className="mt-5 inline-flex items-center rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {resolving ? 'Saving resolution…' : 'Save resolution'}
            </button>
          </fieldset>
        </form>
      )}
    </article>
  );
};

const HumanReviewQueue = ({ selectedAgentRunId, onSelectReview }) => {
  const [statusFilter, setStatusFilter] = useState('pending');
  const [items, setItems] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listRefreshing, setListRefreshing] = useState(false);
  const [listError, setListError] = useState('');
  const [review, setReview] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [resolution, setResolution] = useState('');
  const [resolving, setResolving] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const listRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const resolutionRequestRef = useRef(0);
  const selectedAgentRunIdRef = useRef(selectedAgentRunId);
  selectedAgentRunIdRef.current = selectedAgentRunId;

  const loadList = useCallback(async (background = false) => {
    const requestId = listRequestRef.current + 1;
    listRequestRef.current = requestId;
    if (background) {
      setListRefreshing(true);
    } else {
      setListLoading(true);
      setItems([]);
    }
    setListError('');

    try {
      const nextItems = await fetchHumanReviews({ status: statusFilter });
      if (listRequestRef.current === requestId) {
        setItems(nextItems);
      }
    } catch (error) {
      if (listRequestRef.current === requestId) {
        setListError(getApiErrorMessage(error, 'Could not load human reviews'));
      }
    } finally {
      if (listRequestRef.current === requestId) {
        setListLoading(false);
        setListRefreshing(false);
      }
    }
  }, [statusFilter]);

  const loadDetail = useCallback(async () => {
    const targetAgentRunId = selectedAgentRunId;
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    setResolution('');
    setDetailError('');

    if (!targetAgentRunId) {
      setReview(null);
      setDetailLoading(false);
      return;
    }

    setReview(null);
    setDetailLoading(true);
    try {
      const nextReview = await fetchHumanReview(targetAgentRunId);
      if (
        detailRequestRef.current === requestId &&
        selectedAgentRunIdRef.current === targetAgentRunId
      ) {
        setReview(nextReview);
      }
    } catch (error) {
      if (
        detailRequestRef.current === requestId &&
        selectedAgentRunIdRef.current === targetAgentRunId
      ) {
        setDetailError(getApiErrorMessage(error, 'Could not load review details'));
      }
    } finally {
      if (detailRequestRef.current === requestId) {
        setDetailLoading(false);
      }
    }
  }, [selectedAgentRunId]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    resolutionRequestRef.current += 1;
    setResolving(false);
  }, [selectedAgentRunId]);

  const refreshAll = useCallback(async () => {
    setAnnouncement('');
    await Promise.all([
      loadList(true),
      selectedAgentRunId ? loadDetail() : Promise.resolve()
    ]);
    setAnnouncement('Review data refreshed.');
  }, [loadDetail, loadList, selectedAgentRunId]);

  const submitResolution = async (event) => {
    event.preventDefault();
    if (!review || review.status !== 'pending' || !resolution || resolving) {
      return;
    }

    setResolving(true);
    setAnnouncement('');
    const targetAgentRunId = review.agentRunId;
    const requestId = resolutionRequestRef.current + 1;
    resolutionRequestRef.current = requestId;
    try {
      const updated = await resolveHumanReview(targetAgentRunId, {
        expectedVersion: review.version,
        resolution
      });
      if (
        resolutionRequestRef.current !== requestId ||
        selectedAgentRunIdRef.current !== targetAgentRunId
      ) {
        return;
      }
      setReview(updated);
      setResolution('');
      setAnnouncement(
        resolution === 'confirmed'
          ? 'Policy decision confirmed. The input was not resent to the AI.'
          : 'Review dismissed. The input was not resent to the AI.'
      );
      await loadList(true);
    } catch (error) {
      if (
        resolutionRequestRef.current !== requestId ||
        selectedAgentRunIdRef.current !== targetAgentRunId
      ) {
        return;
      }
      if (error?.response?.status === 409) {
        setAnnouncement('This review changed in another session. Current data was reloaded.');
        await loadList(true);
        if (
          resolutionRequestRef.current === requestId &&
          selectedAgentRunIdRef.current === targetAgentRunId
        ) {
          await loadDetail();
        }
      } else {
        setDetailError(getApiErrorMessage(error, 'Could not resolve this review'));
      }
    } finally {
      if (resolutionRequestRef.current === requestId) {
        setResolving(false);
      }
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold text-gray-900">Human review queue</h2>
            <p className="mt-1 text-sm text-gray-600">
              Tenant-scoped escalations for authorized reviewers.
            </p>
          </div>
          <button
            type="button"
            onClick={refreshAll}
            disabled={listRefreshing || resolving}
            className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ArrowPathIcon
              className={`mr-2 h-4 w-4 ${listRefreshing ? 'animate-spin' : ''}`}
              aria-hidden="true"
            />
            Refresh
          </button>
        </div>

        <div className="sr-only" role="status" aria-live="polite">
          {announcement}
        </div>

        <div className="mb-4 flex items-center gap-3">
          <label htmlFor="review-status-filter" className="text-sm font-medium text-gray-700">
            Status
          </label>
          <select
            id="review-status-filter"
            value={statusFilter}
            disabled={resolving}
            onChange={(event) => {
              detailRequestRef.current += 1;
              resolutionRequestRef.current += 1;
              selectedAgentRunIdRef.current = null;
              setStatusFilter(event.target.value);
              setReview(null);
              setDetailLoading(false);
              setResolving(false);
              setDetailError('');
              setResolution('');
              onSelectReview(null);
            }}
            className="rounded-md border-gray-300 bg-white py-2 pl-3 pr-8 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
          >
            <option value="pending">Pending</option>
            <option value="resolved">Resolved</option>
          </select>
          <p className="text-xs text-gray-500">
            Queue responses contain safe metadata. Exact input loads only after selection.
          </p>
        </div>

        <div className="grid min-h-[620px] grid-cols-[minmax(300px,0.85fr)_minmax(420px,1.5fr)] overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <section className="border-r border-gray-200" aria-label={`${statusLabel(statusFilter)} reviews`} aria-busy={listLoading || listRefreshing}>
            <ReviewQueueList
              items={items}
              loading={listLoading}
              errorMessage={listError}
              selectedAgentRunId={selectedAgentRunId}
              onSelectReview={onSelectReview}
              onRetry={() => loadList()}
              disabled={resolving}
            />
          </section>
          <section aria-label="Selected review" aria-busy={detailLoading || resolving}>
            <ReviewDetail
              review={review}
              loading={detailLoading}
              errorMessage={detailError}
              resolution={resolution}
              onResolutionChange={setResolution}
              onResolve={submitResolution}
              resolving={resolving}
              onRetry={loadDetail}
            />
          </section>
        </div>
      </div>
    </div>
  );
};

export default HumanReviewQueue;
