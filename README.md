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

Version 0.1 tracks manual room status, performs the daily reset, and aggregates
the result across all linked Roborock vacuums. It does not yet infer completed
rooms from Roborock cleaning jobs. The stored relationship between Roborock
config entry, vacuum entity, segment, and Area is designed for that later
phase.

## Support

Please report problems through
[GitHub Issues](https://github.com/isimagan/HA-Roborock-Daily-Cleaning/issues).
