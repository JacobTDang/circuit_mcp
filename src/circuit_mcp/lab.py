"""Bounded, payload-only import of oscilloscope/DMM waveform CSV exports."""
from __future__ import annotations

import csv
import io
import math


class LabDataError(ValueError):
    """A laboratory data payload is malformed or outside safety limits."""


def import_waveform_csv(csv_text: str, time_column: str, value_columns: list[str]) -> dict:
    if not isinstance(csv_text, str) or not csv_text.strip() or len(csv_text) > 5_000_000:
        raise LabDataError("csv_text must contain 1 to 5,000,000 characters")
    if not value_columns or len(value_columns) > 16:
        raise LabDataError("select between 1 and 16 value columns")
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = reader.fieldnames or []
    requested = [time_column, *value_columns]
    missing = [name for name in requested if name not in headers]
    if missing:
        raise LabDataError(f"missing CSV column(s): {missing}; available: {headers}")
    columns = {name: [] for name in requested}
    for index, row in enumerate(reader):
        if index >= 131_072:
            raise LabDataError("CSV exceeds the 131,072-row limit")
        for name in requested:
            try:
                value = float(row[name])
            except (TypeError, ValueError) as exc:
                raise LabDataError(f"row {index + 2}, column {name!r} is not numeric") from exc
            if not math.isfinite(value):
                raise LabDataError(f"row {index + 2}, column {name!r} is not finite")
            columns[name].append(value)
    if len(columns[time_column]) < 2:
        raise LabDataError("waveform needs at least two data rows")
    times = columns[time_column]
    if any(later <= earlier for earlier, later in zip(times, times[1:])):
        raise LabDataError("time values must be strictly increasing")
    intervals = [later - earlier for earlier, later in zip(times, times[1:])]
    mean_interval = sum(intervals) / len(intervals)
    max_jitter = max(abs(value - mean_interval) for value in intervals)
    return {
        "ok": True,
        "rows": len(times),
        "columns": columns,
        "sample_interval_s": mean_interval,
        "sample_rate_hz": 1 / mean_interval,
        "uniform": max_jitter <= max(1e-12, abs(mean_interval) * 1e-6),
        "max_interval_deviation_s": max_jitter,
    }
