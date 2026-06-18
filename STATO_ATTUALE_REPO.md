# Stato attuale della repo ElectiveAI

Questo documento descrive lo stato attuale del codice presente nella repo, come sono organizzate le varie parti e che cosa il robot puo' fare realmente oggi. La parte piu' importante da tenere a mente e' questa:

```text
La missione completa e' un MVP orchestrato con due input della terapeuta.
Il primo input definisce il task fisico (passato a EMPOWER).
Il secondo input definisce l'attivita' da fare col bambino.
L'attivita' viene normalizzata in un tipo (storytelling, emotion recognition, ecc.)
che determina il system prompt e l'apertura della sessione col bambino.
La struttura globale della missione rimane sequenziale e hardcoded.
```

La terapeuta fornisce due comandi vocali separati: prima il task fisico (es. "Take the book"), poi l'attivita' col bambino (es. "Do emotion recognition with the computer"). Il robot esegue EMPOWER sul task fisico, poi va dal bambino e avvia l'attivita' specificata.

## Mappa generale della repo

La repo contiene tre blocchi principali:

```text
src/empower_ros2/
  Adattamento ROS 2 della pipeline EMPOWER:
  capture scena, planning simbolico, visualizzazione, navigazione Nav2,
  orchestrazione della missione completa.

src/empower_hri/
  Nodo HRI locale:
  input voce/testo, STT, LLM locale via Ollama, TTS,
  modalita' comando terapeuta e storytelling con bambino.

src/gaze_estimation/
  Package standalone per stimare se una persona guarda un oggetto target
  da webcam, usando Gazelle + YOLO-World.
```

Altri elementi importanti:

```text
exchange/
  World Gazebo, mappe Nav2, modelli custom, RViz.

empower_data/
  Output runtime della pipeline EMPOWER:
  immagini, depth, point cloud, detection, planning.

models/
  Modelli locali, ad esempio Piper/TTS e gaze estimation.

docker/ e dockerfiles/
  Entrypoint e Dockerfile per ambiente TIAGo/Gazebo/ROS 2.

README.md
  Istruzioni operative principali.

STATO_ARTE_CODICE.md (rimosso)
  Sostituito da questo documento.
```

## Package `empower_ros2`

`empower_ros2` e' il cuore robotico della repo. Espone vari eseguibili ROS 2 definiti in `src/empower_ros2/setup.py`:

```text
scene_capture_node
models_cacher_node
plan_generator_node
export_reports
color_pcl_node
marker_centroids_node
ground_truth_localizer_node
low_level_execution_node
mission_orchestrator_node
```

### Config principali

I file piu' importanti sono:

```text
src/empower_ros2/config/empower_person_table_child.yaml
src/empower_ros2/config/hri_empower_mission.yaml
src/empower_ros2/config/nav2_tiago_ground_truth.yaml
src/empower_ros2/config/object_simulation.yaml
```

`empower_person_table_child.yaml` configura il use case EMPOWER:

```text
use_case: person_table_child_task
task_description: "Take the book from the table"
output_root: /root/exchange/empower_data
topic RGB/depth/camera info
topic point cloud e marker
parametri Nav2
```

`hri_empower_mission.yaml` configura la missione completa:

```text
therapist_pose: [0.0, -1.6, -1.5708]
spawn_pose:     [0.0,  0.0,  0.0]
child_pose:     [0.0,  1.6,  1.5708]

wait_for_task_timeout_sec: 300.0
wait_for_activity_timeout_sec: 300.0
task_ack_text: "Okay, I understood the task."
activity_ack_text: "Okay, I understood the activity."
default_activity_type: "generic_child_activity"
capture_before_empower: true
execute_empower_plan: true
storytelling_timeout_sec: 300.0
child_activity_timeout_sec: 300.0
```

Queste pose sono fondamentali: nella missione completa il robot va dalla terapeuta, torna allo spawn, esegue EMPOWER e poi va sempre alla `child_pose`.

