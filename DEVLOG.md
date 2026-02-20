# DEVLOG — PDF Translator Service (DOMYKA)

Лог разработки и деплоя сервиса генерации PDF-предложений для CRM Planfix.

---

## Архитектура

```
Planfix CRM
    → Webhook → n8n
        → HTTP Request → pdf-translator:8080
            → OpenRouter (Gemini) — перевод описаний позиций
            → Gotenberg — HTML → PDF
            → Planfix Webhook — загрузка PDF обратно в задачу
```

**Стек:**
- FastAPI (Python) — основной сервис
- Gotenberg (Docker) — конвертация HTML → PDF
- OpenRouter / google/gemini-2.0-flash-001 — перевод
- Docker Compose — деплой на сервере `/opt/pdf-translator`
- Сеть: `n8n_net` (внешняя Docker-сеть, общая с n8n)

---

## Сервер

- Путь: `/opt/pdf-translator`
- Порт: `8080` (mapped `0.0.0.0:8080->8080/tcp`)
- Health: `curl http://localhost:8080/health`
- Логи: `docker compose logs pdf-translator -f`
- Деплой: `cd /opt/pdf-translator && git pull && docker compose up -d --build`

## n8n → сервис

- URL: `http://pdf-translator:8080/generate-offer-pdf`
- Method: POST, Body: Raw JSON
- Выражение в n8n: `={{ JSON.stringify({ body: $json.body }) }}`

---

## Формат входящего запроса

```json
{
  "body": {
    "LANGUAGE": ["Англійська"],
    "task_id": 425,
    "client_fio": "Имя Клиента",
    "client_phone": "+34600000000",
    "client_mail": "email@example.com",
    "client_nif": "",
    "positions": "<br/><b>Покупка...</b><br/>Concepto : ...<br/>Ціна : 1000<br/>..."
  }
}
```

---

## Решённые проблемы

### 1. Парсинг позиций без LLM
**Проблема:** n8n workflow использовал два LLM-вызова для парсинга позиций — нестабильно, дорого.
**Решение:** Regex-парсер `parse_positions()` в `main.py`. Надёжно извлекает Concepto/Ціна/Кол-во/IVA%/IVA сум/Сума без IVA/Підсумок из HTML-блоков Planfix.

### 2. docker-compose не читал .env
**Проблема:** `OPENROUTER_API_KEY` был пуст внутри контейнера несмотря на наличие в `.env`.
**Решение:** Заменили `environment:` с host-интерполяцией на `env_file: .env` в `docker-compose.yml`.

### 3. Модель OpenRouter 404
**Проблема:** `google/gemini-flash-1.5` не существует.
**Решение:** `sed -i 's/gemini-flash-1.5/gemini-2.0-flash-001/' /opt/pdf-translator/.env`

### 4. VAT колонка — перенос €
**Проблема:** `21% (2 940,00) €` переносилось на две строки, `€` падал вниз.
**Решение:** Увеличили `.col-iva` с 17% → 21%, добавили `white-space: nowrap`.
**Коммит:** `fix: widen IVA column to prevent euro sign wrapping`

### 5. n8n передавал [object Object]
**Проблема:** При передаче body через "Using Fields Below" объект `positions` сериализовался как `[object Object]`.
**Решение:** Переключились на Raw body с выражением `={{ JSON.stringify({ body: $json.body }) }}`.

### 6. n8n подключался к localhost вместо контейнера
**Проблема:** `ECONNREFUSED 127.0.0.1:8080` — n8n искал сервис на localhost.
**Решение:** URL изменён на `http://pdf-translator:8080/...` (имя контейнера в Docker-сети).

---

## Конфигурация (.env на сервере)

```env
TRANSLATION_PROVIDER=openrouter
OPENROUTER_API_KEY=<ключ>
OPENROUTER_MODEL=google/gemini-2.0-flash-001
PLANFIX_WEBHOOK_URL=<url>
GOTENBERG_URL=http://gotenberg:3000/forms/chromium/convert/html
```

---

## Поддерживаемые языки

Статические метки (labels.py): EN, ES, UK, RU, DE, FR, PL, IT
Статические блоки "Información Importante": EN, UK, RU, DE, FR
Остальные языки (~20) — перевод через OpenRouter.

---

## Pending / TODO

- [ ] Протестировать все языки (DE, UK, FR, PL, IT) с реальной сделкой
- [ ] Получить обратную связь от клиента по визуалу PDF
- [ ] Обновить `OPENROUTER_MODEL` в `.env.server` и git (сейчас только на сервере через sed)
- [ ] Настроить прямой вызов из Planfix (без n8n-прослойки)
