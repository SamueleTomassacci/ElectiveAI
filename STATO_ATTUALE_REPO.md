# Stato attuale della repo ElectiveAI

Questo documento descrive lo stato attuale del codice presente nella repo, come sono organizzate le varie parti e che cosa il robot puo' fare realmente oggi. La parte piu' importante da tenere a mente e' questa:

```text
La missione completa e' ancora un MVP orchestrato.
Il task vocale cambia la descrizione passata a EMPOWER,
ma non cambia la struttura globale della missione.
```

In particolare, dopo la fase EMPOWER il robot va sempre verso la posa configurata del bambino/ragazzo e avvia sempre una sessione di storytelling, indipendentemente dal fatto che la terapeuta abbia detto o meno "talk to the guy".

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

STATO_ARTE_CODICE.md
  Documento precedente di analisi estesa dello stato del codice.
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
capture_before_empower: true
execute_empower_plan: true
storytelling_timeout_sec: 300.0
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
NAVIGATE
```

Azioni non implementate:

```text
GRAB
DROP
PUSH
PULL
```

Quando il piano contiene `NAVIGATE`, il nodo:

1. cerca il target nei marker EMPOWER;
2. prende il centroide dell'oggetto;
3. calcola una posa a distanza `standoff_distance`;
4. orienta TIAGo verso il target;
5. manda un goal Nav2 a `navigate_to_pose`.

Quando il piano contiene `GRAB`, `DROP`, `PUSH` o `PULL`, il nodo scrive un log e salta lo step:

```text
Skipping GRAB book: action not implemented in ROS2 port
```

Quindi oggi il robot non prende davvero il libro, non lo lascia, non spinge e non tira oggetti.

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
GO_TO_SPAWN
RUN_EMPOWER
GO_TO_CHILD
CHILD_READY
STORYTELLING
MISSION_COMPLETE
```

Descrizione:

```text
GO_TO_THERAPIST
  Il robot naviga alla posa della terapeuta.

WAIT_TASK
  Il robot aspetta un testo su /hri/user_utterance.
  Il primo testo utile diventa il task della missione.

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

STORYTELLING
  Invia start_storytelling al nodo HRI.

MISSION_COMPLETE
  La missione termina.
```

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

### Punto cruciale: missione hardcoded

Il task della terapeuta viene usato come stringa, ma non modifica la struttura della missione.

Esempi:

```text
"Take the book from the table"
"Take the book and talk to the guy"
"Move near the table"
"Find the red object"
```

In tutti questi casi, il flusso globale rimane:

```text
terapeuta -> task -> spawn -> EMPOWER -> child_pose -> storytelling
```

Quindi:

```text
Il robot va sempre dal bambino/ragazzo dopo EMPOWER.
Il robot avvia sempre lo storytelling dopo essere arrivato dal bambino/ragazzo.
```

La frase "talk to the guy" non viene interpretata come comando speciale. Non esiste oggi una logica del tipo:

```text
se il task contiene "talk to the guy":
  vai dal ragazzo e parla
altrimenti:
  non fare storytelling
```

Lo storytelling parte perche' e' scritto nella sequenza della missione.

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
child_storytelling
```

All'avvio parte in:

```text
therapist_command
```

In questa modalita':

1. legge testo o voce dalla terapeuta;
2. pubblica il testo su `/hri/user_utterance`;
3. manda il testo al LLM locale;
4. pubblica la risposta su `/hri/robot_utterance`;
5. pronuncia la risposta via TTS.

Il punto importante e' che l'orchestratore cattura il task appena vede il testo su `/hri/user_utterance`. Non aspetta che il LLM HRI "approvi" o normalizzi il task.

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
  STORYTELLING
```

In `GO_TO_CHILD` il robot naviga alla posa fissa `child_pose`.

Poi pubblica:

```text
/mission/child_ready = "child_ready"
```

Poi manda su `/hri/dialog_command`:

```json
{
  "command": "start_storytelling",
  "context": {
    "task": "<task detto dalla terapeuta>"
  }
}
```

Esempio:

```json
{
  "command": "start_storytelling",
  "context": {
    "task": "Take the book and talk to the guy"
  }
}
```

### Cosa fa `voice_llm_node` quando riceve `start_storytelling`

Quando arriva il comando:

```text
command == "start_storytelling"
```

il nodo HRI:

1. salva il contesto, incluso il task;
2. svuota la history del dialogo precedente;
3. resetta lo stop event dello storytelling;
4. cambia modalita' in `child_storytelling`.

Da quel momento il system prompt cambia.

Il prompt storytelling dice al modello:

```text
You are TIAGo, a friendly social robot in a therapy session with a young child.
You have just completed the following task: '<task>'.
Your role is to tell an engaging, age-appropriate, imaginative story
and actively involve the child through warm encouragement and simple questions.
```

Quindi il task della terapeuta viene usato come contesto narrativo:

```text
Il robot "sa" linguisticamente di aver appena completato quel task.
```

Ma non viene usato per decidere se fare o non fare storytelling.

### Apertura automatica della storia

Quando inizia la sessione, il robot non aspetta subito il bambino.

Il nodo HRI manda al LLM:

```text
user: [START_STORY]
```

Il prompt istruisce il modello a:

```text
presentarsi;
iniziare una scena di storia;
finire con una domanda semplice per il bambino.
```

Poi:

