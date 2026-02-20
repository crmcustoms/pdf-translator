import json
import os
import re
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.labels import LABELS


LANGUAGE_MAP = {
    "англійська": "en",
    "английский": "en",
    "english": "en",
    "испанский": "es",
    "іспанська": "es",
    "spanish": "es",
    "немецкий": "de",
    "німецька": "de",
    "german": "de",
    "французский": "fr",
    "французька": "fr",
    "french": "fr",
    "украинский": "uk",
    "українська": "uk",
    "ukrainian": "uk",
    "русский": "ru",
    "російська": "ru",
    "russian": "ru",
}


class RequestPayload(BaseModel):
    LANGUAGE: Optional[list[str] | str] = None
    client_fio: str = ""
    client_phone: str = ""
    client_mail: str = ""
    client_nif: str = ""
    positions: str = ""
    task_id: int | str


class WebhookBody(BaseModel):
    body: RequestPayload


class Item(BaseModel):
    description: str = ""
    unit_price: float = 0.0
    quantity: float = 0.0
    tax_percent: float = 0.0
    tax_amount: float = 0.0
    net_amount: float = 0.0
    total_amount: float = 0.0
    raw_text: str = ""


app = FastAPI(title="PDF Translator Service", version="1.0.0")


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
                raw_text=block,
            )
        )
    return result


async def translate_texts(texts: list[str], target_language: str) -> list[str]:
    provider = os.getenv("TRANSLATION_PROVIDER", "none").lower()
    if not texts:
        return texts
    if provider == "none":
        return texts
    if provider == "deepl":
        return await translate_with_deepl(texts, target_language)
    if provider == "openai":
        return await translate_with_openai(texts, target_language)
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
    prompt = {
        "target_language": target_language,
        "descriptions": texts,
    }
    body = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are strict JSON translator. "
                    "Return only valid JSON: {\"translated\": [\"...\"]}."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
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
            return texts
    return texts


def render_html(
    labels: dict[str, str], items: list[Item], client_data: dict[str, str], doc_number: str, doc_date: str
) -> str:
    def money(value: float) -> str:
        return f"{value:.2f} €"

    rows = []
    for item in items:
        rows.append(
            f"""
            <tr>
                <td><div class="item-description">{item.description}</div></td>
                <td class="text-center">{item.quantity}</td>
                <td class="text-right">{money(item.unit_price)}</td>
                <td class="text-right">{money(item.total_amount)}</td>
            </tr>
            """
        )
    items_html = "".join(rows)

    net_total = round(sum(x.net_amount for x in items), 2)
    tax_total = round(sum(x.tax_amount for x in items), 2)
    grand_total = round(sum(x.total_amount for x in items), 2)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{labels.get("invoice_title", "Offer")}</title>
