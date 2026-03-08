"""Sports science knowledge base — curated rules with peer-reviewed citations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("coach.knowledge_base")

# Try to import yaml; fall back to a simple parser if unavailable
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False
    log.warning("PyYAML not installed — using basic YAML parser (limited)")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    """A single knowledge-base rule."""
    id: str
    category: str
    principle: str
    application: str
    conditions: str
    citation: str
    confidence: str  # "high", "medium", "low"
    tags: list[str] = field(default_factory=list)
    sport_specific: str | None = None


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """Load and query curated sports science rules from YAML files."""

    def __init__(self, knowledge_dir: str | None = None):
        """Load all YAML files from the knowledge directory.

        Parameters
        ----------
        knowledge_dir : str | None
            Path to the directory containing .yaml rule files.
            Defaults to ``<project>/knowledge/``.
        """
        if knowledge_dir is None:
            knowledge_dir = str(Path(__file__).parent.parent / "knowledge")
        self._dir = Path(knowledge_dir)
        self.rules: list[Rule] = []
        self._by_category: dict[str, list[Rule]] = {}
        self._by_tag: dict[str, list[Rule]] = {}
        self._by_id: dict[str, Rule] = {}
        self._load_all()

    def _load_all(self):
        """Load all .yaml files in the knowledge directory."""
        if not self._dir.exists():
            log.warning("Knowledge directory not found: %s", self._dir)
            return

        for yaml_file in sorted(self._dir.glob("*.yaml")):
            try:
                self._load_file(yaml_file)
            except Exception as exc:
                log.warning("Failed to load %s: %s", yaml_file.name, exc)

        log.info(
            "Knowledge base loaded: %d rules across %d categories",
            len(self.rules), len(self._by_category),
        )

    def _load_file(self, path: Path):
        """Parse a single YAML file and add its rules."""
        text = path.read_text(encoding="utf-8")
        if _HAS_YAML:
            data = yaml.safe_load(text)
        else:
            data = _basic_yaml_parse(text)

        if not data or not isinstance(data, dict):
            return

        category = data.get("category", path.stem)
        raw_rules = data.get("rules", [])

        for entry in raw_rules:
            if not isinstance(entry, dict):
                continue
            rule = Rule(
                id=entry.get("id", ""),
                category=category,
                principle=entry.get("principle", ""),
                application=entry.get("application", ""),
                conditions=entry.get("conditions", ""),
                citation=entry.get("citation", ""),
                confidence=entry.get("confidence", "medium"),
                tags=entry.get("tags", []),
                sport_specific=entry.get("sport_specific"),
            )
            self.rules.append(rule)
            self._by_id[rule.id] = rule
            self._by_category.setdefault(category, []).append(rule)
            for tag in rule.tags:
                self._by_tag.setdefault(tag, []).append(rule)

    # -- Query methods --------------------------------------------------------

    def query(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        sport: str | None = None,
        confidence: str | None = None,
    ) -> list[Rule]:
        """Query rules by category, tags, sport, and/or confidence.

        Parameters
        ----------
        category : str | None
            Filter by category (e.g. "recovery", "sleep").
        tags : list[str] | None
            Filter by any matching tag.
        sport : str | None
            Filter by sport_specific field (or include universal rules).
        confidence : str | None
            Minimum confidence level ("high", "medium", "low").

        Returns
        -------
        list[Rule]
            Matching rules, sorted by confidence (high first).
        """
        candidates = list(self.rules)

        if category:
            candidates = [r for r in candidates if r.category == category]

        if tags:
            tag_set = set(tags)
            candidates = [r for r in candidates if tag_set & set(r.tags)]

        if sport:
            candidates = [
                r for r in candidates
                if r.sport_specific is None or r.sport_specific == sport
            ]

        if confidence:
            conf_order = {"high": 3, "medium": 2, "low": 1}
            min_level = conf_order.get(confidence, 0)
            candidates = [
                r for r in candidates
                if conf_order.get(r.confidence, 0) >= min_level
            ]

        # Sort: high confidence first
        conf_rank = {"high": 0, "medium": 1, "low": 2}
        candidates.sort(key=lambda r: conf_rank.get(r.confidence, 3))
        return candidates

    def get_rule(self, rule_id: str) -> Rule | None:
        """Get a specific rule by its ID."""
        return self._by_id.get(rule_id)

    def get_relevant_rules(self, context: dict, max_rules: int = 5) -> list[Rule]:
        """Smart retrieval — find rules most relevant to a context dict.

        Parameters
        ----------
        context : dict
            Keys might include: metric, status, sport, category, tags, etc.
            Example: {"metric": "hrv", "status": "declining", "sport": "running"}

        Returns
        -------
        list[Rule]
            Top matching rules (up to max_rules), ranked by relevance.
        """
        # Build search terms from context
        search_terms = set()
        for key, val in context.items():
            if isinstance(val, str):
                search_terms.update(val.lower().split("_"))
                search_terms.add(val.lower())
            elif isinstance(val, list):
                for v in val:
                    if isinstance(v, str):
                        search_terms.add(v.lower())

        if not search_terms:
            return []

        # Score each rule
        scored: list[tuple[float, Rule]] = []
        sport = context.get("sport")
        conf_bonus = {"high": 0.3, "medium": 0.1, "low": 0.0}

        for rule in self.rules:
            score = 0.0
            # Tag overlap
            rule_tags = set(t.lower() for t in rule.tags)
            overlap = search_terms & rule_tags
            score += len(overlap) * 2.0

            # Category match
            if context.get("category") and rule.category == context["category"]:
                score += 1.5

            # Text search in principle + application
            text = (rule.principle + " " + rule.application).lower()
            for term in search_terms:
                if term in text:
                    score += 0.5

            # Sport match
            if sport and rule.sport_specific == sport:
                score += 1.0
            elif rule.sport_specific is not None and sport and rule.sport_specific != sport:
                score -= 0.5  # penalise wrong sport

            # Confidence bonus
            score += conf_bonus.get(rule.confidence, 0)

            if score > 0:
                scored.append((score, rule))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [rule for _, rule in scored[:max_rules]]

    def format_for_prompt(self, rules: list[Rule], max_rules: int = 5) -> str:
        """Format rules for inclusion in the LLM system prompt.

        Concise format with principle + application + citation.
        """
        if not rules:
            return ""
        lines = ["EVIDENCE-BASED GUIDELINES:"]
        for rule in rules[:max_rules]:
            lines.append(
                f"  [{rule.category}/{rule.id}] {rule.principle}. "
                f"Apply: {rule.application} "
                f"({rule.citation})"
            )
        return "\n".join(lines)

    def format_citation(self, rule: Rule) -> str:
        """Format a single rule with its full citation."""
        return (
            f"{rule.principle}\n"
            f"Application: {rule.application}\n"
            f"Conditions: {rule.conditions}\n"
            f"Source: {rule.citation} (confidence: {rule.confidence})"
        )

    @property
    def categories(self) -> list[str]:
        """List all available categories."""
        return sorted(self._by_category.keys())

    @property
    def stats(self) -> dict:
        """Return counts by category, confidence, and sport."""
        by_conf: dict[str, int] = {}
        by_sport: dict[str, int] = {}
        for rule in self.rules:
            by_conf[rule.confidence] = by_conf.get(rule.confidence, 0) + 1
            sport = rule.sport_specific or "universal"
            by_sport[sport] = by_sport.get(sport, 0) + 1
        return {
            "total": len(self.rules),
            "by_category": {k: len(v) for k, v in self._by_category.items()},
            "by_confidence": by_conf,
            "by_sport": by_sport,
        }


# ---------------------------------------------------------------------------
# Fallback YAML parser (if PyYAML not installed)
# ---------------------------------------------------------------------------

def _basic_yaml_parse(text: str) -> dict:
    """Extremely basic YAML parser for simple key-value + list structures.

    This handles only the specific YAML format used in our knowledge files.
    Install PyYAML for proper parsing.
    """
    import re
    # This is a best-effort fallback — install PyYAML for real use
    result = {"rules": []}
    current_rule = None
    current_key = None

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level keys
        if line.startswith("category:"):
            result["category"] = stripped.split(":", 1)[1].strip().strip('"')
        elif line.startswith("version:"):
            result["version"] = stripped.split(":", 1)[1].strip().strip('"')
        elif stripped == "rules:":
            continue
        elif stripped.startswith("- id:"):
            if current_rule:
                result["rules"].append(current_rule)
            current_rule = {"id": stripped.split(":", 1)[1].strip().strip('"'), "tags": []}
            current_key = "id"
        elif current_rule and ":" in stripped and not stripped.startswith("-"):
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"')
            if key == "tags":
                # Parse inline list [a, b, c]
                match = re.search(r'\[(.+)\]', val)
                if match:
                    current_rule["tags"] = [t.strip().strip('"') for t in match.group(1).split(",")]
                current_key = "tags"
            elif key == "sport_specific":
                current_rule[key] = None if val in ("null", "~", "") else val
                current_key = key
            else:
                current_rule[key] = val
                current_key = key

    if current_rule:
        result["rules"].append(current_rule)
    return result
