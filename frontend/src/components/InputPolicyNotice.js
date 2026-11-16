import React from 'react';
import {
  ExclamationTriangleIcon,
  ShieldExclamationIcon
} from '@heroicons/react/24/outline';

const PRESENTATION = Object.freeze({
  block: {
    title: 'Blocked by policy',
    className: 'border-red-200 bg-red-50 text-red-900',
    iconClassName: 'text-red-600',
    Icon: ShieldExclamationIcon
  },
  escalate: {
    title: 'Human review required',
    className: 'border-amber-200 bg-amber-50 text-amber-900',
    iconClassName: 'text-amber-600',
    Icon: ExclamationTriangleIcon
  }
});

const InputPolicyNotice = ({ notice }) => {
  const presentation = PRESENTATION[notice.action];
  if (!presentation) {
    return null;
  }
  const { Icon } = presentation;
  const visibleFindings = notice.findings.slice(0, 3);
  const remainingCount = notice.findings.length - visibleFindings.length;

  return (
    <aside
      aria-label={presentation.title}
      className={`ml-auto mt-2 max-w-xs lg:max-w-md rounded-md border px-3 py-2 ${presentation.className}`}
    >
      <div className="flex items-start gap-2">
        <Icon
          aria-hidden="true"
          className={`mt-0.5 h-5 w-5 flex-shrink-0 ${presentation.iconClassName}`}
        />
        <div>
          <p className="text-sm font-semibold">{presentation.title}</p>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-sm">
            {visibleFindings.map((finding, index) => (
              <li key={`${finding.severity}-${finding.safeMessage}-${index}`}>
                <span className="font-medium capitalize">{finding.severity}:</span>{' '}
                {finding.safeMessage}
              </li>
            ))}
          </ul>
          {remainingCount > 0 && (
            <p className="mt-1 text-xs font-medium">
              {remainingCount} more {remainingCount === 1 ? 'finding' : 'findings'}
            </p>
          )}
        </div>
      </div>
    </aside>
  );
};

export default InputPolicyNotice;
