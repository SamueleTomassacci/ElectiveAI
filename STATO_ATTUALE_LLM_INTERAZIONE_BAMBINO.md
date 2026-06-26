# Stato attuale LLM e interazione con il bambino

Questo documento e' pensato per essere passato a un assistente AI come contesto
tecnico della repo `ElectiveAI`, con focus specifico sulla parte LLM/HRI e
sull'interazione robot-bambino. Non propone modifiche architetturali: descrive
solo lo stato attuale del codice, i flussi implementati, i topic ROS 2, le
configurazioni e i limiti gia' presenti.

## Scopo del blocco HRI/LLM

La repo simula TIAGo in Gazebo Classic con tre blocchi principali:

1. EMPOWER (`empower`): percezione, planning simbolico, navigazione e
   simulazione di alcune azioni sugli oggetti.
2. HRI/LLM (`llm_interaction`): raccolta input da terapista/bambino, chiamata a
   un LLM locale via Ollama, output vocale via TTS e coordinamento del dialogo.
3. Gaze/emotion (`gaze_estimation`): stima attenzione visiva del bambino verso
   target dell'attivita' e riconoscimento/smoothing dell'emozione del bambino.

Il blocco HRI/LLM ha due ruoli distinti:

- raccogliere dalla terapista il nome del bambino (primo input), e se non
  c'e' storico sessione, raccogliere anche il task fisico e l'attivita';
  se c'e' storico, il robot propone automaticamente l'attivita' con il
  miglior punteggio medio e la terapista puo' confermare o sovrascrivere;
- gestire una sessione conversazionale multi-turn con il bambino dopo che la
  missione fisica ha portato TIAGo nella fase di child activity, includendo
  il monitoraggio del disengagement secondario, il graceful stop da
  terapista e il calcolo/persistenza dei punteggi di sessione.

Il package centrale per questa parte e':

```text
src/llm_interaction
```

Il nodo principale e':

```text
src/llm_interaction/llm_interaction/voice_llm_node.py
```

## Runtime previsto

Il flusso LLM locale usa Ollama sull'host e ROS 2 dentro il container Docker.
Il README dedicato e':

```text
README_QWEN_ENGLISH.md
```

Flusso vocale inglese:

```text
microfono -> faster-whisper -> gemma3:12b via Ollama -> Piper TTS -> speaker
```

Modello LLM configurato di default:

```text
gemma3:12b
```

Fallback configurato:

```text
llama3.2:1b
```

Comando host per Ollama:

```bash
ollama serve
ollama pull gemma3:12b
```

Comando tipico per la missione completa HRI + EMPOWER:

```bash
ros2 launch empower hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/llm_interaction/config/llm_config.yaml \
  hri_input_mode:=voice
```

Non ci sono piu' launch file nella directory `src/llm_interaction/launch/`; il
nodo HRI viene avviato solo dal launch integrato di EMPOWER.

## Package `llm_interaction`

Struttura file corrente:

```text
src/llm_interaction/
  llm_interaction/
    __init__.py
    ollama_client.py
    rdf_child_memory.py
    session_scores.py            (NUOVO)
    speech_to_text.py
    terminal_io.py
    text_to_speech.py
    voice_llm_node.py
  config/
    llm_config.yaml
    hri_cpu_local.yaml
  launch/                      (vuoto)
  test/                        (vuoto)
  setup.py
  setup.cfg
  package.xml
```

Entry point ROS 2:

```text
voice_llm_node = llm_interaction.voice_llm_node:main
```

Il package e' `ament_python` e viene installato da
`src/llm_interaction/setup.py`.

## Nodo `voice_llm_node`

`VoiceLlmNode` coordina input, LLM, TTS, memoria bambino, punteggi sessione,
disengagement secondario, graceful stop e topic ROS. Ha un thread worker
interno (`voice_llm_interaction`) che esegue il loop interattivo mentre il
main thread ROS 2 continua a fare `rclpy.spin`. In modalita' voice, un
ulteriore thread monitor tastiera (basato su `select.select` su stdin) ascolta
il tasto di graceful stop.

Il nodo supporta due modalita' logiche:

```text
therapist_command
child_activity
```

Costanti nel codice:

```python
_MODE_THERAPIST = "therapist_command"
_MODE_CHILD_ACTIVITY = "child_activity"
```

### Modalita' therapist_command

Questa e' la modalita' iniziale. Serve a leggere input dalla terapista.
In missione completa, il launch imposta:

```yaml
therapist_llm_replies: false
follow_mission_state_for_therapist_input: true
```

Quindi, nel flusso orchestrato, il nodo HRI non usa il LLM per rispondere alla
terapista: pubblica semplicemente il testo riconosciuto su
`/hri/user_utterance`. L'orchestratore decide se quel testo e' il task fisico,
la descrizione dell'attivita' sociale o il nome del bambino in base allo stato
missione corrente.

Se `therapist_llm_replies` e' true, invece, il nodo fa un normale turno
STT/text -> LLM -> TTS anche in modalita' terapista.

### Modalita' child_activity

Questa modalita' parte quando il nodo riceve su `/hri/dialog_command` un JSON
con:

```json
{"command": "start_child_activity", "context": {...}}
```

Il contesto contiene:

```json
{
  "child_name": "Luca",
  "task": "...",
  "activity_description": "...",
  "activity_type": "storytelling|mood_check_teddy|computer_emotion_recognition|generic_child_activity",
  "child_memory": { ... }
}
```

Quando parte la child activity:

- il nodo valida/normalizza `activity_type`;
- chiama `attach_child_memory_context()` che interroga la memoria RDF per il
  bambino e inietta il risultato in `context["child_memory"]`;
- salva il contesto in `_child_activity_context`;
- svuota la history conversazionale corrente;
- inizializza `_session_transcript` (lista vuota);
- cancella eventuali alert attenzione pendenti;
- passa a `_MODE_CHILD_ACTIVITY`.

All'inizio della sessione il robot parla senza aspettare input dal bambino,
usando un'apertura personalizzata col nome del bambino e dipendente dal tipo di
attivita'. Poi entra in un loop multi-turn: ascolta/legge il bambino, chiama il
LLM, pronuncia la risposta, gestisce eventuale richiamo attenzione, incrementa
il contatore turni. Ogni utterance (child e assistant) viene aggiunta a
`_session_transcript`.

La sessione termina quando:

- arriva `/quit`, `/exit`, `quit` o `exit`;
- arriva comando `{"command": "stop"}` su `/hri/dialog_command`;
- si raggiunge `storytelling_max_turns`;
- il bambino chiede di fermarsi dopo una domanda di disengagement
  (reason = `child_requested_stop`);
- la terapista preme il tasto di graceful stop (`q` di default, configurabile
  via `graceful_stop_key`) o invia `{"command": "graceful_stop"}` su
  `/hri/dialog_command` (reason = `graceful_stop_by_therapist`);
- il nodo viene fermato o ROS 2 termina.

Nel caso di graceful stop: dopo che lo scambio corrente si completa, il robot
pronuncia una frase di saluto generata dall'LLM (con fallback hardcoded),
poi termina la sessione. In modalita' voice il tasto viene rilevato da un
thread dedicato che monitora stdin con `select.select`. In modalita' text,
digitare `q` come input attiva il graceful stop.

