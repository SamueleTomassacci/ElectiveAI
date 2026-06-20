#!/bin/bash
#
# start_mission.sh
#
# Avvia automaticamente i tre terminali necessari per la missione
# person_table_child: Gazebo, Nav2, e la missione HRI/EMPOWER completa.
#
# Uso: ./start_mission.sh
#
# Lo script apre tre finestre di terminale separate (una per Gazebo, una
# per Nav2, una per la missione), ciascuna gia' dentro il container Docker
# con i comandi giusti pronti. Include attese tra un lancio e il
# successivo per evitare i problemi di timing visti durante lo sviluppo
# (Nav2 lanciato troppo presto, missione lanciata prima che Nav2 sia
# pronto).
#
# Se vuoi modificare i tempi di attesa, cambia le variabili
# WAIT_AFTER_GAZEBO e WAIT_AFTER_NAV2 sotto.

set -e

CONTAINER_NAME="hrai_sim"

# Tempi di attesa (in secondi) tra un terminale e il successivo.
# Questi valori sono prudenziali; se la tua macchina e' piu' rapida/lenta
# del previsto, modifica questi numeri.
WAIT_AFTER_GAZEBO=15
WAIT_AFTER_NAV2=20

# --- Comandi eseguiti dentro ciascun terminale -----------------------

GAZEBO_CMD=$(cat <<'EOF'
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}
WORLD=/root/exchange/exchange/person_table_child.world
echo "=== TERMINALE 1: GAZEBO ==="
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=$WORLD
exec bash
EOF
)

NAV2_CMD=$(cat <<'EOF'
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
MAP=/root/exchange/exchange/maps/person_table_child_static.yaml
echo "=== TERMINALE 2: NAV2 ==="
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=$MAP
exec bash
EOF
)

MISSION_CMD=$(cat <<'EOF'
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
rm -rf /root/exchange/empower_data/images/person_table_child_task
rm -rf /root/exchange/empower_data/output/person_table_child_task
echo "=== TERMINALE 3: MISSIONE (voice) ==="
ros2 launch empower_ros2 hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/empower_hri/config/hri_cpu_english.yaml \
  hri_input_mode:=voice
exec bash
EOF
)

# --- Funzione per aprire un terminale con un comando dentro il container ---

open_terminal() {
    local title="$1"
    local inner_cmd="$2"
    local full_cmd="docker exec -it ${CONTAINER_NAME} bash -c '${inner_cmd}'"

    if command -v gnome-terminal &> /dev/null; then
        gnome-terminal --title="$title" -- bash -c "$full_cmd"
    elif command -v konsole &> /dev/null; then
        konsole --title "$title" -e bash -c "$full_cmd"
    elif command -v xterm &> /dev/null; then
        xterm -title "$title" -e bash -c "$full_cmd" &
    else
        echo "ERRORE: nessun terminal emulator conosciuto trovato (gnome-terminal, konsole, xterm)."
        echo "Installa uno di questi, oppure lancia manualmente questo comando in una finestra nuova:"
        echo "$full_cmd"
        exit 1
    fi
}

# --- Verifica che il container sia attivo -----------------------------

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "ERRORE: il container '${CONTAINER_NAME}' non risulta avviato."
    echo "Avvialo prima di lanciare questo script (es. 'docker start ${CONTAINER_NAME}')."
    exit 1
fi

# --- Sequenza di avvio --------------------------------------------------

echo "Avvio Terminale 1 (Gazebo)..."
open_terminal "1-Gazebo" "$GAZEBO_CMD"

echo "Attendo ${WAIT_AFTER_GAZEBO}s per dare tempo a Gazebo di avviarsi..."
sleep "$WAIT_AFTER_GAZEBO"

echo "Avvio Terminale 2 (Nav2)..."
open_terminal "2-Nav2" "$NAV2_CMD"

echo "Attendo ${WAIT_AFTER_NAV2}s per dare tempo a Nav2 di stabilizzarsi..."
sleep "$WAIT_AFTER_NAV2"

echo "Avvio Terminale 3 (Missione, hri_input_mode:=voice)..."
open_terminal "3-Missione" "$MISSION_CMD"

echo ""
echo "Tutti i terminali sono stati avviati."
echo "Controlla il Terminale 2 (Nav2): se vedi ancora warning 'Waiting for TF'"
echo "quando il Terminale 3 e' partito, potrebbe servire aumentare WAIT_AFTER_NAV2"
echo "in questo script per i prossimi avvii."