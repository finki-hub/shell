import { type AnchorHTMLAttributes, type ButtonHTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

const iconControlClass =
  'inline-flex h-8 w-8 sm:h-9 sm:w-9 cursor-pointer items-center justify-center rounded-md border border-input bg-background text-sm font-medium ring-offset-background transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2';

const IconButton = ({
  className,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) => (
  <button
    className={cn(iconControlClass, className)}
    type={type}
    {...props}
  />
);

const IconLink = ({
  className,
  title,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) => (
  <a
    aria-label={title}
    className={cn(iconControlClass, className)}
    title={title}
    {...props}
  />
);

export { IconButton, IconLink };
