import { createRoot } from 'react-dom/client';

import { App } from '@/App';
import { initializeAnalytics } from '@/lib/analytics';
import '@/index.css';

void initializeAnalytics();

const root = document.querySelector('#root');

if (root === null) {
  throw new Error('Root element not found');
}

createRoot(root).render(<App />);
