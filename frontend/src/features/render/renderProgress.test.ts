import assert from 'node:assert/strict';
import test from 'node:test';

import { presentRenderProgress } from './renderProgress';

test('asset preparation is a phase label, not a rendered percentage', () => {
  assert.deepEqual(
    presentRenderProgress({
      status: 'preparing_assets',
      phase: 'preparing_assets',
      frames_rendered: 0,
      total_frames: 26_290,
    }),
    { kind: 'status', label: 'Preparando assets…' },
  );
});

test('actual rendering uses the canonical frame ratio', () => {
  assert.deepEqual(
    presentRenderProgress({
      status: 'rendering',
      phase: 'rendering',
      frames_rendered: 9_727,
      total_frames: 26_290,
    }),
    {
      kind: 'progress',
      label: 'Renderizando… (9727/26290 frames)',
      value: 9_727 / 26_290,
    },
  );
});
