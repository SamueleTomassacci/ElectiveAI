# Stato attuale LLM e interazione con il bambino

Questo documento e' pensato per essere passato a ChatGPT come contesto tecnico
della repo `ElectiveAI`, con focus specifico sulla parte LLM/HRI e
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

- raccogliere dalla terapista il task fisico per EMPOWER e l'attivita' sociale
  da fare con il bambino;
- gestire una sessione conversazionale multi-turn con il bambino dopo che la
  missione fisica ha portato TIAGo nella fase di child activity.

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
microfono -> faster-whisper -> Qwen via Ollama -> espeak-ng/system TTS -> speaker
```

Modello LLM configurato di default:

```text
qwen2.5:1.5b
```

Fallback configurato:

```text
llama3.2:1b
```

Comando host per Ollama:

```bash
ollama serve
ollama pull qwen2.5:1.5b
```

Comando tipico per il solo dialogo HRI inglese:

```bash
ros2 launch llm_interaction hri_voice_llm_english.launch.py
```

Comando tipico per la missione completa HRI + EMPOWER:

```bash
ros2 launch empower hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/llm_interaction/config/llm_config.yaml \
  hri_input_mode:=voice
```

## Package `llm_interaction`

File principali:

```text
src/llm_interaction/llm_interaction/voice_llm_node.py
src/llm_interaction/llm_interaction/ollama_client.py
src/llm_interaction/llm_interaction/speech_to_text.py
src/llm_interaction/llm_interaction/text_to_speech.py
src/llm_interaction/llm_interaction/terminal_io.py
src/llm_interaction/config/llm_config.yaml
src/llm_interaction/config/hri_cpu_local.yaml
src/llm_interaction/launch/hri_voice_llm_english.launch.py
src/llm_interaction/launch/hri_voice_llm_cpu.launch.py
```

Entry point ROS 2:

```text
voice_llm_node = llm_interaction.voice_llm_node:main
```

Il package e' `ament_python` e viene installato da
`src/llm_interaction/setup.py`.

## Nodo `voice_llm_node`

`VoiceLlmNode` coordina input, LLM, TTS e topic ROS. Ha un thread worker
interno (`voice_llm_interaction`) che esegue il loop interattivo mentre il
main thread ROS 2 continua a fare `rclpy.spin`.

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
`/hri/user_utterance`. L'orchestratore decide se quel testo e' il task fisico
o la descrizione dell'attivita' sociale in base allo stato missione corrente.

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
  "task": "...",
  "activity_description": "...",
  "activity_type": "storytelling|mood_check_teddy|computer_emotion_recognition|generic_child_activity"
}
```

Quando parte la child activity:

- il nodo valida/normalizza `activity_type`;
- salva il contesto in `_child_activity_context`;
- svuota la history conversazionale corrente;
- cancella eventuali alert attenzione pendenti;
- passa a `_MODE_CHILD_ACTIVITY`.

All'inizio della sessione il robot parla senza aspettare input dal bambino,
usando un'apertura hardcoded dipendente dal tipo di attivita'. Poi entra in un
loop multi-turn: ascolta/legge il bambino, chiama il LLM, pronuncia la risposta,
gestisce eventuale richiamo attenzione, incrementa il contatore turni.

La sessione termina quando:

- arriva `/quit`, `/exit`, `quit` o `exit`;
- arriva comando `{"command": "stop"}` su `/hri/dialog_command`;
- si raggiunge `storytelling_max_turns`;
- il nodo viene fermato o ROS 2 termina.

Alla fine pubblica `/hri/dialog_complete` con un payload JSON:

```json
{
  "reason": "max_turns|stopped",
  "turns": 10,
  "activity_type": "storytelling"
}
```

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
    "num_predict": 120
  }
}
```

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

Nel profilo inglese corrente viene usato:

```yaml
tts_provider: "system"
```

Il backend `system` prova `espeak-ng`, poi `espeak`, poi `spd-say`.
Il codice misura e logga la durata reale della riproduzione:

```text
TTS playback took X.Xs (backend='system')
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

`computer_emotion_recognition`:

- TIAGo aiuta il bambino a riconoscere emozioni da immagini su computer;
- usa linguaggio semplice;
- chiede una domanda alla volta;
- puo' proporre scelte come happy, sad, angry, scared, surprised;
- incoraggia il bambino e da' hint semplici.

`mood_check_teddy`:

- TIAGo usa il teddy bear come intermediario sicuro;
- chiede al bambino come si sente;
- non forza la risposta;
- mantiene tono caldo e non pressante.

`storytelling`:

- TIAGo dice di leggere un libro con molte storie;
- inventa una storia breve e child-friendly;
- mantiene continuita' tra turni;
- legge in parti piccole;
- fa domande semplici ogni tanto.

`generic_child_activity`:

- usa `activity_description` data dalla terapista;
- la segue come interazione child-friendly;
- chiede una domanda alla volta.

### Aperture hardcoded

La funzione:

```python
child_activity_opening(activity_type, context)
```

definisce la prima frase pronunciata prima di ascoltare il bambino.
Esempi attuali:

- storytelling: "I found a book with many little stories inside..."
- mood_check_teddy: "This teddy bear would like to know how you feel today..."
- computer_emotion_recognition: "Let's look at the pictures on the computer..."

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

Nel profilo inglese:

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

La frase di richiamo attenzione non viene aggiunta alla history narrativa.
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

Stati missione:

```text
GO_TO_THERAPIST
WAIT_TASK
ACK_TASK
WAIT_ACTIVITY
ACK_ACTIVITY
GO_TO_SPAWN
RUN_EMPOWER
GO_TO_CHILD
CHILD_READY
CHILD_ACTIVITY
MISSION_COMPLETE
MISSION_FAILED
```

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
WAIT_TASK
WAIT_ACTIVITY
```

e solo una volta per ciascuno stato.

### Sequenza missione HRI + EMPOWER

La sequenza implementata e':

1. TIAGo naviga verso la terapista (`GO_TO_THERAPIST`).
2. Orchestratore aspetta il task fisico su `/hri/user_utterance`
   (`WAIT_TASK`).
3. Pubblica ack vocale hardcoded via `/hri/dialog_command`
   (`ACK_TASK`).
4. Aspetta la descrizione dell'attivita' sociale per il bambino
   (`WAIT_ACTIVITY`).
5. Normalizza `activity_type`.
6. Pubblica ack vocale hardcoded (`ACK_ACTIVITY`).
7. Torna allo spawn (`GO_TO_SPAWN`).
8. Esegue capture/planning/execution EMPOWER con il task fisico
   (`RUN_EMPOWER`).
9. Se `child_delivery_handled_by_empower` e' true, salta `GO_TO_CHILD` perche'
   si assume che EMPOWER abbia gia' gestito la consegna al bambino.
10. Pubblica `/mission/child_ready`.
11. Avvia gaze estimation + attention monitor.
12. Pubblica `start_child_activity` su `/hri/dialog_command`.
13. Attende `/hri/dialog_complete` o timeout.
14. Ferma gaze e passa a `MISSION_COMPLETE`.

Nel config attuale:

```yaml
capture_before_empower: true
execute_empower_plan: true
child_delivery_handled_by_empower: true
wait_for_task_timeout_sec: 300.0
wait_for_activity_timeout_sec: 300.0
child_activity_timeout_sec: 300.0
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

`start_storytelling` e' una scorciatoia che imposta `activity_type` a
`storytelling` e poi passa nello stesso flusso di `start_child_activity`.

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
- non la aggiunge alla history conversazionale.

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

Prima di ogni chiamata LLM in modalita' child activity, `_current_system_prompt`
aggiunge il risultato di `_get_emotion_context()`. La nota viene aggiunta solo
se:

- l'emozione non e' `neutral` o `unknown`;
- il timestamp non e' piu' vecchio di `emotion_staleness_threshold_sec`;
- esiste una hint non vuota per quell'emozione.

Parametro:

```yaml
emotion_staleness_threshold_sec: 15.0
```

Hint configurabili nel profilo inglese:

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

Profilo principale inglese:

```text
src/llm_interaction/config/llm_config.yaml
```

Valori principali:

