"""Vector store for semantic search over knowledge base rules using ChromaDB."""

from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import dataclass

log = logging.getLogger("coach.vector_store")

# Default persist directory for ChromaDB
DEFAULT_PERSIST_DIR = str(Path(__file__).parent.parent / "models" / "chromadb")


class VectorStore:
    """ChromaDB-backed vector store for semantic search over knowledge rules.

    Uses ChromaDB's built-in all-MiniLM-L6-v2 embedding function.
    Falls back gracefully if ChromaDB is not installed.
    """

    COLLECTION_NAME = "knowledge_rules"

    def __init__(self, persist_dir: str = None, in_memory: bool = False):
        """Initialize ChromaDB client.

        Parameters
        ----------
        persist_dir : str
            Directory for ChromaDB persistence. Defaults to models/chromadb/
        in_memory : bool
            If True, use ephemeral client (for testing). No persistence.
        """
        self._available = False
        try:
            import chromadb

            if in_memory:
                self._client = chromadb.EphemeralClient()
            else:
                path = persist_dir or DEFAULT_PERSIST_DIR
                Path(path).mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(path=path)
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            log.info("ChromaDB vector store initialized (in_memory=%s)", in_memory)
        except ImportError:
            log.warning("chromadb not installed — vector search disabled")
            self._client = None
            self._collection = None
        except Exception as exc:
            log.warning("Failed to initialize ChromaDB: %s", exc)
            self._client = None
            self._collection = None

    @property
    def available(self) -> bool:
        """Whether the vector store backend is operational."""
        return self._available

    def index_rules(self, rules: list) -> int:
        """Index knowledge base rules into the vector store.

        Each rule becomes a document:
          document = f"{rule.principle}. {rule.application}. {rule.conditions}"
          metadata = {category, confidence, tags (comma-joined), sport}
          id = rule.id

        Returns number of rules indexed.
        Skips if already indexed (by count check) -- use reindex() to force.
        """
        if not self._available:
            return 0

        if not rules:
            return 0

        # Skip if already indexed with the same count
        if self._collection.count() >= len(rules):
            log.debug(
                "Collection already has %d docs (>= %d rules); skipping index",
                self._collection.count(),
                len(rules),
            )
            return self._collection.count()

        documents = []
        metadatas = []
        ids = []

        for rule in rules:
            rule_id = getattr(rule, "id", "") or ""
            if not rule_id:
                continue

            principle = getattr(rule, "principle", "") or ""
            application = getattr(rule, "application", "") or ""
            conditions = getattr(rule, "conditions", "") or ""
            doc = f"{principle}. {application}. {conditions}"

            # Build metadata dict -- ChromaDB requires str, int, float, or bool values
            category = getattr(rule, "category", "") or ""
            confidence = getattr(rule, "confidence", "medium") or "medium"
            tags = getattr(rule, "tags", []) or []
            sport = getattr(rule, "sport_specific", None)

            meta = {
                "category": category,
                "confidence": confidence,
                "tags": ",".join(tags) if tags else "",
                "sport": sport if sport else "",
            }

            documents.append(doc)
            metadatas.append(meta)
            ids.append(rule_id)

        if not documents:
            return 0

        # ChromaDB upsert handles duplicates gracefully
        # Process in batches to avoid hitting ChromaDB limits
        batch_size = 500
        for i in range(0, len(documents), batch_size):
            end = min(i + batch_size, len(documents))
            self._collection.upsert(
                documents=documents[i:end],
                metadatas=metadatas[i:end],
                ids=ids[i:end],
            )

        indexed = self._collection.count()
        log.info("Indexed %d rules into vector store", indexed)
        return indexed

    def search(
        self, query: str, n_results: int = 5, category: str = None
    ) -> list[dict]:
        """Semantic search for rules relevant to a query.

        Parameters
        ----------
        query : str
            Natural language query (e.g., "tired after hard training week")
        n_results : int
            Max results to return
        category : str | None
            Optional category filter (e.g., "recovery")

        Returns list of dicts: [{id, document, metadata, distance}, ...]
        Distance is cosine distance (lower = more similar).
        """
        if not self._available:
            return []

        if not query or not query.strip():
            return []

        # Build optional where filter
        where_filter = None
        if category:
            where_filter = {"category": category}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(n_results, max(self._collection.count(), 1)),
                where=where_filter if where_filter else None,
            )
        except Exception as exc:
            log.warning("Vector search failed: %s", exc)
            return []

        # Unpack ChromaDB results format
        output = []
        if not results or not results.get("ids") or not results["ids"][0]:
            return output

        ids = results["ids"][0]
        documents = results["documents"][0] if results.get("documents") else []
        metadatas = results["metadatas"][0] if results.get("metadatas") else []
        distances = results["distances"][0] if results.get("distances") else []

        for i, rule_id in enumerate(ids):
            entry = {
                "id": rule_id,
                "document": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else 1.0,
            }
            output.append(entry)

        return output

    def is_indexed(self) -> bool:
        """Check if the collection has been populated."""
        if not self._available:
            return False
        return self._collection.count() > 0

    def reindex(self, rules: list) -> int:
        """Drop and re-index all rules.

        Returns number of rules indexed after reindex.
        """
        if not self._available:
            return 0

        # Delete all existing documents
        try:
            existing = self._collection.get()
            if existing and existing.get("ids"):
                self._collection.delete(ids=existing["ids"])
            log.debug("Cleared %d existing documents", len(existing.get("ids", [])))
        except Exception as exc:
            log.warning("Failed to clear collection: %s", exc)
            # Try to recreate the collection
            try:
                self._client.delete_collection(self.COLLECTION_NAME)
                self._collection = self._client.get_or_create_collection(
                    name=self.COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as exc2:
                log.error("Failed to recreate collection: %s", exc2)
                return 0

        # Force index (bypass count check by passing directly)
        return self._force_index(rules)

    def _force_index(self, rules: list) -> int:
        """Index rules without the count-based skip check."""
        if not self._available or not rules:
            return 0

        documents = []
        metadatas = []
        ids = []

        for rule in rules:
            rule_id = getattr(rule, "id", "") or ""
            if not rule_id:
                continue

            principle = getattr(rule, "principle", "") or ""
            application = getattr(rule, "application", "") or ""
            conditions = getattr(rule, "conditions", "") or ""
            doc = f"{principle}. {application}. {conditions}"

            category = getattr(rule, "category", "") or ""
            confidence = getattr(rule, "confidence", "medium") or "medium"
            tags = getattr(rule, "tags", []) or []
            sport = getattr(rule, "sport_specific", None)

            meta = {
                "category": category,
                "confidence": confidence,
                "tags": ",".join(tags) if tags else "",
                "sport": sport if sport else "",
            }

            documents.append(doc)
            metadatas.append(meta)
            ids.append(rule_id)

        if not documents:
            return 0

        batch_size = 500
        for i in range(0, len(documents), batch_size):
            end = min(i + batch_size, len(documents))
            self._collection.upsert(
                documents=documents[i:end],
                metadatas=metadatas[i:end],
                ids=ids[i:end],
            )

        return self._collection.count()

    def count(self) -> int:
        """Return number of indexed documents."""
        if not self._available:
            return 0
        return self._collection.count()

    def get_by_id(self, rule_id: str) -> dict | None:
        """Retrieve a single document by its rule ID.

        Returns dict with id, document, metadata or None if not found.
        """
        if not self._available:
            return None

        try:
            result = self._collection.get(ids=[rule_id])
            if result and result.get("ids") and result["ids"]:
                return {
                    "id": result["ids"][0],
                    "document": result["documents"][0] if result.get("documents") else "",
                    "metadata": result["metadatas"][0] if result.get("metadatas") else {},
                }
        except Exception:
            pass
        return None
