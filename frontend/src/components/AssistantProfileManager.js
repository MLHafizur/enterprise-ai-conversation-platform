import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  EyeIcon,
  PlusIcon,
  SparklesIcon,
  XCircleIcon
} from '@heroicons/react/24/outline';
import { format } from 'date-fns';

import { getApiErrorMessage } from '../api';
import {
  activateAssistantProfileVersion,
  ASSISTANT_PROFILE_COMMUNICATION_STYLES,
  ASSISTANT_PROFILE_EXPERTISE_LEVELS,
  ASSISTANT_PROFILE_TEMPLATE_VERSIONS,
  ASSISTANT_PROFILE_TONES,
  createAssistantProfileVersion,
  fetchAssistantProfileCatalog,
  MAX_ASSISTANT_PROFILE_CONTEXT_CHARACTERS,
  previewAssistantProfile
} from '../assistantProfiles';

const profileKey = (profile) => `${profile.profileId}:${profile.version}`;
const activeKey = (active) => `${active.profileId}:${active.profileVersion}`;

const formatTimestamp = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown time' : format(date, 'MMM d, yyyy p');
};

const shortenId = (value) => `${value.slice(0, 8)}…`;
const humanize = (value) =>
  value
    .split('_')
    .map((word) => `${word.slice(0, 1).toUpperCase()}${word.slice(1)}`)
    .join(' ');

const draftFromProfile = (profile) => ({
  promptTemplateVersion: profile.promptTemplateVersion,
  assistantName: profile.assistantName,
  organizationName: profile.organizationName,
  responseTone: profile.responseTone,
  expertise: profile.expertise,
  communicationStyle: profile.communicationStyle,
  contextCharacterLimit: String(profile.contextCharacterLimit)
});

const requestDraft = (draft) => ({
  ...draft,
  contextCharacterLimit: Number(draft.contextCharacterLimit)
});

