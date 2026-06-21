"""RDF/SPARQL child personalization memory for HRI activities."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

try:
    from rdflib import Graph, Literal, Namespace, URIRef
    from rdflib.namespace import RDF, RDFS
except ImportError:
    Graph = None
    Literal = str
    URIRef = str

    class Namespace(str):
        def __getattr__(self, name: str) -> str:
            return f"{self}{name}"

        def __getitem__(self, name: str) -> str:
            return f"{self}{name}"

    class _RdfFallback:
        type = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

    class _RdfsFallback:
        Class = "http://www.w3.org/2000/01/rdf-schema#Class"
        label = "http://www.w3.org/2000/01/rdf-schema#label"

    RDF = _RdfFallback()
    RDFS = _RdfsFallback()

HRI = Namespace("http://example.org/hri#")
CHILD = Namespace("http://example.org/child/")
SESSION = Namespace("http://example.org/session/")
CONCEPT = Namespace("http://example.org/concept/")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "unknown"


def _safe_filename_part(text: str) -> str:
    return _slug(text).replace("_", "-")


def _unique_preserve_order(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


class RdfChildMemoryStore:
    """Persistent Turtle-backed child memory queried with SPARQL."""

    _PREDICATE_TO_BUCKET = {
        str(HRI.likesStoryTheme): "story_preferences",
        str(HRI.likesStoryCharacter): "story_preferences",
        str(HRI.likesStorySetting): "story_preferences",
        str(HRI.prefersStoryEnding): "story_preferences",
        str(HRI.likesEmotionTopic): "emotion_preferences",
        str(HRI.recognizesEmotionWell): "recognizes_well",
        str(HRI.needsSupportWithEmotion): "needs_support_with",
        str(HRI.likesEmotionRecognitionStimulus): "preferred_stimuli",
        str(HRI.prefersActivityType): "preferred_activity_types",
    }

    def __init__(self, rdf_path: str, seed_if_missing: bool = True):
        if Graph is None:
            raise RuntimeError(
                "RDF child memory requires rdflib. Install the package dependency "
                "or set memory_enabled:=false."
            )
        self.rdf_path = Path(rdf_path)
        self.seed_if_missing = seed_if_missing
        self.graph = Graph()
        self._bind_namespaces()

        if self.rdf_path.exists():
            self.graph.parse(self.rdf_path, format="turtle")
        elif seed_if_missing:
            self.rdf_path.parent.mkdir(parents=True, exist_ok=True)
            self._seed_graph()
            self.save()

    def _bind_namespaces(self) -> None:
        self.graph.bind("hri", HRI)
        self.graph.bind("child", CHILD)
        self.graph.bind("session", SESSION)
        self.graph.bind("concept", CONCEPT)
        self.graph.bind("rdfs", RDFS)

    def save(self) -> None:
        self.rdf_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph.serialize(destination=str(self.rdf_path), format="turtle")

    def _seed_graph(self) -> None:
        for rdf_class in (
            HRI.Child,
            HRI.StoryTheme,
            HRI.StoryCharacter,
            HRI.StorySetting,
            HRI.StoryEnding,
            HRI.Emotion,
            HRI.EmotionRecognitionStimulus,
            HRI.ActivityType,
            HRI.Preference,
        ):
            self.graph.add((rdf_class, RDF.type, RDFS.Class))

        self._add_child(
            "Luca",
            story_themes=["dinosaurs", "space", "robots"],
            story_characters=["friendly robot", "small dinosaur"],
            story_settings=["moon", "jungle"],
            story_endings=["happy ending"],
            emotion_topics=["happy", "curious", "brave"],
            recognizes_well=["happy", "sad"],
            needs_support=["scared", "surprised"],
            recognition_stimuli=["clear face pictures", "cartoon faces"],
            activity_types=["storytelling"],
        )
        self._add_child(
            "Sofia",
            story_themes=["animals", "magic forest", "teddy bear adventures"],
            story_characters=["teddy bear", "cat", "fairy"],
            story_settings=["forest", "bedroom", "garden"],
            story_endings=["calm ending"],
            emotion_topics=["calm", "proud", "kind"],
            recognizes_well=["happy", "angry"],
            needs_support=["scared", "disgusted"],
            recognition_stimuli=["animal characters", "illustrated faces"],
            activity_types=["mood_check_teddy"],
        )

    def _concept(self, label: str, rdf_class: URIRef) -> URIRef:
        uri = CONCEPT[_slug(label)]
        self.graph.add((uri, RDF.type, rdf_class))
        self.graph.add((uri, RDFS.label, Literal(label)))
        return uri

    def _add_values(
        self,
        child_uri: URIRef,
        predicate: URIRef,
        values: Iterable[str],
        rdf_class: URIRef,
    ) -> None:
        for value in values:
            self.graph.add((child_uri, predicate, self._concept(value, rdf_class)))

    def _add_child(
        self,
        display_name: str,
        *,
        story_themes: Iterable[str],
        story_characters: Iterable[str],
        story_settings: Iterable[str],
        story_endings: Iterable[str],
        emotion_topics: Iterable[str],
        recognizes_well: Iterable[str],
        needs_support: Iterable[str],
        recognition_stimuli: Iterable[str],
        activity_types: Iterable[str],
    ) -> None:
        child_uri = CHILD[_slug(display_name)]
        self.graph.add((child_uri, RDF.type, HRI.Child))
        self.graph.add((child_uri, HRI.displayName, Literal(display_name)))
        self.graph.add((child_uri, RDFS.label, Literal(display_name)))
        self._add_values(child_uri, HRI.likesStoryTheme, story_themes, HRI.StoryTheme)
        self._add_values(
            child_uri,
            HRI.likesStoryCharacter,
            story_characters,
            HRI.StoryCharacter,
        )
        self._add_values(child_uri, HRI.likesStorySetting, story_settings, HRI.StorySetting)
        self._add_values(child_uri, HRI.prefersStoryEnding, story_endings, HRI.StoryEnding)
        self._add_values(child_uri, HRI.likesEmotionTopic, emotion_topics, HRI.Emotion)
        self._add_values(child_uri, HRI.recognizesEmotionWell, recognizes_well, HRI.Emotion)
        self._add_values(child_uri, HRI.needsSupportWithEmotion, needs_support, HRI.Emotion)
        self._add_values(
            child_uri,
            HRI.likesEmotionRecognitionStimulus,
            recognition_stimuli,
            HRI.EmotionRecognitionStimulus,
        )
        self._add_values(child_uri, HRI.prefersActivityType, activity_types, HRI.ActivityType)

    def get_child_memory_context(
        self,
        child_name: str,
        activity_type: str,
        max_facts: int = 12,
    ) -> Dict[str, Any]:
        """Return child memory context using a case-insensitive SPARQL query."""
        requested_name = child_name.strip()
        if not requested_name:
            return {
                "child_name": "",
                "found": False,
                "prompt_text": "No child name was provided; no stored memory was used.",
            }

        query = """
            PREFIX hri: <http://example.org/hri#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

            SELECT ?child ?displayName ?predicate ?value ?label
            WHERE {
                ?child a hri:Child ;
                       hri:displayName ?displayName .
                FILTER(LCASE(STR(?displayName)) = LCASE(STR(?requestedName)))
                OPTIONAL {
                    ?child ?predicate ?value .
                    VALUES ?predicate {
                        hri:likesStoryTheme
                        hri:likesStoryCharacter
                        hri:likesStorySetting
                        hri:prefersStoryEnding
                        hri:likesEmotionTopic
                        hri:recognizesEmotionWell
                        hri:needsSupportWithEmotion
                        hri:likesEmotionRecognitionStimulus
                        hri:prefersActivityType
                    }
                    OPTIONAL { ?value rdfs:label ?label }
                }
            }
        """
        rows = list(
            self.graph.query(
                query,
                initBindings={"requestedName": Literal(requested_name)},
            )
        )
        if not rows:
            return {
                "child_name": requested_name,
                "found": False,
                "prompt_text": "No stored memory found for this child.",
            }

        display_name = str(rows[0].displayName)
        memory = {
            "child_name": display_name,
            "found": True,
            "story_preferences": [],
            "emotion_preferences": [],
            "emotion_recognition": {
                "recognizes_well": [],
                "needs_support_with": [],
                "preferred_stimuli": [],
            },
            "preferred_activity_types": [],
        }

        for row in rows:
            if row.predicate is None or row.value is None:
                continue
            bucket = self._PREDICATE_TO_BUCKET.get(str(row.predicate))
            if not bucket:
                continue
            label = str(row.label) if row.label is not None else self._label_from_uri(row.value)
            if bucket in {"recognizes_well", "needs_support_with", "preferred_stimuli"}:
                memory["emotion_recognition"][bucket].append(label)
            else:
                memory[bucket].append(label)

        memory["story_preferences"] = _unique_preserve_order(memory["story_preferences"])
        memory["emotion_preferences"] = _unique_preserve_order(memory["emotion_preferences"])
        memory["preferred_activity_types"] = _unique_preserve_order(
            memory["preferred_activity_types"]
        )
        for key, values in memory["emotion_recognition"].items():
            memory["emotion_recognition"][key] = _unique_preserve_order(values)

        memory["prompt_text"] = self._build_prompt_text(memory, activity_type, max_facts)
        return memory

    @staticmethod
    def _label_from_uri(value: Any) -> str:
        text = str(value).rstrip("/#").split("/")[-1].split("#")[-1]
        return text.replace("_", " ")

    def _build_prompt_text(
        self,
        memory: Mapping[str, Any],
        activity_type: str,
        max_facts: int,
    ) -> str:
        max_facts = max(1, int(max_facts))
        child_name = memory.get("child_name", "the child")
        facts_used = 0
        lines = [f"The child is {child_name}."]

        def take(values: Iterable[str]) -> List[str]:
            nonlocal facts_used
            remaining = max_facts - facts_used
            if remaining <= 0:
                return []
            selected = list(values)[:remaining]
            facts_used += len(selected)
            return selected

        story_values = take(memory.get("story_preferences", []))
        if story_values:
            lines.append("")
            lines.append("Known story preferences:")
            lines.extend(f"- {item}" for item in story_values)

        emotion_values = take(memory.get("emotion_preferences", []))
        emotion_recognition = memory.get("emotion_recognition", {})
        recognizes_well = take(emotion_recognition.get("recognizes_well", []))
        needs_support = take(emotion_recognition.get("needs_support_with", []))
        preferred_stimuli = take(emotion_recognition.get("preferred_stimuli", []))
        if emotion_values or recognizes_well or needs_support or preferred_stimuli:
            lines.append("")
            lines.append("Known emotion-related preferences:")
            if emotion_values:
                lines.append(f"- likes talking about {', '.join(emotion_values)}")
            if recognizes_well:
                lines.append(f"- recognizes {self._join_words(recognizes_well)} well")
            if needs_support:
                lines.append(
                    f"- may need support with {self._join_words(needs_support)}"
                )
            if preferred_stimuli:
                lines.append(
                    "- likes "
                    f"{self._join_words(preferred_stimuli)} for emotion recognition"
                )

        activity_guidance = {
            "storytelling": (
                "For storytelling, prefer these themes, characters, settings, and endings "
                "when they fit the therapist's instruction."
            ),
            "mood_check_teddy": (
                "For mood check with the teddy bear, use preferred emotions and familiar "
                "characters or objects only when relevant."
            ),
            "computer_emotion_recognition": (
                "For computer emotion recognition, start from emotions the child recognizes "
                "well, then gently scaffold emotions that may need support."
            ),
            "generic_child_activity": (
                "For generic activities, use only the preferences that are clearly relevant."
            ),
        }
        lines.append("")
        lines.append(
            activity_guidance.get(activity_type, activity_guidance["generic_child_activity"])
        )
        lines.append(
            "Use this memory only to personalize story content, examples, characters, "
            "emotion vocabulary, and emotion-recognition scaffolding."
        )
        lines.append("Do not say that you are reading from a memory database.")
        lines.append("Do not reveal private notes.")
        lines.append(
            "If the therapist's current activity instruction conflicts with memory, "
            "follow the therapist's current instruction."
        )
        return "\n".join(lines)

    @staticmethod
    def _join_words(values: Iterable[str]) -> str:
        words = list(values)
        if not words:
            return ""
        if len(words) == 1:
            return words[0]
        return ", ".join(words[:-1]) + f" and {words[-1]}"


def format_memory_for_report(memory_context: Mapping[str, Any]) -> str:
    if not memory_context:
        return "Memory was disabled or unavailable."
    if not memory_context.get("found"):
        return memory_context.get("prompt_text", "No stored memory found for this child.")
    return str(memory_context.get("prompt_text", "Stored memory was found."))


def generate_child_memory_report(
    *,
    report_dir: str,
    context: Mapping[str, Any],
    transcript: List[Mapping[str, str]],
    memory_context: Mapping[str, Any],
    completion_reason: str,
    turns: int,
    llm_client: Optional[Any] = None,
    temperature: float = 0.2,
    max_response_tokens: int = 400,
    now: Optional[datetime] = None,
) -> str:
    """Create a Markdown session report without modifying RDF memory."""
    now = now or datetime.now()
    child_name = str(context.get("child_name", "") or "unknown").strip() or "unknown"
    activity_type = str(context.get("activity_type", "generic_child_activity"))
    filename = (
        f"{now.strftime('%Y%m%d_%H%M%S')}_"
        f"{_safe_filename_part(child_name)}_{_safe_filename_part(activity_type)}_"
        "memory_report.md"
    )
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / filename

    summary = _deterministic_session_summary(transcript)
    llm_notes = ""
    if llm_client is not None and transcript:
        llm_notes = _try_generate_report_notes(
            llm_client=llm_client,
            context=context,
            transcript=transcript,
            memory_context=memory_context,
            temperature=temperature,
            max_response_tokens=max_response_tokens,
        )
        if llm_notes:
            summary = llm_notes

    transcript_text = _compact_transcript(transcript)
    candidate_updates = _candidate_updates(transcript, memory_context)
    body = f"""# Child Memory Session Report

