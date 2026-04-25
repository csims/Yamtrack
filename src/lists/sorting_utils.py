from django.apps import apps
from django.db.models import (
    Case,
    DecimalField,
    F,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from app.models import MediaTypes


def sort_field(field_name, direction, *, nulls_last=None, nulls_first=None):
    """Return an OrderBy expression for a field with optional null ordering."""
    order = F(field_name)
    last = True if nulls_last else None
    first = True if nulls_first else None
    if direction == "asc":
        return order.asc(nulls_last=last, nulls_first=first)
    return order.desc(nulls_last=last, nulls_first=first)


def annotate_items_with_user_score(items, *, user):
    """Annotate list items with a `user_score` for rating-based sorting."""
    score_cases = []
    score_field = DecimalField(max_digits=3, decimal_places=1)

    for media_type in MediaTypes.values:
        model = apps.get_model("app", media_type)
        filters = {"item": OuterRef("pk")}

        if media_type == MediaTypes.EPISODE.value:
            filters["related_season__user"] = user
            queryset = model.objects.filter(**filters).order_by(
                "-end_date",
                "-created_at",
            )
        else:
            filters["user"] = user
            queryset = model.objects.filter(**filters)

        score_cases.append(
            When(
                media_type=media_type,
                then=Subquery(
                    queryset.values("score")[:1],
                ),
            ),
        )

    return items.annotate(
        user_score=Coalesce(
            Case(
                *score_cases,
                default=Value(0, output_field=score_field),
                output_field=score_field,
            ),
            Value(0, output_field=score_field),
        ),
    )


def apply_rating_sort(items, *, user, direction):
    """Return items ordered by user_score, then title/season/episode for stability."""
    items = annotate_items_with_user_score(items, user=user)
    return items.order_by(
        sort_field("user_score", direction),
        F("title").asc(nulls_last=True),
        F("season_number").asc(nulls_first=True),
        F("episode_number").asc(nulls_first=True),
    )
