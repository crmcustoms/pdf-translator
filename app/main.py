import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.labels import LABELS, STATIC_BLOCKS_ES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


LANGUAGE_MAP = {
    "англійська": "en",
    "английский": "en",
    "english": "en",
    "іспанська": "es",
    "испанский": "es",
    "spanish": "es",
    "німецька": "de",
    "немецкий": "de",
    "german": "de",
    "французька": "fr",
    "французский": "fr",
    "french": "fr",
    "українська": "uk",
    "украинский": "uk",
    "ukrainian": "uk",
    "російська": "ru",
    "русский": "ru",
    "russian": "ru",
    "польська": "pl",
    "польский": "pl",
    "polish": "pl",
    "італійська": "it",
    "итальянский": "it",
    "italian": "it",
    "португальська": "pt",
    "португальский": "pt",
    "portuguese": "pt",
    "нідерландська": "nl",
    "нидерландский": "nl",
    "dutch": "nl",
    "чеська": "cs",
    "чешский": "cs",
    "czech": "cs",
    "румунська": "ro",
    "румынский": "ro",
    "romanian": "ro",
    "угорська": "hu",
    "венгерский": "hu",
    "hungarian": "hu",
    "шведська": "sv",
    "шведский": "sv",
    "swedish": "sv",
    "норвезька": "no",
    "норвежский": "no",
    "norwegian": "no",
    "датська": "da",
    "датский": "da",
    "danish": "da",
    "фінська": "fi",
    "финский": "fi",
    "finnish": "fi",
    "грецька": "el",
    "греческий": "el",
    "greek": "el",
    "турецька": "tr",
    "турецкий": "tr",
    "turkish": "tr",
}

LOGO_URL = "https://s3.us-east-1.amazonaws.com/crmcustoms.site/Group+8.png"

SELLER_INFO = {
    "name": "DENYS MYKOLAIENKO",
    "nie": "Z2785512X",
    "address_line1": "Calle Frankfurt 11, apt 30,",
    "address_line2": "Ojén, Málaga, España, 29610",
    "address_line3": "",
    "phone": "+34 621 347 502",
    "email": "office@domyka.es",
    "web": "https://domyka.es",
    "bank": "SANTANDER",
    "iban": "ES30 0049 7366 5422 1002 9681",
    "swift": "BSCHESMMXXX",
    "bizum": "621347502",
}


class RequestPayload(BaseModel):
    LANGUAGE: Optional[list[str] | str] = None
    client_fio: str = ""
    client_phone: str = ""
    client_mail: str = ""
    client_nif: str = ""
    direction: str = ""  # client address from Planfix (Задача.Контрагент.Адрес)
    positions: str = ""
    task_id: int | str


class WebhookBody(BaseModel):
    body: RequestPayload


class Item(BaseModel):
    description: str = ""
    characteristics: str = ""
    unit_price: float = 0.0
    quantity: float = 0.0
    tax_percent: float = 0.0
    tax_amount: float = 0.0
    net_amount: float = 0.0
    total_amount: float = 0.0


app = FastAPI(title="PDF Translator Service", version="2.0.0")


def normalize_language(raw: Any) -> str:
    if isinstance(raw, list) and raw:
        raw = raw[0]
    value = str(raw or "").strip().lower()
    if value in LANGUAGE_MAP:
        return LANGUAGE_MAP[value]
    if re.fullmatch(r"[a-z]{2}", value):
        return value
    return "en"


