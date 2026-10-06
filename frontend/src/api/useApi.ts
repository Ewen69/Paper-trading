import { useCallback, useEffect, useState } from 'react';

export type ApiState<T> =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; data: T };

/** Load data on mount (and on `reload`). Errors become an explicit state, never fallback data. */
export function useApi<T>(load: (signal: AbortSignal) => Promise<T>): [ApiState<T>, () => void] {
  const [state, setState] = useState<ApiState<T>>({ kind: 'loading' });
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => {
        setState({ kind: 'ready', data });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof Error ? error.message : 'unknown' });
      });
    return () => {
      controller.abort();
    };
  }, [load, generation]);

  const reload = useCallback(() => {
    setGeneration((g) => g + 1);
  }, []);
  return [state, reload];
}
