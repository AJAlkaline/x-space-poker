/**
 * Event log builder.
 *
 * Takes a stream of ServerMessage and produces human-readable log entries
 * for display in the UI. Entries are derived purely from public messages
 * the client already receives — there is no protocol change. The builder
 * holds enough state to detect phase transitions (by board length diff)
 * and seat changes (by seats array diff).
 *
 * One ServerMessage may produce 0+ entries. For example a state_update
 * that triggers the flop deal produces "Alice calls 30." AND "Flop: 7♠ 8♥ K♣."
 *
 * Entries hold both the human-readable narration and the raw underlying
 * message so the debug-mode toggle can show the payload that produced them.
 */
import type {
  ActionInfo,
  PotDistribution,
  PublicPlayer,
  PublicState,
  SeatInfo,
  ServerMessage,
} from "./types";

export type LogLevel = "info" | "warning" | "error";

export interface LogEntry {
  /** Stable per-process id for React keys. */
  id: number;
  /** Wall-clock millis when the entry was created. */
  timestamp: number;
  level: LogLevel;
  /** Human-readable narration text. */
  text: string;
  /** Source message that produced this entry; rendered when debug mode is on. */
  source: ServerMessage;
}

export interface EventLogState {
  entries: LogEntry[];
  /** Track the last public_state so we can detect phase transitions. */
  lastPublicState: PublicState | null;
  /** Track last seats so we can diff for join/leave/disconnect/reconnect. */
  lastSeats: (SeatInfo | null)[] | null;
  /** Track which hands we've already announced hole cards for, so we
   *  emit "You were dealt..." exactly once per hand. */
  announcedHoleHandIds: Set<string>;
  /** Monotonic id counter. */
  nextId: number;
}

export const MAX_ENTRIES = 500;

export function emptyLogState(): EventLogState {
  return {
    entries: [],
    lastPublicState: null,
    lastSeats: null,
    announcedHoleHandIds: new Set(),
    nextId: 1,
  };
}

// ---------------------------------------------------------------------------
// Card formatting
// ---------------------------------------------------------------------------

const SUIT_GLYPHS: Record<string, string> = {
  s: "♠",
  h: "♥",
  d: "♦",
  c: "♣",
};

/** Convert "7s" -> "7♠", "Ah" -> "A♥". Pass-through if it doesn't match. */
function formatCard(card: string): string {
  if (card.length < 2) return card;
  const rank = card.slice(0, -1).toUpperCase();
  const suit = SUIT_GLYPHS[card.slice(-1).toLowerCase()] ?? card.slice(-1);
  return `${rank}${suit}`;
}

function formatBoard(cards: string[]): string {
  return cards.map(formatCard).join(" ");
}

// ---------------------------------------------------------------------------
// Player lookup helpers
// ---------------------------------------------------------------------------

function playerLabel(state: PublicState | null, playerId: string): string {
  // Format: "POS @handle" when the player has a position label on the
  // current state, "@handle" otherwise. Position is hand-scoped — between
  // hands or for spectators viewing a non-active table, position may be
  // missing and we fall back to just the handle.
  const player = findPlayer(state, playerId);
  const position = player?.position;
  if (position) {
    return `${position} @${playerId}`;
  }
  return `@${playerId}`;
}

