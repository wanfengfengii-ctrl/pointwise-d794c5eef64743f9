from lake.dispatch.archive import next_handoff_id, store_issued
from lake.dispatch.envelope import HandoffEnvelope
from lake.dispatch.joint_proof import issue_joint_proof
from lake.dispatch.selection import select_materials
from lake.pages.verify import verify_delivered_pages
from lake.revisions.store import SourceStore

__all__ = ['issue_handoff', 'issue_joint_proof']


def issue_handoff(store: SourceStore, day: str, anchor_id: int, target_revision: int, page_ids=None) -> int:
    with store.transaction():
        selection = select_materials(store, day, anchor_id, target_revision, page_ids)
        verify_delivered_pages(selection.manifests, selection.delivered_pages)
        handoff_id = next_handoff_id(store)
        envelope = HandoffEnvelope(
            handoff_id,
            day,
            selection.anchor_id,
            selection.anchor,
            selection.target_revision,
            selection.manifests,
            selection.delivered_pages,
        )
        store_issued(store, envelope)
    return handoff_id
