from lake.pages.changes import affected_stations
from lake.pages.manifest import ManifestEntry
from lake.pages.pack import page_fingerprint


def verify_delivered_pages(manifests, pages) -> tuple[ManifestEntry, ...]:
    """Validate the delivered subset and return missing entries in manifest order."""
    expected = {}
    for manifest in manifests:
        for entry in manifest.entries:
            if entry.page_id in expected:
                raise ValueError(f'repeated expected page {entry.page_id}')
            expected[entry.page_id] = (manifest, entry)
    delivered = set()
    changed_by_interval = {}
    for page in pages:
        if page.page_id not in expected or page.page_id in delivered:
            raise ValueError(f'unexpected or repeated delivered page {page.page_id}')
        manifest, entry = expected[page.page_id]
        interval = (page.day, page.lower, page.upper)
        if interval != (manifest.day, manifest.lower, manifest.upper):
            raise ValueError(f'wrong interval for page {page.page_id}')
        if affected_stations(page.changes) != entry.affected_stations:
            raise ValueError(f'affected stations do not match manifest for {page.page_id}')
        if page_fingerprint(page) != entry.fingerprint:
            raise ValueError(f'page fingerprint does not match manifest for {page.page_id}')
        identifiers = {change.observation_id for change in page.changes}
        prior = changed_by_interval.setdefault(interval, set())
        if prior & identifiers:
            raise ValueError(f'repeated observation change in interval of {page.page_id}')
        prior.update(identifiers)
        delivered.add(page.page_id)
    return tuple(entry for page_id, (_, entry) in expected.items() if page_id not in delivered)