Alla fine:

1. Calcola i punteggi sessione tramite `compute_session_scores()`.
2. Salva i punteggi tramite `save_session_score()` in
   `output/interaction_output/session_scores.json`.
3. Genera un memory report Markdown (vedi sezione dedicata), che ora include
   una sezione "Session scores".
4. Pubblica `/hri/dialog_complete` con un payload JSON arricchito:

```json
{
  "reason": "max_turns|stopped|child_requested_stop|graceful_stop_by_therapist",
  "turns": 10,
  "activity_type": "storytelling",
  "child_name": "Luca",
  "memory_report_path": "/root/exchange/output/interaction_output/reports/20260625_143012_luca_storytelling_memory_report.md",
  "scores": {
    "attention": 75,
    "emotion": 80,
    "participation": 85,
    "overall": 80
  }
}
```

## Sistema di memoria bambino (RDF + SPARQL)

### Modulo `rdf_child_memory.py`

File:

```text
src/llm_interaction/llm_interaction/rdf_child_memory.py
```

Questo modulo implementa un sistema di memoria per personalizzare l'attivita'
con il bambino. La memoria viene salvata in formato Turtle (.ttl) usando
`rdflib`. Non c'e' un database esterno: il file .ttl viene letto e scritto
direttamente dal filesystem.

### Ontologia HRI

Namespace custom:

```text
HRI  = http://example.org/hri#
CHILD = http://example.org/child/
SESSION = http://example.org/session/
CONCEPT = http://example.org/concept/
```

Predicati definiti:

```text
hri:likesStoryTheme
hri:likesStoryCharacter
hri:likesStorySetting
hri:prefersStoryEnding
hri:likesEmotionTopic
hri:recognizesEmotionWell
hri:needsSupportWithEmotion
hri:likesEmotionRecognitionStimulus
hri:prefersActivityType
```

Classi RDF: `hri:Child`, `hri:StoryTheme`, `hri:StoryCharacter`,
`hri:StorySetting`, `hri:StoryEnding`, `hri:Emotion`,
`hri:EmotionRecognitionStimulus`, `hri:ActivityType`, `hri:Preference`.

### Classe `RdfChildMemoryStore`

La classe gestisce il ciclo di vita del file .ttl:

- Al primo avvio, se il file non esiste e `memory_seed_if_missing` e' true,
  crea il grafo con dati di default per due bambini (Luca e Sofia).
- Se il file esiste, lo carica con `graph.parse()`.
- L'accesso ai dati avviene tramite SPARQL: `get_child_memory_context()` esegue
  una query SPARQL case-insensitive sul `hri:displayName` del bambino.
- Il risultato viene organizzato in bucket: `story_preferences`,
  `emotion_preferences`, `emotion_recognition` (con sotto-chiavi
  `recognizes_well`, `needs_support_with`, `preferred_stimuli`),
  `preferred_activity_types`.
- `build_prompt_text()` converte i fatti in testo leggibile per il system prompt
  LLM, rispettando il limite `memory_max_prompt_facts`.

### Dati seed di default

Luca:

```text
story_themes: dinosaurs, space, robots
story_characters: friendly robot, small dinosaur
story_settings: moon, jungle
story_endings: happy ending
emotion_topics: happy, curious, brave
recognizes_well: happy, sad
needs_support: scared, surprised
recognition_stimuli: clear face pictures, cartoon faces
activity_types: storytelling
```

Sofia:

```text
story_themes: animals, magic forest, teddy bear adventures
story_characters: teddy bear, cat, fairy
story_settings: enchanted world, bedroom, garden
story_endings: calm ending
emotion_topics: calm, proud, kind
recognizes_well: happy, angry
needs_support: scared, disgusted
recognition_stimuli: animal characters, illustrated faces
activity_types: mood_check_teddy
```

### Iniezione della memoria nel prompt LLM

La funzione `child_memory_prompt_section(context)` estrae il campo
`context["child_memory"]["prompt_text"]` e lo formatta come sezione opzionale
aggiunta alla fine del system prompt dell'attivita':

```text
\n\nChild personalization memory:
The child is Luca.

Known story preferences:
- dinosaurs
- space
- robots
- friendly robot
- small dinosaur
- moon
- jungle
- happy ending

Known emotion-related preferences:
- likes talking about happy, curious, brave
- recognizes happy and sad well
- may need support with scared and surprised
- likes clear face pictures and cartoon faces for emotion recognition

For storytelling, prefer these themes, characters, settings, and endings when they fit the therapist's instruction.
Use this memory only to personalize story content, examples, characters, emotion vocabulary, and emotion-recognition scaffolding.
Do not say that you are reading from a memory database.
Do not reveal private notes.
If the therapist's current activity instruction conflicts with memory, follow the therapist's current instruction.
```

Ogni tipo di attivita' ha una riga di guida specifica (storytelling, mood_check,
emotion_recognition, generic).

### Flusso di attivazione della memoria

1. L'orchestratore invia `start_child_activity` con `child_name` nel contesto.
2. `start_child_activity()` chiama `attach_child_memory_context()`.
3. `attach_child_memory_context()` fa lazy init di `RdfChildMemoryStore` tramite
   `get_memory_store()`.
4. Esegue `memory_store.get_child_memory_context()` con SPARQL.
5. Aggiunge il risultato al contesto come `context["child_memory"]`.
6. `build_child_activity_system_prompt()` usa `child_memory_prompt_section()` per
   accodare la sezione memoria al system prompt.

Se il bambino non e' trovato nel file .ttl, il prompt contiene solo
"No stored memory found for this child." e l'attivita' procede senza
personalizzazione.

### Configurazione memoria

Parametri ROS nel nodo HRI:

```yaml
memory_enabled: true
memory_rdf_path: "/root/exchange/output/interaction_output/children_memory.ttl"
memory_report_dir: "/root/exchange/output/interaction_output/reports"
memory_max_prompt_facts: 12
memory_seed_if_missing: true
memory_update_enabled: false
```

`memory_update_enabled` e' false: il file .ttl non viene mai modificato
automaticamente dopo il seeding iniziale. Le osservazioni sulla sessione vengono
scritte solo nei report Markdown come suggerimenti.

## Report di sessione (Memory Report)

### Generazione

Alla fine di ogni child activity, `run_child_activity_session()` chiama
`generate_memory_report()` che a sua volta chiama
`generate_child_memory_report()` da `rdf_child_memory.py`.

Il report viene salvato come file Markdown in `memory_report_dir` con nome:

```text
YYYYMMDD_HHMMSS_{child_name}_{activity_type}_memory_report.md
```

### Contenuto del report

Il report contiene queste sezioni:

1. **Metadati sessione**: child name, activity type, task, activity description,
   data/ora, numero turni, motivo completamento.
2. **Memory used at session start**: il testo del prompt RDF usato all'inizio
   della sessione (o messaggio che la memoria non era disponibile).