## Pipeline EMPOWER in ROS 2

La pipeline EMPOWER adattata a ROS 2 e' composta da piu' step.

### 1. Scene capture

Nodo:

```text
src/empower_ros2/python/empower_ros2/scene_capture_node.py
```

Il nodo ascolta:

```text
/head_front_camera/rgb/image_raw
/head_front_camera/depth/image_raw
/head_front_camera/depth/camera_info
```

Quando ha ricevuto RGB, depth e camera info, salva:

```text
empower_data/images/<use_case>/scan.jpg
empower_data/output/<use_case>/depth.npy
empower_data/output/<use_case>/depth_pointcloud.pcd
empower_data/output/<use_case>/camera_info.pkl
empower_data/output/<use_case>/camera_info.json
```

Questa e' la fotografia della scena che poi viene data a EMPOWER.

### 2. Model cacher

Nodo:

```text
src/empower_ros2/python/empower_ros2/models_cacher_node.py
```

Carica i modelli pesanti una volta sola:

```text
YOLO-World
EfficientViT-SAM
spaCy
word2vec
```

Poi rimane in ascolto su socket TCP locale:

```text
127.0.0.1:10000
```

Quando riceve un payload tipo:

```json
{
  "command": "detection",
  "task_description": "Take the book from the table"
}
```

avvia la pipeline EMPOWER originale tramite `detection.py`.

### 3. Planning simbolico

Nodo:

```text
src/empower_ros2/python/empower_ros2/plan_generator_node.py
```

Questo nodo non pianifica direttamente. Fa da adapter:

1. legge `task_description`;
2. invia il trigger al `models_cacher_node`;
3. aspetta che venga creato `planning.txt`;
4. converte il testo in `planning.json`;
5. genera report leggibili.

Output principali:

```text
empower_data/output/<use_case>/planning.txt
empower_data/output/<use_case>/planning.json
empower_data/output/<use_case>/planning.pkl
empower_data/output/<use_case>/empower_report.txt
```

### 4. Agent LLM EMPOWER

File:

```text
src/empower_ros2/python/agents.py
src/empower_ros2/python/detection.py
```

`detection.py` prende l'immagine `scan.jpg`, la converte in base64 e crea un oggetto `Agents`.

`Agents.multi_agent_vision_planning()` usa GPT-4o per:

1. descrivere le relazioni rilevanti nella scena;
2. produrre una descrizione utile per il task;
3. generare un piano simbolico.

Il prompt chiede al modello di usare solo queste azioni:

```text
NAVIGATE
GRAB
DROP
PULL
PUSH
```

Esempio di piano possibile:

```text
NAVIGATE to the table
GRAB book
NAVIGATE to the child
DROP book near child
```

Attenzione: questo piano e' simbolico. Non significa che tutte le azioni siano implementate fisicamente.

### 5. Parsing del piano

File:

```text
src/empower_ros2/python/empower_ros2/plan_parser.py
```

Riconosce righe che iniziano con:

```text
NAVIGATE
GRAB
DROP
PULL
PUSH
```

e le trasforma in una lista JSON di step.

Per esempio:

```text
NAVIGATE to the table
GRAB book
```

diventa:

```json
[
  {
    "step": 1,
    "action": "NAVIGATE",
    "description": "NAVIGATE to the table",
    "target": "the table"
  },
  {
    "step": 2,
    "action": "GRAB",
    "description": "GRAB book",
    "target": "book"
  }
]
```

### 6. Point cloud colorata e marker

Nodi:

```text
color_pcl_node
marker_centroids_node
```

`color_pcl_node` usa le maschere di detection per colorare la point cloud e pubblicarla su:

```text
/empower/colored_pointcloud
```

`marker_centroids_node` calcola centroidi e label degli oggetti rilevati e pubblica:

```text
/pcl_centroids
/pcl_maximum
/pcl_names
```

Questi marker servono all'esecutore per sapere dove si trova un oggetto come `book`, `table`, ecc.

