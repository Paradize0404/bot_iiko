# 🧠 Telegram-Бот с интеграцией iiko для ресторанного бизнеса

## 📌 Цель проекта

Бот автоматизирует работу ресторанного бизнеса, упрощая:

- регистрацию сотрудников по фамилии;
- синхронизацию с iiko по ключевым справочникам;
- формирование служебных документов (акты);
- расчёт зарплат по сменам, окладам, часам и выручке.

Проект построен на **async**-архитектуре с поддержкой Webhook и Polling режимов.

---

## 🗂 Структура проекта

```
.
├── bot.py                # Регистрация роутеров и запуск Dispatcher
├── config.py             # Инициализация Telegram-бота через BOT_TOKEN
├── main.py               # Запуск бота в polling-режиме
├── webhook.py            # FastAPI сервер для обработки Webhook
├── states.py             # FSM состояния для логики взаимодействия
├── Procfile              # Конфигурация для запуска на Heroku / Railway
├── .env                  # Секреты: BOT_TOKEN, DATABASE_URL
├── requirements.txt      # Список зависимостей
├── .gitignore
├── db/
│   ├── employees_db.py         # ORM и синхронизация сотрудников
│   ├── group_db.py             # ORM и синхронизация групп товаров
│   ├── nomenclature_db.py      # ORM и синхронизация номенклатуры
│   └── stores_db.py            # ORM и синхронизация складов
├── handlers/
│   ├── commands.py              # /start, /cancel, /load_*
│   ├── document.py              # Шаги создания актов (приготовление/перемещение/списание)
│   └── salary.py                # Зарплатный интерфейс (инлайн-календарь и вывод отчёта)
├── iiko/
│   └── iiko_auth.py             # Получение токена авторизации
├── keyboards/
│   ├── inline_calendar.py      # Генератор инлайн-календаря
│   └── main_keyboard.py        # Главное меню и меню документов
├── services/
│   ├── cash_shift_report.py    # Получение выручки по сменам
│   ├── employees.py            # Получение и сохранение сотрудников из XML
│   └── salary_report.py        # Полный отчёт по зарплате
└── utils/
    ├── db_stores.py            # asyncpg доступ к таблице складов
    ├── stores_kb.py            # Генерация клавиатуры для выбора склада
    └── telegram_helpers.py     # Умная отправка/редактирование сообщений и обработка ошибок
```

---

## 🔄 Основной сценарий работы

1. Пользователь запускает бота с `/start`.
2. Бот запрашивает фамилию и сверяет её с базой сотрудников.
3. В случае успеха — сохраняется `telegram_id` и показывается главное меню.
4. Далее доступны действия:
   - 📦 загрузка справочников `/load_staff`, `/load_products`, `/load_groups`, `/load_stores`
   - 🧾 создание актов: приготовления, перемещения, списания
   - 💰 просмотр зарплаты за выбранный период через инлайн-календарь

---

## ⚙️ Запуск проекта

### 1. Настройка окружения

Создай файл `.env` со следующим содержимым:

```
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
DATABASE_URL=postgresql+asyncpg://user:password@host:port/dbname
MODE=dev
```

### 2. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 3. Запуск в dev-режиме (polling)

```bash
python main.py
```

### 4. Запуск в prod-режиме (webhook)

```bash
uvicorn webhook:app --host 0.0.0.0 --port 8000
```

---

## 🧾 Команды

| Команда         | Описание                                      |
|-----------------|-----------------------------------------------|
| `/start`        | Авторизация сотрудника по фамилии             |
| `/cancel`       | Отмена действия и возврат в главное меню      |
| `/load_staff`   | Загрузка сотрудников из iiko                  |
| `/load_products`| Загрузка номенклатуры                         |
| `/load_groups`  | Загрузка групп товаров                        |
| `/load_stores`  | Загрузка складов                              |

---

## 📊 Зарплатный модуль

Находится в:

- `handlers/salary.py` — логика выбора дат через календарь
- `services/salary_report.py` — генерация отчёта
- `services/cash_shift_report.py` — выручка по сменам
- `db/employees_db.py` — ставки, проценты, тип оплаты

Поддержка:

- Окладов (monthly)
- Почасовой оплаты
- Сменной оплаты
- Процентов от выручки

---

## 📦 Документооборот

Реализован в `handlers/document.py`:

- FSM-шаги по выбору склада-источника/приёмника
- Генерация текста документа и отправка опросов
- Клавиатура выбора склада — `utils/stores_kb.py`

---

## 🧠 Использование

Когда открываешь новый диалог с ИИ — просто скинь этот `README.md`, и ИИ мгновенно поймёт всю архитектуру проекта.