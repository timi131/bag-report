"""Собирает коллекцию Postman REDSMS.postman_collection.json.

Скрипт автобага берётся из redsms-lib.js и кладётся в переменную коллекции redsms_lib.
После правки redsms-lib.js запустите:  python postman/build_collection.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = (HERE / "redsms-lib.js").read_text(encoding="utf-8")


def tests(*lines):
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": list(lines)}}]


def json_body(obj):
    return {"mode": "raw", "raw": json.dumps(obj, ensure_ascii=False, indent=2),
            "options": {"raw": {"language": "json"}}}


def req(name, method, path, description, body=None, events=None, headers=None):
    item = {
        "name": name,
        "request": {
            "method": method,
            "header": headers or [],
            "url": "{{redsms_url}}" + path,
            "description": description,
        },
    }
    if body is not None:
        item["request"]["body"] = body
    if events:
        item["event"] = events
    return item


api = [
    req("Проверка сервера", "GET", "/api/health", "Сервер REDSMS отвечает `{\"app\": \"redsms\"}`.",
        events=tests("pm.test('Сервер REDSMS на связи', () => pm.expect(pm.response.json().app).to.eql('redsms'));")),
    req("Список багов", "GET", "/api/bugs", "Все баги. Первый id сохраняется в переменную `bug_id`.",
        events=tests(
            "const bugs = pm.response.json().bugs;",
            "pm.test('Ответ 200', () => pm.response.to.have.status(200));",
            "if (bugs.length) pm.collectionVariables.set('bug_id', bugs[0].id);",
            "console.log('Багов: ' + bugs.length);")),
    req("Завести баг", "POST", "/api/bugs",
        "Нужен только `title`. Шаги можно передать массивом. Сервер сам выдаст id, статус «Новый» и даты. "
        "Допустимые значения: severity — blocker, critical, major, minor, trivial; priority — high, medium, low; "
        "repro — always, often, sometimes, once; env.stand — test, stage, prod. "
        "С `\"dedupe\": true` сервер вернёт уже открытый баг с тем же заголовком вместо нового.",
        body=json_body({
            "title": "Кнопка «Оплатить» не реагирует в корзине",
            "severity": "critical",
            "priority": "high",
            "env": {"version": "2.14.0", "platform": "macOS 15, Safari 18", "stand": "test", "account": "Тестовый покупатель"},
            "steps": ["Войти под тестовым пользователем", "Добавить товар в корзину", "Нажать «Оплатить»"],
            "expected": "Открывается страница оплаты",
            "actual": "Ничего не происходит, в консоли TypeError",
            "repro": "always",
        }),
        headers=[{"key": "Content-Type", "value": "application/json"}],
        events=tests(
            "pm.test('Баг заведён', () => pm.expect(pm.response.code).to.be.oneOf([200, 201]));",
            "const bug = pm.response.json();",
            "pm.collectionVariables.set('bug_id', bug.id);",
            "console.log('Заведён BUG-' + bug.id.slice(-5).toUpperCase());")),
    req("Получить баг", "GET", "/api/bugs/{{bug_id}}", "Один баг по `bug_id`."),
    req("Сменить статус", "PATCH", "/api/bugs/{{bug_id}}",
        "Дописывает поля в баг. Статусы: new, in_progress, fixed, closed, rejected. Дата изменения ставится сама.",
        body=json_body({"status": "in_progress"}),
        headers=[{"key": "Content-Type", "value": "application/json"}]),
    req("Загрузить вложение", "POST", "/api/files",
        "Во вкладке Body выберите файл (binary) и впишите его имя в заголовок X-Filename. "
        "Ответ `{id, url}` добавьте в поле `attachments` бага: `[{\"id\": \"…\", \"name\": \"…\", \"type\": \"image/png\", \"url\": \"files/…\"}]`.",
        body={"mode": "file", "file": {"src": ""}},
        headers=[{"key": "Content-Type", "value": "image/png"}, {"key": "X-Filename", "value": "screenshot.png"}]),
    req("Удалить баг", "DELETE", "/api/bugs/{{bug_id}}", "Удаляет баг `bug_id` безвозвратно."),
]

example = req(
    "Пример: падающий тест",
    "GET", "/api/health",
    "Тест специально ждёт неверный статус. Отправьте запрос — в трекере появится баг «[Postman] Пример: падающий тест…». "
    "Повторная отправка не создаст дубликат, пока баг открыт.",
    events=tests(
        "eval(pm.variables.get('redsms_lib'));",
        "",
        "redsms.check('Статус ответа 201', () => pm.response.to.have.status(201)); // специально неверно",
        "redsms.check('В ответе есть поле app', () => pm.expect(pm.response.json()).to.have.property('app'));",
        "",
        "redsms.report();"),
)

collection = {
    "info": {
        "name": "REDSMS Баг-репорт",
        "description": (
            "API трекера «Баг-репорт REDSMS» и автосоздание баг-репортов при падении тестов.\n\n"
            "1. Во вкладке Variables задайте `redsms_url` — адрес трекера, без слеша в конце. "
            "Если на сайте стоит пароль, задайте `redsms_user` и `redsms_password`.\n"
            "2. Чтобы падения тестов в вашей коллекции становились багами, скопируйте переменную `redsms_lib` "
            "(и `redsms_url`) в свою коллекцию или окружение и пишите тесты так:\n\n"
            "```js\n"
            "eval(pm.variables.get('redsms_lib'));\n"
            "redsms.check('Статус 200', () => pm.response.to.have.status(200));\n"
            "redsms.report();\n"
            "```"
        ),
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "auth": {
        "type": "basic",
        "basic": [
            {"key": "username", "value": "{{redsms_user}}", "type": "string"},
            {"key": "password", "value": "{{redsms_password}}", "type": "string"},
        ],
    },
    "variable": [
        {"key": "redsms_url", "value": "http://localhost:8501"},
        {"key": "redsms_user", "value": ""},
        {"key": "redsms_password", "value": ""},
        {"key": "redsms_stand", "value": "test"},
        {"key": "redsms_version", "value": ""},
        {"key": "bug_id", "value": ""},
        {"key": "redsms_lib", "value": LIB},
    ],
    "item": [
        {"name": "API трекера", "item": api},
        {"name": "Автобаг при падении теста", "item": [example]},
    ],
}

out = HERE / "REDSMS.postman_collection.json"
out.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Готово: {out}")
