import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

import { stubSocket } from './fakeSocket';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// The app shell keeps a telemetry WebSocket open; tests get a silent fake by default.
beforeEach(() => {
  stubSocket();
});
