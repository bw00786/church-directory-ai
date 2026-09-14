"""Apply the pgvector memory migration and optionally re-embed unlabelled rows."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.database.connection import _engine, SessionLocal
from app.database.migrations import migrate
from app.database.memory_vectors import validate_embedding
from app.database.models import ServiceObservation
from app.memory.embeddings import text_embedder


def backfill(factory=SessionLocal, embedder=text_embedder, batch_size=100):
    count = 0
    while True:
        with factory() as session:
            rows = list(session.scalars(select(ServiceObservation).where(
                ServiceObservation.embedding_space.is_(None)
            ).order_by(ServiceObservation.id).limit(batch_size)))
            if not rows:
                break
            for row in rows:
                embedded = embedder.embed_with_metadata(row.text, input_type="document")
                vector = embedded.vector.tolist()
                validate_embedding(vector)
                row.embedding = vector
                row.embedding_space = embedded.space
            session.commit()
            count += len(rows)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true", help="Re-embed unlabelled legacy rows from text; may call the configured provider")
    args = parser.parse_args()
    result = {"migration": migrate(_engine)}
    if args.backfill:
        result["backfilled"] = backfill()
    print(json.dumps(result))