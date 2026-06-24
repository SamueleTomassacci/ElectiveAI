# Stato attuale della navigazione TIAGo

Questo documento riassume lo stato attuale della repo ElectiveAI con focus
sulla navigazione del robot TIAGo nella simulazione Gazebo. Il sistema completo
e' composto da tre blocchi principali:

- `empower`: planning simbolico, detection/grounding, capture RGB-D, navigazione
  Nav2 ed esecuzione low-level del piano.
- `gaze_estimation`: stima dell'attenzione del bambino durante la fase finale
  dell'interazione.
- `llm_interaction`: interazione vocale/testuale con terapista e bambino,
  usando STT, LLM locale via Ollama e TTS.

La navigazione vera e propria vive quasi tutta in `empower`: gli altri blocchi
servono a raccogliere istruzioni, monitorare attenzione e guidare il dialogo,
ma non decidono direttamente le pose Nav2.

## Scena, mappa e frame

La simulazione usa una sola scena principale:

- world Gazebo: `exchange/scene.world`
- mappa Nav2: `exchange/maps/scene.yaml`
- configurazione EMPOWER: `src/empower/config/empower_config.yaml`
- configurazione missione HRI: `src/empower/config/task_config.yaml`

La mappa statica e' caricata nel frame `map`. Il robot viene comandato nel
frame `base_footprint`, mentre Nav2 riceve goal `NavigateToPose` nel frame
`map`.

Il bringup di Nav2 e':

```bash
ros2 launch empower tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/scene.yaml
```

Questo launch avvia:

- `nav2_map_server`, che carica la mappa statica;
- `nav2_lifecycle_manager`, che porta online il map server;
- `ground_truth_localizer_node`, che pubblica localizzazione da ground truth;
- `nav2_bringup/launch/navigation_launch.py`, che avvia planner, controller,
  behavior server, BT navigator e costmap.

In questo MVP non viene usato AMCL: la posa del robot arriva da
`/ground_truth_odom` tramite `ground_truth_localizer_node`. Questo rende la
navigazione piu' stabile in Gazebo, perche' Nav2 lavora con una localizzazione
perfetta o quasi perfetta.

## Flow di missione

Il flow completo HRI + EMPOWER e' orchestrato da:

```text
src/empower/python/empower/mission_orchestrator_node.py
```

Il launch principale e':

```bash
ros2 launch empower hri_empower_mission.launch.py
```

La sequenza attuale e':

1. TIAGo naviga verso la terapeuta.
2. Aspetta il task fisico dalla terapeuta su `/hri/user_utterance`.
3. Chiede/raccoglie l'attivita' da fare con il bambino.
4. Chiede/raccoglie il nome del bambino.
5. Torna alla posa di spawn/centro stanza.
6. Esegue capture EMPOWER e planning.
7. Esegue il piano EMPOWER, che puo' contenere `NAVIGATE`, `GRAB`, `DROP`.
8. Se EMPOWER non gestisce gia' la consegna al bambino, usa una posa hardcoded
   per andare dal bambino.
9. Pubblica `child_ready` e avvia il dialogo con il bambino.
10. Avvia `gaze_estimation` per monitorare l'attenzione durante l'attivita'.

Nel file corrente `task_config.yaml` il parametro:

```yaml
child_delivery_handled_by_empower: true
```

significa che l'orchestratore non esegue il proprio `GO_TO_CHILD` hardcoded
dopo EMPOWER. Ci si aspetta invece che il piano generato da EMPOWER includa
una navigazione verso il bambino, per esempio `NAVIGATE to child`.

## Pose hardcoded della missione

Le pose principali del flow HRI sono in:

```text
src/empower/config/task_config.yaml
```

Valori attuali:

| Nome | Coordinate `[x, y, yaw]` | Uso |
| --- | --- | --- |
| `therapist_pose` | `[0.0, -1.3, -1.5708]` | Posa davanti/lato terapeuta. TIAGo va qui per ricevere task e attivita'. |
| `spawn_pose` | `[4.8, 0.0, 1.72]` | Posa di ritorno prima di EMPOWER. Serve come punto da cui eseguire capture/planning. |
| `child_pose` | `[0.0, 0.95, 1.5708]` | Posa vicino al bambino, usata solo se `child_delivery_handled_by_empower` e' false. |

Il formato e' sempre:

```text
[x, y, yaw]
```

dove `x` e `y` sono coordinate in `map`, mentre `yaw` e' l'orientamento del
robot in radianti. Per esempio:

- `yaw = 0.0`: robot orientato verso `+X`;
- `yaw = 1.5708`: robot orientato circa verso `+Y`;
- `yaw = -1.5708`: robot orientato circa verso `-Y`;
- `yaw = 3.14159`: robot orientato circa verso `-X`.

Nel codice, queste pose vengono lette da `pose_parameter()`, convertite in
`PoseStamped` da `make_pose_stamped()` e mandate a Nav2 da `navigate_to_pose()`.
Tutte e tre le funzioni sono in `mission_orchestrator_node.py`.