3. **Session summary**: generato dal LLM via `try_generate_report_notes()` se
   possibile (usa il client LLM principale con temperatura 0.2 e max 400 token);
   altrimenti fallback deterministico che conta i turni child/assistant.
4. **Session scores**: punteggi numerici della sessione (attention, emotion,
   participation, overall), ciascuno 0-100.
5. **Story/content observations**: sezioni placeholder per la revisione del
   terapista (temi, personaggi, ambientazioni, preferenze di finale).
6. **Emotion-related observations**: sezioni placeholder (emozioni menzionate,
   riconosciute correttamente, difficili).
7. **Emotion recognition observations**: sezioni placeholder (stimoli utili,
   emozioni che necessitano supporto futuro).
8. **Candidate memory updates**: suggerimenti per il terapista, NON scritti
   automaticamente nel file RDF. Se il bambino non e' nel database, suggerisce
   di aggiungerlo. Se e' presente, suggerisce di validare temi/personaggi/emozioni
   osservati nel transcript.
9. **Compact transcript**: ultimi 24 turni, max 240 caratteri ciascuno, in
   formato `- role: content`.

### Session transcript

L'attributo `_session_transcript: List[Dict[str, str]]` raccoglie tutte le
utterance durante la child activity:

- Ogni utterance del bambino viene aggiunta con `role: "child"`.
- Ogni risposta del robot viene aggiunta con `role: "assistant"`.
- Anche le frasi di richiamo attenzione vengono aggiunte con
  `role: "assistant"` (tramite `handle_attention_recall()`).
- L'apertura dell'attivita' viene aggiunta come primo turno assistant.
- Il transcript viene svuotato all'inizio di ogni nuova child activity.
- Il transcript viene passato a `generate_child_memory_report()` per la
  generazione del report e del summary LLM.

### Limitazioni attuali del report

- Le sezioni di osservazione sono placeholder testuali per review umana.
- I candidate memory updates sono solo suggerimenti, non vengono applicati.
- Il summary LLM puo' fallire e in quel caso si usa il fallback deterministico.
- Il transcript e' troncato a 24 turni nel report.

## Sistema di punteggi sessione (session_scores)

### Modulo

File:

```text
src/llm_interaction/llm_interaction/session_scores.py
```

### Punteggi calcolati

A fine di ogni child activity, `compute_session_scores()` calcola 4 punteggi
normalizzati 0-100:

- **attention** (0-100): derivato dal numero di alert di attenzione
  accumulati durante la sessione (`_session_attention_alerts`). Meno alert
  significano attenzione migliore.
- **emotion** (0-100): derivato dai campioni emozionali raccolti durante
  la sessione (`_session_emotion_samples`). Un ratio maggiore di emozioni
  positive produce un punteggio piu' alto.
- **participation** (0-100): derivato dalla partecipazione attiva del
  bambino nel dialogo.
- **overall** (0-100): media pesata dei tre punteggi precedenti.

### Persistenza

I punteggi vengono salvati in:

```text
output/interaction_output/session_scores.json
```

Il path e' configurabile via `session_scores_path` sia in `task_config.yaml`
sia in `llm_config.yaml`. Il file JSON e' organizzato per bambino e per
sessione.

### Query storico

La funzione `best_activity_for_child(child_name)` interroga il file JSON e
restituisce il tipo di attivita' con il miglior punteggio `overall` medio
per quel bambino. Viene usata dall'orchestratore nello stato
`PROPOSE_ACTIVITY` per proporre automaticamente l'attivita' migliore.

### Mapping ACTIVITY_TO_TASK

```python
ACTIVITY_TO_TASK = {
    "storytelling": "book",
    "mood_check_teddy": "teddy bear",
    "computer_emotion_recognition": "computer",
}
```

Questo mapping converte il tipo di attivita' nel task fisico EMPOWER
corrispondente, usato dall'orchestratore quando la proposta automatica
viene accettata.

### Accumulo dati nel voice_llm_node

Il `voice_llm_node` mantiene nuovi attributi per il calcolo dei punteggi:

- `_session_start_time`: timestamp di inizio sessione.
- `_session_attention_alerts`: contatore alert di attenzione accumulati.
- `_session_emotion_samples`: lista di campioni emozionali raccolti.

I callback esistenti sono stati estesi:

- `attention_alert_callback`: oltre al richiamo breve, incrementa anche
  `_session_attention_alerts` e `_disengagement_alert_count`.
- `emotion_status_callback`: oltre ad aggiornare l'emozione corrente,
  appende anche a `_session_emotion_samples`.

A fine sessione, il nodo chiama `compute_session_scores()` e
`save_session_score()` prima di pubblicare `/hri/dialog_complete`.
Il payload di `/hri/dialog_complete` include un campo `scores` con i
4 punteggi. Il report Markdown include una sezione "Session scores".

## Disengagement secondario

### Differenza dal richiamo attenzione breve

Il richiamo attenzione breve (esistente) agisce a singolo turno: quando il
bambino non guarda il target per piu' di 2.5 secondi, il robot genera una
frase di richiamo. Questo meccanismo e' rimasto invariato.

Il disengagement secondario (nuovo) e' una soglia accumulativa sull'intera
sessione: opera su contatori che crescono nel tempo e valuta se il bambino
e' complessivamente disengaged dalla sessione.

### Logica di check_disengagement()

Dopo ogni turno, `check_disengagement()` nel `voice_llm_node` valuta:

1. **Alert count**: `_disengagement_alert_count >=
   disengagement_alert_threshold` (default 4).
2. **OPPURE negative emotion ratio**: la proporzione di campioni emozionali
   negativi nella finestra `disengagement_check_window_sec` (default 120s)
   e' >= `disengagement_negative_emotion_ratio` (default 0.6).

Se una delle condizioni e' vera:

- Il robot chiede al bambino "Would you prefer to stop?" tramite una frase
  LLM generata (con fallback hardcoded).
- Se il bambino risponde che vuole fermarsi: la sessione termina con
  reason = `"child_requested_stop"`.
- Se il bambino vuole continuare: i contatori vengono resettati e viene
  applicato un cooldown di `disengagement_cooldown_sec` (default 180s)
  prima di poter chiedere di nuovo.

Limite massimo di richieste per sessione: `disengagement_max_asks`
(default 2). Dopo il limite, non vengono piu' fatte domande di
disengagement.

### Parametri di configurazione (in llm_config.yaml)

```yaml
disengagement_alert_threshold: 4
disengagement_negative_emotion_ratio: 0.6
disengagement_check_window_sec: 120.0
disengagement_cooldown_sec: 180.0
disengagement_max_asks: 2
```

## Graceful stop

### Attivazione

La terapista puo' fermare la sessione bambino in modo controllato:

- **Modalita' voice**: un thread monitor tastiera (basato su `select.select`
  su stdin) rileva la pressione del tasto `graceful_stop_key` (default `q`,
  configurabile in `llm_config.yaml`).
- **Modalita' text**: digitare `q` come input attiva il graceful stop.
- **Via topic**: pubblicare `{"command": "graceful_stop"}` su
  `/hri/dialog_command`.

### Comportamento

Dopo lo scambio corrente:

