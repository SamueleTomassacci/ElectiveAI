# Stato attuale del progetto ElectiveAI

Data del report: 2026-06-18

Questo file serve come contesto da incollare in ChatGPT o in un altro assistente
quando si devono fare modifiche al progetto. Il focus principale e' EMPOWER,
cioe' il package `empower_ros2`, perche' e' la parte su cui sono previsti i
cambiamenti.

## Sintesi breve

La repository e' una workspace ROS 2 Humble per una simulazione TIAGo in Gazebo.
Il progetto e' organizzato intorno a tre componenti principali:

1. `empower_ros2`: adattamento ROS 2 della pipeline EMPOWER. Gestisce capture
   RGB-D, perception, grounding, planning simbolico con LLM, generazione report,
   visualizzazione 3D dei rilevamenti, navigazione Nav2 e missione orchestrata
   con HRI.
2. `empower_hri`: interazione vocale/testuale locale. Usa STT con
   `faster-whisper`, LLM locale tramite Ollama/Qwen e TTS locale. Pubblica e
   riceve messaggi ROS 2 su topic `/hri/*` e viene coordinato
   dall'orchestratore missione di `empower_ros2`.
3. `gaze_estimation`: package ROS 2 standalone per stimare se una persona sta
   guardando un oggetto target usando webcam RGB, Gazelle/Gaze-LLE,
   face detector YOLO e YOLO-World. Al momento e' indipendente da EMPOWER e HRI.

La scena supportata e':

```text
exchange/person_table_child.world
exchange/maps/person_table_child_static.yaml
src/empower_ros2/config/empower_person_table_child.yaml
```

La scena contiene TIAGo, una persona/terapista sul lato `-Y`, un bambino sul
lato `+Y`, e un tavolo con oggetti sul lato `+X`. La pipeline EMPOWER assume che
TIAGo possa vedere il tavolo con la camera frontale quando parte dall'origine
guardando verso `+X`, cioe' yaw `0`.

## Struttura generale della repo

File e directory principali:

```text
README.md
README_QWEN_ENGLISH.md
README_GAZE_ESTIMATION.md
start_tiago.sh
exchange/
  person_table_child.world
  maps/person_table_child_static.yaml
  models/person_standing/
empower_data/
  images/person_table_child_task/
  output/person_table_child_task/
src/
  empower_ros2/
  empower_hri/
  gaze_estimation/
models/
  gaze_estimation/
  piper/
```

`empower_data/` contiene gli output generati dalla pipeline: immagine RGB,
depth, point cloud, detection, planning, report e file intermedi.

Attenzione: il file `openai_api_key.txt` e' usato per la chiave OpenAI ma non
deve essere incollato in report o prompt esterni.

## Componente 1: EMPOWER / `empower_ros2`

### Obiettivo di `empower_ros2`

`empower_ros2` porta nel mondo ROS 2 una pipeline EMPOWER che parte da una
snapshot RGB-D della scena e produce:

- relazioni simboliche tra oggetti;
- descrizione testuale della scena;
- piano simbolico con azioni primitive;
- bounding box e maschere degli oggetti;
- point cloud colorata e marker 3D degli oggetti;
- esecuzione low-level parziale tramite Nav2 e simulazione Gazebo.

La pipeline e' centrata su questo task configurato:

```yaml
use_case: person_table_child_task
task_description: "Take the book from the table"
output_root: /root/exchange/empower_data
```

Il file di configurazione principale e':

```text
src/empower_ros2/config/empower_person_table_child.yaml
```

### File principali di EMPOWER

Codice "upstream style" EMPOWER, installato come moduli Python top-level:

```text
src/empower_ros2/agents.py
src/empower_ros2/detection.py
src/empower_ros2/loader.py
src/empower_ros2/models.py
src/empower_ros2/paths.py
```

Gli stessi file sono duplicati in:

```text
src/empower_ros2/python/agents.py
src/empower_ros2/python/detection.py
src/empower_ros2/python/loader.py
src/empower_ros2/python/models.py
src/empower_ros2/python/paths.py
```

Al momento risultano identici alle versioni in `src/empower_ros2/`. Il
`setup.py` installa i moduli top-level tramite `py_modules`, quindi questi file
sono effettivamente importati come `agents`, `detection`, `loader`, `models`,
`paths`.

