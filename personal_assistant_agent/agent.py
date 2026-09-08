"""Personal Assistant Agent for the Google ADK Week 1 assignment.

The agent demonstrates:
1. Four custom function tools.
2. Pydantic input and output validation.
3. ADK session state across turns.
4. Tool-friendly docstrings and type hints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


# -----------------------------------------------------------------------------
# Shared validation base
# -----------------------------------------------------------------------------

class StrictSchema(BaseModel):
    """Forbid unexpected fields so tool payload mistakes are caught early."""

    model_config = ConfigDict(extra="forbid")


# -----------------------------------------------------------------------------
# Tool 1 schemas: preferences/session state
# -----------------------------------------------------------------------------

class PreferenceInput(StrictSchema):
    preferred_city: str | None = Field(
        default=None,
        description="City to remember for later date/time requests.",
    )
    preferred_unit: Literal["km", "miles", "celsius", "fahrenheit"] | None = Field(
        default=None,
        description="Unit to remember for later conversions.",
    )

    @field_validator("preferred_city")
    @classmethod
    def normalise_city(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().title()
        if not cleaned:
            raise ValueError("preferred_city cannot be blank")
        return cleaned


class PreferenceOutput(StrictSchema):
    status: Literal["saved", "no_change"]
    preferred_city: str | None = None
    preferred_unit: str | None = None
    message: str


# -----------------------------------------------------------------------------
# Tool 2 schemas: unit conversion
# -----------------------------------------------------------------------------

DistanceUnit = Literal["km", "miles"]
TemperatureUnit = Literal["celsius", "fahrenheit"]
SupportedUnit = Literal["km", "miles", "celsius", "fahrenheit"]


class ConversionInput(StrictSchema):
    value: float = Field(description="Numeric value to convert.")
    from_unit: SupportedUnit = Field(description="Unit of the supplied value.")
    to_unit: SupportedUnit | None = Field(
        default=None,
        description="Desired unit. If omitted, the saved preferred unit is used when compatible.",
    )


class ConversionOutput(StrictSchema):
    original_value: float
    from_unit: SupportedUnit
    converted_value: float
    to_unit: SupportedUnit
    used_saved_preference: bool


# -----------------------------------------------------------------------------
# Tool 3 schemas: date/time lookup
# -----------------------------------------------------------------------------

class DateTimeInput(StrictSchema):
    city: str | None = Field(
        default=None,
        description="City whose local date and time is required. If omitted, use session preference.",
    )

    @field_validator("city")
    @classmethod
    def normalise_city(cls, value: str | None) -> str | None:
        return value.strip().title() if value and value.strip() else None


class DateTimeOutput(StrictSchema):
    city: str
    timezone: str
    local_datetime: str
    used_saved_preference: bool


# -----------------------------------------------------------------------------
# Tool 4 schemas: mock ticket lookup
# -----------------------------------------------------------------------------

class TicketInput(StrictSchema):
    ticket_id: str = Field(description="Mock ticket ID, for example INC1001.")

    @field_validator("ticket_id")
    @classmethod
    def normalise_ticket_id(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("ticket_id cannot be blank")
        return cleaned


class TicketOutput(StrictSchema):
    ticket_id: str
    status: Literal["Open", "In Progress", "Resolved", "Not Found"]
    owner: str | None = None
    summary: str


# -----------------------------------------------------------------------------
# Helper data
# -----------------------------------------------------------------------------

CITY_TIMEZONES = {
    "Delhi": "Asia/Kolkata",
    "Gurugram": "Asia/Kolkata",
    "Gurgaon": "Asia/Kolkata",
    "London": "Europe/London",
    "New York": "America/New_York",
    "San Francisco": "America/Los_Angeles",
    "Singapore": "Asia/Singapore",
    "Sydney": "Australia/Sydney",
    "Tokyo": "Asia/Tokyo",
}

MOCK_TICKETS = {
    "INC1001": {
        "status": "Open",
        "owner": "Service Desk",
        "summary": "VPN access is unavailable.",
    },
    "INC1002": {
        "status": "In Progress",
        "owner": "Cloud Support",
        "summary": "API authentication is being investigated.",
    },
    "INC1003": {
        "status": "Resolved",
        "owner": "Workplace Technology",
        "summary": "Python PATH configuration was corrected.",
    },
}


def _error(message: str, details: str | None = None) -> dict:
    """Return a consistent, JSON-serialisable error payload."""

    result = {"status": "error", "message": message}
    if details:
        result["details"] = details
    return result


# -----------------------------------------------------------------------------
# Custom function tools
# -----------------------------------------------------------------------------

def save_preferences(
    tool_context: ToolContext,
    preferred_city: str | None = None,
    preferred_unit: str | None = None,
) -> dict:
    """Save a preferred city and/or unit in the current ADK session state.

    Use this tool when the user says to remember, set, change, or update a city
    or unit preference. The saved values are available to later tool calls in
    the same session.
    """

    try:
        validated = PreferenceInput(
            preferred_city=preferred_city,
            preferred_unit=preferred_unit,
        )
    except ValidationError as exc:
        return _error("Invalid preference input.", str(exc))

    if tool_context is None:
        return _error("ADK did not provide ToolContext; session state is unavailable.")

    changed = False
    if validated.preferred_city is not None:
        tool_context.state["preferred_city"] = validated.preferred_city
        changed = True
    if validated.preferred_unit is not None:
        tool_context.state["preferred_unit"] = validated.preferred_unit
        changed = True

    output = PreferenceOutput(
        status="saved" if changed else "no_change",
        preferred_city=tool_context.state.get("preferred_city"),
        preferred_unit=tool_context.state.get("preferred_unit"),
        message=(
            "Preferences saved in this session."
            if changed
            else "No preference value was supplied."
        ),
    )
    return output.model_dump()


def convert_unit(
    tool_context: ToolContext,
    value: float,
    from_unit: str,
    to_unit: str | None = None,
) -> dict:
    """Convert kilometres/miles or Celsius/Fahrenheit.

    If to_unit is omitted, this tool tries to use preferred_unit from the
    current session. Distance units cannot be converted to temperature units,
    and temperature units cannot be converted to distance units.
    """

    saved_unit = tool_context.state.get("preferred_unit") if tool_context else None
    target = to_unit or saved_unit
    used_saved = to_unit is None and saved_unit is not None

    try:
        validated = ConversionInput(
            value=value,
            from_unit=from_unit.lower().strip(),
            to_unit=target.lower().strip() if isinstance(target, str) else None,
        )
    except (ValidationError, AttributeError) as exc:
        return _error("Invalid conversion input.", str(exc))

    if validated.to_unit is None:
        return _error(
            "No target unit was provided or saved. Specify to_unit or save a compatible preferred unit."
        )

    distance_units = {"km", "miles"}
    temperature_units = {"celsius", "fahrenheit"}
    pair = {validated.from_unit, validated.to_unit}

    if not (pair <= distance_units or pair <= temperature_units):
        return _error("Cannot convert between distance and temperature units.")

    if validated.from_unit == validated.to_unit:
        converted = validated.value
    elif validated.from_unit == "km" and validated.to_unit == "miles":
        converted = validated.value * 0.621371
    elif validated.from_unit == "miles" and validated.to_unit == "km":
        converted = validated.value / 0.621371
    elif validated.from_unit == "celsius" and validated.to_unit == "fahrenheit":
        converted = (validated.value * 9 / 5) + 32
    else:
        converted = (validated.value - 32) * 5 / 9

    output = ConversionOutput(
        original_value=validated.value,
        from_unit=validated.from_unit,
        converted_value=round(converted, 2),
        to_unit=validated.to_unit,
        used_saved_preference=used_saved,
    )
    return output.model_dump()


def get_local_datetime(
    tool_context: ToolContext,
    city: str | None = None,
) -> dict:
    """Return the current local date and time for a supported city.

    If city is omitted, use preferred_city from session state. This tool uses
    Python's local timezone database and does not call an external API.
    """

    saved_city = tool_context.state.get("preferred_city") if tool_context else None
    selected_city = city or saved_city
    used_saved = city is None and saved_city is not None

    try:
        validated = DateTimeInput(city=selected_city)
    except ValidationError as exc:
        return _error("Invalid date/time input.", str(exc))

    if validated.city is None:
        return _error("No city was provided or saved in this session.")

    timezone_name = CITY_TIMEZONES.get(validated.city)
    if timezone_name is None:
        supported = ", ".join(sorted(CITY_TIMEZONES))
        return _error(
            f"Unsupported city '{validated.city}'. Supported cities: {supported}."
        )

    try:
        local_now = datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as exc:
        return _error("Timezone data is unavailable in this environment.", str(exc))

    output = DateTimeOutput(
        city=validated.city,
        timezone=timezone_name,
        local_datetime=local_now.strftime("%d %B %Y, %I:%M:%S %p"),
        used_saved_preference=used_saved,
    )
    return output.model_dump()


def get_ticket_status(ticket_id: str) -> dict:
    """Look up a ticket in a small, dummy in-memory ticket register.

    This is a demonstration tool only. It does not connect to a real service
    desk and must not be used with confidential or production ticket data.
    """

    try:
        validated = TicketInput(ticket_id=ticket_id)
    except ValidationError as exc:
        return _error("Invalid ticket input.", str(exc))

    record = MOCK_TICKETS.get(validated.ticket_id)
    if record is None:
        output = TicketOutput(
            ticket_id=validated.ticket_id,
            status="Not Found",
            owner=None,
            summary="No matching mock ticket exists.",
        )
    else:
        output = TicketOutput(ticket_id=validated.ticket_id, **record)

    return output.model_dump()


# ADK discovers this variable when the project is launched with `adk web`.
root_agent = LlmAgent(
    name="personal_assistant_agent",
    model="gemini-3.6-flash",
    description=(
        "A personal assistant that remembers session preferences, converts "
        "units, reports local time, and checks mock ticket statuses."
    ),
    instruction="""
You are a concise and helpful personal assistant.

Tool rules:
1. If the user asks you to remember or set a preferred city/unit, call
   save_preferences. Do not merely acknowledge the preference in text.
2. For unit conversion, call convert_unit. If the target unit is omitted,
   allow the tool to use the session preference.
3. For a local date/time request, call get_local_datetime. If the city is
   omitted, allow the tool to use the session preference.
4. For a ticket-status request, call get_ticket_status.
5. Never invent tool results. Explain any tool validation error clearly.
6. The ticket register is mock data only. State that when relevant.
7. Keep answers short and include the unit, city, or ticket ID used.
""",
    tools=[
        save_preferences,
        convert_unit,
        get_local_datetime,
        get_ticket_status,
    ],
)


