# RDF Child Memory

`empower_hri` includes a first persistent child-memory step based on RDFLib and
SPARQL. The default Turtle file is:

```yaml
memory_rdf_path: "/root/exchange/empower_data/hri_memory/children_memory.ttl"
```

If the file is missing and `memory_seed_if_missing: true`, the node seeds two
example children, Luca and Sofia, with story, emotion, and emotion-recognition
preferences.

The therapist now provides physical task, child activity description, and child
name. The mission orchestrator sends `child_name` in the
`start_child_activity` context, and `voice_llm_node` uses it for a
case-insensitive RDF lookup.

The retrieved memory personalizes story content, characters, settings, endings,
emotion vocabulary, and emotion-recognition scaffolding. The robot should not
say that it is reading from a memory database.

Reports are written to:

```yaml
memory_report_dir: "/root/exchange/empower_data/hri_memory/reports"
```

This step does not implement therapist validation and does not automatically
write learned facts back to the RDF graph. Candidate updates are report-only.
