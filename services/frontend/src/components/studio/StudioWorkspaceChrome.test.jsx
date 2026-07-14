import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  StudioCreatorHeader,
  StudioCompactWorkspaceSwitcher,
  StudioFirstRunOnboarding,
  StudioInspectorHeading,
  StudioInspectorSection,
  StudioRenderStatus,
  StudioSaveStatus,
  StudioSlideRail,
  StudioSmartGuidance,
  StudioStartOption,
  StudioWorkflowStrip,
} from './StudioWorkspaceChrome';
import { studioWorkspaceCopy, studioWorkspaceLocale } from './studioWorkspaceCopy';

describe('Studio workspace chrome', () => {
  let host;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    document.documentElement.lang = '';
    document.documentElement.dir = '';
  });

  it('falls back expanded and unsupported locales safely', () => {
    expect(studioWorkspaceLocale('tr-TR')).toBe('tr');
    expect(studioWorkspaceLocale('de-DE')).toBe('de');
    expect(studioWorkspaceLocale('ar')).toBe('ar');
    expect(studioWorkspaceCopy('tr-TR').slides).toBe('Slaytlar');
  });

  it('renders the slide rail and preserves selection callbacks', async () => {
    const onSelect = vi.fn();
    const onMove = vi.fn();
    const onDelete = vi.fn();
    await act(async () => {
      root.render(
        <StudioSlideRail
          scenes={[
            { key: 'one', label: 'Slide 1', text: 'User-authored title', status: 'draft' },
            { key: 'two', label: 'Slide 2', text: 'Second title', status: 'ready' },
          ]}
          selectedSceneKey="one"
          onSelect={onSelect}
          onMove={onMove}
          onDelete={onDelete}
        />,
      );
    });

    expect(host.querySelector('[data-testid="studio-slide-rail"]')).toBeTruthy();
    expect(host.querySelector('[data-selected="true"]').className).toContain('motion-studio-selection');
    const buttons = host.querySelectorAll('button[aria-label^="Select slide"]');
    expect(buttons[0]).toHaveAttribute('aria-current', 'true');
    await act(async () => buttons[1].click());
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ key: 'two' }), 1);

    await act(async () => {
      buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ key: 'two' }), 1);

    await act(async () => {
      buttons[0].dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true,
        cancelable: true,
        clientX: 24,
        clientY: 32,
      }));
    });
    expect(document.body.textContent).toContain('Delete');
    expect(document.body.querySelector('[role="menu"]').className).toContain('motion-popover-in');
    const deleteItem = Array.from(document.body.querySelectorAll('[role="menuitem"]'))
      .find((item) => item.textContent.includes('Delete'));
    expect(deleteItem.className).toContain('motion-interactive');
    await act(async () => deleteItem.click());
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ key: 'one' }), 0);
    expect(buttons[0].className).toContain('min-h-11');
  });

  it('renders a compact workspace switcher with localized active state', async () => {
    document.documentElement.lang = 'tr-TR';
    const copy = studioWorkspaceCopy('tr-TR');
    const onWorkspaceChange = vi.fn();

    await act(async () => {
      root.render(
        <StudioCompactWorkspaceSwitcher
          copy={copy}
          activeWorkspace="canvas"
          workspaces={[
            { key: 'slides', label: copy.compactWorkspaceScenes, detail: '2 slayt', controls: 'slides-panel' },
            { key: 'canvas', label: copy.compactWorkspaceCanvas, detail: 'Slayt 1', controls: 'canvas-panel' },
            { key: 'inspector', label: copy.compactWorkspaceInspector, detail: copy.inspectorTranscriptPanel, controls: 'inspector-panel' },
          ]}
          onWorkspaceChange={onWorkspaceChange}
        />,
      );
    });

    const switcher = host.querySelector('[data-testid="studio-compact-workspace-switcher"]');
    expect(switcher).toHaveAttribute('aria-label', copy.compactWorkspaceLabel);
    expect(switcher.textContent).toContain(copy.compactWorkspaceScenes);
    expect(switcher.textContent).toContain(copy.compactWorkspaceCanvas);
    expect(switcher.textContent).toContain(copy.compactWorkspaceInspector);
    expect(switcher.outerHTML).not.toContain('Studio workspace sections');

    const buttons = Array.from(switcher.querySelectorAll('button'));
    expect(buttons).toHaveLength(3);
    expect(buttons[1]).toHaveAttribute('aria-current', 'page');
    expect(buttons[1]).toHaveAttribute('aria-controls', 'canvas-panel');
    expect(buttons[1].className).toContain('min-h-10');
    expect(buttons[1].querySelector('span').className).toContain('[overflow-wrap:anywhere]');

    await act(async () => buttons[2].click());
    expect(onWorkspaceChange).toHaveBeenCalledWith('inspector');
  });

  it('renders the Arabic compact switcher without English workspace leakage', async () => {
    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    const copy = studioWorkspaceCopy('ar');

    await act(async () => {
      root.render(
        <StudioCompactWorkspaceSwitcher
          copy={copy}
          activeWorkspace="slides"
          workspaces={[
            { key: 'slides', label: copy.compactWorkspaceScenes, controls: 'slides-panel' },
            { key: 'canvas', label: copy.compactWorkspaceCanvas, controls: 'canvas-panel' },
            { key: 'inspector', label: copy.compactWorkspaceInspector, controls: 'inspector-panel' },
          ]}
          onWorkspaceChange={vi.fn()}
        />,
      );
    });

    const switcher = host.querySelector('[data-testid="studio-compact-workspace-switcher"]');
    expect(switcher).toHaveAttribute('aria-label', copy.compactWorkspaceLabel);
    expect(switcher.textContent).toContain(copy.compactWorkspaceScenes);
    expect(switcher.textContent).toContain(copy.compactWorkspaceCanvas);
    expect(switcher.textContent).toContain(copy.compactWorkspaceInspector);
    ['Scenes', 'Canvas', 'Studio workspace sections'].forEach((literal) => {
      expect(switcher.outerHTML).not.toContain(literal);
    });
  });

  it('keeps compact workspace labels readable for long localized copy', async () => {
    await act(async () => {
      root.render(
        <StudioCompactWorkspaceSwitcher
          activeWorkspace="inspector"
          workspaces={[
            { key: 'slides', label: 'Szenenverwaltung', detail: '12 Produktionsszenen', controls: 'slides-panel' },
            { key: 'canvas', label: 'Leinwand', detail: 'Szene mit sehr langem Titel', controls: 'canvas-panel' },
            { key: 'inspector', label: 'Inspektoreinstellungen', detail: 'Stimme und Avatar', controls: 'inspector-panel' },
          ]}
          onWorkspaceChange={vi.fn()}
        />,
      );
    });

    const switcher = host.querySelector('[data-testid="studio-compact-workspace-switcher"]');
    const buttons = Array.from(switcher.querySelectorAll('button'));
    expect(buttons).toHaveLength(3);
    expect(buttons[2]).toHaveAttribute('aria-current', 'page');
    buttons.forEach((button) => {
      expect(button.className).toContain('leading-tight');
      expect(button.outerHTML).toContain('[overflow-wrap:anywhere]');
      expect(button.outerHTML).not.toContain('truncate');
    });
  });

  it('shows localized loading, empty, and render queue states', async () => {
    document.documentElement.lang = 'tr-TR';
    await act(async () => root.render(<StudioSlideRail loading />));
    expect(host.textContent).toContain('Slaytlar yükleniyor');

    await act(async () => root.render(<StudioSlideRail scenes={[]} />));
    expect(host.textContent).toContain('Henüz slayt yok');

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'processing', progress: 64 }} />));
    expect(host.textContent).toContain('Render durumu: İşleniyor');
    const renderStatus = host.querySelector('[data-testid="studio-render-status"]');
    expect(renderStatus).toHaveAttribute('data-state', 'processing');
    expect(renderStatus).toHaveAttribute('data-render-state', 'active');
    expect(renderStatus.className).toContain('motion-studio-status');
    expect(renderStatus.className).toContain('motion-task-active');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '64');

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'pending', progress: 0 }} />));
    expect(host.querySelector('[data-testid="studio-render-status"]')).toHaveAttribute('data-state', 'queued');
    expect(host.querySelector('[role="progressbar"]')).toBeNull();
    expect(host.querySelector('.motion-task-progress')).not.toBeNull();

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'running', progress: 100 }} />));
    expect(host.textContent).toContain('99%');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '99');

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'done', progress: 100 }} />));
    expect(host.textContent).toContain('100%');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '100');
  });

  it('renders localized Inspector context without changing section disclosure behavior', async () => {
    await act(async () => {
      root.render(
        <>
          <StudioInspectorHeading
            projectTitle="Scene polish"
            sceneLabel="Slide 2"
            sectionLabel="Slides"
            attentionCount={1}
          />
          <StudioSaveStatus saving lastSavedAt="10:30 AM" />
          <StudioInspectorSection
            icon={<span data-testid="section-icon" />}
            title="Scene background"
            description="Selected slide controls"
            status={<span>Ready</span>}
          >
            <button type="button">Focusable control</button>
          </StudioInspectorSection>
        </>,
      );
    });

    const saveStatus = host.querySelector('[data-state="saving"]');
    expect(saveStatus.textContent).toContain('Saving');
    expect(saveStatus.className).toContain('motion-studio-status');

    const heading = host.querySelector('[data-testid="studio-inspector-heading"]');
    expect(heading.textContent).toContain('Inspector');
    expect(heading.textContent).toContain('Scene polish');
    expect(heading.textContent).toContain('Slide 2');
    expect(heading.textContent).toContain('Slides');
    expect(heading.textContent).toContain('Needs attention: 1');
    expect(heading.querySelector('dl')).toHaveAttribute('aria-label', 'Inspector context');
    expect(host.querySelector('[data-testid="section-icon"]')).toBeTruthy();

    const details = host.querySelector('details');
    expect(details.open).toBe(true);
    expect(details.className).toContain('motion-studio-status');
    expect(host.querySelector('summary').className).toContain('motion-interactive');
    expect(host.querySelector('.motion-studio-panel')).not.toBeNull();
    expect(host.textContent).toContain('Ready');
    expect(host.querySelector('button').textContent).toContain('Focusable control');
  });

  it('renders Turkish and Arabic Inspector context from localized copy', async () => {
    document.documentElement.lang = 'tr-TR';
    await act(async () => {
      root.render(
        <StudioInspectorHeading
          projectTitle="Ders"
          sceneLabel="Sahne 1"
          sectionLabel={studioWorkspaceCopy('tr-TR').inspectorSlidesPanel}
          attentionCount={0}
        />,
      );
    });

    let heading = host.querySelector('[data-testid="studio-inspector-heading"]');
    expect(heading.textContent).toContain('Denetleyici');
    expect(heading.textContent).toContain('Engel yok');
    expect(heading.outerHTML).not.toContain('No blockers');
    expect(heading.querySelector('dl')).toHaveAttribute('aria-label', 'Denetleyici bağlamı');

    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    await act(async () => {
      root.render(
        <StudioInspectorHeading
          projectTitle="درس"
          sceneLabel="المشهد 1"
          sectionLabel={studioWorkspaceCopy('ar').inspectorSlidesPanel}
          attentionCount={2}
        />,
      );
    });

    heading = host.querySelector('[data-testid="studio-inspector-heading"]');
    expect(heading.textContent).toContain('المشهد 1');
    expect(heading.textContent).toContain('يتطلب الانتباه: 2');
    expect(heading.outerHTML).not.toContain('Needs attention');
    expect(heading.querySelector('dl')).toHaveAttribute('aria-label', 'سياق المفتش');
  });

  it('renders the Studio workflow as an accessible production sequence', async () => {
    await act(async () => {
      root.render(
        <StudioWorkflowStrip
          steps={[
            { key: 'edit', label: 'Edit', status: 'complete', detail: 'Saved' },
            { key: 'review', label: 'Review', status: 'active', detail: 'Approved' },
            { key: 'render', label: 'Render', status: 'pending', detail: 'Not queued' },
          ]}
        />,
      );
    });

    const workflow = host.querySelector('[data-testid="studio-workflow-strip"]');
    expect(workflow).toBeTruthy();
    expect(workflow).toHaveAttribute('aria-label', 'Studio workflow');
    expect(host.textContent).toContain('AI-assisted studio flow');
    expect(host.textContent).toContain('Edit');
    expect(host.querySelector('[aria-current="step"]').textContent).toContain('Review');
  });

  it('renders the English AI creator header with metadata, chips, CTA, and render status', async () => {
    const onRender = vi.fn();
    const copy = studioWorkspaceCopy('en');
    await act(async () => {
      root.render(
        <StudioCreatorHeader
          copy={copy}
          title="Creator Header Lesson"
          description="A concise lesson summary."
          metadata={[
            { key: 'avatar', label: 'Avatar', value: 'Avatar ready' },
            { key: 'voice', label: 'Voice', value: 'XTTS v2' },
            { key: 'duration', label: 'Duration', value: '2:41' },
          ]}
          chips={[
            { key: 'ready', label: 'Ready', variant: 'success' },
            { key: 'publish-ready', label: 'Publish Ready', variant: 'success' },
          ]}
          nextActionTitle="Render the updated video"
          nextActionDetail="The transcript changes require a fresh render."
          primaryAction={{ label: 'Render', onClick: onRender }}
          renderStatus={{ status: 'ready', progress: 100 }}
        />,
      );
    });

    const header = host.querySelector('[data-testid="studio-creator-header"]');
    expect(header).toBeTruthy();
    expect(header).toHaveAttribute('aria-labelledby', 'studio-creator-header-title');
    expect(host.textContent).toContain('Creator Header Lesson');
    expect(host.textContent).toContain('Avatar ready');
    expect(host.textContent).toContain('Publish Ready');
    expect(host.textContent).toContain('Render status: Ready');
    expect(host.textContent).toContain('Next best action');

    const button = Array.from(host.querySelectorAll('button')).find((item) => item.textContent.includes('Render'));
    await act(async () => button.click());
    expect(onRender).toHaveBeenCalledTimes(1);
  });

  it('renders the Turkish AI creator header without hard-coded English leakage', async () => {
    const onRender = vi.fn();
    const onPreview = vi.fn();
    const copy = studioWorkspaceCopy('tr-TR');

    await act(async () => {
      root.render(
        <StudioCreatorHeader
          copy={copy}
          title={copy.creatorNewLessonDraft}
          description={copy.creatorDescriptionFallback}
          metadata={[
            { key: 'avatar', label: copy.creatorAvatarLabel, value: copy.creatorAvatarReady },
            { key: 'voice', label: copy.creatorVoiceLabel, value: copy.creatorAutoVoice },
            { key: 'duration', label: copy.creatorDurationLabel, value: '2:41' },
          ]}
          chips={[
            { key: 'ready', label: copy.creatorStatusReady, variant: 'success' },
            { key: 'publish-ready', label: copy.creatorPublishReady, variant: 'success' },
          ]}
          nextActionTitle={copy.creatorRenderUpdatedVideo}
          nextActionDetail={copy.creatorDetailRenderUpdated}
          primaryAction={{ label: copy.creatorRender, onClick: onRender }}
          secondaryActions={[{ key: 'preview', label: copy.creatorPreviewDraft, onClick: onPreview }]}
          renderStatus={{ status: 'ready', progress: 100 }}
        />,
      );
    });

    const header = host.querySelector('[data-testid="studio-creator-header"]');
    expect(header.textContent).toContain('Yapay zeka üretim alanı');
    expect(header.textContent).toContain('Sonraki en iyi adım');
    expect(header.textContent).toContain('Yayına hazır');
    expect(header.textContent).toContain('Render durumu: Hazır');
    expect(header.querySelector('dl')).toHaveAttribute('aria-label', 'Üretici metadatası');
    expect(header.querySelector('[aria-label="Üretici özeti"]')).toBeTruthy();

    const leakedEnglish = [
      'AI creator workspace',
      'Next best action',
      'Continue editing',
      'Untitled lesson',
      'Creator metadata',
      'Creator summary',
      'Avatar ready.',
      'Auto voice',
      'Publish Ready',
      'Needs Attention',
    ];
    const headerMarkup = header.outerHTML;
    leakedEnglish.forEach((literal) => {
      expect(headerMarkup).not.toContain(literal);
    });

    const buttons = Array.from(host.querySelectorAll('button'));
    await act(async () => buttons.find((item) => item.textContent.includes(copy.creatorRender)).click());
    await act(async () => buttons.find((item) => item.textContent.includes(copy.creatorPreviewDraft)).click());
    expect(onRender).toHaveBeenCalledTimes(1);
    expect(onPreview).toHaveBeenCalledTimes(1);
  });

  it('renders the Arabic AI creator header with RTL-safe logical classes and no English leakage', async () => {
    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    const copy = studioWorkspaceCopy('ar');

    await act(async () => {
      root.render(
        <StudioCreatorHeader
          copy={copy}
          title={copy.creatorNewLessonDraft}
          description={copy.creatorDescriptionFallback}
          metadata={[
            { key: 'avatar', label: copy.creatorAvatarLabel, value: copy.creatorAvatarReady },
            { key: 'voice', label: copy.creatorVoiceLabel, value: 'XTTS v2' },
            { key: 'language', label: copy.creatorLanguageLabel, value: 'العربية' },
          ]}
          chips={[
            { key: 'draft', label: copy.creatorStatusDraft, variant: 'warning' },
            { key: 'needs-attention', label: copy.creatorNeedsAttention, variant: 'danger' },
          ]}
          nextActionTitle={copy.creatorContinueEditing}
          nextActionDetail={copy.creatorDetailContinueEditing}
          primaryAction={{ label: copy.creatorSaveChanges, onClick: vi.fn() }}
          renderStatus={{ status: 'pending', progress: 0 }}
        />,
      );
    });

    const header = host.querySelector('[data-testid="studio-creator-header"]');
    expect(header.textContent).toContain('مساحة منشئ بالذكاء الاصطناعي');
    expect(header.textContent).toContain('أفضل خطوة تالية');
    expect(header.textContent).toContain('يتطلب الانتباه');
    expect(header.textContent).toContain('حالة التصيير: في قائمة الانتظار');
    expect(header.querySelector('dl')).toHaveAttribute('aria-label', 'بيانات المنشئ');
    expect(header.querySelector('[aria-label="ملخص المنشئ"]')).toBeTruthy();
    expect(header.querySelectorAll('dl > div')[1].className).toContain('border-s');
    expect(header.querySelectorAll('dl > div')[1].className).toContain('ps-3');

    const leakedEnglish = [
      'AI creator workspace',
      'Next best action',
      'Continue editing',
      'Untitled lesson',
      'Creator metadata',
      'Creator summary',
      'Avatar',
      'Voice',
      'Language',
      'Ready',
      'Draft',
      'AI Ready',
      'Publish Ready',
      'Needs Attention',
      'Auto voice',
    ];
    const headerMarkup = header.outerHTML;
    leakedEnglish.forEach((literal) => {
      expect(headerMarkup).not.toContain(literal);
    });
  });

  it('renders Smart Guidance as a truthful checklist without scores or invented actions', async () => {
    const onPublish = vi.fn();
    const onModeration = vi.fn();
    const copy = studioWorkspaceCopy('en');

    await act(async () => {
      root.render(
        <StudioSmartGuidance
          copy={copy}
          status="blocked"
          title={copy.guidanceTitle}
          summary={copy.guidanceSummaryBlocked}
          readiness={[
            { key: 'transcript', label: copy.guidanceTranscriptLabel, state: 'ready', detail: copy.guidanceTranscriptReady },
            { key: 'moderation', label: copy.guidanceModerationLabel, state: 'blocked', detail: 'Needs revision' },
            { key: 'publish', label: copy.guidancePublishLabel, state: 'blocked', detail: copy.guidancePublishBlocked },
          ]}
          blockers={[
            {
              key: 'moderation',
              severity: 'critical',
              title: copy.guidanceModerationLabel,
              detail: 'Text changed in Studio. Moderation needs to scan the updated text.',
              action: { label: copy.guidanceOpenModeration, onClick: onModeration },
            },
          ]}
          nextAction={{
            title: copy.creatorPublishReadyLesson,
            detail: copy.creatorDetailPublishReady,
            action: { label: copy.creatorPublish, onClick: onPublish, disabled: true },
          }}
        />,
      );
    });

    const guidance = host.querySelector('[data-testid="studio-smart-guidance"]');
    expect(guidance).toHaveAttribute('data-status', 'blocked');
    expect(guidance).toHaveAttribute('aria-labelledby', 'studio-smart-guidance-title');
    expect(guidance.textContent).toContain(copy.guidanceStatusBlocked);
    expect(guidance.textContent).toContain(copy.guidanceItemReady);
    expect(guidance.textContent).toContain(copy.guidanceItemBlocked);
    expect(guidance.querySelector(`ul[aria-label="${copy.guidanceReadinessLabel}"]`)).toBeTruthy();
    expect(guidance.querySelector(`ul[aria-label="${copy.guidanceBlockersLabel}"]`)).toBeTruthy();
    expect(guidance.textContent).not.toContain('%');
    expect(guidance.textContent.toLowerCase()).not.toContain('score');

    const publishButton = Array.from(guidance.querySelectorAll('button'))
      .find((button) => button.textContent.includes(copy.creatorPublish));
    expect(publishButton).toBeDisabled();

    const moderationButton = Array.from(guidance.querySelectorAll('button'))
      .find((button) => button.textContent.includes(copy.guidanceOpenModeration));
    await act(async () => moderationButton.click());
    expect(onModeration).toHaveBeenCalledTimes(1);
    expect(onPublish).not.toHaveBeenCalled();
  });

  it('supports ready, needs-attention, rendering, failed, and published Smart Guidance states', async () => {
    const copy = studioWorkspaceCopy('en');
    const states = ['ready', 'needs-attention', 'rendering', 'failed', 'published'];

    for (const state of states) {
      await act(async () => {
        root.render(
          <StudioSmartGuidance
            copy={copy}
            status={state}
            summary={copy.guidanceSummaryReady}
            readiness={[{ key: 'render', label: copy.guidanceRenderLabel, state: state === 'rendering' ? 'in-progress' : 'ready' }]}
            blockers={[]}
          />,
        );
      });

      expect(host.querySelector('[data-testid="studio-smart-guidance"]')).toHaveAttribute('data-status', state);
      expect(host.textContent).toContain(copy.guidanceNoBlockers);
    }
  });

  it('keeps the Creator Header compact when Smart Guidance owns the primary CTA', async () => {
    const onRender = vi.fn();
    const copy = studioWorkspaceCopy('en');

    await act(async () => {
      root.render(
        <>
          <StudioCreatorHeader
            copy={copy}
            title="Lesson"
            nextActionTitle={copy.creatorRenderUpdatedVideo}
            nextActionDetail={copy.creatorDetailRenderUpdated}
            primaryAction={null}
            secondaryActions={[]}
          />
          <StudioSmartGuidance
            copy={copy}
            status="needs-attention"
            summary={copy.guidanceSummaryNeedsAttention}
            readiness={[{ key: 'render', label: copy.guidanceRenderLabel, state: 'needs-attention' }]}
            blockers={[]}
            nextAction={{
              title: copy.creatorRenderUpdatedVideo,
              detail: copy.creatorDetailRenderUpdated,
              action: { label: copy.creatorRender, onClick: onRender },
            }}
          />
        </>,
      );
    });

    const header = host.querySelector('[data-testid="studio-creator-header"]');
    const guidance = host.querySelector('[data-testid="studio-smart-guidance"]');
    expect(header.querySelectorAll('button')).toHaveLength(0);
    expect(guidance.querySelectorAll('button')).toHaveLength(1);

    await act(async () => guidance.querySelector('button').click());
    expect(onRender).toHaveBeenCalledTimes(1);
  });

  it('lets the editor suppress duplicate Creator Header CTA ownership', async () => {
    const onRender = vi.fn();
    const copy = studioWorkspaceCopy('en');

    await act(async () => {
      root.render(
        <StudioCreatorHeader
          copy={copy}
          title="Lesson"
          nextActionTitle={copy.creatorRenderUpdatedVideo}
          nextActionDetail={copy.creatorDetailRenderUpdated}
          primaryAction={{ label: copy.creatorRender, onClick: onRender }}
          renderStatus={{ status: 'ready', progress: 100 }}
          showNextAction={false}
        />,
      );
    });

    const header = host.querySelector('[data-testid="studio-creator-header"]');
    expect(header.textContent).toContain('Render status: Ready');
    expect(header.textContent).not.toContain(copy.creatorNextBestAction);
    expect(header.textContent).not.toContain(copy.creatorRenderUpdatedVideo);
    expect(header.querySelectorAll('button')).toHaveLength(0);
    expect(onRender).not.toHaveBeenCalled();
  });

  it('renders Turkish and Arabic Smart Guidance copy without normal-path English leakage', async () => {
    document.documentElement.lang = 'tr-TR';
    let copy = studioWorkspaceCopy('tr-TR');

    await act(async () => {
      root.render(
        <StudioSmartGuidance
          copy={copy}
          status="needs-attention"
          summary={copy.guidanceSummaryNeedsAttention}
          readiness={[{ key: 'source', label: copy.guidanceSourceLabel, state: 'needs-attention', detail: copy.guidanceSourceMissing }]}
          blockers={[]}
        />,
      );
    });

    let guidance = host.querySelector('[data-testid="studio-smart-guidance"]');
    expect(guidance.textContent).toContain(copy.guidanceEyebrow);
    expect(guidance.textContent).toContain(copy.guidanceStatusNeedsAttention);
    expect(guidance.outerHTML).not.toContain('Smart guidance');
    expect(guidance.outerHTML).not.toContain('Needs attention');

    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    copy = studioWorkspaceCopy('ar');
    await act(async () => {
      root.render(
        <StudioSmartGuidance
          copy={copy}
          status="published"
          summary={copy.guidanceSummaryPublished}
          readiness={[{ key: 'publish', label: copy.guidancePublishLabel, state: 'ready', detail: copy.guidancePublishedDetail }]}
          blockers={[]}
        />,
      );
    });

    guidance = host.querySelector('[data-testid="studio-smart-guidance"]');
    expect(guidance.textContent).toContain(copy.guidanceEyebrow);
    expect(guidance.textContent).toContain(copy.guidanceStatusPublished);
    expect(guidance.outerHTML).not.toContain('Smart guidance');
    expect(guidance.outerHTML).not.toContain('Published');
  });

  it('renders first-run starting paths with real callbacks and dismiss semantics', async () => {
    const copy = studioWorkspaceCopy('en');
    const onUpload = vi.fn();
    const onDraft = vi.fn();
    const onDismiss = vi.fn();

    await act(async () => {
      root.render(
        <StudioFirstRunOnboarding
          copy={copy}
          options={[
            {
              key: 'upload',
              title: copy.startUploadTitle,
              description: copy.startUploadDescription,
              badge: copy.startRecommendedBadge,
              actionLabel: copy.startUploadAction,
              onAction: onUpload,
            },
            {
              key: 'draft',
              title: copy.startLocalDraftTitle,
              description: copy.startLocalDraftDescription,
              actionLabel: copy.startLocalDraftAction,
              onAction: onDraft,
            },
          ]}
          onDismiss={onDismiss}
        />,
      );
    });

    const onboarding = host.querySelector('[data-testid="studio-first-run-onboarding"]');
    expect(onboarding).toBeTruthy();
    expect(onboarding).toHaveAttribute('aria-labelledby', 'studio-first-run-title');
    expect(onboarding.textContent).toContain(copy.startUploadTitle);
    expect(onboarding.textContent).toContain(copy.startLocalDraftTitle);
    expect(onboarding.textContent).toContain(copy.firstRunStepContent);
    expect(onboarding.textContent.toLowerCase()).not.toContain('template');
    expect(onboarding.querySelector(`ul[aria-label="${copy.firstRunOptionsLabel}"]`)).toBeTruthy();
    expect(onboarding.querySelector(`ol[aria-label="${copy.firstRunStepsLabel}"]`)).toBeTruthy();

    const uploadButton = Array.from(onboarding.querySelectorAll('button'))
      .find((button) => button.textContent.includes(copy.startUploadAction));
    const draftButton = Array.from(onboarding.querySelectorAll('button'))
      .find((button) => button.textContent.includes(copy.startLocalDraftAction));
    const dismissButton = Array.from(onboarding.querySelectorAll('button'))
      .find((button) => button.getAttribute('aria-label') === copy.firstRunDismiss);

    await act(async () => uploadButton.click());
    await act(async () => draftButton.click());
    await act(async () => dismissButton.click());

    expect(onUpload).toHaveBeenCalledTimes(1);
    expect(onDraft).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('keeps disabled first-run starting paths keyboard-safe', async () => {
    const onAction = vi.fn();
    await act(async () => {
      root.render(
        <ul>
          <StudioStartOption
            title="Open an existing lesson"
            description="No lessons are available yet."
            actionLabel="Browse lessons"
            onAction={onAction}
            disabled
          />
        </ul>,
      );
    });

    const option = host.querySelector('button');
    expect(option).toBeDisabled();
    expect(option).toHaveAttribute('aria-describedby');
    await act(async () => option.click());
    expect(onAction).not.toHaveBeenCalled();
  });

  it('renders Turkish and Arabic first-run onboarding without normal-path English leakage', async () => {
    document.documentElement.lang = 'tr-TR';
    let copy = studioWorkspaceCopy('tr-TR');

    await act(async () => {
      root.render(
        <StudioFirstRunOnboarding
          copy={copy}
          options={[{
            key: 'upload',
            title: copy.startUploadTitle,
            description: copy.startUploadDescription,
            actionLabel: copy.startUploadAction,
            onAction: vi.fn(),
          }]}
        />,
      );
    });

    let onboarding = host.querySelector('[data-testid="studio-first-run-onboarding"]');
    expect(onboarding.textContent).toContain(copy.firstRunEyebrow);
    expect(onboarding.textContent).toContain(copy.startUploadAction);
    expect(onboarding.outerHTML).not.toContain('Start here');
    expect(onboarding.outerHTML).not.toContain('Upload source');
    expect(onboarding.outerHTML).not.toContain('template');

    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    copy = studioWorkspaceCopy('ar');
    await act(async () => {
      root.render(
        <StudioFirstRunOnboarding
          copy={copy}
          options={[{
            key: 'draft',
            title: copy.startLocalDraftTitle,
            description: copy.startLocalDraftDescription,
            actionLabel: copy.startLocalDraftAction,
            onAction: vi.fn(),
          }]}
          onDismiss={vi.fn()}
        />,
      );
    });

    onboarding = host.querySelector('[data-testid="studio-first-run-onboarding"]');
    expect(onboarding.textContent).toContain(copy.firstRunEyebrow);
    expect(onboarding.textContent).toContain(copy.startLocalDraftAction);
    expect(onboarding.querySelector('button[aria-label]')).toHaveAttribute('aria-label', copy.firstRunDismiss);
    expect(onboarding.outerHTML).not.toContain('Start here');
    expect(onboarding.outerHTML).not.toContain('Start writing');
    expect(onboarding.outerHTML).not.toContain('template');
  });
});
