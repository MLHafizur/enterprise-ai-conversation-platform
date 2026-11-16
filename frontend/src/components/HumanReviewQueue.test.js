import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  fetchHumanReview,
  fetchHumanReviews,
  resolveHumanReview
} from '../reviews';
import HumanReviewQueue, { ReviewDetail, ReviewQueueList } from './HumanReviewQueue';

jest.mock('../reviews', () => ({
  fetchHumanReview: jest.fn(),
  fetchHumanReviews: jest.fn(),
  resolveHumanReview: jest.fn()
}));

const review = (overrides = {}) => ({
  agentRunId: '11111111-1111-4111-8111-111111111111',
  conversationId: '22222222-2222-4222-8222-222222222222',
  inputMessageId: '33333333-3333-4333-8333-333333333333',
  status: 'pending',
  version: 0,
  policyVersion: 'conversation-input.v1',
  riskScore: 0.72,
  findings: [
    {
      code: 'conversation.legal_review',
      severity: 'medium',
      safeMessage: 'A qualified reviewer must handle this legal request.'
    }
  ],
  createdAt: '2026-11-16T15:30:00Z',
  resolvedAt: null,
  resolution: null,
  inputContent: 'Please explain whether this contract clause is enforceable.',
  ...overrides
});

const detailMarkup = (overrides = {}) =>
  renderToStaticMarkup(
    <ReviewDetail
      review={review(overrides)}
      loading={false}
      errorMessage=""
      resolution=""
      onResolutionChange={() => {}}
      onResolve={() => {}}
      resolving={false}
      onRetry={() => {}}
    />
  );

describe('HumanReviewQueue presentation', () => {
  test('shows exact input and safe findings only after selecting a pending review', () => {
    const markup = detailMarkup();

    expect(markup).toContain('Human review details');
    expect(markup).toContain('Exact message association');
    expect(markup).toContain('A qualified reviewer must handle this legal request.');
    expect(markup).toContain('Please explain whether this contract clause is enforceable.');
    expect(markup).toContain('Sensitive content is shown only to authorized tenant reviewers.');
    expect(markup).toContain('Confirm policy decision');
    expect(markup).toContain('Dismiss as false positive');
    expect(markup).toContain('It never resends this input to the AI.');
  });

  test('renders input as escaped text rather than executable markup', () => {
    const markup = detailMarkup({ inputContent: '<script>alert("unsafe")</script>' });

    expect(markup).toContain('&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;');
    expect(markup).not.toContain('<script>');
  });

  test('shows a durable resolved decision without resolution controls', () => {
    const markup = detailMarkup({
      status: 'resolved',
      version: 1,
      resolvedAt: '2026-11-16T16:00:00Z',
      resolution: 'dismissed'
    });

    expect(markup).toContain('Dismissed as a false positive');
    expect(markup).toContain('Resolved');
    expect(markup).not.toContain('Save resolution');
  });

  test('marks the selected queue row and displays no raw input in list view', () => {
    const item = review();
    delete item.inputContent;
    const markup = renderToStaticMarkup(
      <ReviewQueueList
        items={[item]}
        loading={false}
        errorMessage=""
        selectedAgentRunId={item.agentRunId}
        onSelectReview={() => {}}
        onRetry={() => {}}
      />
    );

    expect(markup).toContain('aria-current="true"');
    expect(markup).toContain('Risk 72%');
    expect(markup).toContain('A qualified reviewer must handle this legal request.');
    expect(markup).not.toContain('Please explain whether');
  });

  test('disables queue navigation while a resolution is being saved', () => {
    const item = review();
    delete item.inputContent;
    const markup = renderToStaticMarkup(
      <ReviewQueueList
        items={[item]}
        loading={false}
        errorMessage=""
        selectedAgentRunId={item.agentRunId}
        onSelectReview={() => {}}
        onRetry={() => {}}
        disabled
      />
    );

    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*aria-current="true"/);
  });

  test('renders accessible loading, error, and empty queue states', () => {
    const common = {
      selectedAgentRunId: null,
      onSelectReview: () => {},
      onRetry: () => {}
    };
    const loading = renderToStaticMarkup(
      <ReviewQueueList {...common} items={[]} loading errorMessage="" />
    );
    const error = renderToStaticMarkup(
      <ReviewQueueList
        {...common}
        items={[]}
        loading={false}
        errorMessage="Temporary failure"
      />
    );
    const empty = renderToStaticMarkup(
      <ReviewQueueList {...common} items={[]} loading={false} errorMessage="" />
    );

    expect(loading).toContain('role="status"');
    expect(error).toContain('role="alert"');
    expect(error).toContain('Try again');
    expect(empty).toContain('No reviews in this view');
  });

  test('ignores an old resolution response after the selected route changes', async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const secondRunId = '55555555-5555-4555-8555-555555555555';
    const firstReview = review();
    const secondReview = review({
      agentRunId: secondRunId,
      inputContent: 'Second selected input'
    });
    let finishResolution;
    const resolutionPromise = new Promise((resolve) => {
      finishResolution = resolve;
    });

    fetchHumanReviews.mockResolvedValue([]);
    fetchHumanReview.mockImplementation((agentRunId) =>
      Promise.resolve(agentRunId === secondRunId ? secondReview : firstReview)
    );
    resolveHumanReview.mockReturnValue(resolutionPromise);

    const container = document.createElement('div');
    const root = createRoot(container);
    const onSelectReview = jest.fn();
    await act(async () => {
      root.render(
        <HumanReviewQueue
          selectedAgentRunId={firstReview.agentRunId}
          onSelectReview={onSelectReview}
        />
      );
    });

    const confirmed = container.querySelector('input[value="confirmed"]');
    await act(async () => {
      confirmed.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const form = container.querySelector('form');
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    await act(async () => {
      root.render(
        <HumanReviewQueue
          selectedAgentRunId={secondRunId}
          onSelectReview={onSelectReview}
        />
      );
    });
    expect(container.textContent).toContain('Second selected input');

    await act(async () => {
      finishResolution(
        review({
          status: 'resolved',
          version: 1,
          resolvedAt: '2026-11-16T16:00:00Z',
          resolution: 'confirmed'
        })
      );
      await resolutionPromise;
    });

    expect(container.textContent).toContain('Second selected input');
    expect(container.textContent).not.toContain('Policy decision confirmed');
    act(() => root.unmount());
    delete global.IS_REACT_ACT_ENVIRONMENT;
  });
});
