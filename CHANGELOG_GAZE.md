# Changelog — Gaze Estimation Integration

Data: 2026-06-19

## Panoramica

Integrazione della gaze estimation nell'interazione robot-bambino durante la
fase CHILD_ACTIVITY della missione HRI + EMPOWER. Il sistema rileva se il
bambino non sta guardando l'oggetto/target giusto mentre il robot parla e
genera una frase di richiamo attenzione a fine turno.

## File creati

### `src/gaze_estimation/gaze_estimation/attention_monitor_node.py`

Nuovo nodo ROS 2 che monitora l'attenzione del bambino.

- Sottoscrive: `/gaze/status`, `/hri/dialog_state`, `/hri/dialog_command`
- Pubblica: `/hri/attention_alert`
- Si arma solo durante la fase `speaking` dell'attivita' child
- Conta il tempo di disattenzione e pubblica alert dopo
  `attention_loss_threshold_sec` (default 2.5s)
- Cooldown configurabile (default 20s) per evitare alert ripetuti

### `src/gaze_estimation/config/attention_monitor.yaml`

Config YAML per il nodo attention_monitor con parametri:

- `attention_loss_threshold_sec`: soglia di disattenzione (2.5s)
- `attention_cooldown_sec`: cooldown tra alert (20s)
- `activity_target_map`: mappa attivita' -> classi oggetto valide

### `src/gaze_estimation/launch/gaze_with_attention.launch.py`

Launch file che avvia insieme `gaze_webcam_node` e `attention_monitor_node`.

### `scripts/stop_gaze.sh`

Script di utilita' per pubblicare il comando di stop gaze su
`/mission/gaze_command` da terminale.

## File modificati

### `src/gaze_estimation/gaze_estimation/gaze_webcam_node.py`

- Aggiunto publisher `/gaze/status` (JSON: status, target_class, timestamp)
- Aggiunto publisher `/gaze/node_status` (JSON: state, message)
- Heartbeat READY ogni ~2 secondi
- Pubblica ERROR se webcam o modelli non si inizializzano

### `src/gaze_estimation/setup.py`

- Aggiunto entry point `attention_monitor`

### `src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py`

- Sottoscrive `/mission/gaze_command` per stop gaze manuale
- Sottoscrive `/gaze/node_status` per verificare startup gaze
- Lancia gaze come sottoprocesso prima di CHILD_ACTIVITY
- Attende READY da `/gaze/node_status` con timeout configurabile
- Se gaze fallisce, la missione si ferma con errore
- Ferma gaze a fine attivita' o in caso di eccezione
- Cleanup gaze nel `destroy_node()`
- Nuovi parametri: `gaze_estimation.enabled`, `gaze_estimation.camera_index`,
  `gaze_estimation.device`, `gaze_estimation.show_window`,
  `gaze_estimation.startup_timeout_sec`

### `src/empower_ros2/config/hri_empower_mission.yaml`

- Aggiunta sezione `gaze_estimation` con parametri di configurazione

### `src/empower_hri/empower_hri/voice_llm_node.py`

- Sottoscrive `/hri/attention_alert`
- Crea un secondo `OllamaClient` con timeout breve (4s) per la frase di
  richiamo attenzione
- Dopo la risposta normale del turno, controlla se c'e' un alert pendente
- Se c'e', genera una frase con LLM (o usa fallback) e la pronuncia con TTS
- La frase di richiamo NON viene aggiunta alla history narrativa dell'attivita'
- Costanti `_ATTENTION_RECALL_FALLBACKS` per ogni tipo di attivita'
- Nuovi parametri: `attention_recall_llm_timeout`, `attention_recall_max_tokens`

## Nuovi topic ROS 2

| Topic | Tipo | Publisher | Schema |
|-------|------|-----------|--------|
| `/gaze/status` | `std_msgs/String` | `gaze_webcam_node` | `{"status": "...", "target_class": "..." \| null, "timestamp": float}` |
| `/gaze/node_status` | `std_msgs/String` | `gaze_webcam_node` | `{"state": "READY" \| "ERROR", "message": "..."}` |
| `/hri/attention_alert` | `std_msgs/String` | `attention_monitor_node` | `{"reason": "...", "elapsed_sec": float, "activity_type": "...", "expected_target": "..."}` |
| `/mission/gaze_command` | `std_msgs/String` | (esterno/script) | `{"command": "stop_gaze"}` |

