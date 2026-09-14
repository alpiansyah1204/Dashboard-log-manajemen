#!/bin/bash
#
# install_fluentbit_loki_modular_sources.sh
# RHEL OS Log Management Agent -> Central Loki
#
# Active sources:
#   systemd journal -> source=systemd
#   /var/log/dnf.log -> source=dnf
#
# Prepared but disabled:
#   /var/log/messages -> source=messages
#   /var/log/cron     -> source=cron
#   /var/log/boot.log -> source=boot

set -euo pipefail

LOKI_HOST="192.168.114.75"
LOKI_PORT="80"
LOKI_URI="/loki/api/v1/push"

JOB="os-logs"
BASE_DIR="/etc/fluent-bit"
CONF_DIR="${BASE_DIR}/conf.d"
META_DIR="${BASE_DIR}/metadata"
STATE_DIR="/var/lib/fluent-bit"

MAIN_CONF="${BASE_DIR}/fluent-bit.conf"
LABELS_CONF="${META_DIR}/labels.conf"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: Please run as root."
    exit 1
fi

echo "============================================================"
echo " Fluent Bit Modular Installer - RHEL OS Logs -> Loki"
echo "============================================================"

# OS and hostname detection
HOST_NAME="$(hostname -s)"

# Detect primary IPv4 address.
IP_ADDRESS="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
if [[ -z "${IP_ADDRESS}" ]]; then
    IP_ADDRESS="$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)"
fi
IP_ADDRESS="${IP_ADDRESS:-unknown}"

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    case "${ID,,}" in
        rhel|rocky|almalinux|centos) OS_NAME="rhel" ;;
        *) OS_NAME="${ID,,}" ;;
    esac
    OS_VERSION="${VERSION_ID:-unknown}"
else
    OS_NAME="linux"
    OS_VERSION="unknown"
fi

echo
echo "Detected:"
echo "  Host       : ${HOST_NAME}"
echo "  IP         : ${IP_ADDRESS}"
echo "  OS         : ${OS_NAME}"
echo "  OS Version : ${OS_VERSION}"

echo
echo "Select Environment:"
echo "  1) Prod"
echo "  2) Dmz"
read -rp "Choice [1-2]: " ENV_CHOICE
case "${ENV_CHOICE}" in
    1) ENVIRONMENT="prod" ;;
    2) ENVIRONMENT="dmz" ;;
    *) echo "Invalid choice."; exit 1 ;;
esac

echo
while true; do
    read -rp "Application Group: " APP_GROUP
    APP_GROUP="$(echo "${APP_GROUP}" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')"
    if [[ -n "${APP_GROUP}" && "${APP_GROUP}" =~ ^[a-zA-Z0-9._-]+$ ]]; then
        break
    fi
    echo "Invalid value. Use letters, numbers, dot, underscore, or hyphen."
done

echo
echo "Select Server Role:"
echo "  1) App"
echo "  2) Web"
echo "  3) Db"
read -rp "Choice [1-3]: " ROLE_CHOICE
case "${ROLE_CHOICE}" in
    1) SERVER_ROLE="app" ;;
    2) SERVER_ROLE="web" ;;
    3) SERVER_ROLE="db" ;;
    *) echo "Invalid choice."; exit 1 ;;
esac

echo
echo "Select Site:"
echo "  1) Dc1"
echo "  2) Dc2"
read -rp "Choice [1-2]: " SITE_CHOICE
case "${SITE_CHOICE}" in
    1) SITE="dc1" ;;
    2) SITE="dc2" ;;
    *) echo "Invalid choice."; exit 1 ;;
esac

echo
echo "================ CONFIGURATION SUMMARY ================"
echo "job         : ${JOB}"
echo "host        : ${HOST_NAME}"
echo "ip          : ${IP_ADDRESS}"
echo "os          : ${OS_NAME}"
echo "environment : ${ENVIRONMENT}"
echo "app_group   : ${APP_GROUP}"
echo "server_role : ${SERVER_ROLE}"
echo "site        : ${SITE}"
echo
echo "ACTIVE SOURCES:"
echo "  os.systemd -> source=systemd"
echo "  os.dnf     -> source=dnf"
echo
echo "DISABLED / COMMENTED:"
echo "  os.messages -> source=messages"
echo "  os.cron     -> source=cron"
echo "  os.boot     -> source=boot"
echo "======================================================="
echo

read -rp "Continue installation? [y/N]: " CONFIRM
case "${CONFIRM}" in
    y|Y|yes|YES) ;;
    *) echo "Installation cancelled."; exit 0 ;;
esac

echo
echo "[1/9] Installing Fluent Bit from existing repository..."
if command -v fluent-bit >/dev/null 2>&1 || [[ -x /opt/fluent-bit/bin/fluent-bit ]]; then
    echo "Fluent Bit already installed."
