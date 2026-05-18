function toNumber(value, fallback = 0) {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function labelFromText(text, fallback) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return fallback;
  return clean.length > 56 ? `${clean.slice(0, 56)}...` : clean;
}

export function formatTimestamp(totalSeconds) {
  const seconds = Math.max(0, Math.floor(toNumber(totalSeconds, 0)));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function buildChapters(transcriptPayload, lesson) {
  const pages = Array.isArray(transcriptPayload?.pages) ? transcriptPayload.pages : [];
  if (!pages.length) {
    return [{
      id: `chapter-${lesson?.id || 1}-1`,
      title: lesson?.title || 'Lesson',
      startSeconds: 0,
      endSeconds: 3600,
    }];
  }

  return pages.map((page, index) => {
    const start = toNumber(page.start_seconds, index * 60);
    const duration = toNumber(page.duration_seconds, 60);
    const end = toNumber(page.end_seconds, start + duration);
    const text = page.narration_text || page.original_text || '';
    return {
      id: page.id || `chapter-${index + 1}`,
      title: labelFromText(text, `Chapter ${index + 1}`),
      startSeconds: start,
      endSeconds: Math.max(end, start + 1),
    };
  });
}

export function buildTranscriptLines(transcriptPayload, lesson) {
  const pages = Array.isArray(transcriptPayload?.pages) ? transcriptPayload.pages : [];
  if (!pages.length) {
    return [{
      id: `line-${lesson?.id || 1}-1`,
      text: lesson?.description || 'Transcript will appear after rendering.',
      startSeconds: 0,
      endSeconds: 3600,
    }];
  }

  const lines = [];
  pages.forEach((page, pageIndex) => {
    const timeline = Array.isArray(page.chunk_timeline) ? page.chunk_timeline : [];
    if (timeline.length) {
      timeline.forEach((chunk, chunkIndex) => {
        const start = toNumber(chunk.start, toNumber(page.start_seconds, pageIndex * 60));
        const end = toNumber(chunk.end, start + 4);
        lines.push({
          id: `${page.id || pageIndex}-${chunkIndex}`,
          text: String(chunk.text || '').trim() || '...',
          startSeconds: start,
          endSeconds: Math.max(end, start + 1),
        });
      });
      return;
    }

    const start = toNumber(page.start_seconds, pageIndex * 60);
    const duration = toNumber(page.duration_seconds, 8);
    const end = toNumber(page.end_seconds, start + duration);
    lines.push({
      id: `${page.id || pageIndex}-0`,
      text: String(page.narration_text || page.original_text || '').trim() || '...',
      startSeconds: start,
      endSeconds: Math.max(end, start + 1),
    });
  });

  return lines;
}

