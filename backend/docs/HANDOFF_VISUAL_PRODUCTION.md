# Contrato de Handoff para Produção Visual

Este documento define o contrato de saída do orchestrator para o serviço de produção visual.
Quando o serviço visual existir, ele consumirá o endpoint consolidado `/workflow/{id}/pacote-producao`
em vez de bater em múltiplos endpoints.

## Endpoint Consolidado

```
GET /workflow/{workflow_id}/pacote-producao
```

Retorna todo o contexto necessário para o serviço visual iniciar a produção do vídeo.

## Payload de Resposta

```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "ideia_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
  "roteiro_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",

  "status": "audio_concluido",
  "duracao_audio_segundos": 125.4,

  "perfil_producao": "youtube_shorts_v1",

  "roteiro": {
    "titulo": "5 Dicas de Produtividade",
    "duracao_total_segundos": 120,
    "blocos": [
      {
        "ordem": 0,
        "tipo": "intro",
        "conteudo": "Olá! Hoje vamos ver 5 dicas..."
      },
      {
        "ordem": 1,
        "tipo": "desenvolvimento",
        "conteudo": "A primeira dica é..."
      },
      {
        "ordem": 2,
        "tipo": "conclusao",
        "conteudo": "E assim concluímos..."
      }
    ]
  },

  "audio": {
    "url": "/workflow/550e8400-e29b-41d4-a716-446655440000/audio",
    "arquivo_path": "/data/audio/550e8400-e29b-41d4-a716-446655440000/audio_final.wav",
    "duracao_segundos": 125.4,
    "blocos": [
      {
        "id": "bloco-uuid-1",
        "ordem": 0,
        "tipo": "intro",
        "audio_url": "/workflow/550e8400.../blocos/bloco-uuid-1/audio",
        "duracao_segundos": 15.2
      }
    ]
  },

  "transcricao": {
    "texto_completo": "Olá! Hoje vamos ver 5 dicas de produtividade...",
    "palavras_timestamps": [
      {
        "palavra": "Olá",
        "inicio": 0.0,
        "fim": 0.5
      },
      {
        "palavra": "Hoje",
        "inicio": 0.6,
        "fim": 1.0
      }
    ]
  },

  "metadata": {
    "criado_em": "2024-01-15T10:30:00Z",
    "atualizado_em": "2024-01-15T10:35:00Z",
    "tentativas": 0
  }
}
```

## Campos do Payload

### Identificadores

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `workflow_id` | UUID | ID único do workflow no orchestrator |
| `ideia_id` | UUID | ID da ideia no ideas-hub (rastreabilidade) |
| `roteiro_id` | UUID | ID do roteiro no ideas-hub |

### Status

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `status` | string | Status atual: `audio_concluido` quando pronto para produção visual |
| `duracao_audio_segundos` | float | Duração total do áudio final em segundos |

### Perfil de Produção

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `perfil_producao` | string \| null | Perfil de produção definido no ideas-hub (ex: "youtube_shorts_v1", "podcast_v1") |

### Roteiro

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `roteiro.titulo` | string | Título do vídeo |
| `roteiro.duracao_total_segundos` | int | Duração estimada original |
| `roteiro.blocos` | array | Lista de blocos narrativos |
| `blocos[].ordem` | int | Ordem sequencial (0-based) |
| `blocos[].tipo` | string | Tipo do bloco: `intro`, `desenvolvimento`, `conclusao`, etc. |
| `blocos[].conteudo` | string | Texto narrado no bloco |

### Áudio

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `audio.url` | string | URL relativa para baixar o áudio final completo |
| `audio.arquivo_path` | string | Caminho absoluto do arquivo no servidor (uso interno) |
| `audio.duracao_segundos` | float | Duração real do áudio |
| `audio.blocos` | array | Lista de áudios por bloco (para edição granular) |
| `blocos[].audio_url` | string | URL relativa para baixar áudio do bloco |
| `blocos[].duracao_segundos` | float | Duração do bloco |

### Transcrição

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `transcricao.texto_completo` | string | Texto completo transcrito |
| `transcricao.palavras_timestamps` | array | Timestamps por palavra (para sincronização visual) |
| `palavras_timestamps[].palavra` | string | Palavra transcrita |
| `palavras_timestamps[].inicio` | float | Tempo de início em segundos |
| `palavras_timestamps[].fim` | float | Tempo de fim em segundos |

### Metadata

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `metadata.criado_em` | datetime | Timestamp de criação do workflow |
| `metadata.atualizado_em` | datetime | Timestamp da última atualização |
| `metadata.tentativas` | int | Número de retentativas (0 se sucesso na primeira) |

## Status HTTP

| Código | Condição |
|--------|----------|
| 200 | Workflow concluído, pacote disponível |
| 404 | Workflow não encontrado |
| 400 | Workflow ainda não concluído (status diferente de `audio_concluido`) |

## Exemplo de Uso pelo Serviço Visual

```python
# Python
import httpx

async def obter_pacote_producao(workflow_id: str) -> dict:
    async with httpx.AsyncClient(base_url="http://orchestrator:8011") as client:
        response = await client.get(f"/workflow/{workflow_id}/pacote-producao")
        response.raise_for_status()
        return response.json()

# Uso
pacote = await obter_pacote_producao("550e8400-e29b-41d4-a716-446655440000")

# Baixar áudio final
audio_url = f"http://orchestrator:8011{pacote['audio']['url']}"

# Sincronizar legendas com timestamps
for palavra in pacote['transcricao']['palavras_timestamps']:
    print(f"{palavra['palavra']}: {palavra['inicio']:.2f}s - {palavra['fim']:.2f}s")
```

## Notas de Implementação

1. **Consolidação**: O endpoint agrega dados de múltiplas tabelas (workflow, blocos, transcrição) em uma única resposta.

2. **URLs relativas**: URLs de áudio são relativas à base do orchestrator para flexibilidade de deployment.

3. **Timestamps de palavras**: Permitem sincronização frame-a-frame para legendas, highlights e cortes.

4. **Perfil de produção**: Indica template/configuração visual a ser aplicado (definido no ideas-hub).

5. **Idempotência**: Múltiplas chamadas ao endpoint retornam o mesmo resultado enquanto o workflow não for alterado.

## Versionamento

Este contrato pode evoluir. Mudanças breaking serão sinalizadas por versionamento de endpoint:
- `/workflow/{id}/pacote-producao` (atual)
- `/workflow/{id}/pacote-producao/v2` (futuro, se necessário)
