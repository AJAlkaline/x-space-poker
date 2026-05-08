// Mirrors backend `_public_view()` in services/table_manager.py.
// Keep in sync — there's no shared schema yet (TODO: generate from pydantic).

export type Phase = "pre_deal" | "pre_flop" | "flop" | "turn" | "river" | "showdown" | "complete";
export type PlayerStatus = "active" | "folded" | "all_in" | "sitting_out" | "disconnected";
export type ActionType = "fold" | "check" | "call" | "bet" | "raise" | "post_blind";

export interface PublicPlayer {
  id: string;
  seat: number;
  stack: number;
  status: PlayerStatus;
  street_committed: number;
  last_action: ActionType | null;
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
  current_bet: number;
  min_raise: number;
  to_act: string[];
  button: number;
  players: (PublicPlayer | null)[];
}

export interface PrivateState {
  hole: [string, string] | null;
  legal_actions: LegalAction[];
  action_deadline_unix?: number;
}

export interface LegalAction {
  action_type: ActionType;
  min_amount?: number;
  max_amount?: number;
}

export type ServerMessage =
  | { type: "hand_started"; state: PublicState }
  | { type: "state_update"; state: PublicState }
  | { type: "private"; state: PrivateState }
  | { type: "your_turn"; deadline_unix: number }
  | { type: "illegal_action"; error: string }
  | { type: "ack" };

export type ClientMessage =
  | { type: "action"; action: ActionType; amount?: number }
  | { type: "ack_prompt" }
  | { type: "sit_out" }
  | { type: "sit_in" }
  | { type: "buy_in"; amount: number }
  | { type: "leave" };
