# RIBBON: contexto para o Claude

TCC "RIBBON: Reconhecimento Inteligente de Boletos Baseado em OCR Numérico", em Ciência da Computação nas Faculdades Integradas de Bauru (FIB), 2026. Orientador: Prof. Me. Ronaldo César Dametto.

A aplicação web confere guias de pagamento digitalizadas contra uma lista de consulta (a lista mestre). Ela lê o código e o valor de cada página por OCR, pareia cada guia com uma linha da lista e manda para revisão humana só o que diverge.

## Regras do grupo (seguir sempre)

- **Idioma:** responder em português do Brasil.
- **Ordem dos autores, em qualquer lugar:** Felipe Rosa de Almeida, Gustavo Grancieri, Rafaella Rocha Deganutti, Fabricio Bastos dos Santos.
- **Nunca chamar os testes ou lotes de "sintéticos".** Dizer apenas "testes" ou "lotes de teste".
- **Git:** commit, push e PR só quando o Felipe pedir. Ele mesmo faz o merge das PRs. Trabalhar em branch, nunca direto no `main`.
- **Referências do texto:** públicas, com links que levam direto ao conteúdo. Datas de acesso reais, nunca inventadas.
- **Banca:** os espaços "Prof. [Me. ou Dr.] [Nome Completo do Professor]" ficam assim mesmo, inclusive com o destaque amarelo.
- **Avisar o andamento:** em tarefas longas, dizer o que está fazendo. Ao terminar cada passo, avisar que ele terminou e qual é o próximo.

## Stack e estrutura

- **Backend:** Python 3, FastAPI e Uvicorn, com eventos ao vivo (SSE).
- **OCR e imagens:** Tesseract 5, Poppler e pdf2image, Pillow, pypdf.
- **Banco de dados:** SQLite (modo WAL).
- **Frontend:** JavaScript puro com IBM Carbon. O Chart.js fica local, em `static/vendor/`, para funcionar sem internet.
- **Pastas:**
  - `core/`: `engine.py` (OCR com 8 estratégias e votação por consenso, pareamento por código, código parecido, valor, posição e duplicata), `profiles.py` (modelos de documento), `reports.py` (relatório PDF e exportação CSV), `storage.py` (SQLite), `samples.py` (geração dos lotes de teste).
  - `api/`: `main.py` (rotas, auditorias, revisão e correção), `bi.py` (painel de BI em `/api/v1/bi/overview`).
  - `static/`: `index.html`, `app.js` e `bi.js`.
  - `profiles/`: modelos em JSON. O `padrao` é embutido no código; `exemplo-codigo-z.json` vem no projeto. A **Guia municipal é criada ao vivo no assistente durante a apresentação** (código de exemplo `MAT-80621`), por isso não fica em `profiles/`.
  - `tools/`: `seed_demo.py` (prepara a base de demonstração) e benchmark.
  - `tests/`: pytest, 248 testes.

## Como rodar

- **Windows:** `setup.bat` uma vez (instala Python, bibliotecas, Tesseract e Poppler pela internet, ou usa os do pendrive preparado com `prepare-usb.bat`). Depois `start-server.bat` (ou `start-demo.bat` para a base de demonstração).
- **Testes:** `.venv\Scripts\python -m pytest -q`
- **Conta de demonstração** (dados só de teste): `demonstracao` / `ribbon-demo-2026`.
- **Lotes de exemplo:** `python -m tools.seed_demo --skip-audits` gera os PDFs em `demo/`. O .zip da entrega também traz uma pasta `exemplos/` com 50 guias padrão e o lote municipal (normal e torto).

## Números do TCC (usar sempre estes)

- **Conferência manual:** 50 a 60 min por lote de cerca de 250 guias, ou 12 a 14 s por guia.
- **RIBBON no lote real de 250 guias a 500 DPI:** 354 s (cerca de 6 min), 1,4 s por guia, só 4 guias sinalizadas (1,6% do lote). A 300 DPI: cerca de 1 min, 0,2 s por guia.
- **Com a revisão:** menos de 10 min por lote, redução superior a 80%.
- **Lote de 50 guias padrão** (`exemplos/1`): 50 de 50 conformes a 300 DPI, em cerca de 53 s.

## Situação da entrega (prazo: 09/11/2026, 22:00)

- **Texto final:** `tcc_final ofc4`, aprovado.
- **Pasta de entrega:** `C:\Users\Felipe\Downloads\ENTREGA TCC FINAL\`, com:
  - `01 - Videos (com narracao)`: 5 vídeos narrados pelo Felipe.
  - `02 - Slides`: `RIBBON - Slides.pptx` e o PDF, com Morph e anotações do apresentador; o fio condutor é a conferência manual × RIBBON.
  - `03 - Codigo-fonte`: `RIBBON - Codigo-fonte.zip`.
- **Pendências:** imprimir as 3 cópias (Lab. A18), enviar vídeos, slides e .zip na Tarefa do Teams, e ensaiar a apresentação.
- **Apresentação:** 15 a 20 min no total, com até 5 min de slides e o resto para a demonstração ao vivo com cenários completos. Ordem combinada: lote de 50 guias, depois criar o modelo Guia municipal, rodar as guias municipais normais e as tortas e revisar a divergente, e por fim o painel de BI e as métricas.
