# IC-207H or IC-2720 Connectors

The production radios are **IC-207H**. An **IC-2720** may be used for initial development and testing. Both use the same rear 6-pin Mini-DIN DATA jack for this interface.

## Rear Data Port (6-Pin Mini-DIN)
* **Pin 1 - Data In - use this for audio input, but make sure that the bps setting is 1200, NOT 9600, as 9600 bypasses the limiter circuit**
* **Pin 2 - Ground**
* **Pin 3 - PTT**
* Pin 4 - Data Out
* Pin 5 - AF Out fixed ~270mv peak-to-peak
* **Pin 6 - SQL - goes high when squelch is open** (labeled SQ on the IC-207H, P SQL on the IC-2720)

- Pull pin 3 (PTT) to ground to key the radio
- Instead of trying to sense adiuo, monitor pin 6 - low while receiving ??
  - this apparently requires setting SQL to low/normal (in addtion to tone squelch)
    because it monitors the squelch opening.
  - Keep the radio’s audio output at a normal level, otherwise pin 6 may not assert.
- At 1200 bps, disconnect the microphone during data transmit or mic audio is mixed onto the announcement. At 9600 bps the radio mutes the mic automatically; do not use 9600 for this app.