<style>
@page {{ margin: 2cm; }}
body {{ font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.45; color: #000; max-width: 21cm; margin: 0 auto; padding: 1cm; }}
.header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; border-bottom: 2px solid #999; }}
.header-table td {{ vertical-align: top; padding: 6px 0; }}
.invoice-title {{ font-size: 20pt; font-weight: 700; text-align: right; color: #333; }}
.section-box {{ background: #f2f2f2; padding: 10px 12px; border-radius: 4px; }}
.two-column {{ width: 100%; border-collapse: collapse; margin: 14px 0 18px; }}
.two-column td {{ width: 50%; vertical-align: top; padding: 0 8px; }}
.items-table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
.items-table thead {{ background: #ddd; }}
.items-table th {{ padding: 8px; text-align: left; font-size: 10.5pt; }}
.items-table td {{ padding: 8px; border-bottom: 1px solid #ccc; vertical-align: top; }}
.item-description {{ font-weight: 600; }}
.text-right {{ text-align: right; }}
.text-center {{ text-align: center; }}
.totals-table {{ width: 360px; margin-left: auto; margin-top: 12px; border-collapse: collapse; }}
.totals-table td {{ padding: 5px 6px; }}
.totals-table .label {{ text-align: left; font-weight: 600; }}
.totals-table .amount {{ text-align: right; font-weight: 600; }}
.total-row {{ border-top: 2px solid #999; }}
.footer {{ margin-top: 24px; padding-top: 10px; border-top: 1px solid #ccc; text-align: center; font-size: 9pt; color: #555; }}
</style>
</head>
<body>
<table class="header-table">
<tr>
<td style="width:58%;">
  <div><strong>Denys Mykolaienko</strong></div>
  <div>Calle Frankfurt 11, apt 30, Ojén, Málaga, España, 29610</div>
  <div>NIE: Z2785512X</div>
  <div>Tel: +34 621 347 502</div>
  <div>Email: office@domyka.es</div>
</td>
<td style="width:42%;">
  <div class="invoice-title">{labels.get("invoice_title", "Offer")}</div>
  <div style="text-align:right; margin-top:8px;">
    <div><strong>{labels.get("number", "Number")}:</strong> {doc_number}</div>
    <div><strong>{labels.get("date", "Date")}:</strong> {doc_date}</div>
  </div>
</td>
</tr>
</table>

<table class="two-column">
<tr>
<td>
  <div class="section-box">
    <strong>{labels.get("bank_details", "Bank details")}:</strong><br>
    <strong>{labels.get("bank_name_label", "Bank")}:</strong> SANTANDER<br>
    <strong>IBAN:</strong> ES30 0049 7366 5422 1002 9681<br>
    <strong>BIC/SWIFT:</strong> BSCHESMMXXX<br>
    <strong>Bizum:</strong> 621 347 502
  </div>
</td>
<td>
  <div class="section-box">
    <strong>{labels.get("buyer_block_title", "Buyer")}:</strong><br>
    <div><strong>{client_data.get("fio", "")}</strong></div>
    {"<div>" + labels.get("nif", "NIF") + ": " + client_data.get("nif", "") + "</div>" if client_data.get("nif") else ""}
    <div>{labels.get("phone", "Phone")}: {client_data.get("phone", "")}</div>
    {"<div>" + labels.get("email", "Email") + ": " + client_data.get("email", "") + "</div>" if client_data.get("email") else ""}
  </div>
</td>
</tr>
</table>

<table class="items-table">
<thead>
<tr>
  <th style="width:50%;">{labels.get("item_description", "Description")}</th>
  <th style="width:10%;" class="text-center">{labels.get("item_quantity", "Quantity")}</th>
  <th style="width:20%;" class="text-right">{labels.get("item_unit_price", "Unit Price")}</th>
  <th style="width:20%;" class="text-right">{labels.get("item_total", "Total")}</th>
</tr>
</thead>
<tbody>{items_html}</tbody>
</table>

<table class="totals-table">
<tr>
  <td class="label">{labels.get("summary_subtotal", "Subtotal")}:</td>
  <td class="amount">{money(net_total)}</td>
</tr>
<tr>
  <td class="label">{labels.get("summary_tax", "VAT")}:</td>
  <td class="amount">{money(tax_total)}</td>
</tr>
<tr class="total-row">
  <td class="label">{labels.get("summary_total", "Total amount")}:</td>
  <td class="amount">{money(grand_total)}</td>
</tr>
</table>

<div class="footer">
  <p><strong>{labels.get("thanks_a_clima", "Thank you for choosing DOMYKA")}</strong></p>
</div>
</body>
</html>"""


async def generate_pdf(html: str) -> bytes:
    gotenberg_url = os.getenv("GOTENBERG_URL", "http://gotenberg:3000/forms/chromium/convert/html")
    files = {
        "files": ("index.html", html.encode("utf-8"), "text/html"),
    }
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
    labels = LABELS.get(lang, LABELS["en"])

    items = parse_positions(data.positions or "")
    if not items:
        raise HTTPException(status_code=400, detail="No items parsed from positions")

    source_descriptions = [item.description for item in items]
    translated_descriptions = await translate_texts(source_descriptions, lang)
    for idx, item in enumerate(items):
        item.description = translated_descriptions[idx]

    now = datetime.now()
    doc_date = now.strftime("%d/%m/%Y")
    doc_number = f"P-{now.year}-{now.month:02d}{now.day:02d}"

    html = render_html(
        labels=labels,
        items=items,
        client_data={
            "fio": data.client_fio or "",
            "phone": data.client_phone or "",
            "email": data.client_mail or "",
            "nif": data.client_nif or "",
        },
        doc_number=doc_number,
        doc_date=doc_date,
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

