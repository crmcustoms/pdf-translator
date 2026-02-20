# PDF Translator App

Сервис принимает webhook payload из сделки, парсит позиции, переводит описания, генерирует PDF и отправляет файл в Planfix.

## Быстрый запуск (локально)

1. Перейдите в папку:

```bash
cd pdf_translator_app
```

2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Создайте `.env` из примера и заполните ключи:

```bash
copy .env.example .env
```

4. Запустите сервис:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Проверка:
- `GET /health`
- `POST /generate-offer-pdf`

## Запуск в Docker

```bash
docker build -t pdf-translator-app .
docker run --env-file .env -p 8080:8080 pdf-translator-app
```

## Пример запроса

```json
{
  "body": {
    "LANGUAGE": ["Англійська"],
    "client_fio": "Personal Property Natalia Bogdanova",
    "client_phone": "+34672933034",
    "client_mail": "",
    "client_nif": "",
    "positions": "<br/><b>Покупка: товары/услуги</b><br/>Concepto : Монтажно-Демонтажные работы<br/>Ціна : 14000<br/>IVA% : 21<br/>Кол-во : 1<br/>IVA сум : 2940.00<br/>Сума без IVA : 14000.00<br/>Підсумок : 16940.00",
    "task_id": 425
  }
}
```

## Конфигурация переводчика

`TRANSLATION_PROVIDER`:
- `none` — без перевода (самый стабильный режим для первого запуска)
- `deepl` — перевод через DeepL API
- `openai` — перевод через OpenAI API

## Интеграция с n8n

В n8n оставьте только:
1) trigger/webhook из CRM  
2) HTTP Request в этот сервис (`POST /generate-offer-pdf`)  
3) обработку ответа (лог/нотификация)

Так вы убираете из n8n всю нестабильную логику парсинга/перевода/PDF-сборки.

## Готовый быстрый тест (PowerShell)

Из папки `pdf_translator_app`:

1. Локальный тест приложения:

```powershell
.\run_local_test.ps1 -BaseUrl "http://127.0.0.1:8080"
```

2. Тест через n8n gateway webhook:

```powershell
.\run_n8n_gateway_test.ps1 -GatewayWebhookUrl "https://YOUR_N8N_DOMAIN/webhook/offer-pdf-gateway"
```

3. Если нужен другой payload:

```powershell
.\run_local_test.ps1 -PayloadPath ".\test_payload.json"
```
