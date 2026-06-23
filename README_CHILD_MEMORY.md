# RDF Child Memory

This repo now includes a first persistent child-memory step for the HRI child
activity flow. The memory is stored as RDF Turtle and queried with SPARQL through
`llm_interaction.rdf_child_memory.RdfChildMemoryStore`.

## Mission Flow

The therapist now provides three inputs before the robot starts the physical
mission and child activity:

1. physical task;
2. child activity description;
3. child name.

The mission orchestrator publishes the child activity command with:

```json
{
  "command": "start_child_activity",
  "context": {
    "child_name": "Luca",
    "task": "...",
    "activity_description": "...",
    "activity_type": "storytelling"
  }
}
```

## RDF File

The default Turtle file is configured in
`src/llm_interaction/config/llm_config.yaml`:

```yaml
memory_rdf_path: "/root/exchange/output/interaction_output/children_memory.ttl"
```

If the file is missing and `memory_seed_if_missing: true`, the HRI node creates
an initial graph automatically. The seed contains two example children:

- Luca: dinosaurs, space, robots, friendly robot, small dinosaur, moon, jungle,
  happy ending, happy/curious/brave emotion topics, and storytelling.
- Sofia: animals, magic forest, teddy bear adventures, teddy bear, cat, fairy,
  enchanted world/bedroom/garden settings, calm ending, calm/proud/kind emotion
  topics, and mood check with teddy.

## Prompt Personalization

When `memory_enabled: true`, `voice_llm_node` looks up the child by
case-insensitive `hri:displayName`. The retrieved facts are added to the child
activity system prompt as compact personalization context.

The memory is used only for content personalization: story themes, characters,
settings, endings, emotion vocabulary, and emotion-recognition scaffolding. The
robot must not say that it is reading from a memory database. If the therapist's
current instruction conflicts with stored memory, the current therapist
instruction wins.

If the child name is missing, the RDF file is unavailable, or the child is not
found, the system continues with the generic child activity behavior.

## Reports

At the end of a child activity with memory enabled, the node writes a Markdown
report to:

```yaml
memory_report_dir: "/root/exchange/output/interaction_output/reports"
```

Report filenames follow:

```text
YYYYMMDD_HHMMSS_<child_name>_<activity_type>_memory_report.md
```

The report includes the memory used at session start, a compact transcript
summary, observations to review, and candidate memory updates for future
therapist validation.

## No Automatic Memory Updates Yet

This step does not implement therapist validation and does not write learned
facts back to the RDF graph. `memory_update_enabled` is present as a future
configuration flag but should remain `false`; candidate updates are report-only.

Future work can add a therapist validation workflow that reviews candidate facts
and only then writes approved updates into the Turtle memory.
