"""Retrieval-Augmented Generation engine -- combine vector search with knowledge base."""

from __future__ import annotations

import logging

log = logging.getLogger("coach.rag")


class RAGEngine:
    """Combine vector similarity search with keyword-based knowledge retrieval.

    Enhances LLM context with the most relevant sports science rules.
    """

    def __init__(self, knowledge_base, vector_store=None):
        """
        Parameters
        ----------
        knowledge_base : modules.knowledge_base.KnowledgeBase
        vector_store : modules.vector_store.VectorStore | None
        """
        self.kb = knowledge_base
        self.vs = vector_store

    def ensure_indexed(self):
        """Index knowledge base rules into vector store if not already done."""
        if self.vs and self.vs.available and not self.vs.is_indexed():
            all_rules = self.kb.query()  # Get all rules
            self.vs.index_rules(all_rules)
            log.info("Indexed %d rules into vector store", self.vs.count())

    def retrieve_context(
        self, query: str, max_rules: int = 5, category: str = None
    ) -> str:
        """Retrieve relevant knowledge for a coaching query.

        Strategy:
        1. If vector store available: semantic search for top 2*max_rules candidates
        2. Also get keyword matches from KnowledgeBase.get_relevant_rules()
        3. Merge, de-duplicate by rule ID
        4. Rank: vector results get score boost, keyword results keep their relevance
        5. Return top max_rules formatted for LLM

        If vector store not available: fall back to keyword-only.

        Returns formatted string for injection into LLM system prompt.
        """
        # Build context dict for keyword search
        context = _query_to_context(query, category)

        # Keyword-based retrieval
        keyword_rules = self.kb.get_relevant_rules(context, max_rules=max_rules * 2)

        # Semantic retrieval via vector store
        vector_rule_ids: dict[str, float] = {}  # rule_id -> relevance score
        if self.vs and self.vs.available:
            self.ensure_indexed()
            vector_results = self.vs.search(
                query, n_results=max_rules * 2, category=category
            )
            for vr in vector_results:
                # Convert cosine distance to similarity score (1 - distance)
                similarity = max(0.0, 1.0 - vr.get("distance", 1.0))
                vector_rule_ids[vr["id"]] = similarity

        # Score and merge all candidates
        scored: dict[str, tuple[float, object]] = {}  # id -> (score, rule)

        # Add keyword results with their relevance rank as score
        for rank, rule in enumerate(keyword_rules):
            base_score = max(0.0, 1.0 - rank * 0.1)  # 1.0, 0.9, 0.8, ...
            # Boost if also found by vector search
            vector_boost = vector_rule_ids.get(rule.id, 0.0) * 0.5
            final_score = base_score + vector_boost
            scored[rule.id] = (final_score, rule)

        # Add vector-only results (not already in keyword results)
        for rule_id, similarity in vector_rule_ids.items():
            if rule_id not in scored:
                rule = self.kb.get_rule(rule_id)
                if rule is not None:
                    scored[rule_id] = (similarity * 0.8, rule)

        # Sort by score descending, take top max_rules
        ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
        top_rules = [rule for _, rule in ranked[:max_rules]]

        if not top_rules:
            return ""

        return self.kb.format_for_prompt(top_rules, max_rules=max_rules)

    def augment_prompt(
        self, user_query: str, data_context: str = "", max_rules: int = 5
    ) -> str:
        """Build augmented context with retrieved knowledge.

        Format:
        RELEVANT SPORTS SCIENCE (retrieved for this query):
          [1] {rule.principle} -- {rule.application}
              Citation: {rule.citation}
          [2] ...
        """
        # Retrieve relevant rules as objects
        context = _query_to_context(user_query)
        keyword_rules = self.kb.get_relevant_rules(context, max_rules=max_rules * 2)

        vector_rule_ids: dict[str, float] = {}
        if self.vs and self.vs.available:
            self.ensure_indexed()
            vector_results = self.vs.search(user_query, n_results=max_rules * 2)
            for vr in vector_results:
                similarity = max(0.0, 1.0 - vr.get("distance", 1.0))
                vector_rule_ids[vr["id"]] = similarity

        # Merge and de-duplicate
        scored: dict[str, tuple[float, object]] = {}
        for rank, rule in enumerate(keyword_rules):
            base_score = max(0.0, 1.0 - rank * 0.1)
            vector_boost = vector_rule_ids.get(rule.id, 0.0) * 0.5
            scored[rule.id] = (base_score + vector_boost, rule)

        for rule_id, similarity in vector_rule_ids.items():
            if rule_id not in scored:
                rule = self.kb.get_rule(rule_id)
                if rule is not None:
                    scored[rule_id] = (similarity * 0.8, rule)

        ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
        top_rules = [rule for _, rule in ranked[:max_rules]]

        if not top_rules:
            return data_context

        lines = ["RELEVANT SPORTS SCIENCE (retrieved for this query):"]
        for i, rule in enumerate(top_rules, 1):
            principle = getattr(rule, "principle", "")
            application = getattr(rule, "application", "")
            citation = getattr(rule, "citation", "")
            lines.append(f"  [{i}] {principle} -- {application}")
            if citation:
                lines.append(f"      Citation: {citation}")

        science_block = "\n".join(lines)

        if data_context:
            return f"{data_context}\n\n{science_block}"
        return science_block

    def retrieve_for_session(
        self,
        session_type: str,
        phase: str = "",
        conditions: dict = None,
    ) -> str:
        """Retrieve knowledge relevant to a specific training session.

        Builds a query from session_type + phase + conditions.
        E.g., session_type="long_run", phase="taper" ->
        query = "long run during taper phase"

        Used by periodization engine for session description enrichment.
        """
        # Build natural language query from structured inputs
        query_parts = []

        # Clean session type: "long_run" -> "long run"
        cleaned_type = session_type.replace("_", " ").strip()
        if cleaned_type:
            query_parts.append(cleaned_type)

        if phase:
            cleaned_phase = phase.replace("_", " ").strip()
            query_parts.append(f"during {cleaned_phase} phase")

        # Add conditions to query
        if conditions:
            for key, value in conditions.items():
                if isinstance(value, str) and value:
                    query_parts.append(f"{key}: {value}")
                elif isinstance(value, (int, float)):
                    query_parts.append(f"{key} {value}")

        query = " ".join(query_parts) if query_parts else session_type

        # Determine category from session type
        category = None
        type_lower = session_type.lower()
        if "recovery" in type_lower or "rest" in type_lower or "easy" in type_lower:
            category = "recovery"
        elif "long" in type_lower or "tempo" in type_lower or "interval" in type_lower:
            category = "training_load"

        return self.retrieve_context(query, max_rules=3, category=category)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query_to_context(query: str, category: str = None) -> dict:
    """Convert a natural language query to a context dict for KnowledgeBase.

    Extracts likely metric names, statuses, and tags from the query text
    so the keyword-based retrieval can match on them.
    """
    query_lower = query.lower()
    context: dict = {}

    if category:
        context["category"] = category

    # Detect metrics
    metric_keywords = {
        "hrv": "hrv",
        "heart rate variability": "hrv",
        "resting heart rate": "rhr",
        "rhr": "rhr",
        "sleep": "sleep",
        "ctl": "training_load",
        "fitness": "training_load",
        "training load": "training_load",
        "tss": "training_load",
        "atl": "training_load",
    }
    for keyword, metric in metric_keywords.items():
        if keyword in query_lower:
            context["metric"] = metric
            break

    # Detect status/condition
    status_keywords = {
        "tired": "fatigued",
        "fatigue": "fatigued",
        "exhausted": "fatigued",
        "declining": "declining",
        "improving": "improving",
        "recover": "recovery",
        "sore": "fatigued",
        "overtraining": "overtraining",
        "illness": "illness",
        "sick": "illness",
        "injured": "injury",
        "injury": "injury",
    }
    for keyword, status in status_keywords.items():
        if keyword in query_lower:
            context["status"] = status
            break

    # Extract tags from query words
    tag_candidates = [
        "recovery",
        "sleep",
        "hrv",
        "rhr",
        "training",
        "fatigue",
        "periodization",
        "taper",
        "nutrition",
        "hydration",
        "strength",
        "marathon",
        "cycling",
        "running",
    ]
    matched_tags = [t for t in tag_candidates if t in query_lower]
    if matched_tags:
        context["tags"] = matched_tags

    # If no context could be extracted, use the raw query as a tag
    if not context:
        words = [w.strip(".,!?") for w in query_lower.split() if len(w) > 3]
        if words:
            context["tags"] = words[:5]

    return context
