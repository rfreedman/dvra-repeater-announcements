# IC-207H Connectors

## Rear Data Port (6-Pin Mini-DIN)
* **Pin 1 - Data In - use this for audio input, but make sure that the bps setting is 1200, NNOT 9600, as 9600 bypasses the limiter circuit**
* **Pin 2 - Ground**
* **Pin 3 - PTT**
* Pin 4 - Data Out
* **Pin 6 - SQL - goes high when squelch is open**

- Pull pin 3 (PTT) to ground to key the radio
- Instead of trying to sense adiuo, monitor pin 6 - low while receiving ??
  - this apparently requires setting SQL to low/normal (in addtion to tone squelch)
    because it monitors the squelch opening. 