### 7. Low-level execution

Nodo:

```text
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
```

Questo e' il punto in cui si vede chiaramente cosa il robot puo' fare davvero oggi.

Azioni riconosciute:

```text
NAVIGATE
GRAB
DROP
PUSH
PULL
```

Azioni implementate realmente:

```text
NAVIGATE  (navigazione Nav2)
GRAB      (simulata in Gazebo)
DROP      (simulata in Gazebo)
```

Azioni non implementate:

```text
PUSH
PULL
```

#### NAVIGATE

Quando il piano contiene `NAVIGATE`, il nodo:

1. cerca il target nei marker EMPOWER;
2. prende il centroide dell'oggetto;
3. calcola una posa a distanza `standoff_distance`;
4. orienta TIAGo verso il target;
5. manda un goal Nav2 a `navigate_to_pose`.

Se il piano non contiene alcun step NAVIGATE, il nodo salta l'attesa dei centroidi EMPOWER.

#### GRAB (simulazione Gazebo)

Quando il piano contiene `GRAB` e `object_simulation.enabled` e' `true`:

1. normalizza il nome del target (es. "the book" -> "book");
2. verifica che il target sia nella lista degli oggetti simulati;
3. verifica che il robot non stia gia' tenendo un oggetto;
4. elimina l'entita' Gazebo corrispondente tramite `gz model -d`;
5. salva l'oggetto come `held_object` (con la SDF XML cachata all'avvio).

Oggetti simulati configurati:

```text
book     -> clear_red_book_on_right_table
computer -> laptop_computer_on_right_table
peluche  -> teddy_bear_on_right_table
```

Se `object_simulation.enabled` e' `false`, GRAB viene saltato con un log.

#### DROP (simulazione Gazebo)

Quando il piano contiene `DROP` e `object_simulation.enabled` e' `true`:

1. normalizza il nome del target;
2. verifica che il robot stia tenendo quell'oggetto;
3. calcola la posa di drop davanti al robot (usando TF);
4. ri-spawna l'entita' Gazebo nella nuova posizione tramite `gz model -f`;
5. resetta `held_object` a `None`.

La distanza di drop e' configurabile tramite `object_simulation.drop_distance` (default 0.75m).

#### Configurazione object simulation

File:

```text
src/empower_ros2/config/object_simulation.yaml
src/empower_ros2/config/empower_person_table_child.yaml (sezione object_simulation)
```

Parametri principali:

```text
object_simulation.enabled: true/false
object_simulation.drop_distance: 0.75
object_simulation.world_frame: world
object_simulation.robot_base_frame: base_footprint
object_simulation.gz_command: gz
object_simulation.gz_world_name: person_table_child_room
object_simulation.world_sdf_path: percorso al file .world
object_simulation.objects.<nome>.gazebo_entity: nome entita' Gazebo
object_simulation.objects.<nome>.drop_z: altezza di drop
```

All'avvio, il nodo parsa il file world SDF e cacha la XML di ogni modello configurato, in modo da poterlo ri-spawnare dopo un DROP.

Quando il piano contiene `PUSH` o `PULL`, il nodo scrive un warning e salta lo step.

## Missione completa HRI + EMPOWER

Launch principale:

```text
src/empower_ros2/launch/hri_empower_mission.launch.py
```

Questo launch avvia:

```text
models_cacher_node
voice_llm_node
mission_orchestrator_node
```

Il nodo centrale e':

```text
src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py
```

### Macchina a stati

La missione e' una sequenza hardcoded:

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
```

Descrizione:

```text
GO_TO_THERAPIST
  Il robot naviga alla posa della terapeuta.

WAIT_TASK
  Il robot aspetta il primo testo su /hri/user_utterance.
  Questo diventa il task fisico (es. "Take the book").

ACK_TASK
  Il robot pronuncia task_ack_text ("Okay, I understood the task.")
  tramite il comando "say" inviato al nodo HRI.

