import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
from httpx import ASGITransport

from app import main


SAMPLE_PAYLOAD = {
    "body": {
        "LANGUAGE": ["Англійська"],
        "client_fio": "Personal Property Natalia Bogdanova",
        "client_phone": "+34672933034",
        "client_mail": "test@example.com",
        "client_nif": "X1234567Z",
        "positions": (
            "<br/><b>Покупка: товары/услуги</b><br/>"
            "Concepto : Монтажно-Демонтажные работы<br/>"
            "Ціна : 14000<br/>"
            "IVA% : 21<br/>"
            "Кол-во : 1<br/>"
            "IVA сум : 2940.00<br/>"
            "Сума без IVA : 14000.00<br/>"
            "Підсумок : 16940.00<br/>"
            "<b>Покупка: товары/услуги</b><br/>"
            "Concepto : Тепловой насос Panasonic 30kW<br/>"
            "Ціна : 23025<br/>"
            "IVA% : 21<br/>"
            "Кол-во : 1<br/>"
            "IVA сум : 5121.69<br/>"
            "Сума без IVA : 23025.00<br/>"
            "Підсумок : 28146.69"
        ),
        "task_id": 425,
    }
}


async def run():
    fake_pdf = b"%PDF-1.4 fake-smoke-test-pdf"

    with (
        patch("app.main.generate_pdf", new=AsyncMock(return_value=fake_pdf)),
        patch(
            "app.main.upload_to_planfix",
            new=AsyncMock(return_value={"uploaded": True, "status_code": 200, "response_text": "mocked"}),
        ),
    ):
        transport = ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # --- health check ---
            r = await client.get("/health")
            assert r.status_code == 200, f"Health check failed: {r.text}"
            print("OK /health OK")

            # --- main endpoint ---
            r = await client.post("/generate-offer-pdf", json=SAMPLE_PAYLOAD)
            print("STATUS:", r.status_code)
            body = r.json()
            print(json.dumps(body, ensure_ascii=False, indent=2))

            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
            assert body["status"] == "ok"
            assert body["items_count"] == 2
            assert body["language"] == "en"
            assert body["totals"]["grand_total"] > 0
            print("OK /generate-offer-pdf OK — 2 items parsed, totals non-zero")

            # --- empty positions should return 400 ---
            bad_payload = dict(SAMPLE_PAYLOAD)
            bad_payload = {"body": {**SAMPLE_PAYLOAD["body"], "positions": ""}}
            r2 = await client.post("/generate-offer-pdf", json=bad_payload)
            assert r2.status_code == 400, f"Expected 400 for empty positions, got {r2.status_code}"
            print("OK Empty positions -> 400 OK")

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(run())