def to_number(input_value: Any) -> float:
    cleaned = (
        str(input_value or "")
        .replace(" ", "")
        .replace(",", ".")
        .replace("\u00a0", "")
    )
    cleaned = re.sub(r"[^0-9.-]", "", cleaned)
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def strip_html(text: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    value = re.sub(r"</b>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]*>", "", value)
    value = value.replace("&nbsp;", " ").replace("\r", "")
    return value.strip()


def extract(block: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, block, flags=re.IGNORECASE | re.DOTALL)
        if match and match.group(1):
            return match.group(1).strip()
    return ""


def parse_positions(raw_positions: str) -> list[Item]:
    text = strip_html(raw_positions)
    blocks = [
        part.strip()
        for part in re.split(r"\n(?=Concepto\s*:|Описание\s*:|Description\s*:)", text)
        if re.search(r"Concepto\s*:|Описание\s*:|Description\s*:", part, re.IGNORECASE)
    ]

    result: list[Item] = []
    for block in blocks:
        description = extract(
            block,
            [
                r"Concepto\s*:\s*([\s\S]*?)(?=\n(?:Ціна|Цена|Price|Cantidad|Кол-во|Qty|Quantity|IVA%|Tax%|Tax|Сума без IVA|Subtotal|Підсумок|Total)\s*:|$)",
                r"Описание\s*:\s*([\s\S]*?)(?=\n(?:Ціна|Цена|Price|Cantidad|Кол-во|Qty|Quantity|IVA%|Tax%|Tax|Сума без IVA|Subtotal|Підсумок|Total)\s*:|$)",
                r"Description\s*:\s*([\s\S]*?)(?=\n(?:Ціна|Цена|Price|Cantidad|Кол-во|Qty|Quantity|IVA%|Tax%|Tax|Сума без IVA|Subtotal|Підсумок|Total)\s*:|$)",
            ],
        )
        unit_price = to_number(extract(block, [r"(?:Ціна|Цена|Price)\s*:\s*([0-9.,\s-]+)"]))
        quantity = to_number(
            extract(block, [r"(?:Кол-во|Cantidad|Qty|Quantity)\s*:\s*([0-9.,\s-]+)"])
        )
        tax_percent = to_number(extract(block, [r"(?:IVA%|Tax%)\s*:\s*([0-9.,\s-]+)"]))
        tax_amount = to_number(
            extract(block, [r"(?:IVA сум|IVA sum|Tax amount)\s*:\s*([0-9.,\s-]+)"])
        )
        net_amount = to_number(
            extract(block, [r"(?:Сума без IVA|Importe sin IVA|Subtotal)\s*:\s*([0-9.,\s-]+)"])
        )
        total_amount = to_number(extract(block, [r"(?:Підсумок|Total)\s*:\s*([0-9.,\s-]+)"]))

        if net_amount == 0 and unit_price and quantity:
            net_amount = round(unit_price * quantity, 2)
        if tax_amount == 0 and net_amount and tax_percent:
            tax_amount = round(net_amount * tax_percent / 100, 2)
        if total_amount == 0 and net_amount:
            total_amount = round(net_amount + tax_amount, 2)

        result.append(
            Item(
                description=description,
                unit_price=unit_price,
                quantity=quantity,
                tax_percent=tax_percent,
                tax_amount=tax_amount,
                net_amount=net_amount,
                total_amount=total_amount,
            )
        )
    return result


def fmt_money(value: float) -> str:
    """Format number as Spanish-style: 23 025,00"""
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return formatted


async def translate_texts(texts: list[str], target_language: str) -> list[str]:
    if not texts or target_language == "es":
        return texts
    provider = os.getenv("TRANSLATION_PROVIDER", "none").lower()
    if provider == "none":
        return texts
    if provider == "openrouter":
        return await translate_with_openrouter(texts, target_language)
    if provider == "deepl":
        return await translate_with_deepl(texts, target_language)
    if provider == "openai":
        return await translate_with_openai(texts, target_language)
    return texts


async def translate_with_openrouter(texts: list[str], target_language: str) -> list[str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.error("OPENROUTER_API_KEY is empty — translation skipped")
        return texts
    model = os.getenv("OPENROUTER_MODEL", "google/gemini-flash-1.5")
    logger.info("Translating %d texts to '%s' via OpenRouter model '%s'", len(texts), target_language, model)
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://domyka.es",
    }
    prompt_data = {
        "target_language": target_language,
        "texts": texts,
    }
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional translator specializing in business and commercial documents. "
                    "Translate all texts into the target language using natural, professional business language. "
                    "Avoid overly literal or academic translations — use terms common in real business correspondence. "
                    "For example: 'Autónomo' → 'Self-employed' (not 'Sole Trader'); prefer natural business equivalents. "
                    "Keep brand names, model numbers, product codes, and measurements unchanged. "
                    "Return ONLY valid JSON: {\"translated\": [\"...\", \"...\"]} "
                    "with exactly the same number of items as input."
                ),
            },
            {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
        ],
        "temperature": 0,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=body)
        logger.info("OpenRouter response status: %d", response.status_code)
        if response.status_code >= 400:
            logger.error("OpenRouter error: %s", response.text[:500])
            return texts
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        logger.info("OpenRouter raw content: %s", content[:300])
        # strip possible markdown fences
        content = re.sub(r"```json|```", "", content).strip()
        try:
            parsed = json.loads(content)
            translated = parsed.get("translated", [])
            if isinstance(translated, list) and len(translated) == len(texts):
                logger.info("Translation successful: %s", translated)
                return [str(x) for x in translated]
            else:
                logger.error("Translation count mismatch: got %d, expected %d", len(translated), len(texts))
        except json.JSONDecodeError as e:
            logger.error("JSON parse error: %s | content: %s", e, content[:300])
    return texts