WAIT_ACTIVITY
  Il robot aspetta il secondo testo su /hri/user_utterance.
  Questo diventa la descrizione dell'attivita' col bambino
  (es. "Do emotion recognition with the computer").
  Il testo viene normalizzato in un activity_type.

ACK_ACTIVITY
  Il robot pronuncia activity_ack_text ("Okay, I understood the activity.")

GO_TO_SPAWN
  Il robot torna allo spawn.

RUN_EMPOWER
  Viene fatta la capture della scena.
  Viene lanciato EMPOWER planning.
  Se execute_empower_plan=true, viene lanciato low_level_execution_node.

GO_TO_CHILD
  Il robot naviga alla posa del bambino/ragazzo.

CHILD_READY
  Pubblica child_ready su /mission/child_ready.

CHILD_ACTIVITY
  Invia start_child_activity al nodo HRI con il contesto completo
  (task, activity_description, activity_type).

MISSION_COMPLETE
  La missione termina.
```

### Normalizzazione dell'attivita'

L'orchestratore normalizza il testo della terapeuta in un `activity_type` usando regole keyword-based:

```text
"computer emotion recognition", "emotion recognition", "emotion pictures", ...
  -> computer_emotion_recognition

"how they feel", "feelings", "mood", "teddy", "teddy bear", "plush", ...
  -> mood_check_teddy

"story", "storytelling", "read the story", "tell a story"
  -> storytelling

(nessun match)
  -> generic_child_activity (default)
```

L'`activity_type` determina quale system prompt e quale frase di apertura il nodo HRI usera' col bambino.

### Topic della missione

```text
/mission/state
/mission/feedback
/mission/task_description
/mission/child_ready
```

Topic HRI usati dall'orchestratore:

```text
/hri/user_utterance
  HRI -> orchestratore.
  Contiene il testo detto dalla terapeuta o dal bambino.

/hri/dialog_command
  orchestratore -> HRI.
  Serve a dire al nodo HRI di iniziare o fermare lo storytelling.

/hri/dialog_complete
  HRI -> orchestratore.
  Segnala che la sessione di storytelling e' finita.
```

### Punto cruciale: missione ancora sequenziale

La struttura della missione rimane hardcoded e sequenziale: la terapeuta fornisce sempre due input (task fisico + attivita' col bambino), poi il robot esegue sempre EMPOWER, poi va sempre dal bambino e avvia sempre un'attivita'.

Il miglioramento rispetto alla versione precedente e' che ora la terapeuta puo' scegliere quale tipo di attivita' fare col bambino, e il robot adatta il system prompt e l'apertura della sessione.

Esempi:

```text
Input 1: "Take the book"
Input 2: "Tell a story"
-> EMPOWER su "Take the book", poi storytelling col bambino

Input 1: "Take the computer"
Input 2: "Do emotion recognition with the computer"
-> EMPOWER su "Take the computer", poi emotion recognition col bambino

