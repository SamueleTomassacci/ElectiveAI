# README Gaze Estimation

Questa guida contiene i comandi per scaricare i pesi, buildare e avviare il package ROS 2 `gaze_estimation`.

I comandi assumono che la workspace sia in:

```bash
/root/exchange
```

Se stai lavorando da host e non da container, usa invece la root reale della repo, per esempio:

```bash
/home/samuele/ElectiveAI
```

## 1. Entra nella workspace

```bash
cd /root/exchange
```

```bash
source /opt/ros/humble/setup.bash
```

## 2. Installa dipendenze Python

```bash
python3 -m pip install --upgrade pip
```

```bash
python3 -m pip install torch torchvision opencv-python numpy ultralytics timm pillow pytest
```

Se `curl` non esiste:

```bash
apt update
```

```bash
apt install -y curl
```

## 3. Scarica i pesi

```bash
mkdir -p /root/exchange/models/gaze_estimation
```

```bash
cd /root/exchange/models/gaze_estimation
```

Gazelle/Gaze-LLE:

```bash
curl -L --progress-bar -o gazelle_dinov2_vitl14_inout.pt \
  https://github.com/fkryan/gazelle/releases/download/v1.0.0/gazelle_dinov2_vitl14_inout.pt
```

YOLO-World:

```bash
curl -L --progress-bar -o yolov8x-worldv2.pt \
  https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8x-worldv2.pt
```

Face detector YOLOv8:

```bash
curl -L --progress-bar -o best.pt \
  https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8n-face-lindevs.pt
```

Verifica:

```bash
ls -lh /root/exchange/models/gaze_estimation
```

Dovresti vedere:

```text
best.pt
gazelle_dinov2_vitl14_inout.pt
yolov8x-worldv2.pt
```

## 4. Build del package

Torna alla root della workspace:

```bash
cd /root/exchange
```

```bash
source /opt/ros/humble/setup.bash
```

```bash
colcon build --symlink-install --packages-select gaze_estimation
```

```bash
source install/setup.bash
```

## 5. Test leggeri

```bash
pytest -q src/gaze_estimation/test/test_geometry.py
```

## 6. Avvio base

Con classe target di default dal file YAML:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py
```

## 7. Avvio scegliendo oggetto target

Per verificare se stai guardando una bottiglia:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=auto \
  target_classes:="bottle"
```

Per verificare se stai guardando un libro:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=auto \
  target_classes:="book"
```

Per verificare piu oggetti:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=auto \
  target_classes:="book,bottle,cup"
```

Per uscire premi `q` nella finestra OpenCV oppure `Ctrl+C` nel terminale.

## 8. CPU e GPU

Auto:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=auto \
  target_classes:="bottle"
```

Forza CPU:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=cpu \
  target_classes:="bottle"
```

Prova CUDA, con fallback automatico su CPU se CUDA non e disponibile:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=cuda \
  target_classes:="bottle"
```

## 9. Primo avvio

Al primo avvio Gazelle scarica automaticamente il backbone DINOv2:

```text
/root/.cache/torch/hub/checkpoints/dinov2_vitl14_pretrain.pth
```

Il file e grande, circa 1.1 GB. Se vuoi scaricarlo manualmente con barra di progresso:

```bash
mkdir -p /root/.cache/torch/hub/checkpoints
```

```bash
curl -L --progress-bar \
  -o /root/.cache/torch/hub/checkpoints/dinov2_vitl14_pretrain.pth \
  https://dl.fbaipublicfiles.com/dinov2/dinov2_vitl14/dinov2_vitl14_pretrain.pth
```

## 10. Se la webcam non si apre

Controlla i device video:

```bash
ls -l /dev/video*
```

Se `/dev/video0` non esiste dentro il container, la webcam non e stata passata al container.

Esempio Docker:

```bash
docker run --device=/dev/video0:/dev/video0 ...
```

Oppure, se accettabile nel tuo setup:

```bash
docker run --privileged ...
```

Controlla se qualche processo sta usando la camera:

```bash
fuser -v /dev/video0
```

Ferma eventuali processi rimasti:

```bash
pkill -f gaze_webcam
```

```bash
pkill -f "ros2 launch gaze_estimation"
```

Prova un altro indice camera:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=1 \
  device:=auto \
  target_classes:="bottle"
```

## 11. Comandi rapidi completi

Sequenza compatta per setup e run:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
python3 -m pip install --upgrade pip
python3 -m pip install torch torchvision opencv-python numpy ultralytics timm pillow pytest
apt update
apt install -y curl
mkdir -p /root/exchange/models/gaze_estimation
cd /root/exchange/models/gaze_estimation
curl -L --progress-bar -o gazelle_dinov2_vitl14_inout.pt https://github.com/fkryan/gazelle/releases/download/v1.0.0/gazelle_dinov2_vitl14_inout.pt
curl -L --progress-bar -o yolov8x-worldv2.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8x-worldv2.pt
curl -L --progress-bar -o best.pt https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8n-face-lindevs.pt
cd /root/exchange
colcon build --symlink-install --packages-select gaze_estimation
source install/setup.bash
pytest -q src/gaze_estimation/test/test_geometry.py
ros2 launch gaze_estimation gaze_webcam.launch.py camera_index:=0 device:=auto target_classes:="bottle"
```

