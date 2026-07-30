"""Retention purge deletes only rows past the cutoff, in batches."""

from scripts.purge_audit_logs import purge_audit_logs


class _FakeQuery:
    """Mimics the db.query(...).filter(...).limit(...).all()/.count()/.delete()
    chain used by purge_audit_logs. The ids handed out by the most recent
    all() are what a following delete() removes, which is exactly how the
    real two-statement batch loop behaves."""

    def __init__(self, db, limit=None):
        self._db = db
        self._limit = limit

    def filter(self, *a, **k):
        return self

    def limit(self, n):
        return _FakeQuery(self._db, n)

    def count(self):
        return len(self._db.old_ids)

    def all(self):
        self._db.last_batch = self._db.old_ids[: self._limit]
        return [(i,) for i in self._db.last_batch]

    def delete(self, synchronize_session=False):
        batch = self._db.last_batch
        self._db.old_ids = [i for i in self._db.old_ids if i not in batch]
        self._db.deleted.extend(batch)
        self._db.last_batch = []
        return len(batch)


class _FakeDB:
    def __init__(self, old_ids):
        self.old_ids = list(old_ids)
        self.deleted = []
        self.last_batch = []
        self.commits = 0

    def query(self, *entities):
        return _FakeQuery(self)

    def commit(self):
        self.commits += 1


def test_dry_run_counts_without_deleting():
    db = _FakeDB(range(7))
    assert purge_audit_logs(db, days=30, dry_run=True) == 7
    assert db.deleted == []
    assert db.commits == 0


def test_deletes_every_stale_row_in_batches():
    db = _FakeDB(range(25))
    assert purge_audit_logs(db, days=30, batch_size=10) == 25
    assert db.old_ids == []
    assert sorted(db.deleted) == list(range(25))
    # 10 + 10 + 5 -> three committed batches, so a big backlog never sits in
    # one long transaction.
    assert db.commits == 3


def test_nothing_to_purge_is_a_no_op():
    db = _FakeDB([])
    assert purge_audit_logs(db, days=30) == 0
    assert db.commits == 0