Nodi ROS 2 e adapter:

```text
src/empower_ros2/python/empower_ros2/scene_capture_node.py
src/empower_ros2/python/empower_ros2/models_cacher_node.py
src/empower_ros2/python/empower_ros2/plan_generator_node.py
src/empower_ros2/python/empower_ros2/loader_adapter.py
src/empower_ros2/python/empower_ros2/report_writer.py
src/empower_ros2/python/empower_ros2/plan_parser.py
src/empower_ros2/python/empower_ros2/color_pcl_node.py
src/empower_ros2/python/empower_ros2/marker_centroids_node.py
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
src/empower_ros2/python/empower_ros2/primitive_actions.py
src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py
src/empower_ros2/python/empower_ros2/ground_truth_localizer_node.py
```

Launch file principali:

```text
src/empower_ros2/launch/empower_capture.launch.py
src/empower_ros2/launch/empower_models_cacher.launch.py
src/empower_ros2/launch/empower_plan.launch.py
src/empower_ros2/launch/empower_visualization.launch.py
src/empower_ros2/launch/empower_execute.launch.py
src/empower_ros2/launch/hri_empower_mission.launch.py
src/empower_ros2/launch/tiago_nav2_ground_truth.launch.py
```

### Dipendenze concettuali di EMPOWER

La pipeline EMPOWER usa:

- ROS 2 Humble;
- Gazebo classic con TIAGo;
- Nav2 per la navigazione mobile;
- topic RGB-D della camera frontale di TIAGo;
- OpenAI GPT-4o via `openai` Python SDK per scene understanding e planning;
- YOLO-World tramite MMEngine/MMYOLO per detection open-vocabulary;
- EfficientViT-SAM via encoder/decoder ONNX per segmentazione;
- spaCy `en_core_web_sm`;
- gensim `word2vec-google-news-300`;
- Open3D per point cloud;
- TF2 per trasformazioni fra camera, map e base.

I pesi attesi da EMPOWER sono sotto la config installata del package:

```text
config/yolow/yolow.pth
config/efficientvitsam/l2_encoder.onnx
config/efficientvitsam/l2_decoder.onnx
```

`models_cacher_node.py` controlla che questi file esistano tramite
`EmpowerRos2Loader`.

### Flusso operativo EMPOWER dettagliato

Il flusso completo, senza HRI, e':

1. Avvio Gazebo con `exchange/person_table_child.world`.
2. Avvio Nav2 con localizzazione ground truth.
3. Capture RGB-D con `empower_capture.launch.py`.
4. Avvio model cacher con `empower_models_cacher.launch.py`.
5. Planning con `empower_plan.launch.py`.
6. Visualizzazione opzionale con `empower_visualization.launch.py`.
7. Execution low-level con `empower_execute.launch.py`.

#### 1. Capture scena

Nodo:

```text
empower_ros2.scene_capture_node
```

File:

```text
src/empower_ros2/python/empower_ros2/scene_capture_node.py
```

Input ROS:

```text
/head_front_camera/rgb/image_raw
/head_front_camera/depth/image_raw
/head_front_camera/depth/camera_info
```

Output salvati:

```text
empower_data/images/person_table_child_task/scan.jpg
empower_data/output/person_table_child_task/depth.npy
empower_data/output/person_table_child_task/depth_pointcloud.pcd
empower_data/output/person_table_child_task/camera_info.pkl
empower_data/output/person_table_child_task/camera_info.json
```

Il nodo aspetta un messaggio RGB, uno depth e uno camera info. Poi:

- converte RGB in immagine OpenCV BGR e salva `scan.jpg`;
- normalizza depth in millimetri e salva `depth.npy`;
- usa gli intrinseci camera per generare una point cloud da depth;
- applica una trasformazione assi compatibile con la convenzione usata dal
  resto della pipeline;
- salva camera info in pickle e JSON;
- se `capture_once` e' true, termina il nodo.

#### 2. Model cacher

Nodo:

```text
empower_ros2.models_cacher_node
```

File:

```text
src/empower_ros2/python/empower_ros2/models_cacher_node.py
```

Il model cacher carica una sola volta i modelli pesanti:

- YOLO-World;
- EfficientViT-SAM encoder/decoder;
- spaCy;
- word2vec;
- chiave OpenAI da ambiente o da `openai_api_key_file`.

