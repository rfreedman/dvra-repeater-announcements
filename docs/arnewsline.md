# Amateur Radio Newsline

A **Newsline** library item plays the current week's Amateur Radio Newsline report as a sequence of short pieces. A normal weekly (or any other) schedule points at that item. The app downloads and splits the report on its own during a Thursday–Friday window.

## Download window

Checks run once an hour, in the station timezone, from the **Thursday check start** (default 12:00) through the **Friday check stop** (default 18:00). Both times are on the home screen.

The checks stop when a newly published report is downloaded and split successfully during that window, or when Friday's stop time passes. If the app starts while the window is open and this week's report is not ready yet, it checks immediately, then continues at the hourly times after that moment.

If the feed is still the previous report, the app logs that the new episode is not out yet and keeps checking. A failed split does not erase a report that already split successfully.

**Check for Newsline now** runs the same download and split once, including outside the Thursday–Friday window. It does not change the hourly schedule. If a check or split is already running, the button reports that and does not start a second one. The request returns immediately. The home status line shows checking, then downloading and processing while a new report is fetched and split, then the report number when that finishes. When the feed has no newer report, the line says so and keeps the current report number. A failure is shown on that line. The action is written to the log.

## Files

Reports live under `data/arnewsline/`:

- `state.json` — the report number, recorded only after a successful split
- `{report number}/Report{number}.mp3` and `nsln{number}.txt` — the downloaded episode
- `{report number}/stories/` — the split story files, including a zero-byte `*station-id.mp3` marker where the script has an identification break
- `cache/` — alignment cache for the splitter

Stories are packed so each file stays under the repeater timeout minus 2 seconds (178 seconds when the timeout is 180).

## The three spoken parts

A Newsline item has no script of its own. It points at ordinary spoken announcements:

- **Opening** — optional, played once before the stories
- **Station ID** — required, played in place of each `*station-id.mp3` marker
- **Closing** — optional, played once after the stories

Create those as normal announcements first, then choose them on the Newsline item. A Newsline item cannot be chosen as an opening, station ID, or closing. A spoken announcement that a Newsline item still uses cannot be deleted.

On the Newsline editor, **Play** streams the latest split report in the browser, using the opening, station ID, and closing chosen on that screen. While a piece plays, the status shows **Streaming…**, the piece name, and elapsed minutes and seconds for that piece. The editor fields are locked until it finishes, you press **Stop**, or you leave that screen (Back, another page, or closing the tab). A short tone marks simulated PTT key-up; a slightly lower tone marks unkey. Between pieces it pauses for the usual PTT lead and then the unkey gap from this screen, with the speakers idle rather than playing silence. **Stop** ends the preview, including a pause. **Reset** restores those three choices and the unkey gap. If no report is ready, Play shows a warning and does not start.

## Schedule and airtime

Point a weekly schedule (Friday is typical) at the Newsline item. Playback order is the opening, then the story files in name order with the station ID substituted, then the closing. Each piece is its own transmission: the radio keys, waits the usual PTT lead (`TTS_PTT_LEAD_SECONDS`, default 0.4 seconds), plays that piece, then unkeys.

Between pieces the radio stays unkeyed for at least the item's **PTT gap** (default 2 seconds). A full bulletin is about 17 minutes plus the short spoken parts and those gaps, so it finishes before the next half-hour slot when the channel stays clear.

If this week's stories are not ready when a scheduled fire runs, the app waits and retries at the announcement's busy-retry interval until the slot window ends, then drops the fire and logs it. Other schedules can still fire during that wait. A manual trigger has no slot window, so the same wait uses the announcement's busy give-up.

If the channel is busy before the first piece, that wait is limited by the slot window and the busy give-up, the same as any other announcement. Once the first piece has started, the bulletin finishes every remaining piece even if that runs past the slot, the original give-up, or another schedule.

If the channel is busy during a gap, the radio stays unkeyed and waits for the same clear hold as a normal key-up, including a fresh carrier check, then continues with the next piece. It does not start the bulletin over. That wait is not a scheduler retry.

A non-emergency that comes due while the bulletin is on the air is dropped. It is not played later, and the baseline rotation does not advance for that missed slot. A manual trigger during the bulletin keeps its usual lock retry.

An emergency has priority over the bulletin, but a story already on the air is not cut off. If the emergency comes due in the middle of a piece, that piece finishes, the emergency plays (with its own clear wait and busy give-up, starting when its turn begins), and the bulletin continues with the next piece. If the emergency comes due while the bulletin is unkeyed and waiting for a clear channel, that wait stops and the emergency plays before the next piece is keyed. If the emergency is dropped because the channel stays busy, the bulletin still continues. A later non-emergency during the emergency or the resumed bulletin is dropped.

## Spoken length

Opening, station ID, and closing use the same length limit as every other spoken announcement: the repeater timeout minus 2 seconds. See [How to schedule](how-to-schedule.md#spoken-length). A too-long opening or closing is left out and the stories still play. A too-long station ID means the bulletin does not play; the slot's baseline plays instead, or the slot stays silent if no baseline fits.

## ffmpeg and the first split

`ffmpeg` and `ffprobe` must be on `PATH`. If they are missing, the app logs a warning and keeps running; download, split, and Newsline playback cannot run until they are installed.

The first successful split downloads the speech models it needs (`base.en` for alignment and `small.en` for a check of the exported audio). Later splits reuse those models. A split usually finishes in about a minute and a half and does not block a slot that is ready to fire.
