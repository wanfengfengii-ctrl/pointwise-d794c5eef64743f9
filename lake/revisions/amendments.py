from dataclasses import dataclass

from lake.revisions.observations import Observation, require_text, validate_observation
from lake.revisions.snapshots import DayRevision, freeze_revision, load_revision
from lake.revisions.store import SourceStore


@dataclass(frozen=True, slots=True)
class Amendment:
    action: str
    observation_id: str
    replacement: Observation | None = None


def freeze_amendment(store: SourceStore, day: str, parent: int, amendments) -> DayRevision:
    edits = tuple(amendments)
    with store.transaction():
        try:
            previous = load_revision(store, day, parent)
        except KeyError as exc:
            raise ValueError('unknown parent for this day') from exc
        rows = {row.observation_id: row for row in previous.observations}
        edited = set()
        for edit in edits:
            if not isinstance(edit, Amendment):
                raise ValueError('expected Amendment')
            identifier = require_text(edit.observation_id, 'amended observation ID')
            if identifier in edited:
                raise ValueError(f'multiple amendments for {identifier!r}')
            edited.add(identifier)
            if edit.action == 'delete':
                if identifier not in rows or edit.replacement is not None:
                    raise ValueError(f'invalid deletion for {identifier!r}')
                del rows[identifier]
                continue
            if edit.action not in ('add', 'replace'):
                raise ValueError('amendment action must be add, replace or delete')
            if (identifier in rows) != (edit.action == 'replace'):
                raise ValueError(f'invalid amendment identity {identifier!r}')
            replacement = edit.replacement
            validate_observation(replacement)
            if replacement.observation_id != identifier:
                raise ValueError('replacement observation ID differs from its amendment')
            rows[identifier] = replacement
        revision = freeze_revision(store, day, parent, rows.values())
    return revision
