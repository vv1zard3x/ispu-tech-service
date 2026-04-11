# Ремонт 4-48

Система информационной поддержки процесса ремонта техники.

Веб-приложение на `Django + SQLite + Django Templates` для поддержки полного цикла ремонта техники:
- прием устройства и оформление заявки,
- назначение исполнителя,
- диагностика и расчет сметы,
- согласование с клиентом,
- складские операции по деталям,
- завершение работ и прием оплаты.

## Технологический стек
- Python 3.13
- uv
- Django 6
- SQLite
- Django Templates

## Быстрый запуск
```bash
uv sync
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver
```

Система будет доступна по адресу `http://127.0.0.1:8000`.

## Тестовые пользователи
После команды `seed_demo` доступны:
- `admin / admin12345` — суперпользователь (Django admin),
- `manager / manager12345` — менеджер,
- `technician / technician12345` — исполнитель,
- `warehouse / warehouse12345` — склад.

## Основные URL
- `/` — дашборд
- `/orders/` — список заявок
- `/stock/` — остатки склада
- `/accounts/login/` — вход
- `/admin/` — админ-панель

## Роли и сценарии
- **Менеджер**: создаёт заявку, назначает исполнителя, фиксирует решение клиента, принимает оплату.
- **Исполнитель**: проводит диагностику, добавляет работы и детали, завершает ремонт.
- **Склад**: ведет остатки и резервирует детали под согласованные заказы.
- **Администратор**: управляет учетными записями и справочниками через Django admin.

## Проверка качества
```bash
uv run python manage.py check
uv run python manage.py test
```

## Отчет
Подробный отчет находится в файле `report/REPORT.md`.

## Деплой на сервер (Docker Compose, порт 8000)

Для домена `vv1zard3x.com` и запуска на `8000`:

1. Подготовьте env:
```bash
cp .env.example .env
```

2. Запустите сервис:
```bash
docker compose up -d --build
```

3. Проверка:
- приложение: `http://vv1zard3x.com:8000`
- локально на сервере: `http://127.0.0.1:8000`

### Что используется
- `Dockerfile` — сборка Django + Gunicorn.
- `docker-compose.yml` — запуск контейнера, порт `8000:8000`.
- `.env.example` — пример переменных окружения.
- SQLite хранится в `./data/db.sqlite3` (через `SQLITE_PATH`).

### Важно
- В `DJANGO_ALLOWED_HOSTS` и `DJANGO_CSRF_TRUSTED_ORIGINS` укажите ваш реальный домен.
- Для HTTPS (через reverse proxy) добавьте `https://...` версии домена в `DJANGO_CSRF_TRUSTED_ORIGINS`.
- Если ранее использовался bind `./db.sqlite3:/app/db.sqlite3`, удалите ошибочно созданную директорию/файл `db.sqlite3` на сервере и переходите на `./data:/app/data`.
