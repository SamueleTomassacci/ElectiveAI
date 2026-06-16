# Report tecnico: HRI vocale e LLM locale per EMPOWER/TIAGo

## 1. Scopo del documento

Questo documento descrive lo stato attuale del modulo di interazione
uomo-robot aggiunto alla repository EMPOWER/TIAGo. Il report è pensato come
passaggio di consegne per un altro sviluppatore o LLM che dovrà progettare la
fase successiva: collegare in modo controllato il dialogo vocale alla pipeline
di percezione, grounding, pianificazione simbolica ed esecuzione ROS2.

Il modulo realizzato è un MVP locale che verifica il ciclo:

```text
utente -> microfono -> STT -> LLM -> risposta testuale -> TTS -> speaker
```

Il ciclo funziona end-to-end sia in italiano sia in inglese. Non sono ancora
presenti memoria persistente, profili utente, riconoscimento di
bambino/terapeuta, intenti robotici strutturati o chiamate dirette alla
pipeline EMPOWER.

## 2. Repository preesistente

La repository contiene una pipeline ROS2 Humble per TIAGo in Gazebo:

```text
task testuale
-> acquisizione RGB-D
-> detection e segmentazione
-> grounding degli oggetti
-> rappresentazione simbolica
-> piano simbolico
-> esecuzione NAVIGATE tramite Nav2
```

Il package principale preesistente è `src/empower_ros2`.

Componenti rilevanti:

- `scene_capture_node`: acquisisce RGB, depth e camera info e produce point
  cloud e file di scena.
- `models_cacher_node`: mantiene caricati i modelli EMPOWER e riceve via
  socket richieste contenenti `task_description`.
- `plan_generator_node`: riceve `task_description` come parametro ROS2,
  contatta il model cacher e produce `planning.txt` e `planning.json`.
- `marker_centroids_node`: pubblica nomi e centroidi degli oggetti.
- `low_level_execution_node`: legge il piano da file, risolve i target tramite
  i centroidi e chiama l'action Nav2 `NavigateToPose`.

L'esecuzione low-level supporta fisicamente solo `NAVIGATE`. Le azioni
`GRAB`, `DROP`, `PUSH` e `PULL` sono riconosciute ma saltate.

## 3. Nuovo package `empower_hri`

È stato creato un package ROS2 Python indipendente:

```text
src/empower_hri/
  package.xml
  setup.py
  setup.cfg
  resource/empower_hri
  empower_hri/
    __init__.py
    voice_llm_node.py
    ollama_client.py
    speech_to_text.py
    text_to_speech.py
    terminal_io.py
  config/
    hri_cpu_local.yaml
    hri_cpu_english.yaml
  launch/
    hri_voice_llm_cpu.launch.py
    hri_voice_llm_english.launch.py
  test/
    test_ollama_client.py
    test_text_to_speech.py
  README.md
```

Il package usa `ament_python`, `rclpy` e `std_msgs`. Le dipendenze pesanti o
legate all'audio sono importate a runtime e restano opzionali, in modo che i
provider mock funzionino senza Ollama, microfono o TTS.

## 4. Nodo ROS2 principale

Il nodo è:

```text
voice_llm_node
```

Entry point:

```text
voice_llm_node = empower_hri.voice_llm_node:main
```

Il nodo usa un thread dedicato per il ciclo interattivo. Il thread principale
continua a eseguire `rclpy.spin()`, mentre input da terminale, registrazione,
inferenza e TTS avvengono nel worker `voice_llm_interaction`.

### Topic pubblicati

```text
/hri/user_utterance   std_msgs/String
/hri/robot_utterance  std_msgs/String
/hri/dialog_state     std_msgs/String
```

Stati pubblicati:

```text
ready
waiting_for_text
waiting_for_push_to_talk
recording
transcribing
thinking
speaking
error
stopped
```

Non sono attualmente presenti subscriber, service server, action server o
action client nel package HRI.

### Modalità di input

`input_mode:=text`

- legge una frase dal terminale;
- consente di testare LLM e TTS senza microfono.

`input_mode:=voice`

- mostra un prompt push-to-talk;
- attende INVIO;
- registra un WAV temporaneo;
- lo trascrive;
- elimina il file temporaneo dopo l'uso.

I comandi `/quit`, `/exit`, `quit` ed `exit` terminano il nodo.

`terminal_io.py` prova a leggere direttamente da `/dev/tty`. Questo rende
l'input interattivo utilizzabile anche quando il nodo è avviato tramite
`ros2 launch`, che non sempre inoltra correttamente lo standard input al
processo figlio.

## 5. Speech-to-text

Il modulo è `speech_to_text.py`.

Provider disponibili:

```text
faster_whisper
mock
```

### Provider `faster_whisper`

Tecnologie:

- `sounddevice` per la registrazione;
- `soundfile` per la scrittura WAV PCM 16 bit;
- `faster-whisper` per la trascrizione locale.

Configurazione corrente:

```text
device: cpu
compute_type: int8
sample_rate: 16000
beam_size: 1
vad_filter: true
```

Il codice rifiuta esplicitamente `stt_device` diversi da `cpu`. Non è ancora
presente supporto CUDA.

Il modello viene caricato in modo lazy alla prima trascrizione e poi mantenuto
in RAM. Il caricamento può provocare il download iniziale se il modello non è
già nella cache di Hugging Face.

Profili correnti:

- italiano: modello `tiny`, lingua `it`, registrazione 4 secondi;
- inglese: modello `base`, lingua `en`, registrazione 5 secondi.

### Provider STT `mock`

Non registra audio. Chiede all'utente di digitare manualmente la
trascrizione. Serve per testare il ramo voice senza dipendenze audio.

### Limiti STT attuali

- durata di registrazione fissa;
- nessuna selezione esplicita del device audio via parametro ROS2;
- nessun VAD adattivo o stop automatico sul silenzio;
- nessuna trascrizione streaming;
- `beam_size` non configurabile;
- nessun feedback sul livello del segnale registrato;
- solo CPU.

Nell'ambiente testato il microfono interno utilizzabile dal container è:

```text
card 0, device 7: DMIC16kHz
```

È stato configurato esternamente tramite `/root/.asoundrc` come ingresso
ALSA predefinito. Questa configurazione non fa parte del package e viene persa
quando il container `--rm` viene eliminato.

## 6. LLM locale

Il modulo è `ollama_client.py`.

Provider disponibili:

```text
ollama
mock
```

Non esistono provider cloud e non vengono usate API key.

### Client Ollama

Il client usa soltanto la libreria standard Python:

```text
urllib.request
urllib.error
json
```

Endpoint utilizzati:

```text
GET  /api/tags
POST /api/chat
```

Prima di ogni chat il client:

1. interroga `/api/tags`;
2. verifica che il modello richiesto sia installato;
3. usa il modello fallback se quello principale manca;
4. produce un errore leggibile se nessuno dei due è disponibile.

Messaggio previsto per modello mancante:

```text
Ollama è attivo ma il modello non sembra disponibile.
Esegui: ollama pull <modello>
```

La richiesta `/api/chat` è non-streaming:

```json
{
  "model": "qwen2.5:1.5b",
  "messages": [],
  "stream": false,
  "options": {
    "temperature": 0.4,
    "num_predict": 120
  }
}
```

Modello principale attuale:

```text
qwen2.5:1.5b
```

Fallback configurato:

```text
llama3.2:1b
```

Ollama gira sull'host e il nodo ROS2 gira nel container con rete host. Il
container raggiunge Ollama su:

```text
http://localhost:11434
```

### Provider LLM `mock`

Genera una risposta deterministica senza Ollama. Riconosce se il system
prompt richiede inglese e produce una risposta mock nella lingua corretta.

### System prompt

Il prompt italiano impone che TIAGo:

- parli in italiano;
- sia breve, chiaro e gentile;
- sia adatto a bambini e terapeuti;
- dichiari che può soltanto parlare;
- non inventi navigazione, manipolazione o riconoscimento persone.

Il profilo inglese contiene lo stesso vincolo tradotto in inglese.

### History

La conversazione è conservata soltanto in RAM:

```text
deque(maxlen=2 * max_history_turns)
```

Con il default `max_history_turns:=4`, vengono mantenuti al massimo quattro
turni utente-assistente, oltre al system prompt e al nuovo messaggio.

Non esiste memoria persistente, database, embeddings o RAG.

### Limiti LLM attuali

- risposta non-streaming;
- output solo testo libero;
- nessun JSON Schema;
- nessuna classificazione intenti;
- nessun tool calling;
- nessuna conferma dell'utente;
- nessun controllo semantico esterno;
- modello 1.5B adatto al prototipo ma non ideale come autorità decisionale.

## 7. Text-to-speech

Il modulo è `text_to_speech.py`.

Provider:

```text
piper
pyttsx3
system
mock
```

### Piper

Usa il comando locale `piper`, un modello `.onnx` e il relativo file di
configurazione. Genera un WAV temporaneo e prova a riprodurlo con:

```text
aplay
paplay
ffplay
```

Fallback:

```text
piper -> pyttsx3 -> system -> mock
```

Piper è implementato ma non è il backend attivo nei profili correnti.

### pyttsx3

Usa le voci di sistema. Prova a selezionare una voce italiana cercando
identificatori contenenti `ital`, `it_` o `it-`.

Fallback:

```text
pyttsx3 -> system -> mock
```

### Provider `system`

Prova nell'ordine:

```text
espeak-ng
espeak
spd-say
```

