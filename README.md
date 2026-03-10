# Dashboard Faltas

Projeto com frontend em React e backend em Flask para acompanhar faltas por disciplina a partir do portal do aluno da UFCSPA.

## Como rodar

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### Frontend

```bash
cd frontend
npm install
copy .env.example .env
npm run dev
```

## O que ja funciona

- tela de login no app
- sessao do portal mantida no backend
- leitura dos periodos da pagina de notas
- listagem das disciplinas por semestre
- exibicao das faltas por disciplina
- filtros por busca e selecao de materias
- tema claro e escuro

## Fluxo atual

1. O usuario faz login no app com credenciais do portal.
2. O Flask autentica no portal da UFCSPA.
3. A sessao do portal fica guardada no servidor.
4. O frontend usa apenas o cookie da nossa aplicacao.
5. O usuario escolhe o periodo e acompanha as faltas das disciplinas.

## Debug local do portal

Ao sincronizar, o backend salva o HTML bruto em [backend/data/debug](C:/Users/welli/OneDrive/Documentos/Playground/backend/data/debug).
Isso permite inspecionar localmente a pagina de periodos e as paginas de `Ver Notas` sem expor credenciais no chat.

## Observacao sobre o portal

Neste portal especifico, pode ser necessario desativar a verificacao SSL na tela de login por causa do certificado apresentado ao Python.

## Deploy

### Frontend na Vercel

1. Suba este repositorio para o GitHub.
2. Na Vercel, importe o projeto e defina o `Root Directory` como `frontend`.
3. Configure a variavel `VITE_API_BASE_URL` com a URL publica do backend.
4. Faca o deploy.

### Backend no Render

1. No Render, crie o servico usando o arquivo [render.yaml](C:/Users/welli/OneDrive/Documentos/Playground/render.yaml).
2. O backend usa `gunicorn` e, em producao, prefere sessao em Redis via `REDIS_URL`.
3. Defina `FRONTEND_URL` com a URL da Vercel.
4. Em producao, use `SESSION_COOKIE_SECURE=true`.
5. Para frontend na Vercel e backend em outro dominio, use `SESSION_COOKIE_SAMESITE=None`.

### Backend no Railway

1. Crie um servico a partir da pasta `backend`.
2. Configure o start command como `gunicorn -w 2 -b 0.0.0.0:$PORT app:app`.
3. Adicione um servico Redis e exponha a variavel `REDIS_URL`.
4. Defina `FRONTEND_URL` com a URL da Vercel.

## URLs atuais

- frontend: [dashboard-faltas.vercel.app](https://dashboard-faltas.vercel.app)
- backend: [dashboard-faltas.onrender.com](https://dashboard-faltas.onrender.com)