Poi apre un server TCP locale:

```text
127.0.0.1:10000
```

Quando riceve un payload con comando `"detection"`, importa/reloada il modulo
top-level `detection`, crea `Detection()` e chiama:

```python
detection_instance.set_loader(self.loader)
```

Se viene passato un `task_description`, il model cacher sovrascrive la voce
`task_dict[use_case]` dentro `Detection`, cosi' il planning usa il task arrivato
dal launch/orchestratore invece dei task hardcoded.

Questa architettura evita di ricaricare i modelli a ogni run del planning.

#### 3. Planning e detection EMPOWER

Nodo:

```text
empower_ros2.plan_generator_node
```

File:

```text
src/empower_ros2/python/empower_ros2/plan_generator_node.py
```

Il plan generator:

- rimuove il vecchio `planning.txt`;
- invia al model cacher un JSON con:

```json
{
  "command": "detection",
  "task_description": "..."
}
```

- aspetta che venga generato `planning.txt`;
- parsea il piano in `planning.json`;
- rigenera report leggibili con `report_writer.py`;
- termina.

La logica EMPOWER vera e propria sta in:

```text
src/empower_ros2/detection.py
src/empower_ros2/agents.py
src/empower_ros2/models.py
```

##### `Agents`

`agents.py` usa OpenAI GPT-4o con immagine base64. Ci sono due modalita':

- `single_agent()`: produce relazioni e piano in un'unica chiamata.
- `multi_agent_vision_planning()`: modalita' usata attualmente.

La modalita' multi-agent fa tre passaggi:

1. `enviroment_agent()` produce triple `(subject, relation, object)` rilevanti
   per il task.
2. `description_agent()` produce una descrizione testuale della scena usando le
   relazioni.
3. planning agent produce una sequenza di azioni simboliche usando solo:

```text
NAVIGATE
GRAB
DROP
PULL
PUSH
```

Il modello e' hardcoded a:

```text
gpt-4o
```

Non c'e' ancora un parametro YAML per cambiare modello OpenAI.

##### `Detection.run_experiment()`

Questa funzione:

- legge `scan.jpg`;
- codifica l'immagine in base64;
- istanzia `Agents(encoded_image, task_description)`;
- chiama `multi_agent_vision_planning()`;
- salva:

```text
planning.pkl
planning.txt
```

`planning.pkl` contiene:

```python
{
  "environment_agent_info": "...",
  "description_agent_info": "...",
  "planning_agent_info": "..."
}
```

##### `Detection.run_image()`

Dopo il planning testuale, `run_image()` fa grounding visivo:

1. prende le relazioni prodotte dall'LLM;
2. estrae classi oggetto con spaCy e word2vec;
3. costruisce una stringa di classi per YOLO-World;
4. esegue YOLO-World su `scan.jpg`;
5. per ogni bounding box con score `> 0.1`, esegue EfficientViT-SAM;
6. salva maschere e immagini debug;
7. prova a riordinare/associare le detection alle relazioni testuali;
8. salva `detection.pkl`.

Output principali:

```text
detection.pkl
scan_with_bb.jpg
mask.jpg
yolo.jpg
rgb_0.jpg
rgb_1.jpg
...
```

Il grounding relazionale e' fragile: il codice parsea le relazioni facendo split
su `")"` e `","`, quindi si aspetta un formato molto preciso tipo:

```text
1) (book, on, table)
```

Se l'LLM cambia formato, aggiunge testo extra, usa virgole nei nomi, o produce
relazioni non previste, questa parte puo' rompersi o associare male le label.

#### 4. Report leggibili

File:

```text
src/empower_ros2/python/empower_ros2/report_writer.py
src/empower_ros2/python/empower_ros2/plan_parser.py
src/empower_ros2/python/empower_ros2/export_reports.py
```

Report generati:

```text
relations.txt
relations.json
scene_description.txt
symbolic_plan.txt
planning.json
detections.json
empower_report.txt
```

`plan_parser.py` riconosce solo azioni con primo token:

```text
NAVIGATE, GRAB, DROP, PULL, PUSH
```

Per `NAVIGATE` estrae `target`.
Per `GRAB`, `PULL`, `PUSH` estrae `target`.
Per `DROP` prova a identificare una relazione spaziale, per esempio:

