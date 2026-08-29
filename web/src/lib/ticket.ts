import { TicketSuccessResponseSchema } from '@shell/protocol';

import { readExpiredEnvironmentToken } from '@/lib/environment';
import { type TicketResult } from '@/lib/lab-session-protocol';
import { classifyHttpStatus } from '@/lib/protocol';
import {
  type ChallengeAction,
  type ChallengeChrome,
  isChallengeConfigured,
  solveChallenge,
} from '@/lib/turnstile';

// A held environment means "let me back into my own work"; no token means
// "give me a new one". They are different asks and Cloudflare is told which,
// so a challenge solved for one cannot be spent on the other.
export const challengeAction = (
  environmentToken: null | string,
): ChallengeAction =>
  environmentToken === null ? 'env_create' : 'session_start';

// Distinguished rather than collapsed into a single failure. "Your
// environment is gone" is something the browser can act on by itself — retire
// the token and ask for a new one — and reporting it as a generic failure
// leaves a returning user pressing Restart against a request that will
// never succeed, told their session ended because a check went unanswered.
const parseJson = (text: string): unknown => {
  try {
    return JSON.parse(text);
  } catch (error) {
    if (error instanceof SyntaxError) {
      return null;
    }

    throw error;
  }
};

// The socket cannot open without one of these, so a failure here is a failure
// to start. The widget renders into its own overlay rather than into the page:
// at this point there is no session yet, so there is nothing to put a dialog
// on top of.
type TicketRequest = {
  readonly chrome: ChallengeChrome;
  readonly environmentToken: null | string;
  readonly signal?: AbortSignal;
};

export const requestTicket = async ({
  chrome,
  environmentToken,
  signal,
}: TicketRequest): Promise<TicketResult> => {
  const retired =
    environmentToken === null ? readExpiredEnvironmentToken() : null;
  const action = challengeAction(environmentToken);
  // Whether Cloudflare put a puzzle in front of them, which is the difference
  // between two failures that look identical from here. Both end as a null.
  const asked = { person: false };
  const solved = await solveChallenge(action, {
    chrome,
    environmentToken,
    onInteractive: () => {
      asked.person = true;
    },
    signal,
  });

  // A challenge that is configured and could not be completed is its own
  // answer, and the user deserves to hear it: something on this network is
  // blocking the thing that lets them in. Sending an empty body instead got a
  // 400 back and reported it as an unanswered verification check, about an
  // environment they never had.
  //
  // Unless a puzzle was actually shown, in which case nothing is blocked and
  // saying so sends them hunting for a firewall that does not exist. The solve
  // timeout is a minute, which a real person reading a real puzzle can take,
  // and telling them the truth costs one click on the same screen.
  if (solved === null && (await isChallengeConfigured(signal))) {
    return asked.person ? { kind: 'unanswered' } : { kind: 'blocked' };
  }
  let response: Response;
  let text: string;
  try {
    response = await fetch('/api/session/ticket', {
      body: JSON.stringify({
        action,
        environmentToken,
        ...(solved !== null && {
          nonce: solved.nonce,
          token: solved.token,
        }),
      }),
      headers: {
        'Content-Type': 'application/json',
        // Named only when asking for a replacement, which is the user saying
        // they have finished with it. The server may reclaim its space to make
        // room, and will not touch it otherwise.
        ...(retired !== null && { 'X-Lab-Retired': retired }),
      },
      method: 'POST',
      signal,
    });
    text = await response.text();
  } catch (error) {
    if (error instanceof Error) {
      return { kind: 'network' };
    }

    throw error;
  }

  if (!response.ok) {
    return classifyHttpStatus(response.status);
  }

  const parsed = TicketSuccessResponseSchema.safeParse(parseJson(text));

  return parsed.success
    ? { kind: 'ticket', ticket: parsed.data.ticket }
    : { kind: 'malformed' };
};
