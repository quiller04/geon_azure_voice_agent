
import os
import json
import asyncio
from typing import Any, Dict, Union

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import websockets

"""
GEON Voice Agent – Azure OpenAI Realtime + SIP (Twilio)
------------------------------------------------------
Pequeno backend em Python que:
  - recebe o webhook da Azure quando chega uma chamada SIP
  - aceita a chamada na Realtime API
  - abre um websocket ligado a essa call_id
  - manda o agente de voz atender e conduzir a conversa

IMPORTANTE:
  - Este código é um exemplo de referência. Você ainda precisa:
    * criar o deployment realtime no Foundry (gpt-4o-realtime-preview, etc.)
    * configurar o Webhook de chamadas na Azure apontando para /webhooks/azure-realtime
    * configurar o tronco SIP na Twilio apontando para o SIP URI do seu projectId
"""

# Carrega variáveis do .env
load_dotenv()

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")  # ex: https://guilh-mgn6mi56-eastus2.openai.azure.com
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_REALTIME_MODEL = os.getenv("AZURE_OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
AZURE_PROJECT_ID = os.getenv("AZURE_PROJECT_ID")  # proj_f5cb4...

if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY or not AZURE_PROJECT_ID:
    raise RuntimeError("Verifique AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY e AZURE_PROJECT_ID no .env")

app = FastAPI(
    title="GEON Voice Agent – Azure + Twilio SIP",
    version="0.1.0",
)


# ------------------------------------------------------------------
# 1) Funções utilitárias – HTTP para Azure OpenAI
# ------------------------------------------------------------------
async def accept_call(call_id: str, instructions: str) -> None:
    """
    Aceita uma chamada SIP na Azure OpenAI Realtime API.
    Doc: POST /openai/v1/realtime/calls/{call_id}/accept
    """
    url = f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/v1/realtime/calls/{call_id}/accept"

    payload = {
        "type": "realtime",
        # IMPORTANTE: aqui vai o NOME DO DEPLOYMENT do modelo na Azure,
        # não o nome genérico se você tiver renomeado.
        "model": AZURE_OPENAI_REALTIME_MODEL,
        "instructions": instructions,
        # Codecs típicos de telefonia (Twilio geralmente usa G.711 u-law)
        "input_audio_format": "g711_ulaw",
        "output_audio_format": "g711_ulaw",
        # voz padrão (ajuste se quiser outra)
        "voice": "alloy",
        # detecção de turno pelo servidor (fala do cliente / fala do bot)
        "turn_detection": {"type": "server_vad"},
        "input_language": "pt-BR",
        "output_language": "pt-BR",
    }

    headers = {
        "api-key": AZURE_OPENAI_API_KEY,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            print("Erro ao aceitar chamada:", resp.status_code, resp.text)
            raise HTTPException(status_code=500, detail="Falha ao aceitar chamada na Azure")


async def handle_realtime_session(call_id: str) -> None:
    """
    Abre o WebSocket na sessão Realtime associada à chamada SIP,
    manda o bot "atender" e depois só loga eventos.
    """
    # Troca https -> wss no endpoint
    ws_base = AZURE_OPENAI_ENDPOINT.replace("https://", "wss://").rstrip("/")
    # Para chamadas SIP aceitas com call_id, usamos o parâmetro call_id na query
    ws_url = f"{ws_base}/openai/v1/realtime?call_id={call_id}"

    headers = {
        # Azure aceita api-key em header na conexão pré-handshake
        "api-key": AZURE_OPENAI_API_KEY
    }

    print(f"[WS] Conectando WebSocket para call_id={call_id} em {ws_url}")

    # Para Azure não precisamos de subprotocol específico, só o header com api-key
    async with websockets.connect(ws_url, extra_headers=headers) as ws:
        # Opcional: mandar uma primeira resposta explícita para "atender" a chamada
        response_create = {
            "type": "response.create",
            "response": {
                "instructions": (
                    "Atenda o telefone, apresente-se como assistente de voz "
                    "da GEON AI, em português do Brasil, e pergunte o nome da pessoa."
                )
            },
        }
        await ws.send(json.dumps(response_create))
        print("[WS] response.create enviado para atender e dizer olá")

        # Loop ouvindo eventos da sessão
        async for message in ws:
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                print("[WS] Mensagem não-JSON:", message)
                continue

            event_type = event.get("type")
            if event_type:
                print(f"[WS EVENT] {event_type}")

            # Log simples de textos (se existirem)
            # Em muitos eventos, o texto vem em campos como 'output_text' ou similares.
            if "output_text" in event:
                print("[BOT TEXTO]:", event["output_text"])
            if "transcript" in event:
                print("[TRANSCRIÇÃO]:", event["transcript"])


# ------------------------------------------------------------------
# 2) Orquestra a chamada: aceitar + abrir WS
# ------------------------------------------------------------------
async def handle_incoming_call(call_id: str) -> None:
    """
    Orquestração de uma chamada SIP:
    - Aceita a chamada na Realtime API
    - Abre o WebSocket e mantém a sessão ativa
    """
    print(f"[CALL] Nova chamada recebida: call_id={call_id}")

    base_instructions = """
    Você é um agente de voz da GEON AI, falando em português do Brasil.

    Sua função:
    - Atender ligações de leads e clientes.
    - Se for lead novo: fazer qualificação (nome, empresa, cargo, telefone correto, e-mail).
    - Descobrir interesse em soluções de IA e automação de voz.
    - Se fizer sentido, agendar uma reunião com o time comercial.
    - Se for suporte: entender o problema e coletar contexto para repassar ao time humano.

    Regras:
    - Sempre se apresente como "assistente virtual de voz da GEON AI".
    - Fale de forma natural, mas profissional, sem gírias pesadas.
    - Frases curtas, bem objetivas, para não ficar monótono.
    - Nunca invente informações técnicas. Se não souber, diga que vai encaminhar ao time.
    - Confirme ao final da ligação se a pessoa ficou com alguma dúvida.
    """

    # 1) Aceita a chamada na Azure
    await accept_call(call_id, base_instructions)
    print(f"[CALL] Chamada {call_id} aceita, estabelecendo sessão Realtime...")

    # 2) Abre o WebSocket e controla a sessão
    try:
        await handle_realtime_session(call_id)
    except Exception as e:
        print(f"[CALL] Erro na sessão Realtime da chamada {call_id}: {e}")


# ------------------------------------------------------------------
# 3) Endpoint de webhook que a Azure chama (realtime.call.incoming)
# ------------------------------------------------------------------
@app.post("/webhooks/azure-realtime")
async def azure_realtime_webhook(request: Request) -> JSONResponse:
    """
    Endpoint chamado pela Azure quando chega uma ligação SIP no seu projeto.

    Exemplo de evento (simplificado):
    {
      "object": "event",
      "type": "realtime.call.incoming",
      "data": { "call_id": "..." , "sip_headers": [...] }
    }

    Em alguns casos a Azure pode mandar uma lista de eventos.
    """
    try:
        body: Union[Dict[str, Any], Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    if isinstance(body, list):
        event = body[0]
    else:
        event = body

    event_type = event.get("type")
    data = event.get("data", {}) or {}
    call_id = data.get("call_id")

    print(f"[WEBHOOK] Evento recebido: type={event_type}, call_id={call_id}")

    # Aqui você deveria validar a assinatura do webhook (HMAC) se tiver configurado.
    # Para POC / laboratório estamos pulando esse passo.

    if event_type != "realtime.call.incoming" or not call_id:
        # Ignora eventos que não são de chamada SIP
        return JSONResponse({"status": "ignored"}, status_code=200)

    # Dispara a orquestração da chamada em background
    asyncio.create_task(handle_incoming_call(call_id))

    return JSONResponse({"status": "ok", "call_id": call_id}, status_code=200)


# ------------------------------------------------------------------
# 4) Endpoint de saúde (opcional)
# ------------------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


# ------------------------------------------------------------------
# 5) Ponto de entrada local
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
