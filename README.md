# 📲 Instagram SDR & Prospecção Autônoma via ADB (Olheiro + Sniper + CRM Web)

Sistema completo de **prospecção ativa e qualificação comercial no Instagram usando celulares Android físicos via ADB**, visão computacional em tempo real (**YOLOv11 + OpenCV**), transcrição de fala em Português (**Faster-Whisper GPU**), proteção inteligente **Anti-Block** e **CRM Web Local (`:4950`)** com banco de dados SQLite criado automaticamente.

---

## 🧠 1. Arquitetura do Sistema (Dual-Phone: Olheiro + Sniper)

O sistema foi desenhado para separar o esforço de **descoberta/filtragem** do momento do **disparo de Direct**, protegendo a conta comercial contra bloqueios do Instagram:

1. **📱 Celular 1 — OLHEIRO (Caçador de Leads nos Reels)**:
   - Navega pelo feed de Reels de forma nativa via `uiautomator2`.
   - **Filtro XML Instantâneo (`0.4s`)**: Ignora anúncios patrocinados, grades de Reels sugeridos e vídeos curtos (`<= 15s` sem barra de progresso).
   - **Visão Computacional (`YOLOv11n` + `OpenCV`)**: Verifica se há um especialista em destaque (*Talking Head* ocupando `>= 10%` da tela), descarta cenas de multidão e aplica filtro de gênero/nome.
   - **Validador de Fala em Português (`Faster-Whisper GPU` + `Snapinsta SDK`)**: Extrai os primeiros 9 segundos de áudio do Reel e confirma se a pessoa está realmente falando em Português (descartando vídeos apenas com música de fundo ou em outro idioma).
   - **Qualificação de Perfil**: Abre o perfil do criador, verifica se a conta é pública, se possui entre `1` e `1.000.000` de seguidores, se tem **Story ativo** (sinal de conta ativa hoje) e se a Bio possui indicadores comerciais/profissionais (via heurística local instantânea `0ms` ou `DeepSeek API` opcional).

2. **🎯 Celular 2 — SNIPER (Disparador de DM & Fechador com Escudo Anti-Block)**:
   - Só entra em ação quando o **Olheiro** aprova 100% um lead inédito.
   - Abre o perfil aprovado via *deep-link* direto (`instagram.com/_u/<username>`), clica em **Mensagem** e verifica na árvore XML se já houve conversa anterior (**Trava Anti-Duplicidade**).
   - Digita e envia a abordagem personalizada com o primeiro nome do lead.
   - **Escudo Anti-Block Pós-Envio (`detectar_bloqueio_ou_falha_entrega_dm`)**: Aguarda `1.5s` após o envio e inspeciona o XML da conversa para confirmar que o balão da mensagem realmente entrou na thread sem ícones de falha ou pop-ups de restrição do Instagram. Caso detecte bloqueio de ação, interrompe a execução na hora para proteger a conta.

3. **💻 Dashboard Web SDR & CRM Autônomo (`http://127.0.0.1:4950`)**:
   - **Notification Watcher Silencioso**: Lê `adb shell dumpsys notification --noredact` em background sem tocar na tela do celular.
   - **Boot Catch-Up (Inbox Scanner)**: Varre a Inbox do Direct ao ligar o PC para capturar quem respondeu enquanto o computador estava desligado.
   - **Classificador de Intenção & Roteiros de Áudio de 15s**: Classifica respostas em *Responder Agora*, *Interesse Quente / Vídeo Teste* ou *Reativação*, gerando o roteiro de áudio exato na tela e um botão **"📱 Abrir Chat no Celular (ADB)"** com 1 clique.

---

## 🛠️ 2. Pré-Requisitos

1. **Windows 10 ou 11** com **Python 3.10 ou 3.11** instalado.
2. **ADB (`Android Platform Tools`)** e **Scrcpy** instalados no PC (ou adicionados ao `PATH` do Windows):
   - Via terminal PowerShell (Winget):
     ```powershell
     winget install Google.PlatformTools
     winget install Genymobile.Scrcpy
     winget install Gyan.FFmpeg
     ```
3. **1 ou 2 Celulares Android conectados via Cabo USB** com a **Depuração USB** ativada:
   - No celular, vá em *Configurações > Sobre o telefone > Informações do software* e toque **7 vezes** em **Número de compilação** até ativar as *Opções do desenvolvedor*.
   - Volte em *Configurações > Opções do desenvolvedor* e ative **Depuração USB** (e, em aparelhos Xiaomi/Poco, ative também *Depuração USB (Configurações de segurança)* para permitir toques via ADB).
   - Conecte o cabo USB no PC e marque **"Sempre permitir a partir deste computador"** no alerta que aparecer na tela do celular.

---

## 🚀 3. Instalação Rápida (Passo a Passo)

1. **Extraia ou clone este repositório** em uma pasta do seu computador:
   ```powershell
   git clone https://github.com/NicolasCoder/instagram-sdr-adb-automation.git
   cd instagram-sdr-adb-automation
   ```
