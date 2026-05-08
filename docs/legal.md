# Legal posture

This is **play-money software**. It is designed to be operated as a free social
game with no real-money element. The economic and architectural decisions that
follow are deliberate and load-bearing for legal compliance.

## What this software does and does not do

It does:

- Distribute "play chips" to authenticated users via daily grant or a one-time
  signup bonus. These chips have no cash value, cannot be redeemed for goods,
  services, or other currency, and are not transferable between players.
- Run No-Limit Texas Hold'em cash tables denominated in play chips.
- Authenticate users via X OAuth.

It does not:

- Accept real-money deposits.
- Pay out real money, cryptocurrency, gift cards, NFTs, or anything of value.
- Allow players to send chips to one another.
- Allow chips to be purchased.
- Run tournaments with prize pools (cash or otherwise).
- Process payments of any kind.

The combination of these properties is the difference between "social game" and
"online gambling" under most jurisdictions' definitions. The line is drawn at
*consideration in, prize out*; remove either side and the activity falls outside
gambling regulation in nearly every jurisdiction.

## Why no peer-to-peer chip transfers

Allowing two users to send chips to each other is technically trivial but
legally significant. A "play chip" that can be traded, gifted, or sold to
another user has acquired a secondary-market value. At that point, courts and
regulators have repeatedly held that the chips are a *thing of value*, and
games played with them constitute gambling — even if the operator never
processes a real-money transaction itself. The platform is then liable for
operating an unlicensed gambling business.

This is also why the daily grant is small and refilled algorithmically rather
than purchasable. Selling chips for real money is the definitional act of a
gambling operator.

## The currency-type seam

`accounts.currency_type` is a string column that today only takes the value
`PLAY`. The data model and engine are structured so that other currency types
*could* be added in the future, but doing so is **not a software engineering
project — it is a regulatory licensing project**. The relevant prerequisites
include, depending on the operator's jurisdiction and the product specifics:

- A state-level online gambling license (in the US, only NJ, NV, PA, MI, WV,
  CT, DE permit online poker; each requires a separate license).
- For multi-state play, an interstate compact agreement.
- A federally registered Money Services Business designation (FinCEN) for
  any currency that can be deposited or withdrawn.
- State Money Transmitter Licenses in every state where players can hold
  balances (~49 states have these).
- KYC, AML, and responsible-gambling programs.
- For crypto deposits/withdrawals: BSA compliance and likely SEC engagement
  if the token is novel.

Building any of these on top of this codebase without the corresponding
licenses is not legal in essentially any jurisdiction. The seam exists so a
future *licensed* operator could plug in regulated rails behind the existing
engine — not as an invitation to ship a real-money build.

## X platform terms

X's developer agreement prohibits applications that facilitate gambling. A
real-money or crypto build of this software would violate the agreement and
would result in API access revocation and account termination. Stay play-money.

## Per-jurisdiction notes for play-money operation

Most jurisdictions treat free-to-play poker without prizes as unregulated
entertainment. A few caveats:

- **Washington State (US)**: has historically interpreted online gambling
  statutes broadly. Free-to-play with optional purchases of in-game currency
  has drawn lawsuits (the *Big Fish* line of cases). Avoid any path-to-purchase.
- **Australia**: simulated gambling games are scrutinized when targeted at
  minors. Keep age gating in place.
- **Several countries** (Germany, France, etc.) restrict marketing of poker
  even when free. Don't advertise as "play poker for real prizes" anywhere.

## Disclaimers in the product

The app should display, on signup and somewhere persistent in the UI:

- "Play money only. No cash value. No real-money play."
- "Chips cannot be transferred between players."
- An age confirmation (18+ recommended; consult counsel for the venue).

## This is not legal advice

The author of this codebase is not your lawyer. Before operating this software
publicly, especially before adding any feature that touches real money or
crypto, consult an attorney specializing in gaming law in the jurisdictions
where your players reside.
