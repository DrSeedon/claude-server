# Claude Server — Web Terminal для Claude Code

## Что это
Веб-интерфейс для удалённого управления несколькими Claude Code сессиями через браузер (мобилка/планшет/десктоп). Каждый проект = отдельная tmux-сессия с Claude Code внутри.

## Стек
- **Backend**: Python 3 + aiohttp, без фреймворков
- **Frontend**: Vanilla JS, xterm.js (терминал в браузере), single-file `index.html`
- **Терминал**: tmux + pty (псевдотерминал), WebSocket для real-time I/O
- **Авторизация**: пароль → SHA-256 + salt, Bearer-токен (7 дней), без БД

## Архитектура
```
browser ↔ WebSocket ↔ aiohttp ↔ pty ↔ tmux attach ↔ claude code
browser ↔ REST API  ↔ aiohttp ↔ tmux/subprocess
```

### Файлы
| Файл | Назначение |
|---|---|
| `server.py` | Весь бэкенд: auth, REST API, WebSocket, файловый менеджер |
| `index.html` | Весь фронтенд: логин, список проектов, терминал, файлы, превью |
| `manager.sh` | Хелпер для tmux: list/ensure/stop сессий |
| `start-all.sh` | Точка входа для systemd |
| `setup.py` | Одноразовый скрипт для установки пароля |
| `config.json` | Хеш пароля + соль (НЕ коммитить с реальными данными) |
| `claude-server.service` | systemd unit |
| `static/` | xterm.js + аддоны (fit, web-links) |

### API endpoints
| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/login` | Авторизация по паролю → токен |
| GET | `/api/projects` | Список проектов с состояниями (busy/ready/viewed) |
| POST | `/api/ensure` | Создать/подключить tmux-сессию |
| POST | `/api/stop` | Остановить tmux-сессию |
| GET | `/api/files` | Листинг файлов проекта |
| GET | `/api/files/view` | Отдать файл (изображение/текст/etc) |
| WS | `/ws/{session}` | WebSocket-терминал |

## Деплой
- Работает как **systemd-сервис** `claude-server.service`
- Порт: **8080** (http, без TLS — предполагается reverse proxy или SSH tunnel)
- venv в `./venv`, единственная зависимость — `aiohttp`

### Команды
```bash
# Статус сервиса
sudo systemctl status claude-server

# Перезапуск после правок
sudo systemctl restart claude-server

# Логи
journalctl -u claude-server -f

# Установка пароля (одноразово)
python setup.py
```

## Что можно менять / добавлять
- Кнопки тулбара в `index.html` (секция `.toolbar`) — отправляют escape-последовательности через WebSocket
- Новые API endpoints в `server.py` — стандартный aiohttp роутинг
- Логику обнаружения проектов — `_build_path_map()` и `get_projects()` в `server.py`
- Стили — всё inline в `<style>` в `index.html`
- Состояния сессий (busy/ready/viewed) — `_get_session_states()` в `server.py`

## Ограничения / Особенности
- Весь фронт в одном HTML-файле — так задумано, не разбивать
- Весь бэк в одном Python-файле — так задумано, не разбивать
- Нет TLS — использовать reverse proxy или SSH tunnel
- Токены в памяти — рестарт сервера = перелогин
- Проекты обнаруживаются автоматически из `~/.claude/projects/`
- auto_claude: при создании новой сессии через WebSocket автоматически запускает `claude --dangerously-skip-permissions --resume`

## PROCESS RULES
