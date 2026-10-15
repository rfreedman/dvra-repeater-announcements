# Tech debt backlog (Oct 2026 code review)

Tracked on GitHub ([open issues](https://github.com/rfreedman/dvra-repeater-announcements/issues)). Re-create missing review issues with [`scripts/create-tech-debt-issues.sh`](../scripts/create-tech-debt-issues.sh) (skips existing titles; requires `gh auth login`).

Fixed items and root-cause history: [`code-review-findings.md`](code-review-findings.md).

## Closed (implemented)

| # | Title |
| --- | --- |
| [#1](https://github.com/rfreedman/dvra-repeater-announcements/issues/1) | Align Announcement model with API validation bounds |
| [#2](https://github.com/rfreedman/dvra-repeater-announcements/issues/2) | Use SLOT_MINUTES in is_slot_stamp |
| [#3](https://github.com/rfreedman/dvra-repeater-announcements/issues/3) | Name scheduler defer and manual-nudge timing constants |
| [#4](https://github.com/rfreedman/dvra-repeater-announcements/issues/4) | Extract atomic JSON write helper for data stores |
| [#5](https://github.com/rfreedman/dvra-repeater-announcements/issues/5) | Add named constants for frontend poll intervals and template priorities |
| [#6](https://github.com/rfreedman/dvra-repeater-announcements/issues/6) | Type manual-trigger pending rows instead of dict[str, object] |
| [#7](https://github.com/rfreedman/dvra-repeater-announcements/issues/7) | Shared Pydantic model for TTS speed and sentence pause fields |
| [#8](https://github.com/rfreedman/dvra-repeater-announcements/issues/8) | Deduplicate name_present validators on Announcement and Schedule (via [#30](https://github.com/rfreedman/dvra-repeater-announcements/issues/30)) |
| [#9](https://github.com/rfreedman/dvra-repeater-announcements/issues/9) | Consolidate emergency schedule priority constants (via [#30](https://github.com/rfreedman/dvra-repeater-announcements/issues/30)) |
| [#30](https://github.com/rfreedman/dvra-repeater-announcements/issues/30) | Refactor Schedule and Announcement model helpers (`models.py`) |
| [#12](https://github.com/rfreedman/dvra-repeater-announcements/issues/12) | Constant for CM119 HID read timeout (via [#31](https://github.com/rfreedman/dvra-repeater-announcements/issues/31)) |
| [#13](https://github.com/rfreedman/dvra-repeater-announcements/issues/13) | Name DRA PTT key retry delay tuple (via [#31](https://github.com/rfreedman/dvra-repeater-announcements/issues/31)) |
| [#31](https://github.com/rfreedman/dvra-repeater-announcements/issues/31) | Name radio timing constants (CM119 HID and DRA PTT retries) |

## Closed (documentation / other)

| # | Title |
| --- | --- |
| [#19](https://github.com/rfreedman/dvra-repeater-announcements/issues/19) | Document informal Radio protocol extensions → [`docs/radio-backend.md`](radio-backend.md) |
| [#21](https://github.com/rfreedman/dvra-repeater-announcements/issues/21) | UI clock for earlier today may not match what actually aired (documented limitation) |
| [#23](https://github.com/rfreedman/dvra-repeater-announcements/issues/23) | Document in-memory busy retries and login limiter reset on restart |
| [#24](https://github.com/rfreedman/dvra-repeater-announcements/issues/24) | COS: decode queued HID reports to catch sub-poll kerchunks (Linux) |

## Open

| # | Title | Labels / notes |
| --- | --- | --- |
| [#10](https://github.com/rfreedman/dvra-repeater-announcements/issues/10) | Split app/server.py into routes and schemas modules | tech-debt, enhancement |
| [#11](https://github.com/rfreedman/dvra-repeater-announcements/issues/11) | Extract manual-trigger state and macOS idle assertion from scheduler | tech-debt |
| [#14](https://github.com/rfreedman/dvra-repeater-announcements/issues/14) | Make login rate limiter limits configurable via env | enhancement, security |
| [#15](https://github.com/rfreedman/dvra-repeater-announcements/issues/15) | Remove deprecated serve --preload CLI flag | tech-debt |
| [#16](https://github.com/rfreedman/dvra-repeater-announcements/issues/16) | Add /api/meta for calendar labels and overlay priority defaults | enhancement, frontend |
| [#17](https://github.com/rfreedman/dvra-repeater-announcements/issues/17) | deploy.sh: configurable stop and startup wait timeouts | enhancement |
| [#18](https://github.com/rfreedman/dvra-repeater-announcements/issues/18) | Narrow broad except Exception on HTTP handlers where practical | tech-debt |
| [#20](https://github.com/rfreedman/dvra-repeater-announcements/issues/20) | Style: blank line after radio_backend in config.py | tech-debt |
| [#22](https://github.com/rfreedman/dvra-repeater-announcements/issues/22) | Editing overlay offset into the past leaves stale scheduled job time | enhancement |
| [#25](https://github.com/rfreedman/dvra-repeater-announcements/issues/25) | Browser or E2E tests for manual trigger polling and countdown UI | testing, frontend |
| [#26](https://github.com/rfreedman/dvra-repeater-announcements/issues/26) | Bench-verify interleaved dual-DRA playback on two physical radios | testing |