## Pose per target non visuali

Oltre alle pose della missione, EMPOWER ha pose dedicate ai target che non
vengono necessariamente visti dalla camera, soprattutto `child` e `person`.
Sono definite in:

```text
src/empower/config/empower_config.yaml
```

Valori attuali:

```yaml
child_navigation:
  enabled: true
  frame_id: map
  x: 0.0
  y: 0.95
  yaw: 1.5708
  via_waypoint:
    enabled: true
    x: 3.0
    y: 0.0
    yaw: 3.14159
  aliases: ["child", "the child", "kid"]

person_navigation:
  enabled: true
  frame_id: map
  x: 0.0
  y: 0.0
  yaw: -1.5708
  aliases: ["person", "the person", "adult", "the adult", "standing person", "adult person"]
```

Queste pose sono usate da:

```text
src/empower/python/empower/low_level_execution_node.py
```

Quando il piano contiene `NAVIGATE to child`, il low-level executor non cerca
un marker visuale del bambino. Riconosce il target tramite gli alias di
`child_navigation.aliases`, crea direttamente un `PoseStamped` con
`child_navigation_pose()` e lo manda a Nav2.

Nel caso del bambino e' attivo anche un waypoint intermedio:

```text
via waypoint: (3.0, 0.0, 3.14159)
child goal:   (0.0, 0.95, 1.5708)
```

Quindi la navigazione verso il bambino avviene in due step: prima il waypoint,
poi la posa finale vicino al bambino. Questo e' utile se il percorso diretto
attraversa zone strette o se Nav2 tende a scegliere una traiettoria instabile.

Quando il piano contiene `NAVIGATE to person`, il low-level executor usa invece
`person_navigation_pose()`. Anche qui non serve una detection visuale: basta che
il target nel piano corrisponda a uno degli alias configurati.

## Navigazione verso oggetti visuali

Per target visuali come tavolo, libro, laptop o peluche, il robot non usa una
posa fissa. La posa viene ricavata dalla detection EMPOWER.

Il caso multi-view e' quello attualmente abilitato:

```yaml
multi_view:
  enabled: true
```

Il nodo `multi_view_capture_node` ruota TIAGo sul posto usando Nav2. Per ogni
vista salva RGB, depth, camera info e posa della camera in `map`. Il manifest
principale e':

```text
output/empower_output/therapy_task/views_manifest.json
```

Poi `multi_view_aggregator` unisce le detection delle varie viste e produce:

```text
output/empower_output/therapy_task/global_detections.json
output/empower_output/therapy_task/global_scene_graph.json
output/empower_output/therapy_task/global_scene_description.txt
```

Dentro `global_detections.json`, ogni oggetto ha un `centroid_map`, cioe' un
centroide 3D espresso in frame `map`. Durante l'esecuzione del piano, il
low-level executor cerca il target richiesto nel file `global_detections.json`.
Se lo trova, costruisce un goal Nav2 non esattamente sul centroide, ma a una
distanza di sicurezza.

La funzione chiave e':

```text
src/empower/python/empower/primitive_actions.py
make_nav_goal_pose()
```

La logica e':

1. prendi il centroide dell'oggetto in `map`;
2. leggi la posa corrente del robot da TF;
3. calcola la direzione oggetto -> robot;
4. posiziona il goal a `standoff_distance` dal centroide, lungo quella
   direzione;
5. orienta il robot verso l'oggetto.

Il parametro corrente e':

```yaml
standoff_distance: 1.2
```

Quindi, se il target e' un oggetto sopra un tavolo, TIAGo non prova ad andare
nel centro del tavolo: si ferma circa 1.2 m prima e guarda verso il target.

Se `multi_view.enabled` fosse false, la navigazione verso oggetti visuali
passerebbe dai marker `/pcl_centroids` e `/pcl_names`, pubblicati a partire
dalla point cloud single-view. La logica finale resta la stessa: centroide
target piu' `standoff_distance`.

## Relazione tra terapeuta, centro stanza e bambino

Nel codice oggi ci sono due livelli di pose per questi punti:

- pose di missione HRI in `task_config.yaml`;
- pose di target EMPOWER/non visuali in `empower_config.yaml`.

La terapeuta nel flow HRI usa:

```yaml
therapist_pose: [0.0, -1.3, -1.5708]
```

La persona/terapeuta nel piano EMPOWER usa invece:

```yaml
person_navigation:
  x: 0.0
  y: 0.0
  yaw: -1.5708
```

Queste due pose sono vicine semanticamente ma non identiche: la prima e' la
posa usata dall'orchestratore per parlare con la terapeuta, la seconda e' la
posa usata dal low-level executor se un piano EMPOWER contiene `NAVIGATE to
person`.

Il centro stanza/spawn usa:

```yaml
spawn_pose: [4.8, 0.0, 1.72]
```