else
    dnf install -y fluent-bit
fi

echo "[2/9] Stopping Fluent Bit..."
systemctl stop fluent-bit 2>/dev/null || true

echo "[3/9] Creating directories..."
mkdir -p "${CONF_DIR}" "${META_DIR}" "${STATE_DIR}"

echo "[4/9] Backing up existing configuration..."
BACKUP_DIR="${BASE_DIR}/backup/$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BACKUP_DIR}"
for FILE in "${MAIN_CONF}" "${CONF_DIR}"/*.conf "${LABELS_CONF}"; do
    [[ -f "${FILE}" ]] && cp -a "${FILE}" "${BACKUP_DIR}/" || true
done

echo "[5/9] Creating main configuration..."
cat > "${MAIN_CONF}" <<EOF
[SERVICE]
    Flush        5
    Grace        30
    Log_Level    info
    Daemon       off

@INCLUDE conf.d/00-input-heartbeat.conf
@INCLUDE conf.d/01-input-systemd.conf
@INCLUDE conf.d/02-input-files.conf
@INCLUDE conf.d/03-filters.conf
@INCLUDE conf.d/04-output-loki.conf
EOF

cat > "${LABELS_CONF}" <<EOF
# Endpoint metadata
JOB=${JOB}
HOST=${HOST_NAME}
IP=${IP_ADDRESS}
OS=${OS_NAME}
ENVIRONMENT=${ENVIRONMENT}
APP_GROUP=${APP_GROUP}
SERVER_ROLE=${SERVER_ROLE}
SITE=${SITE}
OS_VERSION=${OS_VERSION}
EOF

echo "[6/9] Creating input configuration..."

cat > "${CONF_DIR}/00-input-heartbeat.conf" <<EOF
# ============================================================
# HEARTBEAT
# Sends one lightweight record every 60 seconds.
# This is used only for server inventory / UP-DOWN detection.
# It is NOT an OS log and must be excluded from log-volume panels.
# ============================================================

[INPUT]
    Name              dummy
    Tag               os.heartbeat
    Interval_Sec      60
    Dummy             {"heartbeat":"true"}
EOF


cat > "${CONF_DIR}/01-input-systemd.conf" <<EOF
# ============================================================
# ACTIVE SOURCE: systemd journal
# Tag -> os.systemd
# Loki label -> source=systemd
# ============================================================

[INPUT]
    Name              systemd
    Tag               os.systemd
    DB                ${STATE_DIR}/systemd.db
    Read_From_Tail    On
    Strip_Underscores On
EOF

cat > "${CONF_DIR}/02-input-files.conf" <<EOF
# ============================================================
# RHEL FILE-BASED OS LOG SOURCES
# ============================================================

# ACTIVE: DNF package management log
# Tag -> os.dnf
# Loki label -> source=dnf

[INPUT]
    Name              tail
    Tag               os.dnf
    Path              /var/log/dnf.log
    DB                ${STATE_DIR}/dnf.db
    Read_from_Head    False
    Refresh_Interval  10


# ============================================================
# DISABLED FOR EVALUATION: General system messages
# Potential duplicate with systemd journal.
# ============================================================

# [INPUT]
#     Name              tail
#     Tag               os.messages
#     Path              /var/log/messages
#     DB                ${STATE_DIR}/messages.db
#     Read_from_Head    False
#     Refresh_Interval  10


# ============================================================
# DISABLED FOR EVALUATION: Cron log
# ============================================================

# [INPUT]
#     Name              tail
#     Tag               os.cron
#     Path              /var/log/cron
#     DB                ${STATE_DIR}/cron.db
#     Read_from_Head    False
#     Refresh_Interval  10


# ============================================================
# DISABLED FOR EVALUATION: Boot log
# ============================================================

# [INPUT]
#     Name              tail
#     Tag               os.boot
#     Path              /var/log/boot.log
#     DB                ${STATE_DIR}/boot.db
#     Read_from_Head    False
#     Refresh_Interval  10
EOF

# Filters are intentionally empty for now; output routing provides source labels.
cat > "${CONF_DIR}/03-filters.conf" <<'EOF'
# ============================================================
# FILTERS
# ============================================================
#
# No mandatory filters are currently applied.
#
# Security exclusion will be added after security log sources
# are explicitly identified and validated.
#
# Source classification is implemented in the Loki OUTPUT
# blocks using static source labels per tag.
EOF

echo "[7/9] Creating Loki outputs with separate source labels..."

cat > "${CONF_DIR}/04-output-loki.conf" <<EOF
# ============================================================
# CENTRAL LOKI OUTPUT
#
# Common labels:
#   job, host, os, environment, app_group, server_role, site
#
# Source is deliberately different for every log input.
# ============================================================



# ============================================================
# HEARTBEAT OUTPUT
# source=heartbeat
# ============================================================

[OUTPUT]
    Name        loki
    Match       os.heartbeat
    Host        ${LOKI_HOST}
    Port        ${LOKI_PORT}
    URI         ${LOKI_URI}
    Labels      job=${JOB},host=${HOST_NAME},ip=${IP_ADDRESS},os=${OS_NAME},environment=${ENVIRONMENT},app_group=${APP_GROUP},server_role=${SERVER_ROLE},site=${SITE},source=heartbeat
    Line_Format json

# ============================================================
# SYSTEMD
# ============================================================

[OUTPUT]
    Name        loki
    Match       os.systemd
    Host        ${LOKI_HOST}
    Port        ${LOKI_PORT}
    URI         ${LOKI_URI}

    Labels      job=${JOB},host=${HOST_NAME},ip=${IP_ADDRESS},os=${OS_NAME},environment=${ENVIRONMENT},app_group=${APP_GROUP},server_role=${SERVER_ROLE},site=${SITE},source=systemd
    Line_Format json


# ============================================================
# DNF
# ============================================================

[OUTPUT]
    Name        loki
    Match       os.dnf
    Host        ${LOKI_HOST}
    Port        ${LOKI_PORT}
    URI         ${LOKI_URI}

    Labels      job=${JOB},host=${HOST_NAME},ip=${IP_ADDRESS},os=${OS_NAME},environment=${ENVIRONMENT},app_group=${APP_GROUP},server_role=${SERVER_ROLE},site=${SITE},source=dnf
    Line_Format json


# ============================================================
# FUTURE: MESSAGES
# Enable together with os.messages INPUT after duplicate review.
# ============================================================

# [OUTPUT]
#     Name        loki
#     Match       os.messages
#     Host        ${LOKI_HOST}
#     Port        ${LOKI_PORT}
#     URI         ${LOKI_URI}
#
#     Labels      job=${JOB},host=${HOST_NAME},ip=${IP_ADDRESS},os=${OS_NAME},environment=${ENVIRONMENT},app_group=${APP_GROUP},server_role=${SERVER_ROLE},site=${SITE},source=messages
#     Line_Format json


# ============================================================
# FUTURE: CRON
# ============================================================

# [OUTPUT]
#     Name        loki
#     Match       os.cron
#     Host        ${LOKI_HOST}
#     Port        ${LOKI_PORT}
#     URI         ${LOKI_URI}
#
#     Labels      job=${JOB},host=${HOST_NAME},ip=${IP_ADDRESS},os=${OS_NAME},environment=${ENVIRONMENT},app_group=${APP_GROUP},server_role=${SERVER_ROLE},site=${SITE},source=cron
#     Line_Format json


# ============================================================
# FUTURE: BOOT
# ============================================================

# [OUTPUT]
#     Name        loki
#     Match       os.boot
#     Host        ${LOKI_HOST}
#     Port        ${LOKI_PORT}
#     URI         ${LOKI_URI}
#
#     Labels      job=${JOB},host=${HOST_NAME},ip=${IP_ADDRESS},os=${OS_NAME},environment=${ENVIRONMENT},app_group=${APP_GROUP},server_role=${SERVER_ROLE},site=${SITE},source=boot
#     Line_Format json
EOF

chmod 755 "${BASE_DIR}" "${CONF_DIR}" "${META_DIR}"
chmod 644 "${MAIN_CONF}" "${CONF_DIR}"/*.conf "${LABELS_CONF}"

echo "[8/9] Testing Loki connectivity..."
if curl -fsS --max-time 5 "http://${LOKI_HOST}:${LOKI_PORT}/ready" >/dev/null; then
    echo "SUCCESS: Loki is reachable."
else
    echo "WARNING: Loki is not reachable."
fi

echo "[9/9] Starting Fluent Bit..."
systemctl daemon-reload
systemctl enable fluent-bit
systemctl restart fluent-bit

sleep 3

echo
echo "============================================================"
echo " INSTALLATION COMPLETED"
echo "============================================================"
echo
echo "Config structure:"
echo "  ${MAIN_CONF}"
echo "  ${CONF_DIR}/00-input-heartbeat.conf"
echo "  ${CONF_DIR}/01-input-systemd.conf"
echo "  ${CONF_DIR}/02-input-files.conf"
echo "  ${CONF_DIR}/03-filters.conf"
echo "  ${CONF_DIR}/04-output-loki.conf"
echo
echo "ACTIVE LOKI SOURCES:"
echo "  source=heartbeat (every 60 seconds; inventory/status only)"
echo "  source=systemd"
echo "  source=dnf"
echo
echo "FUTURE / COMMENTED SOURCES:"
echo "  source=messages"
echo "  source=cron"
echo "  source=boot"
echo
echo "Check status:"
echo "  systemctl status fluent-bit"
echo "  journalctl -u fluent-bit -f"
echo