function findPlayer(
  state: PublicState | null,
  playerId: string,
): PublicPlayer | null {
  if (!state) return null;
  for (const p of state.players) {
    if (p && p.id === playerId) return p;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Entry construction (pure functions returning narration text or null)
// ---------------------------------------------------------------------------

function narrateAction(
  action: ActionInfo,
  newState: PublicState,
  _myHandle: string | null,
): string {
  const who = playerLabel(newState, action.player_id);
  const auto = action.auto ? " (clock)" : "";
  switch (action.action_type) {
    case "fold":
      return `${who} folds${auto}.`;
    case "check":
      return `${who} checks${auto}.`;
    case "call": {
      // The amount on a CALL action is what they paid this action, not the
      // total to-call. The wire protocol sets amount=0 for the inbound CALL,
      // so we look at the player's street_committed in the new state to
      // figure out what they actually paid.
      const player = findPlayer(newState, action.player_id);
      const allIn = player?.status === "all_in" ? " (all-in)" : "";
      return `${who} calls${allIn}${auto}.`;
    }
    case "bet": {
      const player = findPlayer(newState, action.player_id);
      const allIn = player?.status === "all_in" ? " (all-in)" : "";
      return `${who} bets ${action.amount}${allIn}${auto}.`;
    }
    case "raise": {
      const player = findPlayer(newState, action.player_id);
      const allIn = player?.status === "all_in" ? " (all-in)" : "";
      return `${who} raises to ${action.amount}${allIn}${auto}.`;
    }
    case "post_blind":
      return `${who} posts blind ${action.amount}.`;
    default:
      return `${who} acts: ${action.action_type}.`;
  }
}

/** Detect a street transition by comparing board lengths, return the
 *  narration line for the new street or null. */
function narratePhaseTransition(
  prev: PublicState | null,
  next: PublicState,
): string | null {
  const prevLen = prev?.board.length ?? 0;
  const nextLen = next.board.length;
  if (nextLen === prevLen) return null;
  if (nextLen === 3 && prevLen === 0) {
    return `Flop: ${formatBoard(next.board)}`;
  }
  if (nextLen === 4 && prevLen === 3) {
    return `Turn: ${formatCard(next.board[3])}`;
  }
  if (nextLen === 5 && prevLen === 4) {
    return `River: ${formatCard(next.board[4])}`;
  }
  // Unusual jumps (e.g. all-in run-out from preflop directly to 5 cards)
  // happen when run_out_board fires.
  if (nextLen === 5 && prevLen < 3) {
    return `Board: ${formatBoard(next.board)} (run-out)`;
  }
  return null;
}

/** Diff two seat arrays, return narration lines for each change. */
function narrateSeatDiff(
  prev: (SeatInfo | null)[] | null,
  next: (SeatInfo | null)[],
): string[] {
  const lines: string[] = [];
  const prevBySeat = new Map<number, SeatInfo>();
  if (prev) {
    for (let i = 0; i < prev.length; i++) {
      const s = prev[i];
      if (s) prevBySeat.set(s.seat, s);
    }
  }
  const nextBySeat = new Map<number, SeatInfo>();
  for (let i = 0; i < next.length; i++) {
    const s = next[i];
    if (s) nextBySeat.set(s.seat, s);
  }
  // Joins
  for (const [seat, info] of nextBySeat) {
    if (!prevBySeat.has(seat)) {
      lines.push(`@${info.user_id} joined seat ${seat}.`);
    }
  }
  // Leaves
  for (const [seat, info] of prevBySeat) {
    if (!nextBySeat.has(seat)) {
      lines.push(`@${info.user_id} left seat ${seat}.`);
    }
  }
  // Connect/disconnect transitions for players still seated
  for (const [seat, nextInfo] of nextBySeat) {
    const prevInfo = prevBySeat.get(seat);
    if (!prevInfo) continue;
    if (!prevInfo.disconnected && nextInfo.disconnected) {
      lines.push(`@${nextInfo.user_id} disconnected.`);
    } else if (prevInfo.disconnected && !nextInfo.disconnected) {
      lines.push(`@${nextInfo.user_id} reconnected.`);
    }
  }
  return lines;
}

/** Build the hand-complete summary. When pot_distributions are provided
 *  (the modern wire format), we narrate each pot with the winner's hand
 *  description. For fold-wins (everyone else folded), we keep the original
 *  "everyone else folded" framing. */
function narrateHandComplete(
  state: PublicState,
  distributions: PotDistribution[],
): string[] {
  // No distributions (defensive — server should always populate). Fall
  // back to the old behavior.
  if (!distributions || distributions.length === 0) {
    const inHand = state.players.filter(
      (p): p is PublicPlayer =>
        p !== null && (p.status === "active" || p.status === "all_in"),
    );
    if (inHand.length === 1) {
      return [`@${inHand[0].id} wins ${state.pot_total}: everyone else folded.`];
    }
    return [`Hand complete. Pot: ${state.pot_total}.`];
  }

  const lines: string[] = [];
  const isFoldWin =
    distributions.length === 1 &&
    distributions[0].winners.length === 1 &&
    distributions[0].winners[0].hand_description === "";

  if (isFoldWin) {
    const w = distributions[0].winners[0];
    lines.push(
      `@${w.player_id} wins ${distributions[0].amount}: everyone else folded.`,
    );
    return lines;
  }

  // Showdown — narrate each pot. For a single main pot the common case
  // reads cleanly as "Showdown. @alice wins 280 with Two Pair, Kings and
  // Fours." For side pots we prefix with "Side pot X:".
  const labelFor = (i: number, n: number): string => {
    if (n === 1) return "";
    if (i === 0) return "Main pot: ";
    if (n === 2) return "Side pot: ";
    return `Side pot ${i}: `;
  };

  for (let i = 0; i < distributions.length; i++) {
    const d = distributions[i];
    if (d.winners.length === 0) continue; // unfunded / no eligible winners
    const prefix = labelFor(i, distributions.length);
    if (d.winners.length === 1) {
      const w = d.winners[0];
      const handPart = w.hand_description ? ` with ${w.hand_description}` : "";
      lines.push(`${prefix}@${w.player_id} wins ${d.amount}${handPart}.`);
    } else {
      // Chop.
      const names = d.winners.map((w) => `@${w.player_id}`).join(", ");
      const desc = d.winners[0].hand_description;
      const each = Math.floor(d.amount / d.winners.length);
      lines.push(
        `${prefix}${names} split ${d.amount} (${each} each)` +
          (desc ? ` with ${desc}` : "") +
          ".",
      );
    }
  }

  return lines;
}

// ---------------------------------------------------------------------------
// The builder — applies one message, returns a new state
// ---------------------------------------------------------------------------

interface PendingEntry {
  level: LogLevel;
  text: string;
}

export function applyMessage(
  prev: EventLogState,
  msg: ServerMessage,
  myHandle: string | null,
): EventLogState {
  const pending: PendingEntry[] = [];
  let lastPublicState = prev.lastPublicState;
  let lastSeats = prev.lastSeats;
  let announcedHoleHandIds = prev.announcedHoleHandIds;

  switch (msg.type) {
    case "hand_started": {
      const state = msg.state;
      const button = findButtonPlayer(state);
      const sb = state.small_blind;
      const bb = state.big_blind;
      const buttonStr = button ? `${playerLabel(state, button.id)} on button` : "";
      pending.push({
        level: "info",
        text:
          `New hand started. Blinds ${sb}/${bb}` +
          (buttonStr ? `. ${buttonStr}.` : "."),
      });
      lastPublicState = state;
      break;
    }
    case "state_update": {
      const state = msg.state;
      // Action narration first.
      if (msg.action) {
        pending.push({
          level: "info",
          text: narrateAction(msg.action, state, myHandle),
        });
      }
      // Then phase transition if any.
      const phaseLine = narratePhaseTransition(lastPublicState, state);
      if (phaseLine) {
        pending.push({ level: "info", text: phaseLine });
      }
      lastPublicState = state;
      break;
    }
    case "hand_complete": {
      const state = msg.state;
      // Final phase transition (e.g. all-in run-out from earlier streets).
      const phaseLine = narratePhaseTransition(lastPublicState, state);
      if (phaseLine) {
        pending.push({ level: "info", text: phaseLine });
      }
      // Optional showdown reveal line for multi-way pots — list who's still
      // in with their hole cards, before we announce winners.
      const inHand = state.players.filter(
        (p): p is PublicPlayer =>
          p !== null && (p.status === "active" || p.status === "all_in"),
      );
      if (inHand.length >= 2) {
        const revealed = inHand
          .filter((p) => p.hole && p.hole.length === 2)
          .map((p) => `@${p.id} (${p.hole!.map(formatCard).join(" ")})`)
          .join(", ");
        if (revealed) {
          pending.push({ level: "info", text: `Showdown: ${revealed}.` });
        }
      }
      for (const line of narrateHandComplete(state, msg.pot_distributions ?? [])) {
        pending.push({ level: "info", text: line });
      }
      lastPublicState = state;
      break;
    }
    case "hand_aborted": {
      const refunds = Object.entries(msg.refunds);
      if (refunds.length === 0) {
        pending.push({ level: "warning", text: "Hand aborted." });
      } else {
        const refundStr = refunds
          .map(([who, amt]) => `@${who}: ${amt}`)
          .join(", ");
        pending.push({
          level: "warning",
          text: `Hand aborted. Refunds: ${refundStr}.`,
        });
      }
      // After an abort, the server treats the next deal as a fresh hand —
      // reset our public-state tracker.
      lastPublicState = null;
      break;
    }
    case "seats": {
      const lines = narrateSeatDiff(lastSeats, msg.seats);
      for (const text of lines) {
        pending.push({ level: "info", text });
      }
      lastSeats = msg.seats;
      break;
    }
    case "private": {
      // Announce hole cards exactly once per hand. We don't have hand_id on
      // the private message itself, so we use the most recently announced
      // hand from the public state. If no public state yet, skip — the
      // hand_started will land first in practice.
      const handId = lastPublicState?.hand_id;
      if (
        handId &&
        msg.state.hole &&
        msg.state.hole.length === 2 &&
        !announcedHoleHandIds.has(handId)
      ) {
        const [c1, c2] = msg.state.hole;
        pending.push({
          level: "info",
          text: `You were dealt ${formatCard(c1)} ${formatCard(c2)}.`,
        });
        announcedHoleHandIds = new Set(announcedHoleHandIds);
        announcedHoleHandIds.add(handId);
      }
      break;
    }
    case "illegal_action": {
      pending.push({
        level: "warning",
        text: `Illegal action: ${msg.error}`,
      });
      break;
    }
    case "table_error": {
      pending.push({ level: "error", text: `Table error: ${msg.error}` });
      break;
    }
    case "viewer_count":
      // Intentionally not logged — too noisy.
      return prev;
  }

  if (pending.length === 0) {
    // No entries produced; only state shifts (e.g. private without new hole).
    if (
      lastPublicState === prev.lastPublicState &&
      lastSeats === prev.lastSeats &&
      announcedHoleHandIds === prev.announcedHoleHandIds
    ) {
      return prev;
    }
    return {
      ...prev,
      lastPublicState,
      lastSeats,
      announcedHoleHandIds,
    };
  }

  const now = Date.now();
  let nextId = prev.nextId;
  const newEntries: LogEntry[] = pending.map((p) => ({
    id: nextId++,
    timestamp: now,
    level: p.level,
    text: p.text,
    source: msg,
  }));
  // Append, then trim to MAX_ENTRIES from the head.
  const merged = prev.entries.concat(newEntries);
  const trimmed =
    merged.length > MAX_ENTRIES ? merged.slice(merged.length - MAX_ENTRIES) : merged;
  return {
    entries: trimmed,
    lastPublicState,
    lastSeats,
    announcedHoleHandIds,
    nextId,
  };
}

function findButtonPlayer(state: PublicState): PublicPlayer | null {
  for (const p of state.players) {
    if (p && p.seat === state.button) return p;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Format a wall-clock timestamp as "Xs ago" / "Xm ago" relative to now.
// ---------------------------------------------------------------------------

export function relativeTime(ts: number, now: number): string {
  const ageMs = Math.max(0, now - ts);
  const ageSec = Math.floor(ageMs / 1000);
  if (ageSec < 1) return "just now";
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.floor(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  const ageHr = Math.floor(ageMin / 60);
  return `${ageHr}h ago`;
}