```text
DROP book right to laptop
```

diventa:

```json
{
  "action": "DROP",
  "target": "book",
  "spatial_relation": "right to",
  "reference": "laptop"
}
```

#### 5. Visualizzazione 3D

Nodi:

```text
empower_ros2.color_pcl_node
empower_ros2.marker_centroids_node
```

`color_pcl_node.py`:

- legge `depth_pointcloud.pcd`;
- legge `detection.pkl` e `camera_info.pkl`;
- proietta le maschere 2D sulla point cloud;
- colora i punti appartenenti agli oggetti;
- salva:

```text
colored_image.png
colored_pcl.pcd
colors_dict.pkl
```

- pubblica:

```text
/empower/colored_pointcloud
```

`marker_centroids_node.py`:

- legge `detection.pkl` e `colors_dict.pkl`;
- calcola centroidi e punti massimi dei cluster 3D;
- prova a trasformare i punti in `map` usando TF;
- se TF fallisce, pubblica nel frame camera;
- pubblica:

```text
/pcl_centroids
/pcl_names
/pcl_maximum
```

Questi marker sono usati sia per RViz sia per la navigation low-level.

#### 6. Execution low-level

Nodi:

```text
empower_ros2.low_level_execution_node
empower_ros2.primitive_actions
```

`low_level_execution_node.py` legge `planning.json` o `planning.txt`, poi esegue
gli step uno alla volta.

Azioni supportate:

- `NAVIGATE`: eseguita realmente via Nav2 `NavigateToPose`;
- `GRAB`: non usa braccio/gripper, ma puo' simulare la presa in Gazebo;
- `DROP`: non usa braccio/gripper, ma puo' simulare il rilascio in Gazebo;
- `PUSH` e `PULL`: riconosciute ma saltate come non implementate.

Per `NAVIGATE`:

- il nodo aspetta marker su `/pcl_centroids` e `/pcl_names`;
- normalizza le label degli oggetti;
- cerca un marker compatibile col target del piano;
- converte il marker in `map`;
- calcola un goal a distanza `standoff_distance` dal centroide;
- orienta TIAGo verso l'oggetto;
- invia il goal a Nav2.

Limite importante: la navigazione funziona solo se il target del piano coincide
con una label rilevata. Per esempio, se il piano dice:

```text
NAVIGATE to the right side of the table
```

ma i marker sono solo `table`, `book`, `laptop`, la risoluzione target puo'
fallire. Il codice prova a creare candidati da sotto-stringhe, quindi spesso
trova `table`, ma non e' garantito.

Per `GRAB`/`DROP`:

- se `object_simulation.enabled` e' false, vengono saltate;
- se e' true, il nodo simula la manipolazione via CLI Gazebo `gz model`;
- oggetti configurati:

```text
book -> clear_red_book_on_right_table
computer -> laptop_computer_on_right_table
peluche -> teddy_bear_on_right_table
```

`GRAB book` nasconde/sposta il modello Gazebo in una posizione nascosta.
`DROP book` calcola una posa davanti al robot e sposta il modello li'.

Questa e' manipolazione simulata a livello mondo Gazebo, non manipolazione reale
con MoveIt, braccio o gripper.

#### 7. Missione HRI + EMPOWER

Nodo:

```text
empower_ros2.mission_orchestrator_node
```

Launch:

```text
src/empower_ros2/launch/hri_empower_mission.launch.py
```

Config:

```text
src/empower_ros2/config/hri_empower_mission.yaml
```

La missione orchestrata collega Nav2, HRI e EMPOWER.

Stati principali:

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

Flusso:

1. TIAGo naviga verso la terapista.
2. Aspetta un task fisico su `/hri/user_utterance`.
3. Pubblica un ack su `/hri/dialog_command`.
4. Aspetta una descrizione di attivita' per il bambino.
5. Normalizza il tipo di attivita':
   - `computer_emotion_recognition`
   - `mood_check_teddy`
   - `storytelling`
   - fallback `generic_child_activity`
6. Naviga allo spawn.
7. Esegue capture EMPOWER.
8. Lancia planning EMPOWER.
9. Lancia execution EMPOWER.
10. Naviga verso il bambino.
11. Pubblica `/mission/child_ready`.
12. Avvia attivita' child tramite `/hri/dialog_command`.

