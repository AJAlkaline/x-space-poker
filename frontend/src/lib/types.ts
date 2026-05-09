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
  hole: string[] | null;
}

export interface PublicPot {
  amount: number;
  eligible: string[];
}

export interface PublicState {
  hand_id: string;
  phase: Phase;
  board: string[];
  pots: PublicPot[];
  pot_total: number;
  current_bet: number;
  min_raise: number;
  to_act: string[];
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
}

export interface SeatInfo {
  seat: number;
  user_id: string;
  stack: number;
}

export type ServerMessage =
  | { type: "hand_started"; state: PublicState }
  | { type: "state_update"; state: PublicState }
  | { type: "hand_complete"; state: PublicState }
  | { type: "private"; state: PrivateState }
  | { type: "seats"; seats: (SeatInfo | null)[] }
  | { type: "illegal_action"; error: string }
  | { type: "table_error"; error: string };

export type ClientMessage = { type: "action"; action: ActionType; amount?: number };
