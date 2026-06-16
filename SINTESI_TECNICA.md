# Sintesi tecnica della repository

Questa repository contiene una pipeline ROS2 Humble per integrare EMPOWER con il robot TIAGo in simulazione Gazebo. Il sistema parte da una richiesta in linguaggio naturale, acquisisce una scena RGB-D, riconosce e segmenta gli oggetti rilevanti, produce una rappresentazione simbolica dell'ambiente, genera un piano ad alto livello e ne esegue la sola parte di navigazione mobile tramite Nav2.

## Stato attuale del codice

Il codice e' arrivato a una versione funzionante end-to-end in simulazione per task di percezione, grounding, pianificazione simbolica e navigazione verso oggetti. La repository include ambiente Docker, launch file ROS2, configurazioni per TIAGo/Nav2, world Gazebo di test, nodi ROS2 dedicati e output gia' generati nella cartella `empower_data`. Il flusso principale e' documentato nel `README.md` e usa come configurazione principale `src/empower_ros2/config/empower_tiago.yaml`; esiste anche una scena alternativa `nav_challenge` per provare meglio target multipli di navigazione.

## Moduli principali

- `scene_capture_node`: acquisisce RGB, depth e camera info dai topic della camera di TIAGo, salva `scan.jpg`, `depth.npy`, `camera_info` e costruisce una point cloud 3D.
- `models_cacher_node`: carica una volta i modelli pesanti usati da EMPOWER, cioe' YOLO-World, EfficientViT-SAM, spaCy e word2vec; resta in ascolto via socket e avvia detection, segmentation, grounding e planning quando richiesto.
- `detection.py` e moduli EMPOWER: usano la descrizione del task, un agente LLM/vision e i modelli visuali per estrarre relazioni della scena, descrizione testuale, bounding box, maschere e piano simbolico.
- `plan_generator_node`: invia il task al model cacher, aspetta il risultato, salva `planning.txt`, converte il piano in `planning.json` e genera report leggibili.
- `color_pcl_node`: colora la point cloud usando le maschere di segmentazione e pubblica `/empower/colored_pointcloud`.
- `marker_centroids_node`: calcola centroidi e marker testuali degli oggetti segmentati e li pubblica su `/pcl_centroids`, `/pcl_names` e `/pcl_maximum`.
- `ground_truth_localizer_node`: usa l'odometria ground truth di Gazebo per pubblicare la trasformazione `map -> odom`, evitando SLAM/AMCL nella simulazione.
- `low_level_execution_node`: legge il piano simbolico, risolve i target tramite i centroidi EMPOWER e invia a Nav2 goal `NavigateToPose` per gli step `NAVIGATE`.

## Cosa puo' fare attualmente il robot

Attualmente TIAGo puo' avviarsi in Gazebo, usare una mappa statica con Nav2, acquisire una scena RGB-D, rilevare oggetti collegati al task, segmentarli, stimarne posizione/centroide nella mappa, visualizzarli in RViz e generare un piano simbolico da una frase come "bring the cup on the chair on the right" o "visit the cup, then the person, then the plant, then the cabinet". In esecuzione low-level, il robot puo' navigare verso gli oggetti riconosciuti mantenendo una distanza configurabile (`standoff_distance`) rispetto al centroide.

La manipolazione non e' ancora implementata. Le azioni `GRAB`, `DROP`, `PUSH` e `PULL` vengono riconosciute nel piano, loggate e saltate in modo sicuro. Quindi il sistema puo' pianificare task che includono presa o rilascio, ma fisicamente esegue solo la navigazione mobile. Non sono presenti controllo del braccio, gripper, grasping, MoveIt o MoveIt2.

## Output e utilita'

La pipeline produce file intermedi e report in `empower_data/output/<use_case>`, tra cui immagini annotate, point cloud, detection, relazioni simboliche, descrizione della scena, piano testuale, piano JSON e `empower_report.txt`. Questi output permettono di debuggare separatamente percezione, grounding, planning e navigazione. RViz puo' mostrare point cloud colorata, centroidi e nomi degli oggetti, rendendo verificabile se il target scelto dal planner corrisponde alla posizione reale nella scena simulata.
