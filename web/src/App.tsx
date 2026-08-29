import { Lab } from '@/components/Lab';
import { Toaster } from '@/components/ui/sonner';
import { LanguageProvider } from '@/hooks/LanguageProvider';

export const App = () => (
  <LanguageProvider>
    <Lab />
    <Toaster position="bottom-right" />
  </LanguageProvider>
);
