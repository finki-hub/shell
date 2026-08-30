import { afterEach, describe, expect, it, vi } from 'vitest';

import { DialogContent } from '@/components/ui/dialog';

class InteractionTarget extends EventTarget {
  public constructor(private persistent: boolean) {
    super();
  }

  public closest(selector: string): InteractionTarget | null {
    return this.persistent && selector === '[data-dialog-persistent]'
      ? this
      : null;
  }

  public detach(): void {
    this.persistent = false;
  }
}

const outsideHandler = (onInteractOutside: () => void): unknown => {
  const content = DialogContent({ children: null, onInteractOutside });
  const props: unknown = content.props;

  if (
    typeof props !== 'object' ||
    props === null ||
    !('onInteractOutside' in props)
  ) {
    throw new Error('Dialog content has no outside-interaction handler');
  }

  const handler: unknown = Reflect.get(props, 'onInteractOutside');
  return handler;
};

const dispatchOutside = (target: EventTarget, handler: unknown): Event => {
  if (typeof handler !== 'function') {
    throw new TypeError('Dialog outside-interaction handler is not callable');
  }

  const event = new Event('dialog-outside', { cancelable: true });
  target.addEventListener(
    'dialog-outside',
    (outsideEvent) => {
      Reflect.apply(handler, undefined, [outsideEvent]);
    },
    { once: true },
  );
  target.dispatchEvent(event);
  return event;
};

describe('dialog outside interactions', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('does not dismiss when a persistent control rerenders on interaction', () => {
    vi.stubGlobal('Element', InteractionTarget);
    const target = new InteractionTarget(true);
    const originalHandler = vi.fn(() => {
      target.detach();
    });

    const event = dispatchOutside(target, outsideHandler(originalHandler));

    expect(originalHandler).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(true);
  });

  it('still permits dismissal for ordinary outside interactions', () => {
    vi.stubGlobal('Element', InteractionTarget);

    const event = dispatchOutside(
      new InteractionTarget(false),
      outsideHandler(vi.fn()),
    );

    expect(event.defaultPrevented).toBe(false);
  });
});