```yaml
input_mode: "voice"
llm_provider: "ollama"
llm_base_url: "http://localhost:11434"
llm_model: "qwen2.5:1.5b"
llm_fallback_model: "llama3.2:1b"
llm_timeout: 60.0
temperature: 0.4
max_response_tokens: 120
max_history_turns: 4
stt_provider: "faster_whisper"
stt_model: "base"
stt_device: "cpu"
stt_compute_type: "int8"
stt_device_index: 8
language: "en"
record_seconds: 5.0
sample_rate: 16000
tts_provider: "system"
tts_timeout: 30.0
storytelling_max_turns: 10
storytelling_auto_listen: true
stt_vad_energy_threshold: 0.01
stt_vad_silence_sec: 1.5
stt_vad_max_duration_sec: 30.0
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

Il launch `hri_voice_llm_cpu.launch.py` dichiara come default `language: "it"`,
ma il file YAML locale contiene `language: "en"`. Il launch puo' sovrascrivere
i parametri a runtime.

## Dati gia' disponibili nel flusso corrente

Durante una missione completa esistono gia' questi dati testuali/semantici:

- task fisico dato dalla terapista (`MissionInteractionSpec.task_description`);
- descrizione attivita' bambino (`MissionInteractionSpec.activity_description`);
- tipo normalizzato attivita' (`MissionInteractionSpec.activity_type`);
- utterance del bambino pubblicate su `/hri/user_utterance`;
- risposte del robot pubblicate su `/hri/robot_utterance`;
- stato dialogo pubblicato su `/hri/dialog_state`;
- alert attenzione pubblicati su `/hri/attention_alert`;
- emozione grezza pubblicata su `/child/emotion_status`;
- emozione smoothed pubblicata su `/child/emotion_status_smoothed`;
- history breve in RAM nel nodo HRI.

Di questi, nel blocco HRI attuale solo la history breve viene inviata al LLM
come memoria conversazionale immediata. Gli altri dati transitano su topic ROS
o rimangono in campi runtime dell'orchestratore/nodo HRI.

## Cosa viene persistito oggi

Per la parte EMPOWER vengono persistiti molti output sotto:

```text
output/empower_output/therapy_task
output/empower_output/therapy_task
```

Per la parte HRI/LLM, invece, non risulta implementata una persistenza dedicata
di:

- profilo bambino;
- preferenze del bambino;
- riassunti conversazionali;
- storia delle sessioni;
- utterance HRI;
- risposte LLM;
- emozioni/attenzione associate al bambino;
- activity history tra avvii diversi del nodo.

La conversazione child activity corrente e' mantenuta in RAM nella `deque`
`history` e viene persa a fine processo o quando inizia una nuova child
activity.

## Test presenti

Test del package HRI:

```text
src/llm_interaction/test/test_ollama_client.py
src/llm_interaction/test/test_text_to_speech.py
```

Coprono:

- `MockLlmClient`;
- selezione fallback model in `OllamaClient`;
- output `mock` del TTS;
- preferenza `espeak-ng` nel provider TTS `system`.

Non risultano test unitari dedicati a:

- `VoiceLlmNode`;
- cambio modalita' terapista/child activity;
- costruzione prompt per activity;
- gestione alert attenzione;
- iniezione emotion context nel prompt;
- contratti JSON dei topic.

## Limiti comportamentali attuali rilevanti

- Il modulo HRI non controlla direttamente movimento, manipolazione o Nav2.
  Queste parti sono gestite dall'orchestratore e da EMPOWER.
- In missione completa, le risposte LLM alla terapista sono disabilitate:
  la terapista fornisce testo utile per orchestratore/EMPOWER.
- La child activity e' una sessione conversazionale guidata da prompt e
  aperture hardcoded.
- Il tipo di attivita' viene determinato da keyword hardcoded
  nell'orchestratore.
- La history LLM e' breve, volatile e locale al nodo.
- Gli alert attenzione vengono consumati uno alla volta come singolo pending
  alert.
- L'emozione influenza solo il system prompt corrente, non la history.
- Il corretto funzionamento dell'attention monitor dipende dalla durata reale
  del TTS nello stato `speaking`.
- Il fallimento dell'emotion recognition non blocca la missione; il fallimento
  di startup gaze invece blocca la missione se `gaze_estimation.enabled` e'
  true.

## Esempi di input missione

Caso computer + emotion recognition:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Take the computer'}"
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Do emotion recognition with the computer'}"
```

Caso teddy bear + mood questions:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Take the teddy bear'}"
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Ask the child how they feel'}"
```

Caso storytelling:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Take the book'}"
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Tell a story'}"
```

Il primo input utile viene letto nello stato `WAIT_TASK`. Il secondo input utile
viene letto nello stato `WAIT_ACTIVITY`. Dopo l'avvio della child activity,
l'orchestratore ignora nuove utterance su `/hri/user_utterance`; quelle
utterance vengono usate dal nodo HRI per il dialogo col bambino.
