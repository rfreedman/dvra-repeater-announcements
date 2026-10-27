# How to schedule

The repeater clock is a row of **slots**: every hour and every half-hour
(00:00, 00:30, 01:00, … 23:30). Something is chosen for each slot. You write the
words once in the library, then point one or more schedules at that text.

## 1. Write the announcement

Open **New announcement** and save the spoken text. The same text can be used by
a baseline schedule, an overlay, or both. Timing does not live on the text.

## Spoken length

When you save a spoken announcement, the app synthesizes it (including sentence
pauses and `[pause]` tags) and stores how long the audio is. The limit is the
**repeater timeout** minus 2 seconds. The default timeout is 180 seconds, so the
limit is 178 seconds. The PTT lead after key-up sits inside that 2 second margin.

If the audio is over the limit, the text is still saved, and the editor tells
you the length, the limit, and to shorten it and save again. The announcement
is marked too long. Shorten it under the limit and save again to clear the mark.
Changing the repeater timeout on the home screen updates the mark from the stored
length without synthesizing again.

A too-long announcement cannot be chosen for a new or updated schedule. A
schedule that already points at one is kept. When that slot comes due, that
announcement is not played. The baseline that would have played is used instead.
If that baseline is also too long, the next baseline in the rotation that fits
is used. If none fit, the slot stays silent. The home screen, the schedule list,
the schedule editor, the slot clock, and the upcoming line name the schedule,
the announcement, its length, the limit, and what will play instead. A notice
on the home screen names the same substitution when the fire happens.

The same rule applies to an emergency. Amateur Radio Newsline uses this limit
for its opening, station ID, and closing; see
[Amateur Radio Newsline](arnewsline.md).

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

- **Weekly** — a weekday and a slot, often a few minutes early. Example:
  Sundays, 21:00 slot, −5 minutes → 20:55, every Sunday.
- **Monthly** — the 1st, 2nd, 3rd, or 4th weekday of the month, plus months
  to skip (August off).
- **Event countdown** — every slot from a start date up to an event slot, but
  not the event slot itself and not later slots that same day. After that day it
  stops. Put a weekly or monthly overlay on the event slot if the net or meeting
  needs its own announcement.
- **Monthly countdown** — the same until-event rule, repeating each month.
  Choose the event (for example the 1st Wednesday at 19:00) and how many days
  before that event the promo should start. Skip months if needed.
- **Silence** — occupy a slot and transmit nothing (for example while a net is
  already on the air).
- **Emergency** — wins every slot until you turn it off. It does not interrupt a
  transmission that has already started.

Template names such as “Weekly” are only starting points. Rename the
schedule to whatever the operators will recognize.

## 4. When two overlays want the same slot

The higher priority wins. Defaults: emergency 100, weekly/monthly 50, event
countdown 20. A Sunday 21:00 net therefore beats a week-long meeting promo on
that one slot. Two overlays of the same priority on the same slot cannot both be
saved.

## Today's schedule grid vs what played

The home **schedule clock** shows what the app would fire **now** if each slot ran
today, using the current baseline rotation and shuffle setting. It is **not** a log
of what already went on-air.

Earlier today, baseline rows can show a **different announcement name** than what
actually transmitted if you edited schedules, changed baseline order, shuffled, or
restarted the server after those slots. Overlays and fires always **re-resolve** at
fire time; the grid for past slots is only a simulation for planning.

For what really aired, use station records or logs (`data/logs/announcements.log` on
the NUC), not the UI clock alone.

## Suggested first setup

1. Create a station-ID announcement and a baseline schedule that uses it.
2. Add a Weekly overlay for Sunday 21:00, −5 minutes.
3. Add a Monthly overlay for the first Wednesday 19:00, −5 minutes, skip
   August if needed.
4. For an upcoming in-person meeting, add an Event countdown from today until
   the meeting slot.