1. Il robot genera e pronuncia una frase di saluto (farewell) tramite LLM.
   Se l'LLM non risponde in tempo, usa un fallback hardcoded.
2. I punteggi sessione vengono calcolati e salvati.
3. Il report viene generato.
4. La sessione termina con reason = `"graceful_stop_by_therapist"`.

### Configurazione

```yaml
graceful_stop_key: "q"
```

## Flusso child-first e proposta attivita'

### Ordine degli stati missione (aggiornato)

Il nome del bambino e' ora il PRIMO input richiesto. La sequenza completa e':

```text
GO_TO_THERAPIST -> WAIT_CHILD_NAME -> ACK_CHILD_NAME -> PROPOSE_ACTIVITY ->
WAIT_THERAPIST_OVERRIDE -> ACK_PROPOSAL -> GO_TO_SPAWN -> RUN_EMPOWER ->
GO_TO_CHILD -> CHILD_READY -> CHILD_ACTIVITY -> GO_BACK_TO_THERAPIST ->
MISSION_COMPLETE
```

Se non c'e' storico sessione per il bambino, dopo ACK_CHILD_NAME il flusso
passa per WAIT_TASK -> ACK_TASK -> WAIT_ACTIVITY -> ACK_ACTIVITY prima di
GO_TO_SPAWN (fallback al flusso manuale).

### MissionInteractionSpec

Il dataclass `MissionInteractionSpec` ora ha il campo:

```python
@dataclass
class MissionInteractionSpec:
    task_description: str = ""
    activity_description: str = ""
    activity_type: str = "generic_child_activity"
    child_name: str = ""
```

### Sequenza dettagliata (caso con storico)

1. L'orchestratore naviga verso la terapista e pronuncia il testo
   `child_name_ask_text` (es. "What is the child's name?").
2. Transita a `WAIT_CHILD_NAME`.
3. Il nodo HRI legge il nome del bambino (STT o text) e lo pubblica su
   `/hri/user_utterance`.
4. `capture_child_name()` nell'orchestratore salva il nome in
   `_interaction_spec.child_name`.