## Nuovi parametri

### attention_monitor_node

- `attention_loss_threshold_sec` (float, default 2.5)
- `attention_cooldown_sec` (float, default 20.0)
- `activity_target_map.storytelling` (list, default ["book", "robot"])
- `activity_target_map.mood_check_teddy` (list, default ["teddy bear", "robot"])
- `activity_target_map.computer_emotion_recognition` (list, default ["laptop", "computer"])
- `activity_target_map.generic_child_activity` (list, default ["robot"])

### mission_orchestrator_node

- `gaze_estimation.enabled` (bool, default true)
- `gaze_estimation.camera_index` (int, default 0)
- `gaze_estimation.device` (string, default "auto")
- `gaze_estimation.show_window` (bool, default true)
- `gaze_estimation.startup_timeout_sec` (float, default 30.0)

### voice_llm_node

- `attention_recall_llm_timeout` (float, default 4.0)
- `attention_recall_max_tokens` (int, default 30)

---

## Fix post-integrazione (2026-06-19, secondo giro)

### Problema 1 — Logging diagnostico in attention_monitor_node

File: `src/gaze_estimation/gaze_estimation/attention_monitor_node.py`

- Aggiunto log DEBUG nei callback `/gaze/status` e `/hri/dialog_state`
  per diagnosticare ricezione messaggi e stato armed/disarmed
- Log DEBUG su `/gaze/status`: primi 3 messaggi + ogni 100esimo (non
  rumoroso)
- Log INFO quando il contatore di disattenzione supera meta' soglia
  (es. 1.25s su soglia 2.5s), per verificare accumulo prima dell'alert
- Flag `_half_threshold_logged` per evitare log ripetuti nello stesso
  intervallo di disattenzione
- Messaggio di avvio mostra i valori di threshold e cooldown configurati

### Problema 2 — Timeout startup gaze troppo corto

File: `src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py`

- Default di `gaze_estimation.startup_timeout_sec` portato da 8.0 a 30.0
  nel codice Python (il YAML resta a 120.0 per il setup corrente)

### Problema 3 — Fail-fast su fallimento gaze all'avvio

File: `src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py`

- Aggiunto stato `MISSION_FAILED` alla macchina a stati `MissionState`
- Se gaze non diventa READY entro il timeout, la missione transiziona a
  `MISSION_FAILED` e si ferma (non prosegue a CHILD_ACTIVITY)
- Lo stop manuale via `/mission/gaze_command` resta non-bloccante: se
  l'operatore ferma gaze durante CHILD_ACTIVITY, la missione continua

### Problema 4 — Dipendenze Python nel container (diagnosi)

Diagnosi completata, in attesa di conferma per implementazione:

- Il container usa `--rm` (ricreato da zero ogni volta)
- Le dipendenze `gaze_estimation` (torch, torchvision, timm, ultralytics,
  ftfy) non sono nel Dockerfile e si perdono ad ogni riavvio
- I pesi DINOv2 (~350MB via torch.hub) si perdono con il container
- Soluzione proposta: aggiungere pip install al Dockerfile e opzionalmente
  pre-scaricare pesi DINOv2 in build time

---

## Fix audio output TTS + diagnostica attention (2026-06-19, terzo giro)

### Causa radice del mancato triggering dell'attention alert

La fase `speaking` del robot durava ~27ms invece di secondi reali perche'
`espeak-ng` non trovava un device audio di output funzionante: mancava
`pulseaudio-utils` (`libpulse0`) nel container, e il socket PulseAudio
dell'host era montato ma la cookie di autenticazione no. Risultato:
`espeak-ng` usciva con codice 0 senza produrre audio e senza errore.
Il codice TTS (`subprocess.run`) era gia' bloccante — il problema era
puramente infrastrutturale.

### Fix applicati

#### `dockerfiles/Dockerfile`

- Aggiunto `pulseaudio-utils` ai pacchetti APT per HRI audio

#### `start_tiago.sh`

