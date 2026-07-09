const STEPS = ['Edit', 'Render', 'Publish', 'Watch'];

export function studioWorkflowState({
  hasChanges = false,
  renderReady = false,
  published = false,
} = {}) {
  if (hasChanges) {
    return { activeStep: 'Edit', hint: 'Save your edits, then render the updated lesson.' };
  }
  if (!renderReady) {
    return { activeStep: 'Render', hint: 'Render the lesson to create a preview.' };
  }
  if (!published) {
    return { activeStep: 'Publish', hint: 'Review the rendered lesson, then publish when it is ready.' };
  }
  return { activeStep: 'Watch', hint: 'Published. Open Watch to verify the learner experience.' };
}

export default function StudioWorkflowGuide(props) {
  const state = studioWorkflowState(props);
  const activeIndex = STEPS.indexOf(state.activeStep);

  return (
    <section
      data-testid="studio-workflow-guide"
      aria-label="Lesson workflow"
      className="flex min-w-0 flex-col gap-2 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-low)] px-3 py-2.5 md:flex-row md:items-center md:justify-between"
    >
      <ol className="flex min-w-0 flex-wrap items-center gap-1 text-xs font-semibold">
        {STEPS.map((step, index) => (
          <li key={step} className="inline-flex items-center gap-1">
            {index > 0 && <span aria-hidden="true" className="text-[var(--outline)]">→</span>}
            <span
              aria-current={index === activeIndex ? 'step' : undefined}
              className={`rounded-full px-2 py-1 ${
                index === activeIndex
                  ? 'bg-[var(--accent-primary)] text-white'
                  : index < activeIndex
                    ? 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
                    : 'text-[var(--text-secondary)]'
              }`}
            >
              {step}
            </span>
          </li>
        ))}
      </ol>
      <p className="min-w-0 text-xs text-[var(--text-secondary)]">
        <span className="font-semibold text-[var(--text-primary)]">Next:</span> {state.hint}
      </p>
    </section>
  );
}