È presente un timeout configurabile, default 30 secondi, per evitare blocchi
indefiniti. L'aggiunta di `espeak-ng` prima di `spd-say` ha risolto un blocco
osservato nel container.

### Provider `mock`

Stampa:

```text
TIAGo: <risposta>
```

### Audio nel container

Il microfono è accessibile tramite `/dev/snd`, ma lo speaker dell'host è
normalmente occupato da PipeWire/PulseAudio. Per la riproduzione completa il
container deve essere avviato montando:

```text
/run/user/<uid>/pulse/native
~/.config/pulse/cookie
```

e impostando:

```text
PULSE_SERVER=unix:/run/user/<uid>/pulse/native
```

La configurazione ALSA usata durante i test separa:

```text
capture  -> hw:0,7
playback -> PulseAudio host
```

Questa configurazione operativa non è ancora codificata in un'immagine Docker
o script persistente.

## 8. Profili di configurazione

### Italiano

File:

```text
config/hri_cpu_local.yaml
launch/hri_voice_llm_cpu.launch.py
```

Default principali:

```yaml
input_mode: text
llm_provider: ollama
llm_model: qwen2.5:1.5b
stt_model: tiny
stt_device: cpu
stt_compute_type: int8
language: it
record_seconds: 4.0
tts_provider: pyttsx3
```

### Inglese

File:

```text
config/hri_cpu_english.yaml
launch/hri_voice_llm_english.launch.py
```

Default principali:

```yaml
input_mode: voice
llm_provider: ollama
llm_model: qwen2.5:1.5b
stt_model: base
stt_device: cpu
stt_compute_type: int8
language: en
record_seconds: 5.0
tts_provider: system
```

Entrambi i launch permettono di sovrascrivere da command line i parametri
principali.

## 9. Robustezza ed error handling

Sono gestiti con messaggi leggibili:

- Ollama non raggiungibile;
- modello Ollama non installato;
- timeout HTTP;
- risposta HTTP o JSON non valida;
- `faster-whisper` non installato;
- `sounddevice` o `soundfile` non installati;
- microfono non disponibile;
- modello Whisper non caricabile;
- provider non supportato;
- Piper non installato o modello mancante;
- pyttsx3 non funzionante;
- comandi TTS di sistema mancanti;
- timeout dei processi TTS;
- risposta LLM vuota.

In caso di errore il nodo pubblica:

```text
/hri/dialog_state = error
```

e continua il ciclo invece di terminare in modo incomprensibile.

## 10. Test esistenti e verifiche effettuate

Test unitari:

- il mock LLM usa l'ultimo messaggio utente;
- il mock LLM risponde in inglese con system prompt inglese;
- Ollama seleziona il modello fallback installato;
- il TTS mock stampa la risposta;
- il provider system preferisce `espeak-ng`.

Verifiche effettuate durante lo sviluppo:

- `colcon build --symlink-install --packages-select empower_hri`;
- `colcon test --packages-select empower_hri`;
- launch text con LLM e TTS mock;
- launch voice con STT mock;
- pubblicazione reale sui topic HRI;
- STT reale italiano con Whisper `tiny`;
- STT reale inglese con Whisper `base`;
- chiamata reale a Ollama con Qwen 2.5 1.5B;
- risposta TTS tramite `espeak-ng`;
- ciclo completo microfono-STT-LLM-TTS.

È importante terminare il launch con `/quit` o `Ctrl+C`. `Ctrl+Z` sospende i
processi senza liberarli e può lasciare più nodi HRI e device audio occupati.

## 11. Stato reale dell'integrazione con EMPOWER

L'integrazione attuale è a livello di workspace e middleware, non ancora a
livello funzionale.

Già integrato:

- stesso workspace ROS2;
- stesso ambiente Docker;
- stesso ROS domain;
- package installabile con `colcon`;
- topic HRI disponibili agli altri nodi;
- nessuna modifica regressiva al package `empower_ros2`;
- system prompt coerente con le capacità effettivamente implementate.

Non ancora integrato:

- `/hri/user_utterance` non viene inviato a `plan_generator_node`;
- il modulo HRI non avvia la cattura RGB-D;
- il modulo HRI non chiama il model cacher;
- il modulo HRI non legge `planning.json`;
- il modulo HRI non riceve feedback da EMPOWER o Nav2;
- il modulo HRI non chiama direttamente Nav2;
- il modulo HRI non decide se una frase è dialogo o task robotico;
- non esiste conferma vocale prima dell'esecuzione;
- non esiste una rappresentazione strutturata dell'intento.

Il nodo `plan_generator_node` attuale è one-shot: legge `task_description`
come parametro all'avvio, esegue il planning e chiude ROS2. Non è quindi
ancora adatto a ricevere una sequenza di task conversazionali durante una
sessione HRI.