Child name: {child_name}
Activity type: {activity_type}
Task: {context.get("task", "")}
Activity description: {context.get("activity_description", "")}
Date/time: {now.isoformat(timespec="seconds")}
Number of turns: {turns}
Completion reason: {completion_reason}

## Memory used at session start
{format_memory_for_report(memory_context)}

## Session summary
{summary}

## Story/content observations
- Review the transcript for themes the child appeared to engage with.
- Review the transcript for characters mentioned.
- Review the transcript for settings mentioned.
- Review the transcript for story ending preferences if observed.

## Emotion-related observations
- Review the transcript for emotions mentioned by the child.
- Review the transcript for emotions recognized correctly if applicable.
- Review the transcript for emotions that seemed difficult if applicable.

## Emotion recognition observations
- Review whether specific stimuli or image types seemed useful.
- Review which emotions may need future support.

## Candidate memory updates for future therapist validation
These are suggestions only. They were NOT written to the RDF memory.
{candidate_updates}

## Compact transcript
{transcript_text}
"""
    report_path.write_text(body, encoding="utf-8")
    return str(report_path)


def _try_generate_report_notes(
    *,
    llm_client: Any,
    context: Mapping[str, Any],
    transcript: List[Mapping[str, str]],
    memory_context: Mapping[str, Any],
    temperature: float,
    max_response_tokens: int,
) -> str:
    system = (
        "You write concise clinical support notes for a therapist after a child-robot "
        "activity. Do not claim that suggestions were stored. Mention only cautious "
        "candidate observations that need therapist validation."
    )
    user = (
        "Create a concise session summary and candidate memory observations.\n\n"
        f"Context: {dict(context)}\n\n"
        f"Memory used: {format_memory_for_report(memory_context)}\n\n"
        f"Transcript:\n{_compact_transcript(transcript, max_items=18)}"
    )
    try:
        return llm_client.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_response_tokens=max_response_tokens,
        ).strip()
    except Exception:
        return ""


def _deterministic_session_summary(transcript: List[Mapping[str, str]]) -> str:
    if not transcript:
        return "No child activity transcript was captured."
    child_turns = sum(1 for item in transcript if item.get("role") == "child")
    assistant_turns = sum(1 for item in transcript if item.get("role") == "assistant")
    return (
        f"Captured {child_turns} child turn(s) and {assistant_turns} robot turn(s). "
        "No automatic RDF memory update was performed."
    )


def _compact_transcript(
    transcript: List[Mapping[str, str]],
    max_items: int = 24,
    max_chars: int = 240,
) -> str:
    if not transcript:
        return "- No transcript entries captured."
    lines = []
    for item in transcript[-max_items:]:
        role = item.get("role", "unknown")
        content = re.sub(r"\s+", " ", item.get("content", "")).strip()
        if len(content) > max_chars:
            content = content[: max_chars - 3].rstrip() + "..."
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)


def _candidate_updates(
    transcript: List[Mapping[str, str]],
    memory_context: Mapping[str, Any],
) -> str:
    if not transcript:
        return "- No candidate updates; no transcript was captured."
    if memory_context and not memory_context.get("found", False):
        return (
            "- Therapist may validate whether this child should be added to the RDF "
            "memory in a future workflow."
        )
    return (
        "- Therapist may validate any repeated themes, characters, settings, emotion "
        "topics, or emotion-recognition stimuli observed in the transcript."
    )
