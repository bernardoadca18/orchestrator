# Repaginação NeoVids — 17/10/2026

Shell lateral, localização atual, Dashboard operacional, Produções, início de produção,
workspace e comparação de thumbnails receberam a identidade de estúdio. Estados e fases
têm rótulos em português. O próximo passo usa estado/capacidades do servidor; nenhum status
é avançado pelo cliente. A tabela permite rolagem pelo teclado em telas estreitas.

O Dashboard identifica o recorte de até 100 produções, mantém consultas independentes em
falhas parciais e distingue dados indisponíveis de uma lista vazia. Thumbnails preservam o
pacote durante atualização, mostram confirmação somente após a resposta do servidor,
carregam imagens sob demanda e oferecem recuperação de prévia com falha.

## Iniciar

Na pasta `frontend`, execute `npm ci` e `npm run dev -- --host 127.0.0.1`.
O Vite usa a porta 5174 configurada no projeto e informa a URL efetiva no terminal.
Configure `VITE_API_URL` com a API real. Para o proxy de desenvolvimento existente, use
`VITE_API_URL=/api` e `VITE_DEV_PROXY_TARGET` com a origem da API. O destino padrão existente
do proxy é `http://127.0.0.1:8011`. Reinicie o Vite após mudar variáveis.

## Validação

- PASS: build e TypeScript (`npm run build`).
- PASS: 60 testes (`npm test`), incluindo rotas, contratos, bloqueios, idempotência, polling e projeção de etapas.
- PASS: navegador com fixtures isoladas: Dashboard, abrir produção, navegar para Thumbnails,
  comparar A/B/C e selecionar B com confirmação do servidor de teste; C indisponível.
- PASS: viewport móvel de 390×844; Dashboard e Produções sem overflow da página;
  a tabela e a navegação têm rolagem própria. Desktop validado em 1440×900.
- PASS: falha parcial de saúde mantendo produções visíveis.
- SKIP: lint (não há script), smoke com APIs reais e geração/renderização GPU.

Capturas e servidor de fixtures ficam em `C:/workspaces/neovids/visual-qa`.
A expectativa de rotas do teste foi atualizada para incluir Thumbnails, rota já existente
antes desta rodada. Não houve alteração de endpoints, bancos, jobs ou aprovações.

Páginas secundárias herdam os controles e tokens, sem reengenharia específica. Não foram
criados links para consoles externos sem URL operacional verificada. Storyboard e entrega
continuam nas superfícies já suportadas, sem rotas fictícias.

Na continuação, etapas numeradas tornaram-se links para as seções existentes. Fase desconhecida
não é apresentada como conclusão; a fase terminal `completed` confirmada pelo backend continua
sendo reconhecida. Referências técnicas são recolhíveis. Ações sem permissão confirmada ficam
desabilitadas, e uma nova tentativa remove o aviso de sucesso da tentativa anterior.
