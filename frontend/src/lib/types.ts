// Mirrors backend `services/table_manager.py` view helpers.

export type Phase =
  | "pre_deal"
  | "pre_flop"
  | "flop"
  | "turn"
  | "river"
  | "showdown"
  | "complete";
export type PlayerStatus =
  | "active"
  | "folded"
  | "all_in"
  | "sitting_out"
  | "disconnected";
export type ActionType =
  | "fold"
  | "check"
  | "call"
  | "bet"
  | "raise"
  | "post_blind";

export interface PublicPlayer {
  id: string;
  seat: number;
  stack: number;
  status: PlayerStatus;
  street_committed: number;
  total_committed: number;
  last_action: ActionType | null;
  /** Position label: BTN, SB, BB, UTG, UTG+1, MP, LJ, HJ, CO. May be null
   *  during state transitions (e.g. between hands). */
  position: string | null;
  hole: string[] | null;
}

export interface PublicPot {
  amount: number;
  eligible: string[];
}

export interface PotDistributionWinner {
  player_id: string;
  /** "Two Pair, Aces and Kings" — empty string for fold-wins (no showdown). */
  hand_description: string;
  /** The 5 cards that compose the winning hand. Empty for fold-wins. */
  best_five: string[];
}

export interface PotDistribution {
  amount: number;
  winners: PotDistributionWinner[];
}

export interface PublicState {
  hand_id: string;
  /** Session hand counter (1-indexed). 0 when no hand has been dealt yet. */
  hand_number: number;
  phase: Phase;
  board: string[];
  pots: PublicPot[];
  pot_total: number;
  current_bet: number;
  min_raise: number;
  to_act: string[];
  /** Absolute Unix epoch milliseconds by which the to-act player must act.
   *  Used by clients to render countdown badges for the active player
   *  visible to everyone, not just the actor. Null between hands or at
   *  hand complete. */
  to_act_deadline_unix_ms: number | null;
  /** Absolute deadline (ms since epoch) when the to-act player's base
   *  25s timer expires. After this, the time bank takes over until
   *  `to_act_deadline_unix_ms`. Observers render a two-phase countdown
   *  matching the actor's own ActionTimer. Null between hands or when
   *  no one is to act. Optional — older servers may omit this; fall
   *  back to `to_act_deadline_unix_ms` for a single-phase display. */
  to_act_base_deadline_unix_ms?: number | null;
  button: number;
  small_blind: number;
  big_blind: number;
  players: (PublicPlayer | null)[];
}

export interface LegalAction {
  action_type: ActionType;
  min_amount: number;
  max_amount: number;
}

export interface PrivateState {
  hole: [string, string] | null;
  your_turn: boolean;
  legal_actions: LegalAction[];
  base_deadline_unix_ms: number | null;
  bank_deadline_unix_ms: number | null;
  timebank_remaining_ms: number | null;
  action_timer_seconds: number | null;
  /** Best 5-card hand the player currently has, given their hole cards
   *  plus the visible board. Populated on the flop and later. Null
   *  pre-flop (when the player has only 2 cards) or when the player is
   *  folded/not in the hand. */
  current_hand: {
    description: string;
    best_five: string[];
  } | null;
}

export interface SeatInfo {
  seat: number;
  user_id: string;
  stack: number;
  sitting_out: boolean;
  disconnected: boolean;
}

export type ServerMessage =
  | { type: "hand_started"; state: PublicState }
  | { type: "state_update"; state: PublicState; action?: ActionInfo }
  | {
      type: "hand_complete";
      state: PublicState;
      pot_distributions: PotDistribution[];
      /** Absolute deadline (ms since epoch) for the next hand's auto-start.
       * If the countdown passes without a `hand_started` message, the loop is
       * blocked waiting for more eligible players to sit. May be 0 from
       * older servers that don't emit this field — treat 0 as "unknown". */
      next_hand_starts_at_unix_ms?: number;
    }
  | { type: "hand_aborted"; hand_id: string; refunds: Record<string, number> }
  | { type: "private"; state: PrivateState }
  | { type: "seats"; seats: (SeatInfo | null)[] }
  | { type: "viewer_count"; count: number }
  | { type: "illegal_action"; error: string }
  | { type: "table_error"; error: string };

export interface ActionInfo {
  sequence: number;
  player_id: string;
  action_type: ActionType;
  amount: number;
  auto: boolean;
}

export type ClientMessage = { type: "action"; action: ActionType; amount?: number };
