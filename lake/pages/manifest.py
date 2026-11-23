from dataclasses import dataclass

from lake.pages.changes import affected_stations
from lake.pages.pack import (
    canonical_json,
    page_fingerprint,
    page_identifier,
    read_json,
    validate_interval,
)
from lake.revisions.observations import require_array, require_fields, require_text


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    page_id: str
    affected_stations: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class IntervalManifest:
    day: str
    lower: int
    upper: int
    entries: tuple[ManifestEntry, ...]


def freeze_manifest(day: str, lower: int, upper: int, pages) -> IntervalManifest:
    validate_interval(day, lower, upper)
    ordered = sorted(pages, key=lambda page: page.number)
    entries = []
    for number, page in enumerate(ordered, start=1):
        if (page.day, page.lower, page.upper, page.number) != (day, lower, upper, number):
            raise ValueError('page is outside its manifest interval or sequence')
        if page.page_id != page_identifier(day, lower, upper, number):
            raise ValueError('page ID does not match its manifest interval')
        entries.append(ManifestEntry(page.page_id, affected_stations(page.changes), page_fingerprint(page)))
    return IntervalManifest(day, lower, upper, tuple(entries))


def manifest_to_data(manifest: IntervalManifest) -> dict:
    return {
        'day': manifest.day,
        'lower': manifest.lower,
        'upper': manifest.upper,
        'entries': [
            {
                'page_id': entry.page_id,
                'affected_stations': list(entry.affected_stations),
                'fingerprint': entry.fingerprint,
            }
            for entry in manifest.entries
        ],
    }


def manifest_from_data(data: dict) -> IntervalManifest:
    require_fields(data, 'day', 'lower', 'upper', 'entries')
    day, lower, upper = data['day'], data['lower'], data['upper']
    validate_interval(day, lower, upper)
    entries = []
    for number, record in enumerate(require_array(data['entries'], 'manifest entries'), start=1):
        require_fields(record, 'page_id', 'affected_stations', 'fingerprint')
        identifier = page_identifier(day, lower, upper, number)
        if record['page_id'] != identifier:
            raise ValueError('manifest page ID is outside its interval or sequence')
        stations = tuple(
            require_text(station, 'affected station')
            for station in require_array(record['affected_stations'], 'affected stations')
        )
        if not stations or stations != tuple(sorted(set(stations))):
            raise ValueError('manifest affected stations must be nonempty, ordered and unique')
        digest = record['fingerprint']
        if type(digest) is not str or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise ValueError('invalid page fingerprint')
        entries.append(ManifestEntry(identifier, stations, digest))
    return IntervalManifest(day, lower, upper, tuple(entries))


def encode_manifest(manifest: IntervalManifest) -> bytes:
    return canonical_json(manifest_to_data(manifest))


def decode_manifest(body: bytes) -> IntervalManifest:
    return manifest_from_data(read_json(body))