- Aggiunto mount della cookie PulseAudio
  (`~/.config/pulse/cookie:/root/.config/pulse/cookie:ro`) nei
  PULSE_ARGS, necessario per autenticazione verso il server PA dell'host

#### `src/empower_hri/empower_hri/text_to_speech.py`

- Aggiunto log della durata reale della riproduzione TTS:
  `"TTS playback took X.Xs (backend='system')"`

#### `src/gaze_estimation/gaze_estimation/attention_monitor_node.py`

- Log dei primi 3 messaggi `/gaze/status` ricevuti portati a livello INFO
  (erano DEBUG, invisibili nei log normali)
- Log ogni 200 messaggi a livello INFO per heartbeat diagnostico
- Log Armed/Disarmed portati a livello INFO

### Risultato atteso

Con audio funzionante, la fase `speaking` durera' 2-5 secondi per frase.
L'attention_monitor si arma durante `speaking`, accumula tempo di
disattenzione, e dopo 2.5s pubblica `/hri/attention_alert`. Il
voice_llm_node lo consuma a fine turno e pronuncia la frase di richiamo.

### Comandi di test utili nel container

Verifica che PulseAudio funzioni:
```bash
pactl info
```

Verifica che espeak-ng produca audio reale:
```bash
time espeak-ng -v en "Hello, this is a test"
```
(deve durare ~2 secondi e produrre suono)

---

## target_classes dinamico per attivita' (2026-06-20)

### Problema

`gaze_webcam_node` usava un set fisso di classi YOLO-World per tutte le
attivita' (es. "book, peluche, computer" sempre). Questo causava rumore
da rilevazioni irrilevanti per l'attivita' in corso.

### Soluzione

`gaze_webcam_node` sottoscrive `/hri/dialog_command`. Quando riceve
`start_child_activity`, aggiorna dinamicamente le classi YOLO-World in
base all'attivita' usando una mappa `activity_detection_classes`:

- `storytelling` -> `["book"]`
- `mood_check_teddy` -> `["teddy bear"]`
- `computer_emotion_recognition` -> `["laptop", "computer"]`
- `generic_child_activity` / non mappato -> usa le classi iniziali dal YAML

Al `stop`, ripristina le classi iniziali.

### Differenza con activity_target_map di attention_monitor

Le due mappe hanno semantiche diverse e NON sono duplicazioni:

- `activity_detection_classes` (gaze_webcam): classi fisiche che
  YOLO-World deve cercare nel frame webcam
