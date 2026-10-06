import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import '@fontsource/exo-2/400.css';
import '@fontsource/exo-2/700.css';
import '@fontsource/exo-2/800.css';

import { App } from './App';
import './index.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root element missing');

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
