export type Tone = 'good' | 'warn' | 'bad' | 'muted' | 'info';

export const statusTone = (status: string): Tone =>
  status === 'ok' ? 'good' : status === 'warning' ? 'warn' : status === 'error' ? 'bad' : 'muted';

export const severityTone = (severity: string): Tone =>
  severity === 'error' ? 'bad' : severity === 'warning' ? 'warn' : 'info';