Input 1: "Take the teddy bear"
Input 2: "Ask the child how they feel"
-> EMPOWER su "Take the teddy bear", poi mood check col bambino
```

Ma il flusso globale rimane sempre:

```text
terapeuta -> task + activity -> spawn -> EMPOWER -> child_pose -> child_activity
```

Non esiste una logica che decide autonomamente se andare o meno dal bambino, o se saltare l'attivita'.

## Package `empower_hri`

`empower_hri` contiene l'interazione vocale/testuale.

Eseguibile principale:

```text
voice_llm_node
```

File principale:

```text
src/empower_hri/empower_hri/voice_llm_node.py
```

File di supporto:

```text
ollama_client.py
speech_to_text.py
text_to_speech.py
terminal_io.py
```

### Modalita' del nodo HRI

Il nodo ha due modalita':

```text
therapist_command
child_activity
```

All'avvio parte in:

```text
therapist_command
```

In questa modalita':

1. legge testo o voce dalla terapeuta;
2. pubblica il testo su `/hri/user_utterance`;
3. se `therapist_llm_replies` e' `true`, manda il testo al LLM locale e pubblica/pronuncia la risposta;
4. se `therapist_llm_replies` e' `false`, pubblica solo il testo senza risposta LLM.

Nel launch della missione completa, `therapist_llm_replies` e' `false` e `follow_mission_state_for_therapist_input` e' `true`. Questo significa che:

- il nodo HRI accetta input dalla terapeuta solo quando lo stato missione e' `WAIT_TASK` o `WAIT_ACTIVITY`;
- ogni stato accetta un solo input (il nodo traccia `_last_therapist_input_state`);
- il robot non risponde con il LLM alla terapeuta, ma pronuncia solo gli `ack_text` configurati tramite il comando `say`.

Il nodo HRI ascolta anche `/mission/state` per sapere in che fase si trova la missione.

#### Comando `say`

L'orchestratore puo' inviare un comando `say` tramite `/hri/dialog_command`:

```json
{
  "command": "say",
  "text": "Okay, I understood the task."
}
```

Il nodo HRI pronuncia il testo via TTS senza coinvolgere il LLM.

### STT

File:

```text
src/empower_hri/empower_hri/speech_to_text.py
```

Provider supportati:

```text
faster_whisper
mock
```

`faster_whisper` e' CPU-only in questo MVP. La registrazione puo' avvenire in due modi:

```text
push-to-talk
  Premi INVIO, registra per alcuni secondi, trascrive.

auto listen / VAD
  Usato nello storytelling se storytelling_auto_listen=true.
  Rileva energia audio, registra finche' c'e' voce,
  poi trascrive.
```

### LLM locale

File:

```text
src/empower_hri/empower_hri/ollama_client.py
```

Provider:

```text
ollama
mock
```

Con Ollama usa l'API locale:

```text
http://localhost:11434/api/chat
```

Modelli configurabili dai YAML HRI, per esempio:

```text
qwen2.5:1.5b
llama3.2:1b come fallback
```

### TTS

File:

```text
src/empower_hri/empower_hri/text_to_speech.py
```

Provider supportati:

```text
piper
pyttsx3
system
mock
```

Ha fallback progressivi. Per esempio, se `piper` non funziona, prova `pyttsx3`, poi tool di sistema come `espeak-ng`, poi `mock`.

## Interazione con il bambino

Questa e' la parte piu' delicata della repo.

L'interazione con il bambino non nasce da EMPOWER. Nasce dall'orchestratore, dopo `GO_TO_CHILD`.

### Come parte

Dopo EMPOWER:

```text
mission_orchestrator_node:
  GO_TO_CHILD
  CHILD_READY
  CHILD_ACTIVITY
```

In `GO_TO_CHILD` il robot naviga alla posa fissa `child_pose`.

Poi pubblica:

```text
/mission/child_ready = "child_ready"
```

Poi manda su `/hri/dialog_command`:

```json
{
  "command": "start_child_activity",
  "context": {
    "task": "<task fisico>",
    "activity_description": "<descrizione attivita'>",
    "activity_type": "<tipo normalizzato>"
  }
}
```

Esempio:

```json
{
  "command": "start_child_activity",
  "context": {
    "task": "Take the computer",
    "activity_description": "Do emotion recognition with the computer",
    "activity_type": "computer_emotion_recognition"
  }
}
```

### Cosa fa `voice_llm_node` quando riceve `start_child_activity`

Quando arriva il comando:

```text
command == "start_child_activity"
```

il nodo HRI:

1. legge `activity_type` dal contesto (se non valido, usa `generic_child_activity`);
2. salva il contesto completo (task, activity_description, activity_type);
3. svuota la history del dialogo precedente;
4. resetta lo stop event;
5. cambia modalita' in `child_activity`.

Da quel momento il system prompt cambia in base all'`activity_type`.

Il comando `start_storytelling` e' ancora supportato per retrocompatibilita', e viene convertito internamente in `start_child_activity` con `activity_type=storytelling`.

### System prompt per tipo di attivita'

Tipi supportati:

```text
computer_emotion_recognition
  Il robot guida il bambino nel riconoscimento di emozioni da foto
  mostrate su un computer. Usa linguaggio semplice, offre scelte
  (happy, sad, angry, scared, surprised), da' incoraggiamento.