Topic missione:

```text
/mission/state
/mission/feedback
/mission/task_description
/mission/child_ready
```

Topic HRI usati:

```text
/hri/user_utterance
/hri/dialog_command
/hri/dialog_complete
```

### Output EMPOWER attualmente presenti

Nella repo sono presenti output generati per:

```text
empower_data/output/person_table_child_task/
```

Esempio di report corrente:

```text
Relations:
1) (teddy bear, on, table)
2) (laptop, on, table)
3) (book, on, table)
4) (laptop, right to, teddy bear)
5) (book, right to, laptop)

Symbolic plan:
1. NAVIGATE to the table
2. NAVIGATE to the right side of the table
3. GRAB book
4. DROP book right to laptop

Detections:
- laptop
- table
- book
```

Il `planning.json` corrente contiene quattro step:

```text
NAVIGATE -> target "the table"
NAVIGATE -> target "the right side of the table"
GRAB -> target "book"
DROP -> target "book", reference "laptop", relation "right to"
```

### Punti critici e fragilita' di EMPOWER

Questi sono i punti piu' importanti da considerare prima di modificare
`empower_ros2`.

1. Parsing LLM fragile.
   `detection.py` assume che le relazioni abbiano formato preciso con parentesi
   e virgole. `plan_parser.py` e' piu' robusto, ma anche lui dipende da action
   words all'inizio della riga.

2. Prompt e modello OpenAI sono hardcoded.
   `agents.py` usa `gpt-4o` direttamente. Non c'e' parametro YAML per modello,
   temperatura o prompt esterni.

3. `Detection.task_dict` contiene task storici hardcoded.
   Il task reale arriva da YAML/orchestratore solo perche' `models_cacher_node`
   sovrascrive `task_dict[use_case]`. Se si usa `Detection` fuori dal model
   cacher, `person_table_child_task` non e' nel dizionario originale.

4. Label mismatch.
   La scena/config usa `computer`, ma l'LLM/detection puo' produrre `laptop`.
   La simulazione oggetti conosce `computer`, mentre i marker possono chiamarsi
   `laptop`. Per il book funziona meglio perche' il nome coincide.

5. Navigation target mismatch.
   EMPOWER puo' generare target non-oggetto, come "right side of the table".
   L'esecuzione low-level naviga verso marker oggetto, non verso regioni
   semantiche astratte. Il codice prova sotto-stringhe, ma non e' una soluzione
   completa.

6. Manipolazione reale non implementata.
   `GRAB` e `DROP` sono simulati via Gazebo se configurati. Non esiste controllo
   reale del braccio, planning MoveIt, grasp pose o gripper.

7. Object simulation limitata.
   Solo `book`, `computer`, `peluche` sono configurati. Nuovi oggetti richiedono
   mapping YAML e probabilmente modifiche se servono alias.

8. Heavy dependencies.
   YOLO-World, EfficientViT-SAM, spaCy e word2vec sono pesanti. Il model cacher
   riduce i tempi caricando una volta sola, ma il primo avvio puo' essere lento.

9. Frame e TF delicati.
   Capture, point cloud e marker dipendono da frame camera, `map`, `base_footprint`
   e transform disponibili. Se TF non e' disponibile, i marker possono essere
   pubblicati nel frame camera, ma execution verso Nav2 richiede conversione
   coerente in `map`.

10. Duplicazione file top-level e `python/`.
    I file `agents.py`, `detection.py`, `loader.py`, `models.py`, `paths.py`
    sono duplicati. Se si modifica uno, bisogna verificare quale copia viene
    installata/importata e mantenere coerenza.

### Dove intervenire per modifiche EMPOWER

Per cambiare il task di default:

```text
src/empower_ros2/config/empower_person_table_child.yaml
```

Parametro:

```yaml
task_description: "..."
```

Per cambiare prompt, modello o logica LLM:

```text
src/empower_ros2/agents.py
src/empower_ros2/python/agents.py
```

Per rendere piu' robusto parsing relazioni / classi / grounding:

```text
src/empower_ros2/detection.py
src/empower_ros2/python/detection.py
```

Per cambiare parser piano simbolico:

```text
src/empower_ros2/python/empower_ros2/plan_parser.py
```

