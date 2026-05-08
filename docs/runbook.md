# Host runbook

The "host" is you: the human who launches the X Space, opens the host audio
relay tool, and posts the table code. This document describes the steps and
the troubleshooting paths for the issues that come up.

## One-time host setup

### Audio routing

The narration worker produces audio that needs to reach your X client's
microphone input. Two reliable setups:

**macOS — BlackHole + Loopback (or just BlackHole):**

1. Install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole)
2. Create a Multi-Output Device in Audio MIDI Setup combining your headphones + BlackHole (so you can hear what the audience hears)
3. In your browser/Electron client running the Spaces Poker host UI, set the audio output to BlackHole
4. In the X app, set the microphone input to BlackHole

**Windows — VB-Audio Cable:**

1. Install [VB-Cable](https://vb-audio.com/Cable/) (free)
2. Set the host UI's audio output to "CABLE Input"
3. Set X client microphone to "CABLE Output"
4. Use VoiceMeeter Banana if you want to monitor the feed yourself

**Linux — PipeWire null sink:**

```bash
pactl load-module module-null-sink sink_name=spaces media.class=Audio/Sink
# Set browser output to "spaces", X client input to "Monitor of spaces"
```

### Verifying

Before going live, do a "test space" with the volume low. Play a sample
narration. Confirm:

- Audio is reaching the Space (other devices on other accounts hear it)
- You can also hear yourself in monitor (so you know if the queue starves)
- No echo, no clipping, no double-routing

## Per-session checklist

1. Start the Space; pin a post in the Space with the table code and the join URL
2. Open the host audio relay tab; verify it shows "audio: connected"
3. Create the table from your host UI
4. Read the code aloud at the start of the Space, then again every few minutes
5. The bot narrates each action; you handle dispute resolution and pacing

## Common issues

### "Action timer fired before I could click"

The action timer starts when the *client* acknowledges receiving the
"your turn" message, not when the server sends it. If a player insists
they didn't have time, check `table_events` for that player's WS round
trip latency for the hand. If their network was bad, reset the timer
and resume.

### "Audio is delayed by several seconds"

Expected, up to ~3 seconds depending on the Spaces buffer. The web UI
visual countdown is the source of truth; never use audio for action timing.

### "Two players in the same Space are obviously colluding"

You can mute or remove them from the Space directly. To kick them from
the table, use the host control panel: it issues a `kick` and refunds
their seat stack to their account. Their hand is folded immediately if
mid-hand.

### "A player disconnected mid-hand"

The grace period (default 30s) starts when their WebSocket closes.
During the grace, their seat is preserved and they're treated as
auto-fold/check on each street. After the grace, the seat is opened.
Their stack returns to their account balance.

### "I think the engine made a wrong decision"

Every hand has a `deck_seed_commit` written at start and a
`deck_seed_reveal` written at end, plus a full action log. Replay the
hand: feed the actions to a local engine instance with the revealed
seed and confirm the outcome matches. If it doesn't — that's a real
bug; capture the hand ID and report it.

## Shutting down

Closing the table from the host control panel cashes out every remaining
player to their account balance and stops the table loop. After this you
can end the Space.
