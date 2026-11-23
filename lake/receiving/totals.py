from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StationTotals:
    row_count: int
    missing_count: int
    integer_sum: int


def summarize_observations(observations) -> StationTotals:
    row_count = 0
    missing_count = 0
    integer_sum = 0
    for row in observations:
        row_count += 1
        if row.value is None:
            missing_count += 1
        else:
            integer_sum += row.value
    return StationTotals(row_count, missing_count, integer_sum)