2. **Dê dois cliques em `1_INICIAR_SETUP.bat`** (ou execute manualmente):
   - O instalador automático irá:
     - Criar o arquivo `.env` a partir do `.env.example`.
     - Criar o ambiente virtual `venv/` e instalar todas as bibliotecas do `requirements.txt`.
     - Baixar os modelos de visão computacional (`models/deploy_gender.prototxt`, `models/gender_net.caffemodel` e `models/yolo11n.pt`).
     - Injetar o servidor de automação `uiautomator2` nos celulares conectados via USB.
3. **Configure o arquivo `.env` (Opcional, mas recomendado)**:
   - Abra o arquivo `.env` no Bloco de Notas ou VS Code.
   - Para descobrir o código serial de cada celular conectado, rode `adb devices` no terminal (ou clique em `5_RESET_USB_ADB.bat`).
   - Preencha `SERIAL_OLHEIRO` e `SERIAL_SNIPER` no `.env` (se deixar em branco, o sistema detecta automaticamente os celulares plugados via USB!).
   - Personalize seu nome (`SDR_OPERATOR_NAME`), WhatsApp (`SDR_CONTACT_WHATSAPP`), e-mail (`SDR_CONTACT_EMAIL`) e a mensagem padrão de abordagem (`SDR_DEFAULT_OUTREACH_COPY`).

---

## 🎮 4. Como Usar no Dia a Dia

### Passo 1 — Espelhar as Telas dos Celulares no PC
Dê dois cliques em **`2_ABRIR_PAINEL_CELULARES.bat`**.
- Duas janelas do `scrcpy` serão abertas no seu monitor:
  - **`OLHEIRO (Cacador de Leads)`**: Abra o Instagram nesta conta secundária e deixe na aba **Reels**.
  - **`SNIPER (Fechador de Contratos)`**: Deixe o Instagram logado na sua conta principal/comercial e com a tela desbloqueada.

### Passo 2 — Iniciar a Prospecção Automática
Dê dois cliques em **`3_INICIAR_PROSPECCAO.bat`** (ou rode via terminal):
```powershell
# Rodar meta de 100 envios com pausa anti-spam entre 15s e 30s (padrão):
python megazord_prospeccao.py --ciclos 100 --delay-min 15 --delay-max 30

# Ou modo rápido supervisionado (pausa curta de 6s a 10s para acompanhar ao vivo):
python megazord_prospeccao.py --supervisionado
```
> **Nota sobre o Banco de Dados**: Na primeira execução, o sistema cria automaticamente a pasta `data/` com os bancos SQLite vazios (`data/banco_local.sqlite` e `data/instagram_sdr_crm.db`) e a lista `data/prospectados.txt` para garantir que nenhum perfil receba mensagem repetida.

### Passo 3 — Operar o Dashboard Web CRM (`http://127.0.0.1:4950`)
Dê dois cliques em **`4_ABRIR_CRM_DASHBOARD.bat`**.
- O navegador abrirá automaticamente em `http://127.0.0.1:4950`.
- Use os botões do topo:
  - **🔔 Ler Notificações Agora**: Captura respostas recentes do Instagram na bandeja do Android sem mexer na tela.
  - **🚀 Boot Catch-Up (Varrer Inbox ADB)**: Abre a Inbox do Direct no celular e importa conversas não lidas.
- Em qualquer card de lead, clique em **"📱 Abrir Chat no Celular (ADB)"** para o celular abrir instantaneamente a conversa daquele lead pronto para você gravar o áudio de 15 segundos ou clicar em **"🤖 Disparar Texto via ADB"**.

---

## 📂 5. Estrutura de Arquivos do Projeto

```text
├── 1_INICIAR_SETUP.bat             # Instalador automático (venv + requirements + modelos + uiautomator2 init)
├── 2_ABRIR_PAINEL_CELULARES.bat    # Abre as janelas Scrcpy para o Olheiro e o Sniper
├── 3_INICIAR_PROSPECCAO.bat        # Inicia o motor principal megazord_prospeccao.py
├── 4_ABRIR_CRM_DASHBOARD.bat       # Inicia o servidor CRM Web em http://127.0.0.1:4950
├── 5_RESET_USB_ADB.bat             # Reinicia o servidor ADB caso algum cabo USB desconecte
├── megazord_prospeccao.py          # Motor principal Dual-Phone (Olheiro Reels + YOLO/Whisper + Sniper Anti-Block)
├── insta_adb_sdr_engine.py         # Motor de captura de notificações, Inbox Scanner e Auto-Responder SQLite
├── sdr_web_dashboard.py            # Interface Web CRM (:4950)
├── snapinsta_api.py                # Extrator de streams de áudio/vídeo do Instagram para validação de fala
├── models/
│   ├── deploy_gender.prototxt      # Definição da rede OpenCV DNN para filtro de gênero
│   ├── yolo11n.pt                  # Pesos YOLOv11 Nano para detecção de Talking Head em tempo real
│   └── download_models.py          # Script que baixa os modelos automaticamente no setup
├── scripts_extras/                 # Modos alternativos opcionais (Gemini Vision / Single-Phone / Monitor)
│   ├── megazord_multicelular.py
│   ├── modulo_fechamento.py
│   ├── monitor_notificacoes.py
│   └── treinar_perfil_reels.py
├── .env.example                    # Exemplo de configuração de variáveis de ambiente
├── .gitignore                      # Proteção para não subir .env, bancos SQLite ou listas de leads no Git
└── requirements.txt                # Lista de pacotes Python necessários
```
