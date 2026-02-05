#!/bin/bash

# start.sh — Стартовый скрипт для CentOS 7
# Проверяет монтирование NAS, читает из config.py или спрашивает путь,
# монтирует NAS, затем запускает Docker Compose.

set -e
set -o pipefail

### 🍀 Настройки
CONFIG_FILE="api/app/config.py"
NAS_MOUNT_POINT_DEFAULT="/mnt/nas"

echo "=== RAG System Startup Script (CentOS 7) ==="

### 1) Проверки утилит
echo "Проверяем необходимые утилиты..."
for util in docker docker-compose mount grep awk python3; do
    if ! command -v $util &> /dev/null; then
        echo "❌ Утилита $util не найдена. Установите и повторите."
        exit 1
    fi
done
echo "✔️ Все утилиты найдены."

### 2) Чтение пути NAS из config.py
NAS_PATH=""
if [ -f "$CONFIG_FILE" ]; then
    NAS_PATH=$(grep -E "NAS_MOUNT_POINT" "$CONFIG_FILE" | awk -F'=' '{print $2}' | tr -d " '\"")
    NAS_PATH=$(echo "$NAS_PATH" | sed 's/ //g')
fi

### 3) Запросить путь, если не задан в config.py
if [[ -z "$NAS_PATH" || "$NAS_PATH" == "None" ]]; then
    echo
    echo "Путь к NAS не найден в config.py."
    read -p "Введите путь к сетевому ресурсу SMB/CIFS (например //192.168.1.50/share): " NAS_PATH
    if [[ -z "$NAS_PATH" ]]; then
        echo "❗ Путь не введен. Пропускаем монтирование NAS."
    fi
fi

### 4) Монтирование NAS (если указано)
if [[ -n "$NAS_PATH" ]]; then
    echo
    echo "Попытка монтировать NAS:"
    echo "  SMB: $NAS_PATH"
    MOUNT_POINT=${NAS_MOUNT_POINT:-$NAS_MOUNT_POINT_DEFAULT}

    mkdir -p "$MOUNT_POINT"
    echo "Папка для монтирования: $MOUNT_POINT"

    # Чтение учетных данных из config
    NAS_USER=$(grep -E "NAS_USERNAME" "$CONFIG_FILE" | awk -F'=' '{print $2}' | tr -d " '\"")
    NAS_PASS=$(grep -E "NAS_PASSWORD" "$CONFIG_FILE" | awk -F'=' '{print $2}' | tr -d " '\"")

    if [[ -z "$NAS_USER" || -z "$NAS_PASS" ]]; then
        read -p "Введите имя пользователя для NAS: " NAS_USER
        read -s -p "Введите пароль для NAS: " NAS_PASS
        echo
    fi

    # Монтировать read-only
    echo "Монтируем NAS как read-only..."
    sudo mount -t cifs "$NAS_PATH" "$MOUNT_POINT" \
      -o "username=$NAS_USER,password=$NAS_PASS,ro,vers=3.0"
    if [[ $? -ne 0 ]]; then
        echo "❌ Не удалось смонтировать $NAS_PATH в $MOUNT_POINT"
        echo "Проверьте настройки и подключение."
        exit 1
    fi
    echo "✔️ NAS смонтирован в $MOUNT_POINT"
else
    echo "⚠️ Пропущено монтирование NAS."
fi

### 5) Запуск Docker Compose
echo
echo "Запускаем Docker Compose..."
docker-compose down
docker-compose up -d --build

echo
echo "Ждем запуска контейнеров..."
sleep 3
docker-compose ps

echo
echo "🎉 Система RAG запущена!"

echo "UI/API: http://127.0.0.1:8000"
echo "Admin (если включен): http://127.0.0.1:8000/admin"
