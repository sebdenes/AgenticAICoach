"""Long-term athlete memory — episodic, semantic, and procedural memories.

Stores memories in SQLite (structured) + ChromaDB (semantic search).
Extracts notable facts from conversations via Claude Haiku (async, non-blocking).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database import Database
    from modules.vector_store import VectorStore

log = logging.getLogger("coach.memory")

MEMORY_COLLECTION = "athlete_memories"

# Prompt for Claude Haiku to extract memories from conversations
EXTRACTION_PROMPT = """You are a sports coaching memory system. Extract notable coaching facts from this conversation exchange.

Return a JSON array of memory objects. Each object has:
- "type": one of "episodic" (specific events/incidents), "semantic" (learned patterns/preferences), "procedural" (what worked or didn't)
- "content": concise fact worth remembering long-term (1-2 sentences max)
- "importance": 0.0-1.0 (0.3=minor note, 0.5=useful, 0.7=important, 0.9=critical insight)

Rules:
- Only extract facts worth remembering across future sessions
- Skip generic coaching advice or data that's already in the database
- Focus on: injury history, personal preferences, what works/doesn't, key events, behavioral patterns
- Return [] if nothing notable

Examples of good memories:
- {"type": "episodic", "content": "Achilles pain flared up after hill repeat session on 2024-03-15", "importance": 0.8}
- {"type": "semantic", "content": "Athlete performs best after 2 consecutive nights of 7+ hours sleep", "importance": 0.7}
- {"type": "procedural", "content": "Carb loading 3 days before race caused stomach issues — try 2 days instead", "importance": 0.6}

User message: {user_message}

Assistant response: {assistant_response}

Return ONLY the JSON array, nothing else."""


class AthleteMemory:
    """Long-term memory manager with SQLite storage + ChromaDB semantic search."""

    def __init__(self, db: "Database", vector_store: "VectorStore"):
        self.db = db
        self._vs = vector_store
        self._collection = None
        if vector_store and vector_store.available:
            self._collection = vector_store.get_or_create_collection(MEMORY_COLLECTION)
            if self._collection:
                log.info(
                    "Athlete memory initialized — %d memories in vector store",
                    self._collection.count(),
                )

    @property
    def available(self) -> bool:
        return self._collection is not None

    def store(self, memory_type: str, content: str, importance: float = 0.5) -> int | None:
        """Store a new memory in both SQLite and ChromaDB.

        Returns the SQLite row ID, or None on failure.
        """
        if memory_type not in ("episodic", "semantic", "procedural"):
            log.warning("Invalid memory type: %s", memory_type)
            return None

        # Generate embedding ID for ChromaDB
        embedding_id = f"mem_{uuid.uuid4().hex[:12]}"

        # Store in SQLite
        try:
            row_id = self.db.store_memory(
                memory_type=memory_type,
                content=content,
                embedding_id=embedding_id,
                importance=importance,
            )
        except Exception as exc:
            log.error("Failed to store memory in SQLite: %s", exc)
            return None

        # Store in ChromaDB for semantic search
        if self._collection:
            try:
                self._collection.upsert(
                    documents=[content],
                    metadatas=[{
                        "type": memory_type,
                        "importance": importance,
                        "created_at": datetime.now().isoformat(),
                        "sqlite_id": str(row_id),
                    }],
                    ids=[embedding_id],
                )
            except Exception as exc:
                log.warning("Failed to store memory in ChromaDB: %s", exc)

        log.info("Stored %s memory (importance=%.1f): %s", memory_type, importance, content[:80])
        return row_id

    def retrieve(self, query: str, n_results: int = 5) -> list[dict]:
        """Semantic search for memories relevant to a query.

        Returns list of dicts with content, type, importance, distance.
        """
        if not self._collection or not query:
            return []

        try:
            count = self._collection.count()
            if count == 0:
                return []

            results = self._collection.query(
                query_texts=[query],
                n_results=min(n_results, count),
            )
        except Exception as exc:
            log.warning("Memory retrieval failed: %s", exc)
            return []

        memories = []
        if not results or not results.get("ids") or not results["ids"][0]:
            return memories

        ids = results["ids"][0]
        documents = results["documents"][0] if results.get("documents") else []
        metadatas = results["metadatas"][0] if results.get("metadatas") else []
        distances = results["distances"][0] if results.get("distances") else []

        for i, doc_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            dist = distances[i] if i < len(distances) else 1.0

            # Only return memories with reasonable relevance (cosine distance < 1.5)
            if dist > 1.5:
                continue

            memory = {
                "content": documents[i] if i < len(documents) else "",
                "type": meta.get("type", "unknown"),
                "importance": meta.get("importance", 0.5),
                "distance": dist,
                "embedding_id": doc_id,
            }
            memories.append(memory)

            # Update access tracking in SQLite
            sqlite_id = meta.get("sqlite_id")
            if sqlite_id:
                try:
                    self.db.update_memory_access(int(sqlite_id))
                except (ValueError, Exception):
                    pass

        return memories

    async def extract_memories(
        self, user_message: str, assistant_response: str, anthropic_client=None
    ) -> dict | None:
        """Extract notable facts from a conversation exchange using Claude Haiku.

        This runs async and is designed to be fire-and-forget (non-blocking).
        Returns usage dict with token counts, or None.
        """
        if not anthropic_client:
            return None

        haiku_model = "claude-haiku-4-5-20251001"
        prompt = EXTRACTION_PROMPT.format(
            user_message=user_message[:1000],
            assistant_response=assistant_response[:2000],
        )

        try:
            resp = anthropic_client.messages.create(
                model=haiku_model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract usage info
            usage_info = None
            resp_usage = getattr(resp, "usage", None)
            if resp_usage:
                usage_info = {
                    "model": haiku_model,
                    "input_tokens": getattr(resp_usage, "input_tokens", 0) or 0,
                    "output_tokens": getattr(resp_usage, "output_tokens", 0) or 0,
                }

            text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    text += block.text

            if not text.strip():
                return usage_info

            memories = json.loads(text.strip())
            if not isinstance(memories, list):
                return usage_info

            for mem in memories:
                if not isinstance(mem, dict):
                    continue
                mem_type = mem.get("type", "")
                content = mem.get("content", "")
                importance = mem.get("importance", 0.5)

                if mem_type and content:
                    self.store(mem_type, content, importance)

            return usage_info

        except json.JSONDecodeError:
            log.debug("Memory extraction returned non-JSON — skipping")
            return None
        except Exception as exc:
            log.warning("Memory extraction failed: %s", exc)
            return None

    def get_context_block(self, query: str, max_memories: int = 5) -> str:
        """Retrieve relevant memories and format as a system prompt block.

        Returns a formatted string ready to append to the system prompt.
        Returns empty string if no relevant memories found.
        """
        memories = self.retrieve(query, n_results=max_memories)
        if not memories:
            return ""

        lines = ["\n## Athlete Memory (long-term coaching insights)\n"]
        for mem in memories:
            icon = {"episodic": "[Event]", "semantic": "[Pattern]", "procedural": "[Lesson]"}.get(
                mem["type"], "[Note]"
            )
            lines.append(f"- {icon} {mem['content']}")

        return "\n".join(lines)

    def decay(self):
        """Run periodic importance decay on old, unaccessed memories."""
        try:
            self.db.decay_memories(days_old=30, decay_factor=0.95)
            log.debug("Memory decay applied")
        except Exception as exc:
            log.warning("Memory decay failed: %s", exc)

    def count(self) -> int:
        """Return total number of stored memories."""
        if self._collection:
            return self._collection.count()
        return 0
