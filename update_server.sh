set -ex

SAVED_ARGS=("$@")
set --
source "$HOME/miniforge3/bin/activate"
set -- "${SAVED_ARGS[@]}"

git checkout main
git fetch
git reset --hard origin/main

while getopts ":p" opt
do
    case "$opt" in
        p)
            echo "Applying patches..."
            git am
            git push
            exit 0
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
    esac
done
shift $((OPTIND-1))

pip install -r requirements.txt --quiet
sudo "$(which python)" -m playwright install-deps chromium
python -m playwright install chromium

sudo apt-get install -y redis-server
sudo systemctl enable --now redis-server
sudo cp nabicat.conf /etc/nginx/conf.d/
sudo systemctl reload nginx

PROJ_DIR=$(pwd)
USER_NAME=$(whoami)
GUNICORN_BIN=$(which gunicorn)
MERIDIAN_BIN=$(which meridian)

# Number of gunicorn worker processes. Override at deploy time: WORKERS=8 ./update_server.sh
# Cross-worker shared state (scheduler, rate limiter, sessions, per-file data)
# is Redis-backed; the dev terminal is the one single-worker-only feature (see CLAUDE.md).
WORKERS=${WORKERS:-4}

sudo tee /etc/systemd/system/nabicat.service >/dev/null <<EOF
[Unit]
Description=Nabicat web app
After=network.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${PROJ_DIR}
ExecStart=${GUNICORN_BIN} -b 127.0.0.1:5000 -w ${WORKERS} --timeout 300 'web_app.__main__:prod_entry()'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/meridian.service >/dev/null <<EOF
[Unit]
Description=Meridian LLM proxy
After=network.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=/home/${USER_NAME}
Environment=PATH=/home/${USER_NAME}/.local/bin:/home/${USER_NAME}/miniforge3/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${MERIDIAN_BIN}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable meridian.service nabicat.service
sudo systemctl restart meridian.service nabicat.service
