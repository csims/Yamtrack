import datetime

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary using the key."""
    return dictionary.get(key, [])


def get_date(day, month, year):
    """Get the date from a day, month, and year."""
    # Convert to integers
    day = int(day)
    month = int(month)
    year = int(year)

    # Create date object
    return datetime.date(year, month, day)


@register.simple_tag
def day_of_week(day, month, year):
    """Return the day of week given day, month, and year."""
    date_obj = get_date(day, month, year)
    return date_obj.strftime("%A")  # Full name (Monday, Tuesday, etc.)


@register.simple_tag
def day_of_week_abbr(day, month, year):
    """Return the abbreviated day of week given day, month, and year."""
    date_obj = get_date(day, month, year)
    return date_obj.strftime("%a")  # Abbreviated name (Mon, Tue, etc.)


@register.simple_tag
def is_today(day, month, year, today):
    """Return whether the given datetime is today."""
    return day == today.day and month == today.month and year == today.year
