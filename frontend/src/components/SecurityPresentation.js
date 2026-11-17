import React from 'react';

export const severityStyles = Object.freeze({
  info: 'border-sky-200 bg-sky-50 text-sky-800',
  low: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  medium: 'border-amber-200 bg-amber-50 text-amber-800',
  high: 'border-orange-200 bg-orange-50 text-orange-800',
  critical: 'border-red-200 bg-red-50 text-red-800'
});

export const gateStyles = Object.freeze({
  approve: 'bg-emerald-100 text-emerald-800',
  review_required: 'bg-amber-100 text-amber-800',
  block: 'bg-red-100 text-red-800'
});

export const statusStyles = Object.freeze({
  queued: 'bg-slate-100 text-slate-700',
  running: 'bg-sky-100 text-sky-800',
  succeeded: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800',
  cancelled: 'bg-gray-100 text-gray-700'
});

export const humanizeSecurityValue = (value) =>
  value ? value.replace(/_/g, ' ') : 'Pending';

export const SecurityBadge = ({ value, kind = 'severity' }) => {
  const styles =
    kind === 'gate'
      ? gateStyles
      : kind === 'status'
        ? statusStyles
        : severityStyles;
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${
        styles[value] || 'bg-gray-100 text-gray-700'
      }`}
    >
      {humanizeSecurityValue(value)}
    </span>
  );
};
export const formatBytes = (value) => {
  if (!Number.isFinite(value) || value < 0) {
    return '0 B';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 ** 2) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
};