mood_check_teddy
  Il robot usa un peluche come intermediario per parlare col bambino
  dei suoi sentimenti. Domande semplici e non pressanti.

storytelling
  Il robot legge una breve storia da un libro immaginario.
  Racconta la storia in piccole parti, coinvolgendo il bambino
  con domande occasionali.

generic_child_activity
  Prompt generico che segue la descrizione dell'attivita'
  fornita dalla terapeuta.
```

Ogni tipo ha un system prompt specifico e dettagliato definito in `build_child_activity_system_prompt()`.

### Apertura automatica dell'attivita'

Quando inizia la sessione, il robot non aspetta subito il bambino.

Il nodo HRI usa una frase di apertura hardcoded (non generata dal LLM) specifica per ogni `activity_type`:

```text
computer_emotion_recognition:
  "Let's look at the pictures on the computer together.
   What emotion do you see in this picture: happy, sad, angry, or scared?"

mood_check_teddy:
  "This teddy bear would like to know how you feel today.
   Are you happy, sad, tired, calm, or something else?"

storytelling:
  "I found a book with many little stories inside.
   I will choose one and read it with you. Are you ready to begin?"

generic_child_activity:
  "Let's do this activity together: <activity_description>.
   Would you like to start?"
```

Poi:

1. aggiunge `[START_ACTIVITY]` e la frase di apertura alla history;
2. pubblica la frase su `/hri/robot_utterance`;
3. la pronuncia via TTS;
4. entra nel loop interattivo.

### Loop interattivo col bambino

Il loop e':

```text
ascolta il bambino
trascrive la risposta
pubblica su /hri/user_utterance
manda al LLM system prompt + history + risposta bambino
genera risposta
pubblica su /hri/robot_utterance
pronuncia via TTS
ripete
```

Il robot deve:

```text
rispondere in inglese;
mantenere continuita' della storia;
fare risposte brevi;
chiudere ogni turno con una nuova domanda;
coinvolgere il bambino.
```

### Quando finisce

La sessione termina quando:

```text
raggiunge storytelling_max_turns
oppure arriva un comando stop
oppure l'utente dice /quit o exit
oppure si verifica una chiusura/errore
```

Alla fine pubblica:

```text
/hri/dialog_complete
```

con un payload tipo:

```json
{
  "reason": "max_turns",
  "turns": 10,
  "activity_type": "computer_emotion_recognition"
}
```

L'orchestratore aspetta questo messaggio. Quando lo riceve, passa a:

```text
MISSION_COMPLETE
```

### Come viene scelta l'attivita' col bambino

Nello stato attuale, l'attivita' col bambino non e' derivata autonomamente dal task fisico. E' la terapeuta a specificarla esplicitamente come secondo input vocale.

Il testo della terapeuta viene normalizzato in un `activity_type` tramite regole keyword-based nell'orchestratore. Non c'e' un LLM che interpreta il comando; se nessuna keyword matcha, il tipo default e' `generic_child_activity`.

L'attivita' avviene sempre dopo EMPOWER. La missione non decide autonomamente se andare o meno dal bambino.

## Azioni che il robot puo' fare oggi

### Azioni fisiche realmente implementate

```text
1. Navigare a pose fisse
   - terapeuta
   - spawn
   - bambino/ragazzo

2. Navigare verso oggetti rilevati da EMPOWER
   - solo se il piano contiene NAVIGATE
   - solo se il target viene associato a un marker/centroide
   - tramite Nav2 navigate_to_pose

3. GRAB (simulato in Gazebo)
   - elimina l'entita' Gazebo dell'oggetto dalla scena
   - supporta book, computer, peluche
   - richiede object_simulation.enabled=true

