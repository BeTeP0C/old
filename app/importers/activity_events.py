import csv
import io
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError

from app.schemas.activity_event import ActivityEventCreate

REQUIRED_CSV_COLUMNS = {
    "employee_id",
    "source",
    "event_type",
    "title",
    "start_dt",
    "end_dt",
    "timezone",
}


class ActivityEventImportValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("activity event import validation failed")
        self.errors = errors


def parse_csv_activity_events(
    content: str,
    *,
    default_source: str | None = None,
) -> list[ActivityEventCreate]:
    reader = csv.DictReader(io.StringIO(content))
    # Когда вызывающая сторона задала default_source (например, активная вкладка
    # /import/events/csv?source=calendar), колонка source становится опциональной.
    required_columns = REQUIRED_CSV_COLUMNS - ({"source"} if default_source else set())
    missing_columns = required_columns - set(reader.fieldnames or [])
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ActivityEventImportValidationError([f"missing required CSV columns: {missing}"])

    events: list[ActivityEventCreate] = []
    errors: list[str] = []
    for row_number, row in enumerate(reader, start=2):
        event = _parse_raw_event(
            row,
            location=f"row {row_number}",
            errors=errors,
            default_source=default_source,
        )
        if event is not None:
            events.append(event)

    if errors:
        raise ActivityEventImportValidationError(errors)
    return events


def parse_json_activity_events(
    items: Iterable[object],
    *,
    default_source: str | None = None,
) -> list[ActivityEventCreate]:
    events: list[ActivityEventCreate] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"item {index}: expected object")
            continue
        event = _parse_raw_event(
            item,
            location=f"item {index}",
            errors=errors,
            default_source=default_source,
        )
        if event is not None:
            events.append(event)

    if errors:
        raise ActivityEventImportValidationError(errors)
    return events


def _parse_raw_event(
    raw_event: dict[str, object],
    *,
    location: str,
    errors: list[str],
    default_source: str | None = None,
) -> ActivityEventCreate | None:
    try:
        event = ActivityEventCreate.model_validate(
            _normalize_raw_event(raw_event, default_source=default_source),
        )
    except (ValueError, ValidationError) as exc:
        errors.append(f"{location}: {exc}")
        return None

    if event.start_dt >= event.end_dt:
        errors.append(f"{location}: start_dt must be earlier than end_dt")
        return None
    return event


def _normalize_raw_event(
    raw_event: dict[str, object],
    *,
    default_source: str | None = None,
) -> dict[str, object]:
    normalized = dict(raw_event)
    # Если у строки нет source, но клиент передал default (вкладка) — подставим.
    # Явно заданный per-row source имеет приоритет — это позволяет загружать
    # mixed-source CSV без потери семантики.
    if default_source is not None:
        existing = normalized.get("source")
        if existing is None or (isinstance(existing, str) and not existing.strip()):
            normalized["source"] = default_source
    normalized["employee_id"] = _normalize_uuid(normalized.get("employee_id"))
    normalized["external_id"] = _normalize_optional_str(normalized.get("external_id"))
    normalized["start_dt"] = _normalize_datetime(normalized.get("start_dt"))
    normalized["end_dt"] = _normalize_datetime(normalized.get("end_dt"))
    normalized["recurrence_rule"] = _normalize_optional_str(normalized.get("recurrence_rule"))
    normalized["is_recurring"] = _normalize_bool(normalized.get("is_recurring"), default=False)
    if normalized["recurrence_rule"] is not None:
        normalized["is_recurring"] = True
    normalized.setdefault("is_outside_schedule", False)
    return normalized


def _normalize_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value.strip():
        return UUID(value.strip())
    raise ValueError("employee_id is required")


def _normalize_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        return datetime.fromisoformat(value.strip())
    raise ValueError("datetime value is required")


def _normalize_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _normalize_bool(value: object, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise ValueError(f"invalid boolean value: {value}")
