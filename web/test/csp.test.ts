import { describe, expect, it } from 'vitest';

import caddyfile from '../Caddyfile?raw';

const policy = /Content-Security-Policy "(?<policy>[^"]+)"/u.exec(caddyfile)
  ?.groups?.['policy'];

if (policy === undefined) throw new Error('Caddyfile CSP header is missing');

const directives = new Map(
  policy.split('; ').map((directive) => {
    const [name, ...sources] = directive.split(' ');
    return [name, sources] as const;
  }),
);

describe('Caddy Content Security Policy', () => {
  it('allows only the required Cloudflare Web Analytics script origin', () => {
    expect(directives.get('script-src')).toEqual([
      "'self'",
      'https://challenges.cloudflare.com',
      'https://static.cloudflareinsights.com',
    ]);
    expect(directives.get('connect-src')).toEqual([
      "'self'",
      'https://challenges.cloudflare.com',
      'https://eu.i.posthog.com',
    ]);
    expect(directives.get('frame-ancestors')).toEqual(["'none'"]);
    expect(directives.get('object-src')).toEqual(["'none'"]);
    expect(directives.get('base-uri')).toEqual(["'none'"]);
  });
});
