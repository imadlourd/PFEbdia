from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

ARROW_SCHEMA = pa.schema([
    pa.field("event_id",    pa.string(),                    nullable=False),
    pa.field("source",      pa.string(),                    nullable=False),
    pa.field("event_type",  pa.string(),                    nullable=False),
    pa.field("occurred_at", pa.timestamp("us", tz="UTC"),   nullable=False),
    pa.field("ingested_at", pa.timestamp("us", tz="UTC"),   nullable=False),
    pa.field("actor_id",    pa.string(),                    nullable=True),
    pa.field("entity_ref",  pa.string(),                    nullable=True),
    pa.field("entity_id",   pa.string(),                    nullable=True),
    pa.field("payload",     pa.string(),                    nullable=True),
])

VALID_SOURCES = {"github", "jira", "slack"}


@dataclass
class DevEvent:
    source:      str
    event_type:  str
    occurred_at: datetime
    actor_id:    Optional[str]  = None
    entity_ref:  Optional[str]  = None
    entity_id:   Optional[str]  = None
    payload:     Optional[dict] = None

    event_id:    str      = field(init=False)
    ingested_at: datetime = field(init=False)

    def __post_init__(self):
        if self.source not in VALID_SOURCES:
            raise ValueError(f"Invalid source '{self.source}'. Must be one of {VALID_SOURCES}")
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=timezone.utc)
        self.ingested_at = datetime.now(timezone.utc)
        raw = f"{self.source}|{self.event_type}|{self.entity_id or ''}|{self.occurred_at.isoformat()}"
        self.event_id = hashlib.sha256(raw.encode()).hexdigest()[:32]

    def to_dict(self):
        return {
            "event_id":    self.event_id,
            "source":      self.source,
            "event_type":  self.event_type,
            "occurred_at": self.occurred_at,
            "ingested_at": self.ingested_at,
            "actor_id":    self.actor_id,
            "entity_ref":  self.entity_ref,
            "entity_id":   self.entity_id,
            "payload":     json.dumps(self.payload) if self.payload else None,
        }


def write_parquet(events: list[DevEvent], path: str, append: bool = False) -> int:
    if not events:
        return 0

    rows = [e.to_dict() for e in events]
    cols = {f.name: [r[f.name] for r in rows] for f in ARROW_SCHEMA}
    arrays = [pa.array(cols[f.name], type=f.type) for f in ARROW_SCHEMA]
    table = pa.table(dict(zip([f.name for f in ARROW_SCHEMA], arrays)), schema=ARROW_SCHEMA)

    if append:
        try:
            existing = pq.read_table(path)
            import pandas as pd
            df = pa.concat_tables([existing, table]).to_pandas()
            df = df.drop_duplicates(subset=["event_id"], keep="last")
            table = pa.Table.from_pandas(df, schema=ARROW_SCHEMA)
        except FileNotFoundError:
            pass

    pq.write_table(table, path, compression="snappy")
    return len(table)