async def translate_with_deepl(texts: list[str], target_language: str) -> list[str]:
    api_key = os.getenv("DEEPL_API_KEY", "")
    if not api_key:
        return texts
    url = os.getenv("DEEPL_URL", "https://api-free.deepl.com/v2/translate")
    payload = {"text": texts, "target_lang": target_language.upper()}
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, data=payload, headers=headers)
        if response.status_code >= 400:
            return texts
        data = response.json()
        translated = [t.get("text", "") for t in data.get("translations", [])]
        if len(translated) != len(texts):
            return texts
        return translated


async def translate_with_openai(texts: list[str], target_language: str) -> list[str]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return texts
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    prompt_data = {"target_language": target_language, "texts": texts}
    body = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON translator. "
                    "Return only valid JSON: {\"translated\": [\"...\"]}."
                ),
            },
            {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
        ],
        "temperature": 0,
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(url, headers=headers, json=body)
        if response.status_code >= 400:
            return texts
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "{\"translated\":[]}")
        )
        try:
            parsed = json.loads(content)
            translated = parsed.get("translated", [])
            if isinstance(translated, list) and len(translated) == len(texts):
                return [str(x) for x in translated]
        except json.JSONDecodeError:
            pass
    return texts


async def translate_static_blocks(lang: str) -> dict[str, str]:
    """
    Translate the static text blocks (Información Importante, signatures, etc.)
    For 'es' — return Spanish originals directly.
    For known langs — use prebuilt labels if available.
    For unknown langs — translate via LLM.
    """
    if lang == "es":
        return STATIC_BLOCKS_ES

    # Try prebuilt
    from app.labels import STATIC_BLOCKS
    if lang in STATIC_BLOCKS:
        return STATIC_BLOCKS[lang]

    # Translate via LLM
    provider = os.getenv("TRANSLATION_PROVIDER", "none").lower()
    if provider == "none":
        return STATIC_BLOCKS_ES  # fallback to Spanish

    keys = list(STATIC_BLOCKS_ES.keys())
    values = list(STATIC_BLOCKS_ES.values())
    translated_values = await translate_texts(values, lang)

    return dict(zip(keys, translated_values))


async def get_task_characteristics(task_id: Any) -> list[str]:
    """
    Fetch characteristics for each position via Planfix REST API.
    Step 1: GET /rest/analytic/ — find analytic type ID for "Покупка: товары/услуги"
    Step 2: POST /rest/analytic/{id}/list — get records filtered by task_id
    Returns list of characteristics strings indexed by position order.
    """
    base_url = os.getenv("PLANFIX_BASE_URL", "").rstrip("/")
    token = os.getenv("PLANFIX_API_TOKEN", "")
    if not base_url or not token:
        logger.info("PLANFIX_API_TOKEN or PLANFIX_BASE_URL not set — skipping characteristics")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Step 1: Get list of analytic types to find "Покупка" type ID
            types_resp = await client.get(f"{base_url}/rest/analytic/", headers=headers)
            logger.info("Analytic types status: %d", types_resp.status_code)
            if types_resp.status_code >= 400:
                logger.error("Analytic types error: %s", types_resp.text[:300])
                return []
            types_data = types_resp.json()
            logger.info("Analytic types keys: %s", list(types_data.keys()))

            analytic_types = (
                types_data.get("analyticTypes")
                or types_data.get("analytics")
                or types_data.get("items")
                or []
            )
            logger.info("Found %d analytic types", len(analytic_types))

            analytic_type_id = None
            for at in analytic_types:
                name = at.get("name", "") or at.get("title", "")
                logger.info("Analytic type: id=%s name=%s", at.get("id"), name)
                if "покупка" in name.lower():
                    analytic_type_id = at.get("id")
                    break

            if not analytic_type_id:
                logger.error("Could not find 'Покупка' analytic type in list")
                return []

            # Step 2: Get records for this analytic filtered by task_id
            records_url = f"{base_url}/rest/analytic/{analytic_type_id}/list"
            records_body = {
                "offset": 0,
                "pageSize": 100,
                "filters": [{"type": 1, "operator": "equal", "field": "task", "value": str(task_id)}],
            }
            rec_resp = await client.post(records_url, headers=headers, json=records_body)
            logger.info("Analytic records status: %d", rec_resp.status_code)
            if rec_resp.status_code >= 400:
                logger.error("Analytic records error: %s", rec_resp.text[:300])
                return []
            rec_data = rec_resp.json()
            logger.info("Analytic records keys: %s", list(rec_data.keys()))

            records = (
                rec_data.get("analytics")
                or rec_data.get("records")
                or rec_data.get("items")
                or []
            )
            logger.info("Found %d analytic records for task %s", len(records), task_id)

            result = []
            for record in records:
                char_value = ""
                fields = record.get("customFieldData") or record.get("fields") or []
                for field in fields:
                    field_name = (field.get("name") or field.get("title") or "").lower()
                    if "характер" in field_name:
                        char_value = str(field.get("value") or "").strip()
                        break
                result.append(char_value)

            logger.info("Characteristics extracted: %s", result)
            return result

    except Exception as e:
        logger.error("Error fetching Planfix characteristics: %s", e)
    return []


