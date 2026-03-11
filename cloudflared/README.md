# URL fixa com Cloudflare Tunnel

Hoje o app funciona com `trycloudflare.com`, mas essa URL muda sempre que o tunnel reinicia.

Para ter uma URL fixa, como:

`https://dashboard-faltas.seudominio.com`

voce precisa de:

- uma conta Cloudflare
- um dominio adicionado na Cloudflare
- um named tunnel em vez de quick tunnel

## Passo a passo

1. No painel da Cloudflare, crie um tunnel nomeado.
2. Escolha um hostname fixo para o app, por exemplo:
   `dashboard-faltas.seudominio.com`
3. Aponte esse hostname para o servico local:
   `http://localhost:5000`
4. Copie [config.example.yml](C:/Users/welli/OneDrive/Documentos/Playground/cloudflared/config.example.yml)
   para `config.yml` e troque:
   - `SEU_TUNNEL_ID`
   - `dashboard-faltas.seudominio.com`
5. Rode o backend:
   `python app.py`
6. Rode o tunnel nomeado:
   `cloudflared tunnel run SEU_TUNNEL_ID`

## Observacoes

- a URL fica fixa
- o computador ainda precisa ficar ligado
- o link deixa de mudar a cada reinicio
- o PWA no iPhone fica muito melhor com uma URL fixa
