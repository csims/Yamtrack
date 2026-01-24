# History tracking and TV/Season/EpisodeWatch flows

This document explains how historical records are created and how TV/Season
and episode watch flows behave after the EpisodeWatch overhaul.

## History data sources

- **Media history (django-simple-history)**: `TV`, `Season`, and other media
  models inherit `Media`, which defines `history = HistoricalRecords(...)`.
  Each save writes to a `Historical*` table with `history_date`, `history_user`,
  and `history_type`. These histories power the timeline and stats for non-episode
  media.
- **Episode history**: episodes do not use simple-history. The episode timeline
  is built from `EpisodeWatch` rows, which are watch events.

History entries are used in:
- `src/app/views.py` (history/timeline rendering and deletion).
- `src/app/history_processor.py` (diffing changes into user-visible entries).
- `src/app/statistics.py` (activity/streaks for non-episode media, plus
  EpisodeWatch for TV/Season activity).

## Which fields are tracked

- **EpisodeWatch**
  - Model fields: `item`, `related_season`, `watched_at`, `source`, `created_at`.
  - No `HistoricalEpisodeWatch` table; each row is a watch event.
  - Source: `src/app/models.py` (`EpisodeWatch`).

- **Season / TV**
  - History defined in `Media`.
  - History excludes: `item`, `progressed_at`, `user`, `related_tv`, `created_at`.
  - Tracked fields include: `status`, `score`, `progress`, `start_date`,
    `end_date`, `notes`.
  - Source: `src/app/models.py` (`Media.history = HistoricalRecords(...)`).

## Flows

### Watch an episode / rewatch

Entry points:
- `Season.watch()` (single episode)
- `Season.increase_progress()` (next-episode flow)

What happens:
- Creates an `EpisodeWatch` row for the episode `Item`.
- Rewatching creates an additional `EpisodeWatch` row.

Relevant code:
- `Season.watch()` and `Season.increase_progress()` in `src/app/models.py`.

### Mark season completed

Entry point: `Season.save()` when `status` changes to `Completed`.

What happens:
- Fetches season metadata.
- Computes the latest watched episode number via `EpisodeWatch`.
- Creates `EpisodeWatch` rows for missing episodes **above** the latest watched.
- Each watch is stored with `source="bulk"` and `watched_at` resolved by user
  preference (now vs air date).

Relevant code:
- `Season.save()` and `Season.get_remaining_eps()` in `src/app/models.py`.

### Mark TV completed

Entry point: `TV._completed()` when `TV.status` changes to `Completed`.

What happens:
- Ensures `Season` rows exist and are marked `Completed`.
- For each season, creates `EpisodeWatch` rows for remaining episodes using
  `Season.get_remaining_eps()`.

Relevant code:
- `TV._completed()` in `src/app/models.py`.

### History modal + deletion

- `history_modal`:
  - For `media_type="episode"`, it builds the timeline from `EpisodeWatch` rows.
  - For other media types, it uses `Historical*` tables.
- `delete_history_record`:
  - For `media_type="episodewatch"`, deletes the `EpisodeWatch` row by id.
  - For other media types, deletes the matching `Historical*` row.

Relevant code:
- `history_modal` and `delete_history_record` in `src/app/views.py`.

## Known quirks

- Season/TV completion only fills episodes **after** the latest watched episode;
  gaps below the max remain unwatched (no backfill).
- EpisodeWatch events do not create history rows, so the episode timeline is
  built directly from EpisodeWatch data instead of `Historical*` tables.
