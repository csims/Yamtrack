from app.models import MediaTypes, Sources


def mock_tv_with_seasons(
    *episode_counts,
    title="Test TV Show",
    image=None,
    media_id="1668",
    source=Sources.TMDB.value,
):
    """Return minimal tv_with_seasons metadata for shared view tests."""
    if image is None:
        image = "http://example.com/image.jpg"

    data = {
        "title": title,
        "media_id": media_id,
        "source": source,
        "media_type": MediaTypes.TV.value,
        "image": image,
        "related": {"seasons": []},
    }

    for index, episode_count in enumerate(episode_counts, start=1):
        season_title = f"Season {index}"
        season_data = {
            "title": title,
            "media_id": media_id,
            "media_type": MediaTypes.SEASON.value,
            "source": source,
            "image": image,
            "season_number": index,
            "season_title": season_title,
            "details": {"episodes": episode_count},
            "episodes": [
                {"episode_number": episode_number}
                for episode_number in range(1, episode_count + 1)
            ],
        }
        data[f"season/{index}"] = season_data
        data["related"]["seasons"].append(
            {
                "title": title,
                "media_id": media_id,
                "media_type": MediaTypes.SEASON.value,
                "source": source,
                "image": image,
                "season_number": index,
                "season_title": season_title,
                "max_progress": episode_count,
            },
        )

    return data


def mock_metadata_side_effect(*episode_counts, title="Test TV Show", image=None):
    """Return a minimal get_media_metadata side effect for TV/season lookups."""
    tv_with_seasons = mock_tv_with_seasons(
        *episode_counts,
        title=title,
        image=image,
    )

    def side_effect(
        media_type,
        media_id,  # noqa: ARG001
        source,  # noqa: ARG001
        season_numbers=None,
        episode_number=None,  # noqa: ARG001
    ):
        if media_type == "tv_with_seasons":
            return tv_with_seasons
        if media_type == MediaTypes.SEASON.value:
            season_number = (season_numbers or [None])[0]
            return tv_with_seasons[f"season/{season_number}"]
        msg = f"Unexpected get_media_metadata call in test helper: {media_type}"
        raise AssertionError(msg)

    return side_effect