- `activity_target_map` (attention_monitor): classi target valide per
  giudicare se il bambino sta prestando attenzione (include "robot",
  che non e' un oggetto rilevabile da YOLO nel frame webcam)

### File modificati

- `src/gaze_estimation/gaze_estimation/gaze_webcam_node.py`:
  nuovi parametri `activity_detection_classes.*`, sottoscrizione a
  `/hri/dialog_command`, aggiornamento dinamico YOLO classes
- `src/gaze_estimation/config/gaze_webcam.yaml`:
  aggiunta sezione `activity_detection_classes`

---

## Facial Emotion Recognition + integrazione LLM (2026-06-20)

### Obiettivo

Riconoscimento continuo delle emozioni del bambino durante l'attivita',
con influenza soft sul tono/contenuto delle risposte dell'LLM.

### Architettura

1. **Inferenza grezza** (`gaze_webcam_node`): EmotiEffLib (backend PyTorch,
   modello `enet_b0_8_best_vgaf`) gira nello stesso loop frame, riusando
   la face-crop gia' rilevata da Gazelle. Pubblica emozione frame-by-frame
   su `/child/emotion_status`.

2. **Smoothing temporale** (`attention_monitor_node`): sottoscrive
   `/child/emotion_status`, mantiene una finestra scorrevole (default 7s),
   calcola la moda pesata per confidenza, pubblica il risultato stabilizzato
   su `/child/emotion_status_smoothed`. Gira sempre durante CHILD_ACTIVITY,
   indipendentemente dalla fase speaking (diverso dall'attention monitor
   che si arma solo durante speaking).

3. **Consumo LLM** (`voice_llm_node`): sottoscrive
   `/child/emotion_status_smoothed`, mantiene l'ultimo valore ricevuto.
   Ad ogni turno di risposta durante CHILD_ACTIVITY, se l'emozione e'
   significativa (non neutral/unknown) e non scaduta (< 15s), aggiunge
   una nota di tono al system prompt dell'LLM. La nota non entra nella
   history narrativa.

### Gestione errori

Se EmotiEffLib non si carica, `gaze_webcam_node` continua a funzionare
normalmente per gaze/attention, pubblicando `{"emotion": "unavailable"}`
sul topic emozione. Il fallimento dell'emotion recognition non blocca
mai la missione.

### File modificati

#### `src/gaze_estimation/gaze_estimation/gaze_webcam_node.py`

- Aggiunto publisher `/child/emotion_status` (JSON: emotion, confidence,
  all_scores, timestamp)
- Caricamento EmotiEffLib in `initialize()` con fallback graceful
- Metodo `_process_emotion()`: crop volto da bbox Gazelle, inferenza
  EmotiEffLib, mapping classi (Anger->angry, Happiness->happy, ecc.;
  Contempt mappato a neutral)
- Consolidato selezione volto in `_process_frame()` per riuso tra gaze
  e emotion
- Nuovi parametri: `emotion_process_every_n_frames` (default 1),
  `emotion_model_name` (default "enet_b0_8_best_vgaf")

#### `src/gaze_estimation/gaze_estimation/attention_monitor_node.py`

- Aggiunto docstring che documenta la coesistenza di attention monitoring
  e emotion smoothing nello stesso nodo
- Sottoscrizione a `/child/emotion_status`
- Publisher `/child/emotion_status_smoothed`
- Finestra scorrevole con moda pesata per confidenza
- Log INFO quando l'emozione smoothed cambia
- Nuovo parametro: `emotion_smoothing_window_sec` (default 7.0)

#### `src/empower_hri/empower_hri/voice_llm_node.py`

- Sottoscrizione a `/child/emotion_status_smoothed`
- Metodo `_get_emotion_context()`: costruisce la nota di tono basata
  sull'emozione corrente (con check staleness)
- Nota iniettata nel system prompt ad ogni turno child activity
  (non nella history)
- Mappa `emotion_prompt_hints` configurabile da YAML
- Log INFO `[EMOTION_CONTEXT]` quando una nota viene inclusa
- Nuovi parametri: `emotion_staleness_threshold_sec` (default 15.0),
  `emotion_prompt_hints.*`

#### `dockerfiles/Dockerfile`

- `pip install --no-deps emotiefflib` (evita di tirare onnxruntime)
- Pre-cache pesi `enet_b0_8_best_vgaf` in build time

#### Config YAML

- `gaze_webcam.yaml`: aggiunti `emotion_process_every_n_frames`,
  `emotion_model_name`
- `attention_monitor.yaml`: aggiunto `emotion_smoothing_window_sec`
- `hri_cpu_english.yaml`: aggiunti `emotion_staleness_threshold_sec`,
  `emotion_prompt_hints.*`

### Nuovi topic ROS 2

| Topic | Tipo | Publisher | Schema |
|-------|------|-----------|--------|
| `/child/emotion_status` | `std_msgs/String` | `gaze_webcam_node` | `{"emotion": "happy"\|..., "confidence": float, "all_scores": {...}, "timestamp": float}` |
| `/child/emotion_status_smoothed` | `std_msgs/String` | `attention_monitor_node` | `{"emotion": "happy"\|..., "window_sec": float, "sample_count": int, "timestamp": float}` |

### Nuovi parametri

#### gaze_webcam_node

- `emotion_process_every_n_frames` (int, default 1)
- `emotion_model_name` (string, default "enet_b0_8_best_vgaf")

#### attention_monitor_node

- `emotion_smoothing_window_sec` (float, default 7.0)

#### voice_llm_node

- `emotion_staleness_threshold_sec` (float, default 15.0)
- `emotion_prompt_hints.sad` (string)
- `emotion_prompt_hints.angry` (string)
- `emotion_prompt_hints.fear` (string)
- `emotion_prompt_hints.happy` (string)
- `emotion_prompt_hints.surprise` (string)
- `emotion_prompt_hints.disgust` (string)
- `emotion_prompt_hints.neutral` (string, vuoto)
- `emotion_prompt_hints.unknown` (string, vuoto)
