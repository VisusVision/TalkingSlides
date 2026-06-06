export function formatDuration(minutes) {
  const total = Math.max(1, Number(minutes || 0));
  const hours = Math.floor(total / 60);
  const mins = Math.round(total % 60);
  if (hours > 0) {
    return `${hours}h ${mins}m`;
  }
  return `${mins}m`;
}

export function formatViews(value) {
  const count = Math.max(0, Number(value || 0));
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M views`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K views`;
  return `${count} views`;
}

export function normalizeLesson(input, fallbackBadge = '') {
  const lesson = input || {};
  return {
    id: lesson.id,
    title: lesson.title || `Lesson #${lesson.id || 'X'}`,
    description: lesson.description || lesson.summary || '',
    teacherName: lesson.teacher_name || lesson.publisher_name || 'VISUS Instructor',
    teacherId: lesson.teacher_id || null,
    teacherUsername: lesson.teacher_username || lesson.publisher_username || '',
    categoryName: lesson.category_name || 'General',
    categorySlug: lesson.category_slug || '',
    durationMinutes: Number(lesson.duration_minutes || 8),
    views: Number(lesson.view_count || 0),
    progress: Math.max(0, Math.min(100, Number(lesson.user_progress || 0))),
    isSaved: Boolean(lesson.is_saved),
    badge: fallbackBadge || (lesson.is_recommended ? 'Recommended' : ''),
    createdAt: lesson.created_at || null,
    imageUrl: lesson.thumbnail_url || lesson.cover_url || '',
    followerCount: Number(lesson.follower_count || lesson.publisher_follower_count || 0),
    isFollowingPublisher: Boolean(lesson.is_following_publisher || lesson.publisher_is_following),
  };
}

export function sectionsFromFeed(feedPayload) {
  const sections = Array.isArray(feedPayload?.sections) ? feedPayload.sections : [];

  return sections
    .map((section) => {
      const title = section.title || 'For You';
      const items = Array.isArray(section.items)
        ? section.items.map((item) => normalizeLesson(item, title.includes('Recommended') ? 'Recommended' : ''))
        : [];

      return {
        key: section.key || title.toLowerCase().replace(/\s+/g, '-'),
        title,
        items,
      };
    })
    .filter((section) => section.items.length > 0);
}

export function fallbackSections(catalogPayload = []) {
  const list = (Array.isArray(catalogPayload) ? catalogPayload : catalogPayload.results || [])
    .map((item) => normalizeLesson(item, 'Recommended'));

  return [
    { key: 'recommended', title: 'Recommended For You', items: list.slice(0, 12) },
    { key: 'trending', title: 'Trending Right Now', items: list.slice(4, 16) },
    {
      key: 'continue-learning',
      title: 'Continue Learning',
      items: list.filter((item) => item.progress > 0).slice(0, 12),
    },
  ].filter((section) => section.items.length > 0);
}