Questa posa viene raggiunta prima della capture EMPOWER. In pratica e' il punto
da cui il robot osserva/ricostruisce la scena prima di generare il piano.

Il bambino ha due definizioni coerenti tra loro:

```yaml
task_config.yaml:
  child_pose: [0.0, 0.95, 1.5708]

empower_config.yaml:
  child_navigation:
    x: 0.0
    y: 0.95
    yaw: 1.5708
```

La prima e' usata dall'orchestratore solo nel fallback `GO_TO_CHILD`; la seconda
e' usata dall'esecuzione EMPOWER quando il piano include `NAVIGATE to child`.
Con la configurazione attuale, quella piu' importante e' `child_navigation`,
perche' `child_delivery_handled_by_empower` e' true.

## Manipolazione simulata e drop vicino al bambino

La manipolazione fisica reale non e' implementata: non ci sono MoveIt, gripper
o controllo braccio. Pero' `low_level_execution_node.py` simula `GRAB` e `DROP`
spostando modelli Gazebo.

Oggetti supportati:

- `book` -> `clear_red_book_on_right_table`
- `computer` / `laptop` -> `laptop_computer_on_right_table`
- `peluche` / `teddy bear` -> `teddy_bear_on_right_table`

Quando il robot fa `GRAB`, il modello viene rimosso da Gazebo. Quando fa
`DROP`, viene respawnato. Se l'ultima navigazione riuscita era verso il bambino,
il drop usa una posa fissa sul tavolo di consegna:

```yaml
object_simulation:
  child_delivery_table_drop_x: 0.0
  child_delivery_table_drop_y: 1.65
  child_delivery_table_drop_z: 0.65
  child_delivery_table_drop_yaw: 1.5708
```

Questo e' collegato a `last_navigation_target_is_child`: quando `NAVIGATE to
child` va a buon fine, il flag viene impostato a true e il drop successivo viene
interpretato come consegna al bambino.

## Dove modificare le pose

Per cambiare dove TIAGo va durante il flow HRI:

```text
src/empower/config/task_config.yaml
```

Modifica:

- `therapist_pose` per la posa davanti alla terapeuta;
- `spawn_pose` per la posa centrale/di partenza EMPOWER;
- `child_pose` solo se vuoi usare il fallback orchestrato verso il bambino.

Per cambiare dove EMPOWER manda il robot quando il piano contiene target non
visuali:

```text
src/empower/config/empower_config.yaml
```

Modifica:

- `child_navigation.x/y/yaw`;
- `child_navigation.via_waypoint` se vuoi cambiare o disattivare il waypoint;
- `person_navigation.x/y/yaw`;
- gli alias se il planner produce nomi diversi, per esempio `therapist` invece
  di `person`.

Per cambiare quanto il robot resta lontano dagli oggetti visuali:

```yaml
standoff_distance: 1.2
```

Aumentarlo aiuta se Nav2 prova ad arrivare troppo vicino a tavolo/ostacoli.
Diminuirlo rende TIAGo piu' vicino al target, ma aumenta il rischio che il goal
cada in una zona occupata o dentro l'inflation della costmap.

## Limiti attuali

- La navigazione e' basata su ground truth, non su localizzazione realistica con
  AMCL.
- I target non visuali (`child`, `person`) sono pose hardcoded, non vengono
  stimati dinamicamente dalla percezione.
- Gli oggetti visuali dipendono dalla qualita' di detection, depth e fusione
  multi-view.
- La manipolazione e' simulata via Gazebo CLI: `GRAB` e `DROP` non muovono il
  braccio reale del robot.
- La posa `person_navigation` e la posa `therapist_pose` non sono unificate: se
  si cambia posizione della terapeuta nella scena, conviene aggiornarle entrambe.

## File principali da guardare

- `src/empower/config/task_config.yaml`: pose missione HRI.
- `src/empower/config/empower_config.yaml`: Nav2, target non visuali, multi-view
  e simulazione oggetti.
- `src/empower/python/empower/mission_orchestrator_node.py`: sequenza missione e
  navigazione verso terapeuta/spawn/bambino.
- `src/empower/python/empower/low_level_execution_node.py`: esecuzione del piano
  EMPOWER e risoluzione dei target `NAVIGATE`.
- `src/empower/python/empower/primitive_actions.py`: invio goal a Nav2 e calcolo
  della posa di avvicinamento agli oggetti.
- `src/empower/python/empower/multi_view_capture_node.py`: rotazione sul posto e
  salvataggio delle viste RGB-D.
- `src/empower/python/empower/multi_view_aggregator.py`: costruzione dei
  centroidi globali in `map`.
- `src/empower/launch/tiago_nav2_ground_truth.launch.py`: bringup Nav2 con
  localizzazione ground truth.
- `src/empower/config/nav2_tiago_ground_truth.yaml`: parametri planner,
  controller, costmap e goal tolerance.
