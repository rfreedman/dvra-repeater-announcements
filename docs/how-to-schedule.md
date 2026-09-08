# How to schedule

The repeater clock is a row of **slots**: every hour and every half-hour
(00:00, 00:30, 01:00, … 23:30). Something is chosen for each slot. You write the
words once in the library, then point one or more schedules at that text.

## 1. Write the announcement

Open **New announcement** and save the spoken text. The same text can be used by
a baseline schedule, an overlay, or both. Timing does not live on the text.

## 2. Fill the clock with a baseline

Create a **Baseline** schedule and pick an announcement. That text plays in
every slot that nothing else claims, exactly on the hour or half-hour.

You may add more than one baseline. They take turns: each is used once, then
the list starts over. Enable **Shuffle baseline order each cycle** if you want a
new order when the list restarts. Disable a baseline to take it out of the
rotation without deleting the text.

## 3. Override a slot when you need something else

Overlays replace the baseline for the slots they match. The overlay still
belongs to that slot even if you slide the fire time a few minutes early or
late. Example: the 15:30 slot shifted −5 minutes transmits at **15:25**. Nothing
plays at 15:30. The 15:00 slot is unchanged.

The shift must stay inside the slot window (default ±10 minutes around the slot
time).

- **Weekly Net** — a weekday and a slot, often a few minutes early. Example:
  Sundays, 21:00 slot, −5 minutes → 20:55, every Sunday.
- **Monthly Net** — the 1st, 2nd, 3rd, or 4th weekday of the month, plus months
  to skip (August off).
- **Event countdown** — every slot from a start date up to an event slot, but
  not the event slot itself and not later slots that same day. After that day it
  stops. Put a weekly or monthly overlay on the event slot if the net or meeting
  needs its own announcement.
- **Monthly countdown** — the same until-event rule, repeating each month.
  Choose the event (for example the 1st Wednesday at 19:00) and how many days
  before that event the promo should start. Skip months if needed.
- **Once** — selected slots on a single date.
- **Silence** — occupy a slot and transmit nothing (for example while a net is
  already on the air).
- **Emergency** — wins every slot until you turn it off. It does not interrupt a
  transmission that has already started.

Template names such as “Weekly Net” are only starting points. Rename the
schedule to whatever the operators will recognize.

## 4. When two overlays want the same slot

The higher priority wins. Defaults: emergency 100, weekly/monthly/once 50, event
countdown 20. A Sunday 21:00 net therefore beats a week-long meeting promo on
that one slot. Two overlays of the same priority on the same slot cannot both be
saved.

## Suggested first setup

1. Create a station-ID announcement and a baseline schedule that uses it.
2. Add a Weekly Net overlay for Sunday 21:00, −5 minutes.
3. Add a Monthly Net overlay for the first Wednesday 19:00, −5 minutes, skip
   August if needed.
4. For an upcoming in-person meeting, add an Event countdown from today until
   the meeting slot.
