import { Globe } from 'lucide-react';
import { useId, useRef } from 'react';

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { isLanguage, type Language, languageNames } from '@/lib/i18n';
import { cn } from '@/lib/utils';

type LanguageSelectProps = {
  readonly label: string;
  readonly language: Language;
  readonly mobile?: boolean;
  readonly setLanguage: (language: Language) => void;
};

export const LanguageSelect = ({
  label,
  language,
  mobile = false,
  setLanguage,
}: LanguageSelectProps) => {
  const id = useId();
  const languages = Object.keys(languageNames).filter(isLanguage);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Radix restores focus from script, which browsers treat as keyboard focus,
  // so a mouse pick would otherwise leave the focus ring on the trigger.
  const usedPointerRef = useRef(false);
  const markPointer = () => {
    usedPointerRef.current = true;
  };
  const markKeyboard = () => {
    usedPointerRef.current = false;
  };

  return (
    <div
      className={cn(mobile && 'border-t border-border pt-4')}
      data-dialog-persistent=""
    >
      <label
        className="sr-only"
        htmlFor={id}
        id={`${id}-label`}
      >
        {label}
      </label>
      <Select
        onValueChange={(value) => {
          if (isLanguage(value)) setLanguage(value);
        }}
        value={language}
      >
        <SelectTrigger
          className={cn(
            'transition-colors [&>svg:last-child]:transition-transform [&[data-state=open]>svg:last-child]:rotate-180',
            mobile
              ? 'h-auto min-h-11 gap-3 rounded-xl border-border bg-background/70 font-medium hover:bg-muted'
              : 'w-auto gap-1.5 rounded-xl border-border/60 bg-background/80 px-3 font-medium shadow-sm backdrop-blur-sm hover:bg-muted data-[state=open]:bg-muted',
          )}
          id={id}
          onKeyDown={markKeyboard}
          onPointerDown={markPointer}
          ref={triggerRef}
        >
          {mobile ? (
            <>
              <span aria-hidden="true">{label}</span>
              <span className="ml-auto flex items-center gap-2 font-normal text-muted-foreground">
                <Globe className="size-4" />
                <SelectValue>
                  <span lang={language}>{languageNames[language]}</span>
                </SelectValue>
              </span>
            </>
          ) : (
            <>
              <Globe className="size-4" />
              <SelectValue>
                <span title={languageNames[language]}>
                  {language.toUpperCase()}
                </span>
              </SelectValue>
            </>
          )}
        </SelectTrigger>
        <SelectContent
          align="end"
          aria-labelledby={`${id}-label`}
          className="min-w-52 rounded-xl border-border/60 bg-popover/95 shadow-lg backdrop-blur-md"
          data-dialog-persistent=""
          onCloseAutoFocus={(event) => {
            if (!usedPointerRef.current) return;
            event.preventDefault();
            triggerRef.current?.focus({
              focusVisible: false,
              preventScroll: true,
            });
          }}
          onKeyDown={markKeyboard}
          onPointerDown={markPointer}
          onPointerDownOutside={markPointer}
          position="popper"
          sideOffset={6}
        >
          <SelectGroup>
            {!mobile && (
              <SelectLabel className="pb-1 text-xs font-medium text-muted-foreground">
                {label}
              </SelectLabel>
            )}
            {languages.map((code) => (
              <SelectItem
                className={cn(
                  'group gap-3 rounded-lg focus:bg-muted focus:text-foreground [&_svg]:text-primary',
                  mobile ? 'min-h-11' : 'min-h-10',
                )}
                key={code}
                textValue={languageNames[code]}
                value={code}
              >
                <span className="flex items-center gap-3">
                  <span
                    aria-hidden="true"
                    className="inline-flex h-6 w-8 items-center justify-center rounded-md border border-border bg-muted/60 text-[0.6875rem] font-semibold tracking-wide text-muted-foreground transition-colors group-data-[state=checked]:border-primary/30 group-data-[state=checked]:bg-primary/10 group-data-[state=checked]:text-primary"
                  >
                    {code.toUpperCase()}
                  </span>
                  <span
                    className="group-data-[state=checked]:font-medium"
                    lang={code}
                  >
                    {languageNames[code]}
                  </span>
                </span>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  );
};
