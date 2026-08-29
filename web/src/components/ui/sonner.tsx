import { Toaster as Sonner } from 'sonner';

import { useTheme } from '@/hooks/useTheme';

export const Toaster = (props: Parameters<typeof Sonner>[0]) => {
  const theme = useTheme();

  return (
    <Sonner
      className="toaster group"
      richColors
      theme={theme}
      toastOptions={{
        classNames: {
          actionButton:
            'group-[.toast]:bg-primary group-[.toast]:text-primary-foreground',
          cancelButton:
            'group-[.toast]:bg-muted group-[.toast]:text-muted-foreground',
          description: 'group-[.toast]:text-muted-foreground',
          toast:
            'group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg',
        },
      }}
      {...props}
    />
  );
};
