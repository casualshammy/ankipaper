# kindlanki

Anki-клиент для Amazon Kindle. Позволяет авторизоваться в AnkiWeb,
просматривать колоды со статистикой и проводить ревью карточек через
встроенный браузер Kindle (без JavaScript, монохромный UI под e-ink).

## Стек

- Python 3.11+, FastAPI, Jinja2
- anki 26.x (Rust-бэкенд, нативный формат коллекции Anki)
- SQLite (нативный формат Anki, через `anki.collection` API)
- Docker

## Архитектура

1 процесс FastAPI обслуживает 1 пользователя. Состояние — в файлах
`/data/*`:

```
data/
├── collection.anki21       # SQLite, нативный формат Anki
├── collection.media/       # медиа-файлы карточек
├── hostkey.enc             # Fernet(hostKey), mode 0600
└── session.secret          # Fernet-ключ, mode 0600
```

TLS обеспечивается реверс-прокси (nginx) и не входит в scope проекта.

## Запуск через Docker

```bash
cp .env.example .env
docker compose -f deploy/compose.yml up -d
```

Сервис будет доступен на `http://localhost:8000`. Healthcheck:
`curl http://localhost:8000/healthz`.

## Первый запуск

1. Откройте `http://localhost:8000/login`.
2. Введите логин/пароль AnkiWeb.
3. После авторизации сервис скачает коллекцию (`full_download`).
4. Перейдите на `/` — там будет список колод.

## Локальная разработка

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e .
uvicorn app.main:app --reload --port 8000
```

## Хранение и бэкап

Все данные — в `deploy/data/`. Для бэкапа достаточно скопировать эту
папку при остановленном контейнере.

## Безопасность

- `hostKey` хранится зашифрованным Fernet-ключом.
- Cookie-сессии — HttpOnly, SameSite=Lax, Secure при работе за proxy.
- HTTPS не настраивается в проекте, только через reverse proxy.

## Ограничения

- Без JavaScript в шаблонах (Kindle WebKit 1.x не поддерживает).
- Цветовая схема оптимизирована под 32 градации серого.
- Один пользователь на инстанс.