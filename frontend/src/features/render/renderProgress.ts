export interface RenderProgressState {
  status: string;
  phase: string;
  frames_rendered: number;
  total_frames: number | null;
}

export type RenderProgressPresentation =
  | { kind: 'status'; label: string }
  | { kind: 'progress'; label: string; value: number };

export function presentRenderProgress(render: RenderProgressState): RenderProgressPresentation {
  if (render.phase === 'preparing_assets') {
    return { kind: 'status', label: 'Preparando assets…' };
  }
  if (render.phase === 'rendering') {
    const total = render.total_frames;
    const value = total && total > 0 ? render.frames_rendered / total : 0;
    return {
      kind: 'progress',
      label: `Renderizando… (${render.frames_rendered}/${total ?? '?'} frames)`,
      value,
    };
  }
  if (render.phase === 'validating') {
    return { kind: 'status', label: 'Validando render…' };
  }
  if (render.status === 'queued' || render.phase === 'pending' || render.phase === 'queued') {
    return { kind: 'status', label: 'Render na fila…' };
  }
  if (render.status === 'failed') return { kind: 'status', label: 'Render falhou.' };
  if (render.status === 'completed') return { kind: 'status', label: 'Render concluído.' };
  return { kind: 'status', label: render.phase.replace(/_/g, ' ') };
}