5. Transita a `ACK_CHILD_NAME`: conferma il nome ricevuto.
6. `PROPOSE_ACTIVITY`: l'orchestratore chiama
   `best_activity_for_child(child_name)` dal modulo `session_scores`.
   Se trova uno storico, propone l'attivita' migliore usando il template
   `proposal_text` (es. "Based on past sessions, I suggest storytelling.
   Say something to override, or wait to accept."). Il task EMPOWER viene
   derivato dal mapping `ACTIVITY_TO_TASK`.
7. `WAIT_THERAPIST_OVERRIDE`: attende `proposal_acceptance_timeout_sec`
   secondi. Se la terapista parla prima del timeout, l'input sovrascrive
   la proposta. Se il timeout scade, la proposta viene accettata.
8. `ACK_PROPOSAL`: conferma l'attivita' scelta.
9. Procede a `GO_TO_SPAWN`.

### Sequenza dettagliata (caso senza storico)

1-5. Come sopra.
6. L'orchestratore pronuncia `no_history_text` (es. "I don't have session
   history for this child. Please tell me the task.").
7. Transita a `WAIT_TASK`: aspetta il task fisico.
8. `ACK_TASK`.
9. `WAIT_ACTIVITY`: aspetta la descrizione dell'attivita'.
10. `ACK_ACTIVITY`.
11. Procede a `GO_TO_SPAWN`.

### Dopo CHILD_ACTIVITY: ritorno dalla terapista

Dopo la fine dell'attivita' bambino, il robot transita a
`GO_BACK_TO_THERAPIST`: naviga verso la terapista e pronuncia un riepilogo
dell'attivita' che include i punteggi sessione. Poi transita a
`MISSION_COMPLETE`.

Il nome viene incluso nel contesto passato a `start_child_activity` e usato
per:

- personalizzare l'apertura dell'attivita' (prefix: "Hi Luca, ...");
- interrogare la memoria RDF;
- includerlo nel payload di `/hri/dialog_complete`;
- interrogare lo storico punteggi per la proposta attivita'.

### `can_read_therapist_input()`

Ora riconosce anche `WAIT_CHILD_NAME` e `WAIT_THERAPIST_OVERRIDE` come
stati validi per leggere input:

```python
if self._mission_state not in {"WAIT_CHILD_NAME", "WAIT_TASK", "WAIT_ACTIVITY", "WAIT_THERAPIST_OVERRIDE"}:
    return False
```

### Configurazione orchestratore (nuovi parametri)

```yaml
wait_for_child_name_timeout_sec: 300.0
proposal_acceptance_timeout_sec: 15.0
child_name_ask_text: "What is the child's name?"
no_history_text: "I don't have session history for this child. Please tell me the task."
proposal_text: "Based on past sessions, I suggest {activity}. Say something to override, or wait to accept."
session_scores_path: "/root/exchange/output/interaction_output/session_scores.json"
child_name_ack_text: "Okay, I will personalize the activity for {child_name}."
```

## Regole di stile per risposte child activity

La costante `_CHILD_ACTIVITY_STYLE_RULES` viene accodata a TUTTI i system prompt
delle child activity (storytelling, mood_check_teddy, computer_emotion_recognition,
generic):

```python
_CHILD_ACTIVITY_STYLE_RULES = (
    "Use exactly two short sentences for each child-facing response. "
    "Sentence 1: contribute one small new piece of the activity. "
    "Sentence 2: ask one simple question or invitation for the child. "
    "Do not answer with only a question. "
    "Do not add lists, headings, explanations, or extra options. "
    "Always finish with a complete sentence. "
    "Never stop in the middle of a sentence. "
)
```

Questo vincola il LLM a produrre risposte brevi e strutturate: un contributo
concreto all'attivita' piu' una domanda/invito per il bambino.

## Input: testo, push-to-talk e auto-listening

Il parametro `input_mode` puo' essere:

```text
text
voice
```

In modalita' `text`, il nodo legge dal terminale con `read_terminal`.

In modalita' `voice`, usa `FasterWhisperSpeechToText`:

- registra audio con `sounddevice`;
- scrive un file WAV temporaneo;
- trascrive con `faster_whisper.WhisperModel`;
- usa CPU obbligatoriamente (`stt_device` deve essere `cpu`);
- supporta `transcribe_push_to_talk()`;
- supporta `transcribe_auto()` con VAD energetico semplice.

Durante la child activity, se:

```yaml
input_mode: "voice"
storytelling_auto_listen: true
```

e lo STT espone `transcribe_auto`, il nodo usa auto-listening invece di
push-to-talk. I parametri VAD sono:

```yaml
stt_vad_energy_threshold: 0.01
stt_vad_silence_sec: 1.5
stt_vad_max_duration_sec: 30.0
```

## LLM: client Ollama

Il file:

```text
src/llm_interaction/llm_interaction/ollama_client.py
```

contiene `OllamaClient`, un client HTTP minimale senza dipendenze esterne.
Usa:

```text
GET  /api/tags
POST /api/chat
```

La generazione e' non-streaming:

```json
{
  "model": "...",
  "messages": [...],
  "stream": false,
  "options": {
    "temperature": 0.4,
    "num_predict": 0
  }
}
```

Con `max_response_tokens: 0`, il parametro `num_predict` viene passato come 0
ad Ollama, il che significa nessun limite esplicito sulla lunghezza della
risposta. Il LLM genera fino al suo contesto massimo o al token di stop. Il
formato di risposta viene controllato solo dalle `_CHILD_ACTIVITY_STYLE_RULES`
nel prompt.

Prima della chat, `_select_model()` controlla se il modello configurato e'
presente tra quelli di Ollama. Se non c'e' e il fallback e' presente, usa il
fallback. Se nessuno dei due e' presente, solleva `LlmError`.

Esiste anche `MockLlmClient`, usato nei test e utile quando non si vuole
dipendere da Ollama.

## TTS

Il file:

```text
src/llm_interaction/llm_interaction/text_to_speech.py
```

implementa `TextToSpeech` con provider:

```text
piper
pyttsx3
system
mock
```

Fallback attuali:

- `piper` -> `pyttsx3` -> `system` -> `mock`;
- `pyttsx3` -> `system` -> `mock`;
- `system` -> `mock`;
- `mock` stampa a terminale.

Nel profilo corrente viene usato:

```yaml
tts_provider: "piper"
tts_timeout: 60.0
piper_model_path: "/root/exchange/models/piper/en_GB-jenny_dioco-medium.onnx"
piper_config_path: "/root/exchange/models/piper/en_GB-jenny_dioco-medium.onnx.json"
```

Piper usa modelli ONNX locali. Il modello configurato e' la voce inglese
britannica Jenny Dioco (qualita' media).

Il codice misura e logga la durata reale della riproduzione:

```text
TTS playback took X.Xs (backend='piper')
```

Questo e' rilevante per l'attenzione: l'attention monitor si arma durante lo
stato `speaking`, quindi se il TTS finisce immediatamente o non produce audio
reale, la finestra di monitoraggio e' troppo breve.

## Prompt e comportamento LLM

### Prompt generale

Il prompt generale di default dice che TIAGo e' un social robot in simulazione
Gazebo, in questa fase puo' solo parlare, non puo' muoversi o afferrare oggetti
dal modulo HRI, e deve rispondere in inglese con tono breve, chiaro, gentile e
adatto a bambini/terapisti.

Nel flusso missione completa, questo prompt e' meno usato verso la terapista
perche' `therapist_llm_replies` e' false.

### Prompt per child activity

I prompt per attivita' sono costruiti in:

```python
build_child_activity_system_prompt(activity_type, context)
```

Tipi supportati:

```text
computer_emotion_recognition
mood_check_teddy
storytelling
generic_child_activity
```

Ogni prompt di attivita' e' composto da tre parti concatenate:

1. **Istruzione specifica del tipo di attivita'** (tono, ruolo, contenuto).
2. **`_CHILD_ACTIVITY_STYLE_RULES`** (formato due frasi, niente liste, ecc.).
3. **`{memory_section}`** opzionale (sezione "Child personalization memory"
   con i dati RDF del bambino, se disponibili).

`computer_emotion_recognition`:

- TIAGo aiuta il bambino a riconoscere emozioni da immagini su computer;
- frase 1: un piccolo hint visivo, un praise, o un'osservazione sul volto;
- frase 2: chiede al bambino di nominare l'emozione o scegliere tra massimo due.

`mood_check_teddy`:

- TIAGo usa il teddy bear come intermediario sicuro;
- frase 1: il teddy bear fa un commento gentile su un sentimento;
- frase 2: chiede al bambino come si sente lui o il teddy bear.

`storytelling`:

- TIAGo immagina di leggere da un libro;
- frase 1: continua la storia con un piccolo evento nuovo;
- frase 2: fa una domanda sulla storia (cosa succede dopo, come si sente un
  personaggio, se continuare).

`generic_child_activity`:

- usa `activity_description` data dalla terapista;
- frase 1: un piccolo passo utile nell'attivita';
- frase 2: una domanda o invito semplice.

### Aperture personalizzate con nome bambino

La funzione:

```python
child_activity_opening(activity_type, context)
```

definisce la prima frase pronunciata prima di ascoltare il bambino. Se
`child_name` e' presente nel contesto, l'apertura inizia con "Hi {name}, ..."
e le lettere maiuscole iniziali si adattano di conseguenza.

Esempi attuali con nome:

- storytelling: "Hi Luca, I found a book with many little stories inside..."
- mood_check_teddy: "Hi Luca, this teddy bear would like to know..."
- computer_emotion_recognition: "Hi Luca, let's look at the pictures..."

Queste aperture entrano nella history come risposta assistant dopo un marker:

```python
{"role": "user", "content": "[START_ACTIVITY]"}
{"role": "assistant", "content": opening}
```

## History conversazionale attuale

La history e' una `deque` in memoria:

```python
self.history: Deque[Dict[str, str]] = deque(
    maxlen=2 * int(self.get_parameter("max_history_turns").value)
)
```

Nel profilo corrente:

```yaml
max_history_turns: 4
```

Quindi il contesto inviato al LLM contiene al massimo 4 turni completi
precedenti, cioe' fino a 8 messaggi user/assistant.

La history viene:

- usata nella costruzione dei messaggi LLM;
- aggiornata dopo ogni risposta normale del LLM;
- svuotata quando inizia una nuova child activity;
- mantenuta solo in RAM;
- persa quando il nodo termina;
- non scritta su file, database o topic persistente.

La frase di richiamo attenzione non viene aggiunta alla history narrativa
(ma viene aggiunta al session transcript per il report).
Anche la nota emozionale non viene aggiunta alla history: e' iniettata nel
system prompt del turno corrente.

## Integrazione con mission orchestrator

Il nodo:

```text
src/empower/python/empower/mission_orchestrator_node.py
```

e' il ponte tra HRI, Nav2, EMPOWER e child activity.

Launch integrato:

```text
src/empower/launch/hri_empower_mission.launch.py
```

Config missione:

```text
src/empower/config/task_config.yaml
```

### Static Scene Graph

L'orchestratore non legge piu' pose di navigazione direttamente dal config
YAML. Usa invece un `StaticSceneGraph` caricato da un file JSON:

```yaml
static_scene_graph_path: "/root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json"
```

Le pose vengono ottenute tramite:

```python
self.static_nav_pose("therapist", "receive_instruction")  # posa terapista
self.static_nav_pose("center", "capture_origin")           # posa spawn/centro
self.static_nav_pose("child", "deliver_object")            # posa bambino
```

### Stati missione

```text
GO_TO_THERAPIST
WAIT_CHILD_NAME
ACK_CHILD_NAME
PROPOSE_ACTIVITY
WAIT_THERAPIST_OVERRIDE
ACK_PROPOSAL
GO_TO_SPAWN
RUN_EMPOWER
GO_TO_CHILD
CHILD_READY
CHILD_ACTIVITY
GO_BACK_TO_THERAPIST
MISSION_COMPLETE
MISSION_FAILED
```

Se non c'e' storico per il bambino, dopo ACK_CHILD_NAME vengono usati anche
gli stati WAIT_TASK, ACK_TASK, WAIT_ACTIVITY, ACK_ACTIVITY prima di
GO_TO_SPAWN.

Nel launch integrato vengono avviati:

- `models_cacher_node`;
- `voice_llm_node`;
- `mission_orchestrator_node`.

Il launch forza sul nodo HRI:

```yaml
therapist_llm_replies: false
follow_mission_state_for_therapist_input: true
```

In questo modo il nodo HRI legge input dalla terapista solo negli stati:

```text
WAIT_CHILD_NAME
WAIT_TASK
WAIT_ACTIVITY
WAIT_THERAPIST_OVERRIDE
```

e solo una volta per ciascuno stato.

### Sequenza missione HRI + EMPOWER

La sequenza implementata e' ora "child-first":

1. TIAGo naviga verso la terapista (`GO_TO_THERAPIST`) usando
   `static_nav_pose("therapist", "receive_instruction")`.
2. Pronuncia `child_name_ask_text` e aspetta il nome del bambino su
   `/hri/user_utterance` (`WAIT_CHILD_NAME`).
3. Salva il nome in `MissionInteractionSpec.child_name`.
4. Pubblica ack vocale (`ACK_CHILD_NAME`).
5. `PROPOSE_ACTIVITY`: chiama `best_activity_for_child()` dal modulo
   `session_scores`. Se c'e' storico, propone l'attivita' migliore.
6. `WAIT_THERAPIST_OVERRIDE`: attende
   `proposal_acceptance_timeout_sec` (default 15s). Se la terapista
   parla, l'input sovrascrive la proposta; altrimenti accetta.
7. `ACK_PROPOSAL`: conferma l'attivita'.
   (Se non c'e' storico: passa invece per WAIT_TASK -> ACK_TASK ->
   WAIT_ACTIVITY -> ACK_ACTIVITY con il vecchio flusso manuale.)
8. Torna allo spawn usando `static_nav_pose("center", "capture_origin")`
   (`GO_TO_SPAWN`).
9. Esegue capture/planning/execution EMPOWER con il task fisico
    (`RUN_EMPOWER`).
10. Se `child_delivery_handled_by_empower` e' true, salta `GO_TO_CHILD`.
11. Pubblica `/mission/child_ready`.
12. Avvia gaze estimation + attention monitor.
13. Pubblica `start_child_activity` su `/hri/dialog_command` con contesto
    che include `child_name`.
14. Attende `/hri/dialog_complete` o timeout.
15. Ferma gaze.
16. `GO_BACK_TO_THERAPIST`: naviga verso la terapista e pronuncia un
    riepilogo dell'attivita' con i punteggi sessione.
17. Passa a `MISSION_COMPLETE`.

Nel config attuale:

```yaml
capture_before_empower: true
execute_empower_plan: true
child_delivery_handled_by_empower: true
wait_for_task_timeout_sec: 300.0
wait_for_activity_timeout_sec: 300.0
wait_for_child_name_timeout_sec: 300.0
proposal_acceptance_timeout_sec: 15.0
child_activity_timeout_sec: 300.0
session_scores_path: "/root/exchange/output/interaction_output/session_scores.json"
```

### Normalizzazione activity type

La funzione:

```python
_normalize_activity_type(activity_description)
```

usa keyword hardcoded. Esempi:

- parole come "emotion recognition", "recognize emotions", "emotion photos"
  -> `computer_emotion_recognition`;
- parole come "how they feel", "feelings", "mood", "teddy", "plush"
  -> `mood_check_teddy`;
- parole come "story", "storytelling", "read the story", "tell a story"
  -> `storytelling`;
- altrimenti usa `default_activity_type`, oggi `generic_child_activity`.

## Topic ROS 2 della parte HRI/LLM

Topic pubblicati da `voice_llm_node`:

```text
/hri/user_utterance      std_msgs/String
/hri/robot_utterance     std_msgs/String
/hri/dialog_state        std_msgs/String
/hri/dialog_complete     std_msgs/String
```

Topic sottoscritti da `voice_llm_node`:

```text
/hri/dialog_command
/hri/attention_alert
/child/emotion_status_smoothed
/mission/state
```

Topic pubblicati da `mission_orchestrator_node` rilevanti per HRI:

```text
/mission/state
/mission/feedback
/mission/task_description
/mission/child_ready
/hri/dialog_command
```

Topic sottoscritti da `mission_orchestrator_node` rilevanti per HRI:

```text
/hri/user_utterance
/hri/dialog_complete
/mission/gaze_command
/gaze/node_status
```

`/mission/state` usa QoS `TRANSIENT_LOCAL`, profondita' 1. Anche
`/mission/task_description` e `/mission/child_ready` sono latched tramite QoS
transient local.

## Comandi JSON su `/hri/dialog_command`

Il nodo HRI riconosce:

```json
{"command": "say", "text": "Okay, I understood the task."}
```

```json
{"command": "speak_text", "text": "..."}
```

```json
{
  "command": "start_child_activity",
  "context": {
    "child_name": "Luca",
    "task": "Take the teddy bear",
    "activity_description": "Ask the child how they feel",
    "activity_type": "mood_check_teddy"
  }
}
```

```json
{
  "command": "start_storytelling",
  "context": {
    "activity_description": "Tell a short story."
  }
}
```

```json
{"command": "stop"}
```

```json
{"command": "graceful_stop"}
```

`start_storytelling` e' una scorciatoia che imposta `activity_type` a
`storytelling` e poi passa nello stesso flusso di `start_child_activity`.

`graceful_stop` segnala al nodo HRI di terminare la sessione in modo
controllato: aspetta la fine dello scambio corrente, genera e pronuncia un
saluto, calcola i punteggi e genera il report prima di chiudere. La reason
nel payload di `/hri/dialog_complete` sara' `"graceful_stop_by_therapist"`.
Lo stesso effetto si ottiene premendo il tasto `graceful_stop_key` (default
`q`) in modalita' voice, o digitando `q` in modalita' text.

## Stati pubblicati su `/hri/dialog_state`

Il nodo HRI pubblica stringhe semplici. Stati osservabili nel codice:

```text
ready
waiting_for_text
waiting_for_push_to_talk
recording
transcribing
listening
thinking
speaking
child_activity_active
error
stopped
```

`recording`, `transcribing` e `listening` vengono pubblicati dallo STT tramite
callback verso il nodo HRI.

Lo stato `speaking` e' importante per il monitoraggio attenzione: quando il
bambino non guarda il target giusto mentre TIAGo parla, il nodo
`attention_monitor` puo' generare un alert.

## Gaze, attenzione ed emotion feedback verso l'LLM

Il launch gaze integrato e':

```text
src/gaze_estimation/launch/gaze_with_attention.launch.py
```

L'orchestratore lo avvia come sottoprocesso poco prima di `CHILD_ACTIVITY`.
Aspetta che `/gaze/node_status` pubblichi `READY`; se riceve `ERROR` o scade il
timeout, passa a `MISSION_FAILED`.

Nel config missione:

```yaml
gaze_estimation:
  enabled: true
  camera_index: 0
  device: "auto"
  show_window: true
  startup_timeout_sec: 120.0
```

### `gaze_webcam_node`

File:

```text
src/gaze_estimation/gaze_estimation/gaze_webcam_node.py
```

Publisher:

```text
/gaze/status
/gaze/node_status
/child/emotion_status
```

Il nodo usa:

- Gazelle/Gaze-LLE per volto e gaze;
- YOLO-World per oggetti target;
- EmotiEffLib per emotion recognition, se caricabile.

Payload `/gaze/status`:

```json
{
  "status": "LOOKING_AT_OBJECT|LOOKING_ELSEWHERE|NO_FACE|NO_FRAME|GAZE_OUT_OF_FRAME|NO_TARGET_OBJECT",
  "target_class": "book|null",
  "timestamp": 1710000000.0
}
```

Payload `/gaze/node_status`:

```json
{"state": "READY"}
```

oppure:

```json
{"state": "ERROR", "message": "..."}
```

Payload `/child/emotion_status`:

```json
{
  "emotion": "happy|sad|angry|fear|surprise|disgust|neutral|no_face|unavailable",
  "confidence": 0.8,
  "all_scores": {"happy": 0.8, "neutral": 0.1},
  "timestamp": 1710000000.0
}
```

Se EmotiEffLib non si carica, il nodo continua a funzionare per gaze e pubblica
emozione `unavailable`.

### Classi dinamiche per gaze in base all'attivita'

`gaze_webcam_node` ascolta `/hri/dialog_command`. Quando riceve
`start_child_activity`, cambia le classi YOLO-World in base a:

```yaml
activity_detection_classes:
  storytelling: ["book"]
  mood_check_teddy: ["teddy bear"]
  computer_emotion_recognition: ["laptop", "computer"]
```

Questa mappa indica cosa cercare nel frame webcam.

### `attention_monitor_node`

File:

```text
src/gaze_estimation/gaze_estimation/attention_monitor_node.py
```

Publisher:

```text
/hri/attention_alert
/child/emotion_status_smoothed
```

Subscriber:

```text
/gaze/status
/hri/dialog_state
/hri/dialog_command
/child/emotion_status
```

Il nodo ha due funzioni indipendenti nello stesso processo:

- attention monitoring;
- emotion smoothing.

#### Attention monitoring

Si attiva quando:

1. riceve `start_child_activity` su `/hri/dialog_command` e salva
   `activity_type`;
2. riceve `speaking` su `/hri/dialog_state`.

Si disarma quando `/hri/dialog_state` diventa diverso da `speaking` o quando
arriva comando `stop`.

Parametri:

```yaml
attention_loss_threshold_sec: 2.5
attention_cooldown_sec: 20.0
activity_target_map:
  storytelling: ["book", "robot"]
  mood_check_teddy: ["teddy bear", "robot"]
  computer_emotion_recognition: ["laptop", "computer"]
  generic_child_activity: ["robot"]
```

Questa mappa indica quali target sono validi per giudicare attenzione. E'
diversa da `activity_detection_classes`: qui puo' comparire anche `"robot"`,
che non e' necessariamente una classe YOLO cercata nel frame.

Payload `/hri/attention_alert`:

```json
{
  "reason": "no_face|out_of_frame|looking_elsewhere",
  "elapsed_sec": 2.6,
  "activity_type": "storytelling",
  "expected_target": "book"
}
```

#### Consumo alert nel nodo HRI

`voice_llm_node` mantiene un solo alert pendente in:

```python
self._pending_attention_alert
```

Dopo ogni risposta normale del turno child activity chiama:

```python
_handle_attention_recall()
```

Se c'e' un alert pendente:

- prova a generare una frase di richiamo con un secondo client Ollama con
  timeout breve;
- se fallisce usa fallback hardcoded;
- pubblica la frase su `/hri/robot_utterance`;
- la pronuncia con TTS;
- la aggiunge a `_session_transcript` come `role: "assistant"`;
- non la aggiunge alla history conversazionale del LLM.

Prompt richiamo attenzione:

```text
You are TIAGo, a friendly social robot interacting with a young child.
The child is not paying attention to the activity.
Generate ONE short, warm, non-judgmental sentence (maximum 12 words)
to gently redirect the child's attention back to {target}.
Do not scold. Do not mention that you are monitoring gaze or attention.
Just be kind and playful.
```

Fallback hardcoded:

```text
storytelling -> "Hey! Can you look at the book with me?"
mood_check_teddy -> "The teddy bear wants to see your eyes!"
computer_emotion_recognition -> "Let's look at the screen together!"
generic_child_activity -> "Hey, look at me! Let's continue together!"
```

Parametri HRI:

```yaml
attention_recall_llm_timeout: 4.0
attention_recall_max_tokens: 30
```

#### Emotion smoothing

`attention_monitor_node` riceve `/child/emotion_status`, mantiene una finestra
temporale e pubblica `/child/emotion_status_smoothed`.

Parametro:

```yaml
emotion_smoothing_window_sec: 7.0
```

Payload `/child/emotion_status_smoothed`:

```json
{
  "emotion": "happy",
  "window_sec": 7.0,
  "sample_count": 12,
  "timestamp": 1710000000.0
}
```

Lo smoothing ignora `no_face` e `unavailable`. Se non ci sono campioni validi,
pubblica `unknown`.

#### Consumo emozione nel nodo HRI

`voice_llm_node` ascolta `/child/emotion_status_smoothed` e salva l'ultima
emozione con timestamp.

Prima di ogni chiamata LLM in modalita' child activity, `current_system_prompt`
aggiunge il risultato di `get_emotion_context()`. La nota viene aggiunta solo
se:

- l'emozione non e' `neutral` o `unknown`;
- il timestamp non e' piu' vecchio di `emotion_staleness_threshold_sec`;
- esiste una hint non vuota per quell'emozione.

Parametro:

```yaml
emotion_staleness_threshold_sec: 15.0
```

Hint configurabili nel profilo corrente:

```yaml
emotion_prompt_hints:
  sad: "seems a bit sad or down. Keep a warm, gentle, reassuring tone. Avoid sad, scary, or negative-outcome content in stories or topics."
  angry: "seems frustrated or upset. Stay calm, patient, and validating. Avoid pushing or rushing them."
  fear: "seems a bit scared or anxious. Be extra gentle and reassuring. Avoid scary or suspenseful content."
  happy: "seems happy and engaged. Feel free to keep the energy light and playful."
  surprise: "seems surprised or very engaged. You can build on their curiosity."
  disgust: "seems uncomfortable with something. Consider gently changing the topic if it persists."
  neutral: ""
  unknown: ""
```

La nota generata ha forma:

```text
Note: the child currently {hint}
```

Questa nota influenza il system prompt del turno corrente ma non viene salvata
nella history.

## Configurazione HRI corrente

Profilo principale:

```text
src/llm_interaction/config/llm_config.yaml
```

Valori principali:

```yaml
input_mode: "voice"
llm_provider: "ollama"
llm_base_url: "http://localhost:11434"
llm_model: "gemma3:12b"
llm_fallback_model: "llama3.2:1b"
llm_timeout: 600.0
temperature: 0.4
max_response_tokens: 0
max_history_turns: 4
stt_provider: "faster_whisper"
stt_model: "base"
stt_device: "cpu"
stt_compute_type: "int8"
stt_device_index: 8
language: "en"
record_seconds: 5.0
sample_rate: 16000
tts_provider: "piper"
tts_timeout: 60.0
piper_model_path: "/root/exchange/models/piper/en_GB-jenny_dioco-medium.onnx"
piper_config_path: "/root/exchange/models/piper/en_GB-jenny_dioco-medium.onnx.json"
storytelling_max_turns: 10
storytelling_auto_listen: true
stt_vad_energy_threshold: 0.01
stt_vad_silence_sec: 1.5
stt_vad_max_duration_sec: 30.0
memory_enabled: true
memory_rdf_path: "/root/exchange/output/interaction_output/children_memory.ttl"
memory_report_dir: "/root/exchange/output/interaction_output/reports"
memory_max_prompt_facts: 12
memory_seed_if_missing: true
memory_update_enabled: false
emotion_staleness_threshold_sec: 15.0
session_scores_path: "/root/exchange/output/interaction_output/session_scores.json"
disengagement_alert_threshold: 4
disengagement_negative_emotion_ratio: 0.6
disengagement_check_window_sec: 120.0
disengagement_cooldown_sec: 180.0
disengagement_max_asks: 2
graceful_stop_key: "q"
```

Profilo locale/CPU:

```text
src/llm_interaction/config/hri_cpu_local.yaml
```

E' simile, ma di default:

```yaml
input_mode: "text"
stt_model: "tiny"
stt_device_index: -1
language: "en"
tts_provider: "pyttsx3"
```

## Cosa viene persistito oggi

### Parte EMPOWER

Per la parte EMPOWER vengono persistiti molti output sotto:

```text
output/empower_output/therapy_task
```

### Parte HRI/LLM

Per la parte HRI/LLM la persistenza include:

1. **File RDF memoria bambini** (`.ttl`):
   - Percorso: `/root/exchange/output/interaction_output/children_memory.ttl`
   - Creato al primo avvio con dati seed per Luca e Sofia.
   - Attualmente in sola lettura: non viene mai aggiornato automaticamente
     dopo il seeding.
   - Contiene: preferenze di storie, emozioni, stimoli di riconoscimento,
     tipi di attivita' preferiti per ogni bambino.

2. **Report di sessione** (Markdown):
   - Percorso: `/root/exchange/output/interaction_output/reports/`
   - Un file per ogni sessione di child activity completata.
   - Contiene: metadati sessione, memoria usata, summary (LLM o deterministico),
     punteggi sessione, osservazioni placeholder, candidate memory updates,
     transcript compatto.

3. **Punteggi sessione** (JSON):
   - Percorso: `/root/exchange/output/interaction_output/session_scores.json`
   - Organizzato per bambino e per sessione.
   - Contiene: punteggi attention, emotion, participation, overall (0-100)
     per ogni sessione completata.
   - Usato dall'orchestratore per proporre automaticamente l'attivita'
     con il miglior punteggio medio tramite `best_activity_for_child()`.
   - Fornisce aggregazione cross-sessione per lo stesso bambino.

### Cosa NON viene persistito

- Utterance history tra sessioni (la `deque` in RAM viene persa).
- Aggiornamenti automatici alla memoria RDF basati sulle osservazioni.
- Log emozionali o di attenzione strutturati per analisi dettagliata
  (solo i punteggi aggregati vengono salvati).
- I contatori di disengagement (resettati a ogni sessione).

## Limiti comportamentali attuali rilevanti

- Il modulo HRI non controlla direttamente movimento, manipolazione o Nav2.
  Queste parti sono gestite dall'orchestratore e da EMPOWER.
- In missione completa, le risposte LLM alla terapista sono disabilitate:
  la terapista fornisce testo utile per orchestratore/EMPOWER.
- La child activity e' una sessione conversazionale guidata da prompt,
  regole di stile a due frasi, aperture personalizzate col nome del bambino
  e memoria RDF opzionale.
- Il tipo di attivita' viene determinato da keyword hardcoded
  nell'orchestratore, oppure dalla proposta automatica basata sullo storico
  punteggi.
- La history LLM e' breve, volatile e locale al nodo.
- Gli alert attenzione vengono consumati uno alla volta come singolo pending
  alert per il richiamo breve; pero' vengono anche accumulati per la
  soglia di disengagement secondaria.
- L'emozione influenza sia il system prompt corrente sia il calcolo della
  soglia di disengagement (ratio emozioni negative).
- Il corretto funzionamento dell'attention monitor dipende dalla durata reale
  del TTS nello stato `speaking`.
- Il fallimento dell'emotion recognition non blocca la missione; il fallimento
  di startup gaze invece blocca la missione se `gaze_estimation.enabled` e'
  true.
- Il file .ttl della memoria non viene mai aggiornato dopo il seeding:
  le informazioni sono statiche finche' non si implementa un workflow di
  validazione terapista -> aggiornamento RDF.
- Il graceful stop in modalita' voice dipende da `select.select` su stdin,
  che potrebbe non funzionare in tutti gli ambienti containerizzati o in
  terminali non standard.
- I punteggi sessione sono salvati in un file JSON locale; non c'e' un
  database centralizzato.
- Il timeout LLM e' molto alto (600 secondi) per accommodare gemma3:12b
  su hardware non-GPU.

## Esempi di input missione

### Caso con storico sessione (proposta automatica)

Se il bambino ha gia' sessioni salvate in `session_scores.json`, basta
fornire il nome:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Luca'}"
# Il robot propone l'attivita' migliore e attende conferma/override
# Se la terapista non parla entro proposal_acceptance_timeout_sec, accetta
```

Se la terapista vuole sovrascrivere la proposta, parla prima del timeout:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Luca'}"
# Robot propone "storytelling" basandosi sullo storico
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Do emotion recognition with the computer'}"
# La terapista sovrascrive la proposta
```

### Caso senza storico (flusso manuale)

Se il bambino non ha storico, il robot chiede task e attivita' manualmente:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Luca'}"
# Robot dice "I don't have session history for this child..."
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Take the computer'}"
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Do emotion recognition with the computer'}"
```

### Graceful stop durante l'attivita'

In modalita' voice, la terapista preme `q` sulla tastiera. In modalita' text,
digita `q`. Via topic:

```bash
ros2 topic pub --once /hri/dialog_command std_msgs/msg/String "{data: '{\"command\": \"graceful_stop\"}'}"
```

Il primo input utile viene letto nello stato `WAIT_CHILD_NAME`. Se non c'e'
storico, il secondo input viene letto in `WAIT_TASK` e il terzo in
`WAIT_ACTIVITY`. Se c'e' storico, l'unico input aggiuntivo e' l'eventuale
override in `WAIT_THERAPIST_OVERRIDE`. Dopo l'avvio della child activity,
l'orchestratore ignora nuove utterance su `/hri/user_utterance`; quelle
utterance vengono usate dal nodo HRI per il dialogo col bambino.