def render_html(
    labels: dict[str, str],
    static_blocks: dict[str, str],
    items: list[Item],
    client_data: dict[str, str],
    doc_number: str,
    doc_date: str,
    tax_percent: float,
) -> str:

    net_total = round(sum(x.net_amount for x in items), 2)
    tax_total = round(sum(x.tax_amount for x in items), 2)
    grand_total = round(sum(x.total_amount for x in items), 2)

    # Build item rows
    rows = []
    for item in items:
        qty_int = int(item.quantity) if item.quantity == int(item.quantity) else item.quantity
        rows.append(f"""
        <tr>
            <td class="col-concepto">{item.description}{f'<br><span class="char">{item.characteristics}</span>' if item.characteristics else ''}</td>
            <td class="col-cantidad">{qty_int} x {fmt_money(item.unit_price)} &euro;</td>
            <td class="col-base">{fmt_money(item.net_amount)} &euro;</td>
            <td class="col-iva">{int(item.tax_percent)}% ({fmt_money(item.tax_amount)}) &euro;</td>
        </tr>""")
    items_html = "".join(rows)

    # Client block fields
    client_nif_row = f'<p><strong>{labels.get("nif","NIF")}/NIF:</strong> {client_data.get("nif","")}</p>' if client_data.get("nif") else f'<p><strong>{labels.get("nif","NIF")}/NIF:</strong></p>'
    client_addr_row = f'<p><strong>{labels.get("address","Dirección")}:</strong> {client_data.get("address","")}</p>'
    client_phone_row = f'<p><strong>{labels.get("phone","Telf")}:</strong> {client_data.get("phone","")}</p>' if client_data.get("phone") else ""
    client_email_row = f'<p><strong>{labels.get("email","Email")}:</strong> {client_data.get("email","")}</p>' if client_data.get("email") else ""

    # Static blocks
    info_importante_title = static_blocks.get("info_title", "Información Importante:")
    info_lines = [
        static_blocks.get("info_1", ""),
        static_blocks.get("info_2", ""),
        static_blocks.get("info_3", ""),
        static_blocks.get("info_4", ""),
        static_blocks.get("info_5", ""),
        static_blocks.get("info_6", ""),
        static_blocks.get("info_7", ""),
        static_blocks.get("info_8", ""),
        static_blocks.get("info_9", ""),
        static_blocks.get("info_10", ""),
        static_blocks.get("info_11", ""),
    ]
    info_html = "<br>---<br>".join(f'<p>{line}</p>' for line in info_lines if line)

    firma_cliente = static_blocks.get("firma_cliente", "Firma cliente")
    firma_autonomo = static_blocks.get("firma_autonomo", "Firma Autónomo")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{doc_number}</title>
<style>
@page {{ margin: 2cm 2cm 2cm 2cm; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 10pt;
    line-height: 1.4;
    color: #000;
}}

/* HEADER */
.header-wrap {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 24px;
}}
.logo img {{
    height: 48px;
}}
.doc-meta {{
    text-align: right;
    font-size: 10pt;
}}
.doc-meta p {{ margin-bottom: 2px; }}

/* TWO COLUMNS: Proveedor + Cliente */
.info-columns {{
    display: flex;
    gap: 24px;
    margin-bottom: 24px;
}}
.info-col {{
    flex: 1;
}}
.info-col h2 {{
    font-size: 13pt;
    font-weight: bold;
    margin-bottom: 8px;
}}
.info-col p {{
    margin-bottom: 2px;
    font-size: 10pt;
}}