Per cambiare report generati:

```text
src/empower_ros2/python/empower_ros2/report_writer.py
```

Per cambiare logica di navigazione verso oggetti:

```text
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
src/empower_ros2/python/empower_ros2/primitive_actions.py
```

Per aggiungere oggetti simulati `GRAB`/`DROP`:

```text
src/empower_ros2/config/empower_person_table_child.yaml
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
```

Per cambiare il flusso missione HRI + EMPOWER:

```text
src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py
src/empower_ros2/config/hri_empower_mission.yaml
src/empower_ros2/launch/hri_empower_mission.launch.py
```

## Componente 2: Interazione / `empower_hri`

### Obiettivo

`empower_hri` implementa un nodo ROS 2 per dialogo locale:

```text
microfono o terminale -> STT -> LLM locale -> TTS -> speaker
```

Il package non controlla direttamente il robot. Pubblica utterance e riceve
comandi da altri nodi. Nella missione completa, il controllo del robot resta in
`mission_orchestrator_node.py`.

### File principali

```text
src/empower_hri/empower_hri/voice_llm_node.py
src/empower_hri/empower_hri/speech_to_text.py
src/empower_hri/empower_hri/text_to_speech.py
src/empower_hri/empower_hri/ollama_client.py
src/empower_hri/empower_hri/terminal_io.py
src/empower_hri/config/hri_cpu_english.yaml
src/empower_hri/config/hri_cpu_local.yaml
src/empower_hri/launch/hri_voice_llm_english.launch.py
src/empower_hri/launch/hri_voice_llm_cpu.launch.py
```

### Topic ROS 2

Pubblica:

```text
/hri/user_utterance
/hri/robot_utterance
/hri/dialog_state
/hri/dialog_complete
```

Sottoscrive:

```text
/hri/dialog_command
/mission/state
```

### Modalita'

`voice_llm_node.py` ha due modalita' principali:

1. `therapist_command`
   - usata per ricevere task e descrizione attivita' dal terapista;
   - puo' rispondere con LLM oppure limitarsi a pubblicare l'utterance;
   - nel launch missione, `therapist_llm_replies` e' impostato a false.

2. `child_activity`
   - usata dopo che EMPOWER ha concluso il task e TIAGo arriva dal bambino;
   - il nodo genera un'apertura dell'attivita';
   - poi fa turni interattivi con STT/LLM/TTS.

Tipi attivita' supportati:

```text
computer_emotion_recognition
mood_check_teddy
storytelling
generic_child_activity
```

### STT

File:

```text
src/empower_hri/empower_hri/speech_to_text.py
```

Provider:

- `faster_whisper`: registra WAV e trascrive su CPU;
- `mock`: input da terminale.

Il codice forza STT CPU-only. Se si configura `stt_device != cpu`, il nodo
solleva errore.

Sono disponibili:

- push-to-talk: premi invio, registra per `record_seconds`;
- auto-listening con VAD per attivita' child.

### LLM

File:

```text
src/empower_hri/empower_hri/ollama_client.py
```

Provider:

- `ollama`;
- `mock`.

Modello default nei README/config:

```text
qwen2.5:1.5b
```

Fallback:

```text
llama3.2:1b
```

Il client usa le API HTTP locali di Ollama:

```text
http://localhost:11434/api/chat
```

### TTS

File:

```text
src/empower_hri/empower_hri/text_to_speech.py
```

Provider supportati:

- `piper`;
- `pyttsx3`;
- `system` (`espeak-ng`, `espeak`, `spd-say`);
- `mock`.

Il TTS usa fallback automatici. Per esempio, se `pyttsx3` non funziona, prova
`system`, poi `mock`.

### Relazione con EMPOWER

`empower_hri` non lancia EMPOWER. La relazione avviene tramite:

- `/hri/user_utterance`: il nodo HRI pubblica task/attivita';
- `mission_orchestrator_node`: riceve utterance, decide quando avviare EMPOWER;
- `/hri/dialog_command`: l'orchestratore chiede al nodo HRI di parlare o avviare
  attivita' child.

Quindi, se si vuole modificare il comportamento robotico, di solito bisogna
modificare `empower_ros2`, non `empower_hri`.

## Componente 3: Gaze estimation / `gaze_estimation`

### Obiettivo

