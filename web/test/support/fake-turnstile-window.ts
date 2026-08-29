import { vi } from 'vitest';

export type CapturedRenderOptions = {
  readonly action: string;
  readonly appearance: string;
  readonly 'before-interactive-callback': () => void;
  readonly callback: (token: string) => void;
  readonly cdata: string;
  readonly 'error-callback': () => void;
  readonly execution: string;
  readonly sitekey: string;
  readonly 'timeout-callback': () => void;
};

export type FakeTurnstileApi = {
  readonly execute: ReturnType<typeof vi.fn<(widget: string) => void>>;
  readonly remove: ReturnType<typeof vi.fn<(widget: string) => void>>;
  readonly render: ReturnType<
    typeof vi.fn<
      (target: FakeElement, options: CapturedRenderOptions) => string
    >
  >;
};

export type FakeWindow = {
  readonly api: FakeTurnstileApi;
  readonly body: FakeElement;
  readonly head: FakeElement;
  readonly main: FakeElement;
  readonly renders: CapturedRenderOptions[];
  readonly scripts: FakeElement[];
};

type Listener = () => void;

class FakeClassList {
  public constructor(private readonly element: FakeElement) {}

  public add(...names: readonly string[]): void {
    const current = new Set(this.element.className.split(' ').filter(Boolean));
    for (const name of names) current.add(name);
    this.element.className = [...current].join(' ');
  }

  public contains(name: string): boolean {
    return this.element.className.split(' ').includes(name);
  }

  public remove(...names: readonly string[]): void {
    const removed = new Set(names);
    this.element.className = this.element.className
      .split(' ')
      .filter((name) => name !== '' && !removed.has(name))
      .join(' ');
  }
}

export class FakeElement {
  public ariaHidden: null | string = null;
  public async = false;
  public readonly children: FakeElement[] = [];
  public readonly classList = new FakeClassList(this);
  public className = '';
  public readonly dataset: Record<string, string> = {};
  public parentElement: FakeElement | null = null;
  public src = '';
  public textContent: null | string = null;
  public get firstElementChild(): FakeElement | null {
    return this.children.at(0) ?? null;
  }

  private readonly listeners = new Map<string, Listener[]>();

  public constructor(public readonly tagName: string) {}

  public addEventListener(type: string, listener: Listener): void {
    const listeners = this.listeners.get(type);
    if (listeners !== undefined) {
      listeners.push(listener);
      return;
    }
    this.listeners.set(type, [listener]);
  }

  public append(...children: readonly FakeElement[]): void {
    for (const child of children) {
      child.remove();
      child.parentElement = this;
      this.children.push(child);
    }
  }

  public dispatch(type: string): void {
    const listeners = this.listeners.get(type) ?? [];
    for (const listener of listeners) listener();
  }

  public remove(): void {
    const parent = this.parentElement;
    if (parent === null) return;
    const index = parent.children.indexOf(this);
    if (index !== -1) parent.children.splice(index, 1);
    this.parentElement = null;
  }

  public replaceChildren(...children: readonly FakeElement[]): void {
    for (const child of this.children) child.parentElement = null;
    this.children.length = 0;
    this.append(...children);
  }
}

export const installFakeTurnstileWindow = (): FakeWindow => {
  const body = new FakeElement('body');
  const head = new FakeElement('head');
  const main = new FakeElement('main');
  body.append(main);
  const scripts: FakeElement[] = [];
  const renders: CapturedRenderOptions[] = [];
  const api: FakeTurnstileApi = {
    execute: vi.fn(),
    remove: vi.fn(),
    render: vi.fn((_target, options) => {
      renders.push(options);
      return `widget-${renders.length}`;
    }),
  };
  const document = {
    body,
    createElement: (tagName: string) => {
      const element = new FakeElement(tagName);
      if (tagName === 'script') scripts.push(element);
      return element;
    },
    head,
    querySelector: (selector: string) => (selector === 'main' ? main : null),
  };
  vi.stubGlobal('document', document);
  vi.stubGlobal('HTMLElement', FakeElement);
  return { api, body, head, main, renders, scripts };
};

export const chrome = {
  body: 'Complete the check',
  title: 'Verify',
  verifying: 'Verifying',
} as const;
