export const GITHUB_URL = 'https://github.com/finki-hub/shell';

export const CHALLENGE_CHANNEL = 'lab.challenge';
// Long enough for every open tab to answer, short enough to be invisible
// against a grace window measured in minutes.
export const ELECTION_WINDOW_MS = 300;
// The elected tab says so while it holds the challenge; the others treat
// silence as its disappearance and elect somebody else.
export const ELECTION_HEARTBEAT_MS = 1_000;
export const ELECTION_SILENCE_MS = 4_000;

// Often enough that a visible tab never trips the idle reaper, rare enough to
// be invisible next to the terminal's own traffic.
export const PRESENCE_INTERVAL_MS = 60_000;
