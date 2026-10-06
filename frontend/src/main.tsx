import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import '@fontsource/press-start-2p/400.css';

import { App } from './App';
import './index.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root element missing');

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
