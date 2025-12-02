
# GEON Voice Agent – Azure OpenAI Realtime + Twilio SIP

Este projeto é um exemplo completo de backend em Python para receber chamadas
telefônicas via SIP (por exemplo, Twilio Elastic SIP Trunk) e atendê-las com
um agente de voz usando a **Azure OpenAI Realtime API**.

## Visão geral do fluxo

1. Um cliente liga para o seu **número de telefone na Twilio**.
2. A Twilio envia a chamada via **SIP Trunk** para o endpoint SIP da Azure,
   usando o `projectId` como usuário:
   `sip:proj_<internalId>@eastus2.sip.ai.azure.com;transport=tls`
3. A Azure OpenAI dispara um webhook de evento `realtime.call.incoming`
   para o endpoint HTTP definido por você.
4. Este backend Python recebe o webhook, **aceita a chamada** na Realtime API,
   abre um **WebSocket** associado à `call_id` e manda o agente de voz atender.

---

## 1. Pré-requisitos

- Conta Azure com recurso **Azure OpenAI** criado em região suportada
  (por exemplo, `eastus2`).
- Deployment de um modelo realtime, por exemplo:
  - `gpt-4o-realtime-preview`
  - `gpt-4o-mini-realtime-preview`
  - `gpt-realtime`
  - `gpt-realtime-mini`
- Um `internalId` do recurso Azure OpenAI (obtido na **JSON View** do recurso).
- Uma conta **Twilio** com **Elastic SIP Trunking** habilitado.
- Python 3.10+ instalado.

---

## 2. Clonar / baixar o projeto

Extraia o `.zip` em alguma pasta, por exemplo:

```bash
cd geon_azure_voice_agent
```

Crie e ative um ambiente virtual (recomendado):

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# ou
.venv\Scripts\activate   # Windows
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

---

## 3. Configurar o `.env`

Use o arquivo `.env.example` como base:

```bash
cp .env.example .env
```

Edite o `.env` com seus valores reais:

- `AZURE_OPENAI_ENDPOINT`  
  Exemplo real (do JSON que você já pegou):
  `https://guilh-mgn6mi56-eastus2.openai.azure.com`
- `AZURE_OPENAI_API_KEY`  
  Uma API key válida do recurso Azure OpenAI (Foundry / Keys).
- `AZURE_OPENAI_REALTIME_MODEL`  
  Nome do deployment realtime criado no Foundry, por exemplo:
  `gpt-4o-realtime-preview` (ou o nome customizado do deployment).
- `AZURE_PROJECT_ID`  
  No formato `proj_<internalId>`, por exemplo:
  `proj_f5cb433cedff4fd99b2db2757647c5d3`
- `PORT` (opcional)  
  Porta onde o servidor vai rodar localmente (por padrão 8000).

---

## 4. Rodar o backend localmente

```bash
uvicorn main:app --reload --port 8000
```

Você pode testar o endpoint de saúde:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## 5. Expor o webhook para a Azure (ngrok ou produção)

Para testes locais, você pode usar **ngrok**:

```bash
ngrok http 8000
```

Ele vai te dar uma URL pública, por exemplo:

```
https://xyz123.ngrok.app
```

Seu webhook de chamadas ficará em:

```
https://xyz123.ngrok.app/webhooks/azure-realtime
```

---

## 6. Configurar o Webhook na Azure OpenAI

Siga a documentação da Microsoft **“Use a GPT Realtime API via SIP”**
para criar um webhook do tipo `realtime.call.incoming` apontando para:

```
https://SEU_DOMINIO/webhooks/azure-realtime
```

Passos resumidos:

1. Use o **Azure OpenAI Webhook Service** (via REST) para criar um webhook.
2. Configure o tipo de evento para `realtime.call.incoming`.
3. Aponte o `endpointUrl` para o seu backend HTTP (ngrok ou produção).

Quando uma chamada SIP chegar ao seu projeto, a Azure enviará um evento JSON
para este endpoint, contendo um `call_id`.

---

## 7. Montar o SIP URI com o `projectId`

No JSON do recurso Azure OpenAI você encontra o campo:

```json
"internalId": "f5cb433cedff4fd99b2db2757647c5d3"
```

O `projectId` é:

```text
proj_f5cb433cedff4fd99b2db2757647c5d3
```

O **SIP URI completo** fica:

```text
sip:proj_f5cb433cedff4fd99b2db2757647c5d3@eastus2.sip.ai.azure.com;transport=tls
```

Use esse valor na Twilio como destino do seu tronco SIP.

---

## 8. Configurar o Trunk na Twilio (Elastic SIP Trunking)

1. No painel Twilio, vá em **Elastic SIP Trunking → Trunks → Create new Trunk**.
2. Dê um nome, por exemplo: `geon-azure-voice`.
3. Na aba **Origination** (ou Termination, conforme o fluxo desejado), adicione
   um **Origination URI** com o SIP URI da Azure:
   ```
   sip:proj_f5cb433cedff4fd99b2db2757647c5d3@eastus2.sip.ai.azure.com;transport=tls
   ```
4. Habilite **TLS** para a sinalização.
5. Em **Phone Numbers**, compre ou use um número existente e vincule ao Trunk.

Resultado:
- Quando alguém ligar para esse número Twilio, a chamada será enviada via SIP
  para a Azure OpenAI, que por sua vez chamará o seu webhook Python.

---

## 9. Fluxo em execução

Quando tudo estiver ligado:

1. Cliente liga para o número Twilio.
2. Twilio → SIP → Azure (`proj_...@eastus2.sip.ai.azure.com`).
3. Azure dispara webhook `realtime.call.incoming` para `/webhooks/azure-realtime`.
4. O backend:
   - pega o `call_id`,
   - aceita a chamada na Realtime API,
   - abre o WebSocket vinculado a essa `call_id`,
   - envia uma primeira `response.create` para o agente se apresentar.
5. O modelo `gpt-4o-realtime-preview` (ou outro) conversa com o cliente por voz.

---

## 10. Personalizar o agente

No arquivo `main.py`, na função `handle_incoming_call`, você vai encontrar
o bloco `base_instructions`. É ali que você pode escrever o comportamento do
agente, por exemplo:

- SDR de vendas (pré-vendas)
- Cobrança
- Suporte nível 1
- Retenção, etc.

Também dentro de `handle_realtime_session` você pode tratar os eventos do
WebSocket para, por exemplo:

- extrair transcrições,
- gravar informações em banco,
- disparar webhooks para outros sistemas,
- fazer analytics, etc.

---

## 11. Produção

Para usar em produção, considere:

- Rodar o backend em um serviço gerenciado (Azure App Service, Container Apps, etc.).
- Colocar HTTPS com certificado válido (se não estiver atrás de um proxy como o ngrok).
- Implementar **validação de assinatura do webhook** da Azure (HMAC), se disponível.
- Logging estruturado e rastreamento (App Insights, por exemplo).
- Mecanismo de retry / handling de falhas de rede.

---

Qualquer dúvida, você pode ir adaptando este exemplo para o seu stack (n8n,
Supabase, CRMs, etc.) e usar este backend apenas como “coração de voz”,
enquanto o restante da orquestração fica em outros serviços.
