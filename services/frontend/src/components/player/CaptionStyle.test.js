import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const currentDir = dirname(fileURLToPath(import.meta.url));

function playerSource(filename) {
  return readFileSync(join(currentDir, filename), 'utf8');
}

describe('player caption readability styling', () => {
  it.each(['VideoStage.jsx', 'HlsPlayer.jsx'])('%s uses a readable caption pill', (filename) => {
    const source = playerSource(filename);

    expect(source).toContain('CAPTION_PILL_CLASSNAME');
    expect(source).toContain('bg-black/80');
    expect(source).toContain('text-white');
    expect(source).toContain('ring-white/20');
    expect(source).toContain('CAPTION_TEXT_SHADOW');
    expect(source).toContain('.visus-shell-video::cue');
    expect(source).toContain('background-color: rgba(0, 0, 0, 0.82)');
    expect(source).not.toContain('bg-black/78');
  });
});