`gaze_estimation` e' un package ROS 2 standalone che usa una webcam RGB per
stimare se la persona sta guardando uno degli oggetti target.

E' esplicitamente indipendente da:

```text
empower_ros2
empower_hri
EMPOWER
TIAGo
Gazebo
Nav2
/hri/*
/empower/*
socket TCP esistenti
```

Non usa RealSense, rosbag, depth, point cloud o immagini salvate come input
obbligatorio.

### File principali

```text
src/gaze_estimation/gaze_estimation/gaze_webcam_node.py
src/gaze_estimation/gaze_estimation/gazelle_wrapper.py
src/gaze_estimation/gaze_estimation/yolo_world_wrapper.py
src/gaze_estimation/gaze_estimation/geometry.py
src/gaze_estimation/gaze_estimation/visualization.py
src/gaze_estimation/gaze_estimation/config_loader.py
src/gaze_estimation/gaze_estimation/device.py
src/gaze_estimation/config/gaze_webcam.yaml
src/gaze_estimation/launch/gaze_webcam.launch.py
```

### Modelli usati

Pesi attesi in:

```text
models/gaze_estimation/
```

File:

```text
gazelle_dinov2_vitl14_inout.pt
yolov8x-worldv2.pt
best.pt
```

Nota: nella config `gaze_webcam.yaml` attuale il face detector e' indicato come
`yolov8n-face.pt`, mentre i README parlano di `best.pt`. Il nodo risolve il
checkpoint dal parametro YAML; se il file non esiste, stampa un errore chiaro.
Questo e' un punto da controllare quando si esegue il package.

### Flusso runtime

Nodo:

```text
gaze_estimation.gaze_webcam_node
```

Il nodo:

1. apre la webcam con OpenCV;
2. ridimensiona il frame se `resize_width > 0`;
3. ogni `process_every_n_frames` esegue inferenza;
4. Gazelle/face detector trova facce e heatmap gaze;
5. YOLO-World rileva oggetti target;
6. sceglie la faccia piu' grande;
7. controlla `inout_score`;
8. verifica se gaze point cade nella bounding box oggetto, con margine;
9. opzionalmente controlla quanta massa della heatmap cade nella bbox;
10. disegna overlay OpenCV.

Status possibili:

```text
LOOKING_AT_OBJECT: <class>
LOOKING_ELSEWHERE
NO_FACE
NO_TARGET_OBJECT
GAZE_OUT_OF_FRAME
NO_FRAME
```

Al momento lo status viene loggato e mostrato nella finestra OpenCV. Non risulta
un topic ROS pubblicato con lo status, quindi non e' ancora integrato con
EMPOWER o con la missione HRI.

### Parametri importanti

```yaml
camera_index: 0
device: "auto"
target_classes: ["book", "bottle", "cube", "sunglasses", "smartphone"]
resize_width: 640
process_every_n_frames: 2
yolo_confidence: 0.10
gaze_inout_threshold: 0.50
bbox_margin_px: 30
use_heatmap_ratio_check: true
heatmap_inside_bbox_threshold: 0.15
```

`device` puo' essere:

```text
auto
cpu
cuda
```

Se si chiede CUDA ma non e' disponibile, il codice fa fallback su CPU.

## Relazione fra le tre componenti

Stato attuale:

- `empower_ros2` e' la componente centrale robotica.
- `empower_hri` e' integrato con `empower_ros2` solo tramite mission orchestrator
  e topic `/hri/*`.
- `gaze_estimation` e' separato e non alimenta ancora decisioni EMPOWER/HRI.

Flusso integrato attuale HRI + EMPOWER:

```text
therapist speech/text
  -> empower_hri pubblica /hri/user_utterance
  -> mission_orchestrator cattura task e attivita'
  -> mission_orchestrator naviga con Nav2
  -> mission_orchestrator lancia capture EMPOWER
  -> mission_orchestrator lancia planning EMPOWER
  -> mission_orchestrator lancia execution EMPOWER
  -> mission_orchestrator arriva dal bambino
  -> mission_orchestrator comanda empower_hri per attivita' child
```

Gaze estimation oggi non compare in questo flusso.

## Comandi principali