1. pubblica la frase su `/hri/robot_utterance`;
2. la pronuncia via TTS;
3. entra nel loop interattivo.

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
  "turns": 10
}
```

L'orchestratore aspetta questo messaggio. Quando lo riceve, passa a:

```text
MISSION_COMPLETE
```

### Cosa significa quindi "talk to the guy"

Nello stato attuale:

```text
"talk to the guy" non e' una primitiva robotica.
```

Non viene interpretato da EMPOWER come:

```text
trova il ragazzo;
vai dal ragazzo;
inizia una conversazione generica.
```

La conversazione con il ragazzo/bambino avviene perche' la missione e' hardcoded per farlo sempre dopo EMPOWER.

Il testo "talk to the guy" puo' influenzare solo il contenuto linguistico generato dal LLM, perche' viene passato dentro:

```text
context.task
```

Quindi il robot potrebbe raccontare una storia che richiama il libro, il ragazzo o il task, ma non e' una pianificazione autonoma della conversazione.

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

3. Parlare
   - TTS via piper/pyttsx3/system/mock
   - pubblicazione su /hri/robot_utterance

4. Ascoltare
   - input testuale
   - push-to-talk
   - ascolto automatico VAD nello storytelling
```

### Azioni simboliche ma non fisiche

```text
GRAB
DROP
PUSH
PULL
```

Sono azioni che possono apparire nel piano EMPOWER, ma il robot non le esegue realmente.

### Azioni non presenti come primitive autonome

```text
talk_to_person
find_person
give_object_to_person
ask_question
wait_for_confirmation
bring_object_to_therapist
choose_next_goal_from_task
```

Alcune di queste cose avvengono in forma hardcoded o linguistica, ma non come azioni pianificate.

Per esempio:

```text
Il robot "parla col bambino" perche' parte lo storytelling.
Non perche' esista una primitiva talk_to_person nel piano.
```

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

Oggi non risulta collegato alla missione completa. Non viene usato per decidere se il bambino guarda il libro o se e' coinvolto nello storytelling.

## Launch principali

### Missione completa

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py
```

Avvia:

```text
models_cacher_node
voice_llm_node
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

### 1. Missione globale non adattiva

La missione non viene ricostruita dal task.

Qualunque task venga detto, dopo EMPOWER il robot va comunque dal bambino e avvia storytelling.

### 2. Task non normalizzato

Il testo della terapeuta viene passato cosi' com'e':

```text
/hri/user_utterance -> task_description
```

Non c'e' una fase che trasforma:

```text
"take the book and talk to the guy"
```

in qualcosa di strutturato tipo:

```json
{
  "physical_goal": "take_book",
  "social_goal": "talk_to_child",
  "target_person": "child"
}
```

### 3. Manipolazione non implementata

Il robot non usa braccio o gripper.

Quindi un task come:

```text
Take the book from the table
```

oggi puo' portare il robot a navigare vicino al libro/tavolo, ma non a prenderlo davvero.

### 4. Conversazione col bambino hardcoded come storytelling

Il robot non sceglie tra:

```text
fare domande;
spiegare il task;
fare small talk;
raccontare una storia;
aspettare una risposta specifica;
verificare attenzione;
```

Fa sempre storytelling.

### 5. Gaze estimation non integrata

La repo contiene un package per stimare lo sguardo, ma non e' collegato al nodo HRI o alla missione.

### 6. Feedback vocale della missione incompleto

`mission_orchestrator_node` pubblica `/mission/state` e `/mission/feedback`, ma `voice_llm_node` non li usa per dire vocalmente alla terapeuta cosa sta succedendo.

### 7. Dipendenza da modelli esterni

EMPOWER usa GPT-4o via OpenAI. HRI usa Ollama locale. Queste parti richiedono setup corretto e modelli disponibili.

## Cosa sarebbe necessario per supportare comandi diversi

Per rendere il robot davvero predisposto a comandi diversi, servirebbe uno strato di interpretazione mission-level prima dell'orchestratore.

Esempio desiderabile:

```text
Input terapeuta:
  "Take the book and talk to the guy"

Parser/LLM mission-level:
  physical_task: "take the book"
  social_task: "talk to the guy"
  target_person: "child"
  after_physical_task: "start_dialogue"

Orchestratore:
  esegue physical_task con EMPOWER
  se social_task presente, va dal target_person
  sceglie modalita' dialogo coerente
```

Oppure:

```text
Input terapeuta:
  "Take the book from the table"

Parser:
  physical_task: "take the book from the table"
  social_task: null

Orchestratore:
  esegue EMPOWER
  non avvia storytelling automaticamente
```

Oggi questa distinzione non esiste.

## Riassunto operativo

Stato attuale:

```text
Il robot puo' navigare.
Il robot puo' parlare.
Il robot puo' ascoltare.
Il robot puo' generare uno storytelling interattivo.
Il robot puo' generare piani simbolici EMPOWER.
```

Ma:

```text
Il robot non manipola oggetti.
Il robot non interpreta davvero task multi-step a livello missione.
Il robot non decide autonomamente quando parlare col bambino.
Il robot non usa "talk to the guy" come azione.
Il robot va sempre dal bambino/ragazzo e racconta sempre una storia dopo EMPOWER.
```

La repo e' quindi una buona base integrata HRI + EMPOWER + Nav2, ma la parte di autonomia semantica della missione e la manipolazione fisica sono ancora da implementare.
