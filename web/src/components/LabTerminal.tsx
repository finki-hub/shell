import '@xterm/xterm/css/xterm.css';
import { type RefObject } from 'react';

type LabTerminalProps = {
  readonly containerRef: RefObject<HTMLDivElement | null>;
};

export const LabTerminal = ({ containerRef }: LabTerminalProps) => (
  <div className="ph-no-capture h-full w-full overflow-hidden rounded-xl border">
    <div
      className="terminal-surface h-full w-full bg-[#0a0a0a] p-2"
      ref={containerRef}
    />
  </div>
);