export const ProfileVersionList = ({
  versions,
  active,
  selectedKey,
  onSelect,
  disabled = false
}) => (
  <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
    <div className="border-b border-gray-200 px-5 py-4">
      <h2 className="text-sm font-semibold text-gray-900">Version history</h2>
      <p className="mt-1 text-xs text-gray-500">
        Saved versions are immutable and safe to audit.
      </p>
    </div>
    {versions.length === 0 ? (
      <div className="px-5 py-10 text-center">
        <SparklesIcon className="mx-auto h-9 w-9 text-gray-300" aria-hidden="true" />
        <p className="mt-3 text-sm font-semibold text-gray-900">No profile versions</p>
        <p className="mt-1 text-sm text-gray-500">
          The tenant profile has not been initialized yet.
        </p>
      </div>
    ) : (
      <ul className="divide-y divide-gray-200" aria-label="Assistant profile versions">
        {versions.map((profile) => {
          const key = profileKey(profile);
          const isActive = key === activeKey(active);
          const isSelected = key === selectedKey;
          return (
            <li key={key}>
              <button
                type="button"
                onClick={() => onSelect(key)}
                disabled={disabled}
                aria-current={isSelected ? 'true' : undefined}
                className={`w-full px-5 py-4 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 ${
                  isSelected ? 'bg-indigo-50' : 'bg-white hover:bg-gray-50'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-gray-900">
                      {profile.assistantName}
                    </p>
                    <p className="mt-1 text-xs text-gray-500">
                      Version {profile.version} · {humanize(profile.responseTone)}
                    </p>
                  </div>
                  {isActive && (
                    <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-800">
                      Active
                    </span>
                  )}
                </div>
                <p className="mt-3 text-xs text-gray-400">
                  {formatTimestamp(profile.createdAt)}
                </p>
              </button>
            </li>
          );
        })}
      </ul>
    )}
  </div>
);

const DetailItem = ({ label, children }) => (
  <div>
    <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">
      {label}
    </dt>
    <dd className="mt-1 text-sm text-gray-900">{children}</dd>
  </div>
);

export const ProfileDetail = ({
  profile,
  isActive,
  busy,
  onPreview,
  onCreateVersion,
  onActivate
}) => (
  <section aria-labelledby="assistant-profile-detail-heading">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2
            id="assistant-profile-detail-heading"
            className="text-xl font-semibold text-gray-900"
          >
            {profile.assistantName}
          </h2>
          <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
            Immutable v{profile.version}
          </span>
          {isActive && (
            <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-800">
              Active
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-gray-500">
          Profile <span className="font-mono" title={profile.profileId}>{shortenId(profile.profileId)}</span>
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onPreview}
          disabled={busy}
          className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50"
        >
          <EyeIcon className="mr-2 h-4 w-4" aria-hidden="true" />
          Preview prompt
        </button>
        <button
          type="button"
          onClick={onCreateVersion}
          disabled={busy}
          className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50"
        >
          <PlusIcon className="mr-2 h-4 w-4" aria-hidden="true" />
          Create new version
        </button>
        {!isActive && (
          <button
            type="button"
            onClick={onActivate}
            disabled={busy}
            className="inline-flex items-center rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50"
          >
            <CheckCircleIcon className="mr-2 h-4 w-4" aria-hidden="true" />
            Activate version
          </button>
        )}
      </div>
    </div>

    <dl className="mt-6 grid grid-cols-1 gap-5 rounded-lg border border-gray-200 bg-gray-50 p-5 sm:grid-cols-2 xl:grid-cols-3">
      <DetailItem label="Organization">{profile.organizationName}</DetailItem>
      <DetailItem label="Response tone">{humanize(profile.responseTone)}</DetailItem>
      <DetailItem label="Expertise">{humanize(profile.expertise)}</DetailItem>
      <DetailItem label="Communication style">
        {humanize(profile.communicationStyle)}
      </DetailItem>
      <DetailItem label="Context budget">
        {profile.contextCharacterLimit.toLocaleString()} characters
      </DetailItem>
      <DetailItem label="Prompt template">
        <span className="font-mono">{profile.promptTemplateVersion}</span>
      </DetailItem>
      <DetailItem label="Created">
        <time dateTime={profile.createdAt}>{formatTimestamp(profile.createdAt)}</time>
      </DetailItem>
      <DetailItem label="Created by">
        <span className="font-mono" title={profile.createdBy}>{shortenId(profile.createdBy)}</span>
      </DetailItem>
    </dl>
  </section>
);

const SelectField = ({ id, label, value, options, onChange }) => (
  <label htmlFor={id} className="block">
    <span className="text-sm font-medium text-gray-700">{label}</span>
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:bg-gray-100"
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {humanize(option)}
        </option>
      ))}
    </select>
  </label>
);

export const ProfileVersionForm = ({
  draft,
  sourceVersion,
  nextVersion,
  saving,
  errorMessage,
  onChange,
  onPreview,
  onCancel,
  onSubmit
}) => (
  <form onSubmit={onSubmit} aria-labelledby="new-profile-version-heading">
    <fieldset disabled={saving}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 id="new-profile-version-heading" className="text-xl font-semibold text-gray-900">
            Create profile version {nextVersion}
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Starts as a copy of version {sourceVersion}. Saving does not activate it.
          </p>
        </div>
        <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-900">
          Draft
        </span>
      </div>

      {errorMessage && (
        <div className="mt-5 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">
          {errorMessage}
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2">
        <label htmlFor="assistant-profile-name" className="block">
          <span className="text-sm font-medium text-gray-700">Assistant name</span>
          <input
            id="assistant-profile-name"
            value={draft.assistantName}
            onChange={(event) => onChange('assistantName', event.target.value)}
            maxLength={128}
            required
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
        <label htmlFor="assistant-profile-organization" className="block">
          <span className="text-sm font-medium text-gray-700">Organization name</span>
          <input
            id="assistant-profile-organization"
            value={draft.organizationName}
            onChange={(event) => onChange('organizationName', event.target.value)}
            maxLength={128}
            required
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
        <SelectField
          id="assistant-profile-tone"
          label="Response tone"
          value={draft.responseTone}
          options={ASSISTANT_PROFILE_TONES}
          onChange={(value) => onChange('responseTone', value)}
        />
        <SelectField
          id="assistant-profile-expertise"
          label="Expertise"
          value={draft.expertise}
          options={ASSISTANT_PROFILE_EXPERTISE_LEVELS}
          onChange={(value) => onChange('expertise', value)}
        />
        <SelectField
          id="assistant-profile-style"
          label="Communication style"
          value={draft.communicationStyle}
          options={ASSISTANT_PROFILE_COMMUNICATION_STYLES}
          onChange={(value) => onChange('communicationStyle', value)}
        />
        <label htmlFor="assistant-profile-context-limit" className="block">
          <span className="text-sm font-medium text-gray-700">Context character limit</span>
          <input
            id="assistant-profile-context-limit"
            type="number"
            min="1"
            max={MAX_ASSISTANT_PROFILE_CONTEXT_CHARACTERS}
            step="1"
            value={draft.contextCharacterLimit}
            onChange={(event) => onChange('contextCharacterLimit', event.target.value)}
            required
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <span className="mt-1 block text-xs text-gray-500">
            This is a character budget, not a model-token limit.
          </span>
        </label>
        <label htmlFor="assistant-profile-template" className="block sm:col-span-2">
          <span className="text-sm font-medium text-gray-700">Prompt template</span>
          <select
            id="assistant-profile-template"
            value={draft.promptTemplateVersion}
            onChange={(event) => onChange('promptTemplateVersion', event.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {ASSISTANT_PROFILE_TEMPLATE_VERSIONS.map((version) => (
              <option key={version} value={version}>{version}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-6 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onPreview}
          className="inline-flex items-center rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
        >
          <EyeIcon className="mr-2 h-4 w-4" aria-hidden="true" />
          Preview draft
        </button>
        <button
          type="submit"
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {saving ? 'Saving version…' : 'Save new version'}
        </button>
      </div>
    </fieldset>
  </form>
);

export const PromptPreview = ({ preview, loading, errorMessage }) => (
  <section className="mt-7 border-t border-gray-200 pt-6" aria-labelledby="prompt-preview-heading">
    <div className="flex items-center gap-2">
      <EyeIcon className="h-5 w-5 text-indigo-600" aria-hidden="true" />
      <h3 id="prompt-preview-heading" className="text-sm font-semibold text-gray-900">
        Server-rendered prompt preview
      </h3>
    </div>
    <p className="mt-1 text-xs text-gray-500">
      Generated by the same allowlisted template renderer used by the worker.
    </p>
    {loading ? (
      <div className="mt-3 rounded-lg bg-gray-50 p-5 text-sm text-gray-500" role="status">
        Rendering prompt preview…
      </div>
    ) : errorMessage ? (
      <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert">
        {errorMessage}
      </div>
    ) : preview ? (
      <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-gray-900 p-5 text-sm leading-6 text-gray-100">
        {preview.systemInstruction}
      </pre>
    ) : (
      <div className="mt-3 rounded-lg border border-dashed border-gray-300 p-5 text-sm text-gray-500">
        Choose “Preview prompt” to inspect the exact trusted instruction.
      </div>
    )}
  </section>
);

export const ActivationDialog = ({ profile, activating, errorMessage, onCancel, onConfirm }) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/50 p-4">
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="activate-profile-heading"
      aria-describedby="activate-profile-description"
      className="w-full max-w-lg rounded-xl bg-white p-6 shadow-xl"
    >
      <div className="flex items-start gap-3">
        <div className="rounded-full bg-indigo-100 p-2">
          <SparklesIcon className="h-6 w-6 text-indigo-600" aria-hidden="true" />
        </div>
        <div>
          <h2 id="activate-profile-heading" className="text-lg font-semibold text-gray-900">
            Activate {profile.assistantName} version {profile.version}?
          </h2>
          <p id="activate-profile-description" className="mt-2 text-sm text-gray-600">
            New user turns will use version {profile.version}. Jobs already queued, running, or retried keep the profile version they were originally given.
          </p>
        </div>
      </div>
      {errorMessage && (
        <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">
          {errorMessage}
        </div>
      )}
      <div className="mt-6 flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={activating}
          className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={activating}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {activating ? 'Activating…' : 'Activate version'}
        </button>
      </div>
    </div>
  </div>
);

const AssistantProfileManager = () => {
  const [catalog, setCatalog] = useState(null);
  const [selectedKey, setSelectedKey] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [activationOpen, setActivationOpen] = useState(false);
  const [activating, setActivating] = useState(false);
  const [activationError, setActivationError] = useState('');
  const [notice, setNotice] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const catalogRequestRef = useRef(0);
  const previewRequestRef = useRef(0);
  const actionRequestRef = useRef(0);
  const selectedKeyRef = useRef('');

  const invalidatePreview = useCallback(() => {
    previewRequestRef.current += 1;
    setPreview(null);
    setPreviewError('');
    setPreviewLoading(false);
  }, []);

  const loadCatalog = useCallback(async (background = false) => {
    const requestId = catalogRequestRef.current + 1;
    catalogRequestRef.current = requestId;
    setLoadError('');
    if (background) {
      setRefreshing(true);
    } else {
      setLoading(true);
      setCatalog(null);
    }
    try {
      const nextCatalog = await fetchAssistantProfileCatalog();
      if (catalogRequestRef.current !== requestId) {
        return null;
      }
      setCatalog(nextCatalog);
      const currentKey = selectedKeyRef.current;
      const nextKey = nextCatalog.versions.some(
        (profile) => profileKey(profile) === currentKey
      )
        ? currentKey
        : activeKey(nextCatalog.active);
      if (currentKey && nextKey !== currentKey) {
        setEditing(false);
        setDraft(null);
        setFormError('');
        invalidatePreview();
      }
      selectedKeyRef.current = nextKey;
      setSelectedKey(nextKey);
      return nextCatalog;
    } catch (error) {
      if (catalogRequestRef.current === requestId) {
        setLoadError(getApiErrorMessage(error, 'Could not load assistant profiles'));
      }
      return null;
    } finally {
      if (catalogRequestRef.current === requestId) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [invalidatePreview]);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    invalidatePreview();
    actionRequestRef.current += 1;
    setActivationOpen(false);
    setActivationError('');
    setActivating(false);
  }, [invalidatePreview, selectedKey]);

  const selectedProfile = catalog?.versions.find(
    (profile) => profileKey(profile) === selectedKey
  ) || null;
  const selectedIsActive = Boolean(
    selectedProfile && profileKey(selectedProfile) === activeKey(catalog.active)
  );

  const selectProfile = (key) => {
    invalidatePreview();
    setEditing(false);
    setDraft(null);
    setFormError('');
    setNotice('');
    selectedKeyRef.current = key;
    setSelectedKey(key);
  };

  const beginCreatingVersion = () => {
    if (!selectedProfile) {
      return;
    }
    invalidatePreview();
    setDraft(draftFromProfile(selectedProfile));
    setEditing(true);
    setFormError('');
    setNotice('');
  };

  const changeDraft = (field, value) => {
    invalidatePreview();
    setDraft((current) => ({ ...current, [field]: value }));
    setFormError('');
  };

  const renderPreview = async () => {
    const source = editing && draft
      ? requestDraft(draft)
      : selectedProfile
        ? requestDraft(draftFromProfile(selectedProfile))
        : null;
    if (!source) {
      return;
    }

    const requestId = previewRequestRef.current + 1;
    previewRequestRef.current = requestId;
    setPreview(null);
    setPreviewError('');
    setPreviewLoading(true);
    try {
      const nextPreview = await previewAssistantProfile(source);
      if (previewRequestRef.current === requestId) {
        setPreview(nextPreview);
      }
    } catch (error) {
      if (previewRequestRef.current === requestId) {
        setPreviewError(getApiErrorMessage(error, 'Could not render prompt preview'));
      }
    } finally {
      if (previewRequestRef.current === requestId) {
        setPreviewLoading(false);
      }
    }
  };

  const refreshAfterConflict = async (message) => {
    setActivationOpen(false);
    await loadCatalog(true);
    setNotice(message);
    setAnnouncement(message);
  };

  const createVersion = async (event) => {
    event.preventDefault();
    if (!selectedProfile || !draft || saving) {
      return;
    }
    const requestId = actionRequestRef.current + 1;
    actionRequestRef.current = requestId;
    setSaving(true);
    setFormError('');
    setNotice('');
    try {
      const result = await createAssistantProfileVersion(
        selectedProfile.profileId,
        {
          ...requestDraft(draft),
          expectedLatestVersion: catalog.latestVersion
        }
      );
      if (actionRequestRef.current !== requestId) {
        return;
      }
      const { catalog: nextCatalog, profile: created } = result;
      setCatalog(nextCatalog);
      const createdKey = profileKey(created);
      selectedKeyRef.current = createdKey;
      setSelectedKey(createdKey);
      setEditing(false);
      setDraft(null);
      const message = `Version ${created.version} created. It is not active yet.`;
      setNotice(message);
      setAnnouncement(message);
    } catch (error) {
      if (actionRequestRef.current !== requestId) {
        return;
      }
      if (error?.response?.status === 409) {
        await refreshAfterConflict(
          'Another administrator created a version first. The catalog was refreshed; review the latest version and try again.'
        );
      } else {
        setFormError(getApiErrorMessage(error, 'Could not create profile version'));
      }
    } finally {
      if (actionRequestRef.current === requestId) {
        setSaving(false);
      }
    }
  };

  const confirmActivation = async () => {
    if (!selectedProfile || !catalog || activating) {
      return;
    }
    const target = selectedProfile;
    const requestId = actionRequestRef.current + 1;
    actionRequestRef.current = requestId;
    setActivating(true);
    setActivationError('');
    try {
      const nextCatalog = await activateAssistantProfileVersion(
        target.profileId,
        target.version,
        catalog.active.revision
      );
      if (actionRequestRef.current !== requestId) {
        return;
      }
      setCatalog(nextCatalog);
      setActivationOpen(false);
      const message = `Version ${target.version} is now active for new user turns.`;
      setNotice(message);
      setAnnouncement(message);
    } catch (error) {
      if (actionRequestRef.current !== requestId) {
        return;
      }
      if (error?.response?.status === 409) {
        await refreshAfterConflict(
          'Another administrator changed the active profile. The catalog was refreshed; review it before trying again.'
        );
      } else {
        setActivationError(getApiErrorMessage(error, 'Could not activate profile version'));
      }
    } finally {
      if (actionRequestRef.current === requestId) {
        setActivating(false);
      }
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center bg-gray-50" role="status">
        <div className="text-center">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-b-2 border-indigo-600" aria-hidden="true" />
          <p className="mt-3 text-sm text-gray-500">Loading assistant profiles…</p>
        </div>
      </div>
    );
  }

  if (loadError || !catalog) {
    return (
      <div className="h-full overflow-y-auto bg-gray-50 p-6">
        <div className="mx-auto max-w-xl rounded-xl border border-red-200 bg-red-50 p-6 text-center" role="alert">
          <XCircleIcon className="mx-auto h-8 w-8 text-red-500" aria-hidden="true" />
          <h2 className="mt-3 text-base font-semibold text-red-900">Assistant profiles unavailable</h2>
          <p className="mt-1 text-sm text-red-700">{loadError || 'The profile catalog was empty.'}</p>
          <button
            type="button"
            onClick={() => void loadCatalog()}
            className="mt-4 rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50 p-6">
      <div className="mx-auto max-w-7xl">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold text-gray-900">Assistant profiles</h2>
            <p className="mt-1 max-w-3xl text-sm text-gray-600">
              Version the trusted personality used for new turns without changing jobs that are already pinned to an older version.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadCatalog(true)}
            disabled={refreshing || saving || activating}
            className="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-50"
          >
            <ArrowPathIcon className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        <div className="sr-only" aria-live="polite">{announcement}</div>
        {notice && (
          <div className="mt-5 rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-800" role="status">
            {notice}
          </div>
        )}

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <ProfileVersionList
            versions={catalog.versions}
            active={catalog.active}
            selectedKey={selectedKey}
            onSelect={selectProfile}
            disabled={saving || activating}
          />

          <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            {!selectedProfile ? (
              <div className="py-12 text-center">
                <ClockIcon className="mx-auto h-10 w-10 text-gray-300" aria-hidden="true" />
                <p className="mt-3 text-sm font-semibold text-gray-900">Select a version</p>
                <p className="mt-1 text-sm text-gray-500">
                  Choose a profile version to inspect its immutable settings.
                </p>
              </div>
            ) : editing && draft ? (
              <ProfileVersionForm
                draft={draft}
                sourceVersion={selectedProfile.version}
                nextVersion={catalog.latestVersion + 1}
                saving={saving}
                errorMessage={formError}
                onChange={changeDraft}
                onPreview={() => void renderPreview()}
                onCancel={() => {
                  invalidatePreview();
                  setEditing(false);
                  setDraft(null);
                  setFormError('');
                }}
                onSubmit={createVersion}
              />
            ) : (
              <ProfileDetail
                profile={selectedProfile}
                isActive={selectedIsActive}
                busy={saving || activating}
                onPreview={() => void renderPreview()}
                onCreateVersion={beginCreatingVersion}
                onActivate={() => {
                  setActivationError('');
                  setActivationOpen(true);
                }}
              />
            )}

            {selectedProfile && (
              <PromptPreview
                preview={preview}
                loading={previewLoading}
                errorMessage={previewError}
              />
            )}
          </div>
        </div>
      </div>

      {activationOpen && selectedProfile && (
        <ActivationDialog
          profile={selectedProfile}
          activating={activating}
          errorMessage={activationError}
          onCancel={() => setActivationOpen(false)}
          onConfirm={() => void confirmActivation()}
        />
      )}
    </div>
  );
};

export default AssistantProfileManager;