Build dei package principali:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
colcon build --symlink-install --packages-select empower_ros2 empower_hri gaze_estimation
source /root/exchange/install/setup.bash
```

Avvio Gazebo:

```bash
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/person_table_child.world
```

Avvio Nav2 ground truth:

```bash
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/person_table_child_static.yaml
```

Capture:

```bash
ros2 launch empower_ros2 empower_capture.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Model cacher:

```bash
ros2 launch empower_ros2 empower_models_cacher.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Planning:

```bash
ros2 launch empower_ros2 empower_plan.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Execution:

```bash
ros2 launch empower_ros2 empower_execute.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Missione completa HRI + EMPOWER:

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/empower_hri/config/hri_cpu_english.yaml \
  hri_input_mode:=voice
```

Gaze estimation:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=auto \
  target_classes:="book,bottle,cup"
```

## Indicazioni pratiche per prossime modifiche EMPOWER

Se l'obiettivo e' rendere EMPOWER piu' affidabile, le modifiche con piu' impatto
sono probabilmente:

1. Rendere configurabili modello OpenAI e prompt in YAML.
2. Sostituire l'output LLM libero con JSON strutturato per relazioni e piano.
3. Rendere robusto il parsing in `detection.py`, evitando split manuali su
   stringhe fragili.
4. Aggiungere alias oggetti, per esempio `laptop` == `computer`.
5. Separare target di navigazione astratti da oggetti fisici.
6. Migliorare `_resolve_target_marker()` per gestire target come "right side of
   the table".
7. Se serve manipolazione reale, introdurre un layer dedicato per MoveIt/gripper
   invece della simulazione Gazebo attuale.
8. Integrare eventualmente `gaze_estimation` pubblicando uno status ROS e
   facendolo consumare da mission orchestrator o EMPOWER.

## Prompt rapido da dare a ChatGPT

Se devo chiedere modifiche a un altro ChatGPT, posso incollare questo blocco:

```text
Sto lavorando sulla repo ElectiveAI, una workspace ROS 2 Humble per TIAGo in
Gazebo. Le componenti principali sono:

- empower_ros2: pipeline EMPOWER per capture RGB-D, GPT-4o vision planning,
  YOLO-World detection, EfficientViT-SAM segmentation, grounding, report,
  marker 3D, Nav2 execution e mission orchestrator.
- empower_hri: dialogo locale STT/LLM/TTS con faster-whisper, Ollama/Qwen e TTS
  locale. Si integra con EMPOWER tramite topic /hri/* e mission_orchestrator.
- gaze_estimation: package standalone webcam + Gazelle/Gaze-LLE + YOLO-World per
  stimare se una persona guarda un oggetto. Non e' ancora integrato con EMPOWER.

Il focus e' empower_ros2. I file piu' importanti sono:
src/empower_ros2/agents.py
src/empower_ros2/detection.py
src/empower_ros2/models.py
src/empower_ros2/python/empower_ros2/models_cacher_node.py
src/empower_ros2/python/empower_ros2/plan_generator_node.py
src/empower_ros2/python/empower_ros2/plan_parser.py
src/empower_ros2/python/empower_ros2/report_writer.py
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py
src/empower_ros2/config/empower_person_table_child.yaml

EMPOWER funziona cosi': scene_capture_node salva scan.jpg, depth.npy,
depth_pointcloud.pcd e camera_info; models_cacher_node carica YOLO-World,
EfficientViT-SAM, spaCy, word2vec e OpenAI key, poi ascolta su TCP 127.0.0.1:10000;
plan_generator_node manda il trigger "detection"; detection.py chiama agents.py
per ottenere relazioni, descrizione scena e piano simbolico, poi usa YOLO-World
e SAM per bounding box/maschere, salva detection.pkl e planning.txt; report_writer
genera json/txt leggibili; color_pcl_node e marker_centroids_node generano marker
3D; low_level_execution_node esegue NAVIGATE con Nav2 e simula GRAB/DROP in
Gazebo se object_simulation.enabled=true. PUSH/PULL sono saltate. Non c'e'
manipolazione reale con braccio/gripper.

Fragilita' principali: parsing LLM fragile in detection.py, modello/prompt
OpenAI hardcoded in agents.py, possibili mismatch label tipo laptop/computer,
target di navigazione astratti tipo "right side of the table", duplicazione dei
file top-level in src/empower_ros2 e src/empower_ros2/python.
```
