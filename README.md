# Daily Cleaning for Home Assistant

Daily Cleaning is a Home Assistant custom integration that tracks which rooms
still need cleaning today. It links directly to the built-in Roborock
integration and uses the room segments reported by each selected vacuum.

This repository is intended for installation as a **custom HACS repository**.
It is not prepared for submission to the HACS default repository.

## Requirements

- Home Assistant 2026.9.0 or newer
- The built-in Roborock integration configured and loaded
- Roborock maps with named room segments
- Home Assistant Areas (Roborock's native segment-to-Area mapping is reused
  when available)

## Installation through a custom HACS repository

1. Open HACS in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/isimagan/HA-Roborock-Daily-Cleaning` with category
   **Integration**.
4. Install **Daily Cleaning**.
5. Restart Home Assistant.
6. Open **Settings → Devices & services → Add integration** and select
   **Daily Cleaning**.

## Configuration

The config flow lets you:

1. Select one or more loaded Roborock vacuums.
2. Select the reported room segments to track.
3. Confirm or correct the Home Assistant Area for every room.

Use **Configure** on the integration later to change vacuums, rooms, or Area
mappings.

## Entities

Every selected room gets a switch such as:

```text
switch.daily_cleaning_living_room
```

- `on`: the room still needs cleaning
- `off`: the room is considered cleaned

Turning a switch off manually records `last_cleaned`. The state is persisted
across normal Home Assistant restarts.

The whole home gets one aggregate entity:

```text
binary_sensor.daily_cleaning
```

It is on while at least one configured room still needs cleaning. Its
attributes include room totals and lists of cleaned and remaining rooms.

All entities are grouped on a **Daily Cleaning** device page under the
integration. Each room switch still has its own Home Assistant Area assignment.

## Cleaning day

A new cleaning day starts at **03:00 in Home Assistant's local time zone**.
All room switches are reset to on. The stored cleaning-day identifier is
checked during startup, so a restart around 03:00 cannot permanently skip the
reset.

## Current scope

Version 0.2 tracks manual room status, performs the daily reset, aggregates the
result across all linked Roborock vacuums, and conservatively marks rooms as
cleaned after supported Roborock jobs.

### Automatic room completion

Daily Cleaning uses Roborock's cached raw state as the authoritative job type.
It accepts normal whole-home cleaning (`cleaning`, raw state `5`) and segment
cleaning (`segment_cleaning`, raw state `18`). Zoned cleaning, spot cleaning,
go-to/navigation, and returning without a preceding supported session never
complete rooms.

A room switch is changed only after the vacuum reports both
`returning_home` (`6`) and confirmed docking/charging (`8`). A pause before the
first returning state makes the active room incomplete. A pause after returning
has started is treated as a paused return and does not invalidate an otherwise
completed job.

Completion evidence uses positive, session-local deltas for both cleaning area
and cleaning time. Old values seen at startup are not counted, counter resets
are handled, and delayed measurements received during return are credited to
the last active cleaning room. `clean_progress == 100` is not required.

For whole-home cleaning, only configured rooms that were observed with local
area and time growth are completed after successful docking. A room that was
not reached remains on.

For segment cleaning, Daily Cleaning captures the exact ordered targets when
the job is started through Home Assistant's `vacuum.clean_area` action or an
explicit `app_segment_clean` command. Physical `current_room` changes are used
only to follow those known targets; transport rooms are never promoted to
targets. If a multi-room job is interrupted in room 2, a documented room 1 may
be completed while room 2 and later targets remain on.

The tested Roborock model did not expose `cleaning_info.segment_id` or
`target_segment_id` in its loaded status object. Segment jobs started outside
Home Assistant therefore remain unchanged on that model. On models that expose
an exact cached `segment_id`, that identifier can safely supply the same target
evidence. Daily Cleaning deliberately does not guess segment targets from
`current_room` alone.

An integration reload or Home Assistant restart during a job discards the
in-flight session. Missing fields, unavailable states, stale service targets,
and unexpected transitions fail closed and leave room switches on.

### Temporary Roborock diagnostics

Version 0.2 includes a bounded, in-memory diagnostic recorder for collecting
evidence before automatic room detection is designed. It observes only cached
Home Assistant and Roborock entity data. It never refreshes the vacuum, sends a
command, or changes a Daily Cleaning switch.

When a configured vacuum enters `cleaning`, cached fields are sampled about
every two seconds. Sampling continues briefly after cleaning to capture
returning and docked transitions. Only changes and sparse heartbeats are kept,
with at most 1000 observations per vacuum. Buffers are cleared by a Home
Assistant restart or integration reload.

Depending on the Roborock model and library support, diagnostics attempts to
capture:

- Home Assistant vacuum state and raw Roborock state/work status
- `in_cleaning` and `in_returning`
- `cleaning_info.segment_id` and `target_segment_id`
- clean time, area, and progress
- allowlisted task, mode, returning, map, and current-room fields
- matching Daily Cleaning room, segment, and Home Assistant Area

Unsupported fields are recorded as `null` and never prevent setup.

To enable the diagnostic transition log, add this to `configuration.yaml` and
restart Home Assistant:

```yaml
logger:
  logs:
    custom_components.daily_cleaning.diagnostic_recorder: debug
    custom_components.daily_cleaning.diagnostic_manager: debug
```

Messages use the prefix `DAILY_CLEANING_DIAG`. To download the full sanitized
buffer after a test, open **Settings → Devices & services → Daily Cleaning**,
open the integration entry menu, and choose **Download diagnostics**. The file
does not include credentials, tokens, MQTT details, account identifiers, map
images, or coordinates.

## Support

Please report problems through
[GitHub Issues](https://github.com/isimagan/HA-Roborock-Daily-Cleaning/issues).
