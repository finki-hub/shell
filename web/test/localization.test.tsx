import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FileList } from '@/components/file-panel/FileList';
import { LanguageProvider } from '@/hooks/LanguageProvider';
import { useLanguage } from '@/hooks/useLanguage';
import { type ContentsEntry } from '@/lib/contents-api';
import {
  isLanguage,
  type Language,
  languageLocales,
  translations,
} from '@/lib/i18n';
import { localizeHtml, updatePageMetadata } from '@/lib/page-metadata';
import { formatBytes, formatUsage } from '@/lib/storage';

import index from '../index.html?raw';

const languages = Object.keys(translations).filter(isLanguage);

const leafEntries = (value: object, prefix = ''): Array<[string, unknown]> =>
  Object.entries(value).flatMap(([key, item]: [string, unknown]) => {
    const path = `${prefix}${key}`;
    return typeof item === 'object' && item !== null
      ? leafEntries(item, `${path}.`)
      : [[path, item]];
  });

const setStoredLanguage = (value: null | string) => {
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => (key === 'finki-hub-lang' ? value : null),
    setItem: vi.fn(),
  });
};

const Probe = () => {
  const { language, t } = useLanguage();
  return <span lang={language}>{t.title}</span>;
};

describe('localization', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(languages)(
    '%s has the same nonempty dictionary shape',
    (language) => {
      const entries = leafEntries(translations[language]);
      expect(entries.map(([key]) => key)).toEqual(
        leafEntries(translations.en).map(([key]) => key),
      );
      for (const [, value] of entries) {
        expect(typeof value).toBe('string');
        expect(String(value).trim()).not.toBe('');
      }
    },
  );

  it.each(languages)('restores a saved %s preference', (language) => {
    setStoredLanguage(language);
    const html = renderToStaticMarkup(
      <LanguageProvider>
        <Probe />
      </LanguageProvider>,
    );
    expect(html).toContain(`lang="${language}"`);
    expect(html).toContain(translations[language].title);
  });

  it.each([null, '', 'de', 'SQ', 'constructor', '__proto__'])(
    'defaults to Macedonian for invalid preference %s',
    (value) => {
      expect(isLanguage(value)).toBe(false);
      setStoredLanguage(value);
      expect(
        renderToStaticMarkup(
          <LanguageProvider>
            <Probe />
          </LanguageProvider>,
        ),
      ).toContain('lang="mk"');
    },
  );

  it('still renders when local storage is blocked', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new DOMException('Blocked', 'SecurityError');
      },
    });
    expect(
      renderToStaticMarkup(
        <LanguageProvider>
          <Probe />
        </LanguageProvider>,
      ),
    ).toContain('Школка');
  });

  it.each(languages)(
    'localizes HTML and runtime metadata for %s',
    (language) => {
      const t = translations[language];
      const html = localizeHtml(index, language);
      expect(html).not.toContain('__LOCALIZED_');
      expect(html).toContain(`<html lang="${language}">`);
      expect(html).toContain(`<title>${t.brand} / ${t.title}</title>`);
      expect(html).toContain(t.metadataDescription);

      const attributes = new Map<string, string>();
      const page = {
        documentElement: { lang: '' },
        head: {
          querySelector: (selector: string) => ({
            setAttribute: (name: string, value: string) => {
              expect(name).toBe('content');
              attributes.set(selector, value);
            },
          }),
        },
        title: '',
      };
      updatePageMetadata(language, page as unknown as Document);
      expect(page.documentElement.lang).toBe(language);
      expect(page.title).toBe(`${t.brand} / ${t.title}`);
      expect(attributes.values().toArray()).toEqual([
        t.brand,
        t.metadataDescription,
        page.title,
        t.metadataDescription,
        languageLocales[language].replace('-', '_'),
        page.title,
        t.metadataDescription,
      ]);
    },
  );

  it('builds Macedonian metadata by default', () => {
    expect(localizeHtml(index)).toBe(localizeHtml(index, 'mk'));
  });

  it.each(languages)(
    'formats file sizes and storage with the %s locale',
    (language) => {
      const decimal = new Intl.NumberFormat(languageLocales[language], {
        maximumFractionDigits: 1,
        minimumFractionDigits: 1,
      });
      expect(formatBytes(1_572_864, language)).toBe(
        `${decimal.format(1.5)} MB`,
      );
      expect(
        formatUsage(
          {
            inodesTotal: 20_000,
            inodesUsed: 1_234,
            totalBytes: 5_242_880,
            usedBytes: 1_572_864,
          },
          language,
        ),
      ).toBe(`${decimal.format(1.5)} / ${decimal.format(5)} MB`);
    },
  );

  it.each(languages)(
    'uses %s dates and keeps original filenames',
    (language: Language) => {
      setStoredLanguage(language);
      const entry: ContentsEntry = {
        modifiedAt: '2026-10-15T09:30:00Z',
        name: 'белешки.txt',
        path: 'белешки.txt',
        size: 1_572_864,
        type: 'file',
      };
      const html = renderToStaticMarkup(
        <LanguageProvider>
          <FileList
            busy={false}
            entries={[entry]}
            onDelete={vi.fn()}
            onDownload={vi.fn()}
            onOpen={vi.fn()}
            onRename={vi.fn()}
          />
        </LanguageProvider>,
      );
      // eslint-disable-next-line unicorn/prefer-temporal -- match the browser Date formatter
      const modified = new Date(entry.modifiedAt);
      expect(html).toContain(
        modified.toLocaleString(languageLocales[language], {
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          month: 'short',
        }),
      );
      expect(html).toContain('белешки.txt');
      expect(html).toContain(translations[language].files.columnModified);
      expect(html).toContain(formatBytes(entry.size ?? 0, language));
    },
  );
});