4. DROP (simulato in Gazebo)
   - ri-spawna l'entita' Gazebo davanti al robot
   - usa la posa TF corrente del robot per calcolare dove spawnare
   - richiede object_simulation.enabled=true

5. Parlare
   - TTS via piper/pyttsx3/system/mock
   - pubblicazione su /hri/robot_utterance
   - pronuncia di testi hardcoded tramite comando "say"

6. Ascoltare
   - input testuale
   - push-to-talk
   - ascolto automatico VAD nelle attivita' col bambino

7. Condurre attivita' interattive col bambino
   - computer_emotion_recognition
   - mood_check_teddy
   - storytelling
   - generic_child_activity
```

### Azioni simboliche ma non fisiche

```text
PUSH
PULL
```

Sono azioni che possono apparire nel piano EMPOWER, ma il robot non le esegue. GRAB e DROP sono ora simulati in Gazebo (vedi sopra).

### Azioni non presenti come primitive autonome

```text
find_person
give_object_to_person (DROP e' in simulazione, non consegna reale)
wait_for_confirmation
bring_object_to_therapist
choose_next_goal_from_task
```

Il robot va dal bambino e avvia un'attivita' perche' la missione e' hardcoded per farlo. Non esiste una primitiva `talk_to_person` o `start_activity` nel piano EMPOWER.

## Package `gaze_estimation`

Questo package e' separato dalla missione HRI+EMPOWER.

Eseguibile:

```text
gaze_webcam
```

File principale:

```text
src/gaze_estimation/gaze_estimation/gaze_webcam_node.py
```

Config:

```text
src/gaze_estimation/config/gaze_webcam.yaml
```

Funzionamento:

1. apre una webcam;
2. carica Gazelle per la stima dello sguardo;
3. carica un detector face YOLO;
4. carica YOLO-World per oggetti target, default `book`;
5. per ogni frame rileva volto, gaze point/heatmap e oggetti;
6. decide se lo sguardo cade dentro la bbox dell'oggetto o se abbastanza massa della heatmap cade sull'oggetto;
7. mostra overlay debug e logga stati.

Stati possibili:

```text
NO_FRAME
NO_FACE
GAZE_OUT_OF_FRAME
NO_TARGET_OBJECT
LOOKING_AT_OBJECT: <class_name>
LOOKING_ELSEWHERE
```

Oggi non risulta collegato alla missione completa. Non viene usato per decidere se il bambino guarda un oggetto o se e' coinvolto nell'attivita'.

## Launch principali

### Missione completa

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py
```

Avvia:

```text
models_cacher_node
voice_llm_node (con therapist_llm_replies=false, follow_mission_state_for_therapist_input=true)
mission_orchestrator_node
```

### Solo HRI

```bash
ros2 launch empower_hri hri_voice_llm_english.launch.py
ros2 launch empower_hri hri_voice_llm_cpu.launch.py
```

### EMPOWER singoli step

```bash
ros2 launch empower_ros2 empower_capture.launch.py
ros2 launch empower_ros2 empower_models_cacher.launch.py
ros2 launch empower_ros2 empower_plan.launch.py
ros2 launch empower_ros2 empower_execute.launch.py
ros2 launch empower_ros2 empower_visualization.launch.py
```

### Nav2 TIAGo ground truth

```bash
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py
```

### Gaze estimation

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py
```

## Dipendenze importanti

Per EMPOWER:

```text
OpenAI API key
YOLO-World
EfficientViT-SAM
spaCy
word2vec
OpenCV
Open3D
ROS 2 Humble
Nav2
TIAGo simulation
```

Per HRI:

```text
Ollama
faster-whisper
sounddevice
soundfile
pyttsx3 / espeak-ng / piper
```

Per gaze estimation:

```text
torch
ultralytics
Gazelle checkpoint
YOLO-World checkpoint
face detector checkpoint
OpenCV
```

## Limiti attuali principali

### 1. Missione globale ancora sequenziale

La missione ha ora due input della terapeuta (task + attivita'), ma la struttura rimane hardcoded. Dopo EMPOWER il robot va sempre dal bambino e avvia sempre un'attivita'. Non esiste la possibilita' di saltare fasi o riordinarle in base al contesto.

### 2. Normalizzazione attivita' a keyword

La normalizzazione dell'attivita' in `activity_type` e' basata su keyword hardcoded. Se la terapeuta usa frasi non previste, il tipo sara' `generic_child_activity`. Non c'e' un LLM che interpreta il comando.

### 3. Manipolazione solo simulata

GRAB e DROP sono implementati solo come simulazione Gazebo (elimina/ri-spawna entita'). Il robot non usa braccio o gripper fisici. La manipolazione e' visiva nella simulazione ma non corrisponde a un'azione fisica reale.

### 4. Oggetti simulati limitati

Solo tre oggetti sono configurati per la simulazione:

```text
book, computer, peluche
```

Aggiungere nuovi oggetti richiede: modificare la config YAML, avere il modello nel file world SDF, e aggiungere il nome alla tupla `SIMULATED_OBJECTS` nel codice.

### 5. Gaze estimation non integrata

La repo contiene un package per stimare lo sguardo, ma non e' collegato al nodo HRI o alla missione.

### 6. Feedback vocale della missione parziale

L'orchestratore ora puo' inviare testi hardcoded via il comando `say` (usato per gli ACK), ma non c'e' un meccanismo generico per aggiornare vocalmente la terapeuta sullo stato della missione in tempo reale.

### 7. Dipendenza da modelli esterni

EMPOWER usa GPT-4o via OpenAI. HRI usa Ollama locale. Queste parti richiedono setup corretto e modelli disponibili.

## Cosa sarebbe necessario per migliorare

### Missione adattiva

Per rendere la missione non sequenziale, servirebbe un orchestratore che decida dinamicamente le fasi in base agli input. Per esempio:

```text
Se la terapeuta chiede solo un task fisico senza attivita' col bambino:
  esegui EMPOWER
  torna dalla terapeuta
  non andare dal bambino

Se la terapeuta chiede solo un'attivita' col bambino senza task fisico:
  vai direttamente dal bambino
  salta EMPOWER
```

### Normalizzazione attivita' con LLM

Sostituire le keyword hardcoded con un LLM che interpreti il comando della terapeuta e determini l'`activity_type`, gestendo anche frasi ambigue o non previste.

### Manipolazione fisica

Implementare GRAB e DROP reali tramite MoveIt e il gripper di TIAGo, mantenendo la simulazione Gazebo come fallback per testing.

## Riassunto operativo

Stato attuale:

```text
Il robot puo' navigare (Nav2).
Il robot puo' afferrare e rilasciare oggetti (simulazione Gazebo: book, computer, peluche).
Il robot puo' parlare (TTS + testi hardcoded).
Il robot puo' ascoltare (STT + input testuale).
Il robot puo' condurre attivita' interattive col bambino:
  - computer emotion recognition
  - mood check con peluche
  - storytelling
  - attivita' generica
Il robot puo' generare piani simbolici EMPOWER (via GPT-4o).
La terapeuta fornisce due input separati: task fisico e attivita' col bambino.
```

Ma:

```text
La manipolazione e' solo simulata (no braccio/gripper reale).
La missione e' ancora sequenziale e hardcoded.
La normalizzazione dell'attivita' e' a keyword, non interpretata da LLM.
Il robot va sempre dal bambino e avvia sempre un'attivita' dopo EMPOWER.
Il robot non decide autonomamente se saltare fasi della missione.
Solo tre oggetti sono configurati per la simulazione.
PUSH e PULL non sono implementati.
```

La repo e' una base integrata HRI + EMPOWER + Nav2 + simulazione manipolazione, con supporto per attivita' diverse col bambino. L'autonomia semantica della missione e la manipolazione fisica reale sono ancora da implementare.