/* ITEMS TABLE */
table.items {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 16px;
    font-size: 10pt;
}}
table.items thead tr {{
    border-top: 1px solid #000;
    border-bottom: 1px solid #000;
}}
table.items thead th {{
    padding: 6px 8px;
    text-align: left;
    font-weight: bold;
}}
table.items tbody tr {{
    border-bottom: 1px solid #ddd;
}}
table.items tbody td {{
    padding: 6px 8px;
    vertical-align: top;
}}
table.items tfoot tr {{
    border-top: 2px solid #000;
}}
table.items tfoot td {{
    padding: 6px 8px;
    font-weight: bold;
    text-align: right;
}}

.col-concepto {{ width: 45%; }}
.col-cantidad {{ width: 17%; }}
.col-base     {{ width: 17%; }}
.col-iva      {{ width: 21%; white-space: nowrap; }}
.char         {{ color: #888888; font-size: 9pt; }}

/* TOTALS */
.totals-wrap {{
    display: flex;
    justify-content: flex-end;
    margin-bottom: 24px;
}}
.totals-table {{
    width: 280px;
    font-size: 10pt;
}}
.totals-table tr td {{
    padding: 3px 6px;
}}
.totals-table tr td:last-child {{
    text-align: right;
    font-weight: bold;
}}
.totals-table .total-row td {{
    border-top: 2px solid #000;
    font-weight: bold;
    font-size: 11pt;
}}

/* INFORMACIÓN IMPORTANTE */
.info-section {{
    margin-top: 16px;
    font-size: 9.5pt;
}}
.info-section h2 {{
    font-size: 12pt;
    font-weight: bold;
    margin-bottom: 8px;
}}
.info-section p {{
    margin-bottom: 4px;
}}

/* SIGNATURES */
.signatures {{
    margin-top: 24px;
    font-size: 10pt;
}}
.signatures p {{
    margin-bottom: 4px;
}}
</style>
</head>
<body>

<!-- HEADER: logo + doc meta -->
<div class="header-wrap">
    <div class="logo">
        <img src="{LOGO_URL}" alt="DOMYKA">
    </div>
    <div class="doc-meta">
        <p><strong>{labels.get("number","Número de presupuesto")}:</strong> {doc_number}</p>
        <p><strong>{labels.get("date","Fecha")}:</strong> {doc_date}</p>
    </div>
</div>

<!-- PROVEEDOR + CLIENTE -->
<div class="info-columns">
    <div class="info-col">
        <h2>{labels.get("seller_title","Datos del Proveedor:")}</h2>
        <p><strong>{labels.get("autonomo","Autónomo")}:</strong> {SELLER_INFO["name"]}</p>
        <p><strong>NIE:</strong> {SELLER_INFO["nie"]}</p>
        <p><strong>{labels.get("address","Dirección")}:</strong> {SELLER_INFO["address_line1"]}</p>
        <p>{SELLER_INFO["address_line2"]}</p>
        <p>{SELLER_INFO["address_line3"]}</p>
        <p><strong>{labels.get("phone","Teléfono")}:</strong> {SELLER_INFO["phone"]}</p>
        <p><strong>Email:</strong> {SELLER_INFO["email"]}</p>
        <p><strong>Web:</strong> {SELLER_INFO["web"]}</p>
        <p><strong>{labels.get("bank_name_label","Banco")}:</strong> {SELLER_INFO["bank"]}</p>
        <p><strong>IBAN:</strong> {SELLER_INFO["iban"]}</p>
        <p><strong>SWIFT/BIC:</strong> {SELLER_INFO["swift"]}</p>
        <p><strong>Bizum:</strong> {SELLER_INFO["bizum"]}</p>
    </div>
    <div class="info-col">
        <h2>{labels.get("buyer_block_title","Cliente:")}</h2>
        <p><strong>{client_data.get("fio","")}</strong></p>
        {client_nif_row}
        {client_addr_row}
        {client_phone_row}
        {client_email_row}
    </div>
</div>

<!-- ITEMS TABLE -->
<table class="items">
    <thead>
        <tr>
            <th class="col-concepto">{labels.get("item_description","Concepto")}</th>
            <th class="col-cantidad">{labels.get("item_quantity","Cantidad")}</th>
            <th class="col-base">{labels.get("item_base","Base imp.")}</th>
            <th class="col-iva">{labels.get("item_tax","IVA")}</th>
        </tr>
    </thead>
    <tbody>
        {items_html}
    </tbody>
</table>

<!-- TOTALS -->
<div class="totals-wrap">
    <table class="totals-table">
        <tr>
            <td>{labels.get("summary_subtotal","Total Base Imponible")}:</td>
            <td>{fmt_money(net_total)} &euro;</td>
        </tr>
        <tr>
            <td>{labels.get("summary_tax","IVA")} {int(tax_percent)}%:</td>
            <td>{fmt_money(tax_total)} &euro;</td>
        </tr>
        <tr class="total-row">
            <td>{labels.get("summary_total","TOTAL")}:</td>
            <td>{fmt_money(grand_total)} &euro;</td>
        </tr>
    </table>
</div>

<!-- INFORMACIÓN IMPORTANTE -->
<div class="info-section">
    <h2>{info_importante_title}</h2>
    {info_html}
</div>

<!-- SIGNATURES -->
<div class="signatures">
    <p>{firma_cliente}______________</p>
    <p>{firma_autonomo}______________</p>
</div>

</body>
</html>"""


async def generate_pdf(html: str) -> bytes:
    gotenberg_url = os.getenv(
        "GOTENBERG_URL", "http://gotenberg:3000/forms/chromium/convert/html"
    )
    files = {"files": ("index.html", html.encode("utf-8"), "text/html")}
    data = {"waitDelay": "1s"}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(gotenberg_url, files=files, data=data)
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Gotenberg error: {response.text}")
        return response.content


async def upload_to_planfix(pdf_bytes: bytes, task_id: Any, filename: str) -> dict[str, Any]:
    planfix_url = os.getenv("PLANFIX_WEBHOOK_URL", "").strip()
    if not planfix_url:
        return {"uploaded": False, "reason": "PLANFIX_WEBHOOK_URL is empty"}
    files = {"file": (filename, pdf_bytes, "application/pdf")}
    data = {
        "filename": filename,
        "task_id": str(task_id),
        "description": "Offer PDF",
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(planfix_url, data=data, files=files)
        return {
            "uploaded": response.status_code < 400,
            "status_code": response.status_code,
            "response_text": response.text[:1000],
        }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate-offer-pdf")
async def generate_offer_pdf(payload: WebhookBody) -> dict[str, Any]:
    data = payload.body
    lang = normalize_language(data.LANGUAGE)
    labels = LABELS.get(lang, LABELS.get("en", {}))

    items = parse_positions(data.positions or "")
    if not items:
        raise HTTPException(status_code=400, detail="No items parsed from positions")

    # Fetch characteristics from Planfix API and attach to items
    characteristics = await get_task_characteristics(data.task_id)
    for idx, item in enumerate(items):
        if idx < len(characteristics):
            item.characteristics = characteristics[idx]

    # Translate descriptions (skip if Spanish — already in Spanish)
    if lang != "es":
        descriptions = [item.description for item in items]
        translated = await translate_texts(descriptions, lang)
        for idx, item in enumerate(items):
            item.description = translated[idx]

    # Translate static blocks
    static_blocks = await translate_static_blocks(lang)

    # Detect dominant tax percent
    tax_percents = [item.tax_percent for item in items if item.tax_percent]
    dominant_tax = tax_percents[0] if tax_percents else 21.0

    now = datetime.now()
    doc_date = now.strftime("%d/%m/%Y")
    doc_number = f"P-{now.year}-{data.task_id}"

    html = render_html(
        labels=labels,
        static_blocks=static_blocks,
        items=items,
        client_data={
            "fio": data.client_fio or "",
            "phone": data.client_phone or "",
            "email": data.client_mail or "",
            "nif": data.client_nif or "",
            "address": data.direction or "",
        },
        doc_number=doc_number,
        doc_date=doc_date,
        tax_percent=dominant_tax,
    )

    pdf = await generate_pdf(html)

    file_name = f"offer_{doc_number}_{lang}.pdf"
    upload_result = await upload_to_planfix(pdf, data.task_id, file_name)

    net_total = round(sum(x.net_amount for x in items), 2)
    tax_total = round(sum(x.tax_amount for x in items), 2)
    grand_total = round(sum(x.total_amount for x in items), 2)

    return {
        "status": "ok",
        "task_id": data.task_id,
        "language": lang,
        "items_count": len(items),
        "totals": {
            "net_total": net_total,
            "tax_total": tax_total,
            "grand_total": grand_total,
        },
        "upload": upload_result,
    }
