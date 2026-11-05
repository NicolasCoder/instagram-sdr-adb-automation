# -*- coding: utf-8 -*-
"""
Motor Híbrido de Captura & Disparo ADB para Instagram Direct (insta_adb_sdr_engine.py)
Conectado ao celular Android via ADB (configurado via .env ou auto-detectado via `adb devices`).

Possui 3 Módulos Integrados ao SQLite (data/instagram_sdr_crm.db):
  1. Módulo 1 — Notification Watcher (Tempo Real Silencioso via `dumpsys notification --noredact`)
  2. Módulo 2 — Boot Catch-Up / Inbox Scanner (Leitura de conversas não lidas/recentes via UIAutomator)
  3. Módulo 3 — Auto-Responder Inbound até Interesse no Vídeo Teste + Alerta de Áudio / Abrir Chat no Celular via ADB
"""
import os
import re
import sys
import time
import json
import shutil
import sqlite3
import argparse
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# ESCUDO ANTI-FLICKER DE CONSOLE NO WINDOWS (Força CREATE_NO_WINDOW=0x08000000 + SW_HIDE em 100% dos subprocessos)
if sys.platform == "win32":
    _orig_popen = subprocess.Popen

    class _NoWindowPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000
            if "startupinfo" not in kwargs or kwargs["startupinfo"] is None:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                kwargs["startupinfo"] = si
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("CRM_DB_PATH", str(DATA_DIR / "instagram_sdr_crm.db")))

SDR_OPERATOR_NAME = os.getenv("SDR_OPERATOR_NAME", "Operador").strip() or "Operador"
SDR_CONTACT_EMAIL = os.getenv("SDR_CONTACT_EMAIL", "seuemail@dominio.com").strip()
SDR_CONTACT_WHATSAPP = os.getenv("SDR_CONTACT_WHATSAPP", "(00) 90000-0000").strip()
SDR_PRICE_RANGE = os.getenv("SDR_PRICE_RANGE", "R$ 40 a R$ 50").strip()
DEFAULT_SERIAL = os.getenv("SERIAL_SNIPER", "").strip()


def init_crm_db():
    """
    Inicializa automaticamente o banco SQLite do CRM (`instagram_sdr_crm.db`) na primeira execução
    caso o arquivo `.db` ainda não exista.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_folder TEXT UNIQUE,
            username TEXT,
            username_source TEXT,
            title TEXT,
            box TEXT,
            category TEXT,
            funnel_stage TEXT,
            priority_score INTEGER DEFAULT 0,
            msg_count INTEGER DEFAULT 0,
            nicolas_msg_count INTEGER DEFAULT 0,
            client_msg_count INTEGER DEFAULT 0,
            audio_count INTEGER DEFAULT 0,
            nicolas_audio_count INTEGER DEFAULT 0,
            client_audio_count INTEGER DEFAULT 0,
            last_sender TEXT,
            last_message_text TEXT,
            last_message_dt TEXT,
            last_timestamp_ms INTEGER DEFAULT 0,
            first_nicolas_dt TEXT,
            whatsapp_phone TEXT,
            drive_links TEXT,
            recommended_action_type TEXT,
            suggested_text_reply TEXT,
            suggested_audio_script TEXT,
            context_summary TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_folder TEXT,
            username TEXT,
            sender TEXT,
            sender_raw TEXT,
            timestamp_ms INTEGER,
            dt_str TEXT,
            content TEXT,
            audio_uris TEXT,
            audio_transcription TEXT,
            share_link TEXT,
            reactions TEXT,
            source TEXT DEFAULT 'ADB'
        );

        CREATE TABLE IF NOT EXISTS audio_alerts_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_folder TEXT,
            username TEXT,
            title TEXT,
            client_last_msg TEXT,
            context_one_liner TEXT,
            audio_script_15s TEXT,
            suggested_text_fallback TEXT,
            alert_type TEXT,
            priority_score INTEGER DEFAULT 90,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS adb_sync_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_type TEXT,
            details TEXT,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_conv_stage ON conversations(funnel_stage);
        CREATE INDEX IF NOT EXISTS idx_conv_username ON conversations(username);
        CREATE INDEX IF NOT EXISTS idx_msg_folder ON messages(thread_folder);
        """
    )
    conn.commit()
    conn.close()


init_crm_db()


def get_adb_exe():
    env_adb = os.getenv("ADB_PATH", "").strip()
    if env_adb and Path(env_adb).exists():
        return env_adb
    local_candidates = [
        BASE_DIR / "platform-tools" / "adb.exe",
        BASE_DIR / "scrcpy" / "adb.exe",
    ]
    for cand in local_candidates:
        if cand.exists():
            return str(cand)
    which_adb = shutil.which("adb")
    return which_adb or "adb"


def _hidden_startupinfo():
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return si
    return None


def get_adb_status(preferred_serial=DEFAULT_SERIAL):
    adb = get_adb_exe()
    try:
        res = subprocess.run(
            [adb, "devices", "-l"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            startupinfo=_hidden_startupinfo(),
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip() and not ln.startswith("List of devices")]
        devices = []
        for ln in lines:
            parts = ln.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                model_m = re.search(r"model:(\S+)", ln)
                model = model_m.group(1) if model_m else "Android"
                devices.append({"serial": serial, "model": model, "raw": ln})

        if not devices:
            return {"connected": False, "serial": None, "model": None, "devices": []}

        chosen = devices[-1]  # Por padrão, se houver 2 celulares (Olheiro e Sniper), o 2º costuma ser o Sniper
        if preferred_serial:
            for d in devices:
                if d["serial"] == preferred_serial:
                    chosen = d
                    break

        return {
            "connected": True,
            "serial": chosen["serial"],
            "model": chosen["model"],
            "devices": devices,
        }
    except Exception as e:
        return {"connected": False, "serial": None, "model": None, "error": str(e), "devices": []}


def resolve_target_serial(serial=DEFAULT_SERIAL):
    if serial:
        return serial
    status = get_adb_status()
    return status.get("serial") or ""


def run_adb_cmd(args, serial=DEFAULT_SERIAL, timeout=15):
    adb = get_adb_exe()
    target_serial = resolve_target_serial(serial)
    cmd = [adb]
    if target_serial:
        cmd.extend(["-s", target_serial])
    cmd.extend(args)
    try:
        res = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            startupinfo=_hidden_startupinfo(),
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        return res.returncode == 0, res.stdout, res.stderr
    except Exception as e:
        return False, "", str(e)


# ==============================================================================
# MÓDULO 3: CLASSIFICADOR DE INTENÇÃO INBOUND & GERADOR DE RESPOSTAS / ÁUDIOS
# ==============================================================================
def classify_inbound_intent(lead_name, username, client_text):
    """
    Classifica a resposta do lead e gera:
      - funnel_stage
      - recommended_action_type ('ENVIAR_TEXTO_ADB', 'GRAVAR_AUDIO', 'CHAMAR_WHATSAPP', 'NENHUMA')
      - alert_type ('GRAVAR_AUDIO_AGORA', 'LEAD_QUER_VIDEO_TESTE', None)
      - suggested_text_reply
      - suggested_audio_script (roteiro de 15-20s entre aspas pronto para leitura)
      - context_summary
    """
    first_name = (lead_name or username or "irmão").split()[0].replace("@", "")
    txt_lower = (client_text or "").lower()

    # 1. Desinteressado
    if any(
        w in txt_lower
        for w in [
            "não tenho interesse",
            "nao tenho interesse",
            "não preciso",
            "nao preciso",
            "no momento não",
            "no momento nao",
            "já tenho equipe fechada",
            "estou bem atendido",
        ]
    ):
        return {
            "intent": "DESINTERESSADO",
            "funnel_stage": "DESINTERESSADO",
            "priority_score": 10,
            "recommended_action_type": "NENHUMA",
            "alert_type": None,
            "context_summary": f"Lead informou que não precisa no momento ('{client_text[:60]}').",
            "suggested_text_reply": f"Tranquilo {first_name}, agradeço demais a atenção! Precisando no futuro pode contar comigo, sucesso aí!",
            "suggested_audio_script": "",
        }

    # 2. Aceitou Vídeo Teste / Passou WhatsApp / Mandou Link
    has_phone = bool(re.search(r"\d{4,5}[\s-]?\d{4}", txt_lower))
    has_drive = "drive.google" in txt_lower or "we.tl" in txt_lower or "wetransfer" in txt_lower
    if (
        has_phone
        or has_drive
        or any(
            w in txt_lower
            for w in [
                "topo",
                "bora",
                "vamos testar",
                "quero fazer o teste",
                "quero o vídeo teste",
                "quero o video teste",
                "como te mando",
                "onde te mando",
                "vou te mandar",
                "me chama no whats",
                "meu whats",
            ]
        )
    ):
        return {
            "intent": "ACEITOU_VIDEO_TESTE",
            "funnel_stage": "INTERESSE_QUENTE",
            "priority_score": 99,
            "recommended_action_type": "CHAMAR_WHATSAPP" if has_phone else "GRAVAR_AUDIO",
            "alert_type": "LEAD_QUER_VIDEO_TESTE",
            "context_summary": f"🎬 LEAD TOPOU O VÍDEO TESTE / AVANÇOU: '{client_text[:90]}'",
            "suggested_text_reply": (
                f"Show demais {first_name}! Pode me mandar o vídeo bruto por link do Google Drive aqui mesmo, "
                f"no meu e-mail {SDR_CONTACT_EMAIL} ou me chama no WhatsApp {SDR_CONTACT_WHATSAPP} que já edito pra você hoje!"
            ),
            "suggested_audio_script": (
                f"Show demais {first_name}, fechou! Ó, escolhe qualquer vídeo bruto seu de até 1 minutinho — "
                f"aquele que você achar que tem um conteúdo bem legal — e me manda o link do Google Drive aqui, "
                f"ou no meu WhatsApp que te mandei aí embaixo, que eu já vou transformar ele pra você ver na prática hoje mesmo!"
            ),
        }

    # 3. Perguntou Preço / Orçamento / Pacotes
    if any(
        w in txt_lower
        for w in ["valor", "preço", "preco", "quanto cobra", "quanto custa", "orçamento", "orcamento", "pacote"]
    ):
        return {
            "intent": "PERGUNTOU_PRECO",
            "funnel_stage": "INTERESSE_QUENTE",
            "priority_score": 96,
            "recommended_action_type": "GRAVAR_AUDIO",
            "alert_type": "GRAVAR_AUDIO_AGORA",
            "context_summary": f"Lead perguntou sobre preço/pacotes ('{client_text[:80]}'). Momento ideal para áudio + Vídeo Teste!",
            "suggested_text_reply": (
                f"Cara, em pacotes fechados (15, 30, 60 ou 90 vídeos) o valor fica na faixa de {SDR_PRICE_RANGE} por vídeo "
                f"com entrega no mesmo dia! Mas antes de falar de pacote, eu faço questão de editar 1 Vídeo Teste sem custo "
                f"em cima de um bruto seu pra você aprovar minha edição na prática. Me manda 1 vídeo bruto aí!"
            ),
            "suggested_audio_script": (
                f"Fala {first_name}, beleza irmão? Ó, em pacote fechado de 15, 30 ou 60 vídeos, o valor fica na faixa de "
                f"{SDR_PRICE_RANGE} por vídeo, entregando no mesmo dia! Mas antes de você gastar qualquer centavo, faz o seguinte: "
                f"me manda 1 vídeo bruto seu hoje que eu faço 1 vídeo teste por conta da casa pra você aprovar o estilo na prática, combinado?"
            ),
        }

    # 4. Pediu explicação / Como funciona / Mandou áudio
    if any(
        w in txt_lower
        for w in [
            "como funciona",
            "me conta",
            "me explica",
            "fala mais",
            "seu trabalho",
            "portfólio",
            "portfolio",
            "mensagem de voz",
            "[áudio]",
            "áudio",
        ]
    ):
        return {
            "intent": "CURIOSO_COMO_FUNCIONA",
            "funnel_stage": "RESPONDER_AGORA",
            "priority_score": 95,
            "recommended_action_type": "GRAVAR_AUDIO",
            "alert_type": "GRAVAR_AUDIO_AGORA",
            "context_summary": f"Lead pediu mais detalhes ou mandou áudio ('{client_text[:80]}'). Áudio de 15s converte >60% aqui!",
            "suggested_text_reply": (
                f"Fala {first_name}! Eu trabalho com edição dinâmica de Reels focada nos 3 primeiros segundos de retenção "
                f"e uso automação/IA no fluxo pra entregar no mesmo dia. Que tal me mandar 1 vídeo bruto seu pra eu fazer "
                f"1 Vídeo Teste 100% por conta da casa pra você ver no seu próprio conteúdo?"
            ),
            "suggested_audio_script": (
                f"Fala {first_name}, tranquilo irmão? Ó, resumindo: eu trabalho com edição de Reels focada em "
                f"alta retenção e uso bastante automação e IA no meu fluxo, então consigo te entregar vídeos no mesmo dia "
                f"com cortes dinâmicos, legenda e B-roll. Me manda 1 vídeo bruto seu de 1 minutinho que eu faço 1 vídeo teste "
                f"100% por conta da casa pra você ver na prática como fica no seu perfil, bora?"
            ),
        }

    # 5. Edita sozinho / Não tem editor
    if any(
        w in txt_lower
        for w in [
            "eu mesmo",
            "faço tudo",
            "faco tudo",
            "não tenho",
            "nao tenho",
            "sozinho",
            "nem edito",
            "ainda não",
            "ainda nao",
            "nenhum",
            "zero",
        ]
    ):
        return {
            "intent": "EDITA_SOZINHO",
            "funnel_stage": "RESPONDER_AGORA",
            "priority_score": 92,
            "recommended_action_type": "ENVIAR_TEXTO_ADB",
            "alert_type": "GRAVAR_AUDIO_AGORA",
            "context_summary": f"Lead disse que edita sozinho / não tem editor ('{client_text[:80]}').",
            "suggested_text_reply": (
                f"Guerreiro mesmo hein {first_name} kkk Gravar, tocar o negócio e ainda editar tudo sozinho na raça toma um tempo absurdo! "
                f"Ó, que tal eu fazer 1 vídeo teste 100% por conta da casa em cima de um vídeo bruto seu? "
                f"Aí você vê na prática como fica meu estilo de edição no seu perfil sem compromisso nenhum, topa?"
            ),
            "suggested_audio_script": (
                f"Fala {first_name}, guerreiro demais hein irmão kkk Cuidar do negócio, gravar e ainda parar pra editar tudo sozinho "
                f"toma um tempo absurdo! Faz o seguinte: me manda 1 vídeo bruto que você já gravou aí que eu faço 1 edição teste "
                f"100% por conta da casa e te entrego no mesmo dia pra você ver a diferença na retenção e no seu tempo livre, topa?"
            ),
        }

    # 6. Default (Tem editor / Respondeu volume / Outra resposta)
    return {
        "intent": "QUALIFICACAO_GERAL",
        "funnel_stage": "RESPONDER_AGORA",
        "priority_score": 89,
        "recommended_action_type": "ENVIAR_TEXTO_ADB",
        "alert_type": "GRAVAR_AUDIO_AGORA",
        "context_summary": f"Lead respondeu ('{client_text[:80]}') e aguarda oferta do Vídeo Teste.",
        "suggested_text_reply": (
            f"Show demais {first_name}! Perguntei porque sou especialista em edição dinâmica focada em retenção nos 3 primeiros segundos "
            f"com entrega no mesmo dia. Topa me mandar 1 vídeo bruto seu pra eu fazer 1 Vídeo Teste 100% gratuito só pra você comparar o resultado na prática?"
        ),
        "suggested_audio_script": (
            f"Fala {first_name}, show demais irmão! Te perguntei porque eu sou especialista em edição dinâmica focada em retenção "
            f"nos 3 primeiros segundos e entrega no mesmo dia. Topa me mandar 1 vídeo bruto seu pra eu fazer 1 edição teste 100% "
            f"por conta da casa só pra você comparar a retenção e a velocidade na prática?"
        ),
    }


def record_inbound_message_in_db(username, title, msg_text, source="ADB_NOTIFICATION"):
    """
    Salva uma nova mensagem detectada via ADB Notification ou Inbox Scan no banco SQLite,
    evitando duplicatas e gerando automaticamente recomendação + Card de Áudio se aplicável.
    """
    init_crm_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    clean_user = (username or "").strip().lstrip("@").lower()
    clean_title = (title or clean_user).strip()
    clean_msg = (msg_text or "").strip()
    if not clean_msg:
        conn.close()
        return None

    cur.execute(
        """
        SELECT id FROM messages
        WHERE (LOWER(username) = ? OR LOWER(sender_raw) = ?)
          AND content = ?
        LIMIT 1
        """,
        (clean_user, clean_title.lower(), clean_msg),
    )
    if cur.fetchone():
        conn.close()
        return None

    cur.execute(
        """
        SELECT * FROM conversations
        WHERE LOWER(username) = ? OR LOWER(title) = ?
        ORDER BY last_timestamp_ms DESC LIMIT 1
        """,
        (clean_user, clean_title.lower()),
    )
    conv = cur.fetchone()

    now_ms = int(time.time() * 1000)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    classification = classify_inbound_intent(clean_title, clean_user, clean_msg)

    if conv:
        folder = conv["thread_folder"]
        resolved_u = conv["username"] or clean_user
        resolved_title = conv["title"] or clean_title
        is_personal = conv["category"] == "PERSONAL_FRIEND" or conv["funnel_stage"] == "PESSOAL"
        new_stage = "PESSOAL" if is_personal else classification["funnel_stage"]
        new_prio = 5 if is_personal else classification["priority_score"]
        new_action = "NENHUMA" if is_personal else classification["recommended_action_type"]
        if is_personal:
            classification["alert_type"] = None
        cur.execute(
            """
            UPDATE conversations
            SET funnel_stage = ?,
                priority_score = ?,
                msg_count = msg_count + 1,
                client_msg_count = client_msg_count + 1,
                last_sender = 'CLIENT',
                last_message_text = ?,
                last_message_dt = ?,
                last_timestamp_ms = ?,
                recommended_action_type = ?,
                suggested_text_reply = ?,
                suggested_audio_script = ?,
                context_summary = ?,
                updated_at = ?
            WHERE thread_folder = ?
            """,
            (
                new_stage,
                new_prio,
                clean_msg,
                now_str,
                now_ms,
                new_action,
                "" if is_personal else classification["suggested_text_reply"],
                "" if is_personal else classification["suggested_audio_script"],
                "Conversa pessoal." if is_personal else classification["context_summary"],
                now_str,
                folder,
            ),
        )
    else:
        folder = f"adb_{re.sub(r'[^a-z0-9]', '', clean_user or clean_title.lower())}_{now_ms}"
        resolved_u = clean_user or re.sub(r"[^a-z0-9_.]", "", clean_title.lower())
        resolved_title = clean_title
        cur.execute(
            """
            INSERT INTO conversations (
                thread_folder, username, username_source, title, box, category, funnel_stage,
                priority_score, msg_count, nicolas_msg_count, client_msg_count,
                audio_count, nicolas_audio_count, client_audio_count,
                last_sender, last_message_text, last_message_dt, last_timestamp_ms,
                first_nicolas_dt, whatsapp_phone, drive_links,
                recommended_action_type, suggested_text_reply, suggested_audio_script,
                context_summary, updated_at
            ) VALUES (?, ?, ?, ?, 'inbox', 'COMMERCIAL_LEAD', ?, ?, 1, 0, 1, 0, 0, 0, 'CLIENT', ?, ?, ?, '', '', '', ?, ?, ?, ?, ?)
            """,
            (
                folder,
                resolved_u,
                source,
                resolved_title,
                classification["funnel_stage"],
                classification["priority_score"],
                clean_msg,
                now_str,
                now_ms,
                classification["recommended_action_type"],
                classification["suggested_text_reply"],
                classification["suggested_audio_script"],
                classification["context_summary"],
                now_str,
            ),
        )

    cur.execute(
        """
        INSERT INTO messages (
            thread_folder, username, sender, sender_raw, timestamp_ms, dt_str,
            content, audio_uris, audio_transcription, share_link, reactions, source
        ) VALUES (?, ?, 'CLIENT', ?, ?, ?, ?, '[]', '', '', '[]', ?)
        """,
        (folder, resolved_u, resolved_title, now_ms, now_str, clean_msg, source),
    )

    if classification["alert_type"]:
        cur.execute(
            "SELECT id FROM audio_alerts_queue WHERE thread_folder = ? AND status = 'PENDING'",
            (folder,),
        )
        existing_card = cur.fetchone()
        if existing_card:
            cur.execute(
                """
                UPDATE audio_alerts_queue
                SET client_last_msg = ?,
                    context_one_liner = ?,
                    audio_script_15s = ?,
                    suggested_text_fallback = ?,
                    alert_type = ?,
                    priority_score = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    clean_msg,
                    classification["context_summary"],
                    classification["suggested_audio_script"],
                    classification["suggested_text_reply"],
                    classification["alert_type"],
                    classification["priority_score"],
                    now_str,
                    existing_card["id"],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO audio_alerts_queue (
                    thread_folder, username, title, client_last_msg, context_one_liner,
                    audio_script_15s, suggested_text_fallback, alert_type, priority_score, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (
                    folder,
                    resolved_u,
                    resolved_title,
                    clean_msg,
                    classification["context_summary"],
                    classification["suggested_audio_script"],
                    classification["suggested_text_reply"],
                    classification["alert_type"],
                    classification["priority_score"],
                    now_str,
                ),
            )

    conn.commit()
    conn.close()
    return {
        "username": resolved_u,
        "title": resolved_title,
        "message": clean_msg,
        "classification": classification,
    }


# ==============================================================================
# MÓDULO 1: NOTIFICATION WATCHER (TEMPO REAL SILENCIOSO SEM TOCAR NA TELA)
# ==============================================================================
def scan_notifications_once(serial=DEFAULT_SERIAL):
    """
    Executa `adb shell dumpsys notification --noredact` e extrai todas as notificações
    ativas do pacote `com.instagram.android`. Não toca na tela do celular!
    """
    ok, stdout, stderr = run_adb_cmd(["shell", "dumpsys", "notification", "--noredact"], serial=serial, timeout=15)
    if not ok:
        return {"ok": False, "error": stderr or "Falha ao rodar dumpsys notification", "new_messages": []}

    blocks = stdout.split("NotificationRecord(")
    extracted_notifs = []
    new_saved = []

    for blk in blocks[1:]:
        header_line = blk.splitlines()[0] if blk.splitlines() else ""
        if "pkg=com.instagram.android" not in header_line and "com.instagram.android" not in blk[:300]:
            continue

        title_m = re.search(r"android\.title=(?:String\s*\()?([^\r\n]+)", blk)
        text_m = re.search(r"android\.text=(?:String\s*\()?([^\r\n]+)", blk)
        big_m = re.search(r"android\.bigText=(?:String\s*\()?([^\r\n]+)", blk)

        raw_title = (title_m.group(1).strip().rstrip(")") if title_m else "").strip()
        raw_text = (big_m.group(1).strip().rstrip(")") if big_m else (text_m.group(1).strip().rstrip(")") if text_m else "")).strip()

        if not raw_text or raw_text == "null":
            continue

        lower_t = f"{raw_title} {raw_text}".lower()
        if any(
            ign in lower_t
            for ign in [
                "novas notificações",
                "new notifications",
                "enviando",
                "uploading",
                "sincronizando",
                "novos seguidores",
                "novo seguidor",
                "começaram a seguir",
                "começou a seguir",
                "curtiu sua foto",
                "curtiu seu vídeo",
                "curtiu seu reel",
                "iniciou um vídeo ao vivo",
            ]
        ):
            continue

        sender_name = raw_title if raw_title and raw_title.lower() != "instagram" else ""
        msg_body = raw_text
        if ":" in raw_text and (not sender_name or sender_name.lower() == "instagram"):
            parts = raw_text.split(":", 1)
            sender_name = parts[0].strip()
            msg_body = parts[1].strip()
        elif sender_name and msg_body.startswith(f"{sender_name}:"):
            msg_body = msg_body[len(sender_name) + 1 :].strip()

        if not sender_name:
            continue

        extracted_notifs.append({"sender": sender_name, "text": msg_body})
        saved = record_inbound_message_in_db(sender_name, sender_name, msg_body, source="ADB_NOTIFICATION")
        if saved:
            new_saved.append(saved)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO adb_sync_logs (sync_type, details, created_at) VALUES (?, ?, ?)",
            (
                "NOTIFICATION_WATCHER",
                json.dumps({"found_in_tray": len(extracted_notifs), "new_saved": len(new_saved)}, ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return {
        "ok": True,
        "found_in_tray": len(extracted_notifs),
        "notifications": extracted_notifs,
        "new_messages": new_saved,
    }


# ==============================================================================
# MÓDULO 2: BOOT CATCH-UP / INBOX SCANNER
# ==============================================================================
def run_boot_catchup(serial=DEFAULT_SERIAL, max_scrolls=2):
    """
    1. Primeiro lê todas as notificações pendentes na bandeja do Android.
    2. Depois acorda o celular, abre a Inbox do Direct do Instagram (`instagram://direct-inbox`)
       e faz dump da árvore XML (`uiautomator dump`) para ler as conversas recentes/não lidas.
    """
    notif_res = scan_notifications_once(serial=serial)
    inbox_items = []
    new_from_inbox = []

    run_adb_cmd(["shell", "input", "keyevent", "224"], serial=serial)
    run_adb_cmd(["shell", "wm", "dismiss-keyguard"], serial=serial)
    time.sleep(0.8)

    run_adb_cmd(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "instagram://direct-inbox",
            "-p",
            "com.instagram.android",
        ],
        serial=serial,
    )
    time.sleep(4.0)

    for scroll_idx in range(max_scrolls):
        ok_dump, _, _ = run_adb_cmd(["shell", "uiautomator", "dump", "/sdcard/sdr_inbox_dump.xml"], serial=serial, timeout=15)
        if not ok_dump:
            break
        ok_cat, xml_str, _ = run_adb_cmd(["shell", "cat", "/sdcard/sdr_inbox_dump.xml"], serial=serial, timeout=15)
        if not ok_cat or "<hierarchy" not in xml_str:
            break

        try:
            xml_clean = xml_str[xml_str.index("<hierarchy") :]
            root = ET.fromstring(xml_clean)
            for node in root.iter("node"):
                res_id = node.attrib.get("resource-id", "")
                if "row_inbox_container" in res_id:
                    row_Title = ""
                    row_Digest = ""
                    row_Unread = False
                    for child in node.iter("node"):
                        cid = child.attrib.get("resource-id", "")
                        ctext = (child.attrib.get("text") or "").strip()
                        cdesc = (child.attrib.get("content-desc") or "").strip()
                        if "row_inbox_username" in cid and ctext:
                            row_Title = ctext
                        elif "row_inbox_digest" in cid and ctext:
                            row_Digest = ctext
                        elif "unread" in cid.lower() or " não lida" in cdesc.lower() or "unread" in cdesc.lower():
                            row_Unread = True

                    if row_Title and row_Digest:
                        inbox_items.append(
                            {
                                "title": row_Title,
                                "digest": row_Digest,
                                "unread": row_Unread,
                            }
                        )
                        d_low = row_Digest.lower()
                        if not (
                            d_low.startswith("você:")
                            or d_low.startswith("you:")
                            or d_low.startswith("enviado")
                            or d_low.startswith("visto")
                            or d_low.startswith("ativo ")
                            or d_low.startswith("online")
                        ):
                            if row_Unread or ":" in row_Digest or len(row_Digest) > 3:
                                saved = record_inbound_message_in_db(
                                    row_Title, row_Title, row_Digest, source="ADB_INBOX_SCAN"
                                )
                                if saved:
                                    new_from_inbox.append(saved)
        except Exception as e:
            print(f"[Boot Catch-Up] Erro parseando XML: {e}")

        if scroll_idx < max_scrolls - 1:
            run_adb_cmd(["shell", "input", "swipe", "540", "1450", "540", "450", "450"], serial=serial)
            time.sleep(2.0)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO adb_sync_logs (sync_type, details, created_at) VALUES (?, ?, ?)",
            (
                "BOOT_CATCHUP",
                json.dumps(
                    {
                        "notif_new": len(notif_res.get("new_messages", [])),
                        "inbox_scanned": len(inbox_items),
                        "inbox_new": len(new_from_inbox),
                    },
                    ensure_ascii=False,
                ),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return {
        "ok": True,
        "notifications_result": notif_res,
        "inbox_rows_scanned": len(inbox_items),
        "inbox_items": inbox_items,
        "new_messages_synced": notif_res.get("new_messages", []) + new_from_inbox,
    }


# ==============================================================================
# MÓDULO 3B: ABRIR CONVERSA DO LEAD NO CELULAR & ENVIAR RESPOSTA DE TEXTO VIA ADB
# ==============================================================================
def _parse_bounds_center(bounds_str):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def adb_open_chat(username, serial=DEFAULT_SERIAL):
    """
    Acorda o celular, abre o perfil `@username` no Instagram e clica automaticamente
    no botão 'Mensagem' / 'Enviar mensagem', deixando a conversa aberta no celular.
    """
    clean_u = (username or "").strip().lstrip("@")
    if not clean_u:
        return {"ok": False, "error": "Username vazio"}

    target_serial = resolve_target_serial(serial)
    run_adb_cmd(["shell", "input", "keyevent", "224"], serial=target_serial)
    run_adb_cmd(["shell", "wm", "dismiss-keyguard"], serial=target_serial)
    time.sleep(0.5)

    url = f"https://www.instagram.com/{clean_u}"
    ok, _, stderr = run_adb_cmd(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            url,
            "-p",
            "com.instagram.android",
        ],
        serial=target_serial,
    )
    if not ok:
        return {"ok": False, "error": f"Falha ao abrir perfil @{clean_u}: {stderr}"}

    time.sleep(3.8)

    clicked_message_btn = False
    ok_dump, _, _ = run_adb_cmd(["shell", "uiautomator", "dump", "/sdcard/sdr_profile_dump.xml"], serial=target_serial, timeout=12)
    if ok_dump:
        ok_cat, xml_str, _ = run_adb_cmd(["shell", "cat", "/sdcard/sdr_profile_dump.xml"], serial=target_serial, timeout=12)
        if ok_cat and "<hierarchy" in xml_str:
            try:
                root = ET.fromstring(xml_str[xml_str.index("<hierarchy") :])
                for node in root.iter("node"):
                    txt = (node.attrib.get("text") or "").strip().lower()
                    desc = (node.attrib.get("content-desc") or "").strip().lower()
                    rid = (node.attrib.get("resource-id") or "").strip().lower()
                    if (
                        txt in {"mensagem", "message", "enviar mensagem"}
                        or desc in {"mensagem", "message", "enviar mensagem"}
                        or "button_message" in rid
                        or "profile_header_button_action" in rid
                    ):
                        center = _parse_bounds_center(node.attrib.get("bounds", ""))
                        if center:
                            run_adb_cmd(["shell", "input", "tap", str(center[0]), str(center[1])], serial=target_serial)
                            clicked_message_btn = True
                            break
            except Exception:
                pass

    try:
        conn = sqlite3.connect(str(DB_PATH))
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO adb_sync_logs (sync_type, details, created_at) VALUES (?, ?, ?)",
            (
                "OPEN_CHAT_ADB",
                json.dumps({"username": clean_u, "clicked_message_btn": clicked_message_btn}, ensure_ascii=False),
                now_str,
            ),
        )
        conn.execute(
            "UPDATE audio_alerts_queue SET status = 'OPENED_ON_PHONE' WHERE LOWER(username) = ? AND status = 'PENDING'",
            (clean_u.lower(),),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return {
        "ok": True,
        "username": clean_u,
        "clicked_message_btn": clicked_message_btn,
        "message": f"Chat de @{clean_u} aberto no celular ({target_serial or 'ADB'})! Só segurar o microfone e gravar o áudio.",
    }


def adb_send_dm(username, message_text, serial=DEFAULT_SERIAL, dry_run=False):
    """
    Abre o chat do lead `@username` via ADB e envia `message_text` usando o fluxo do Sniper.
    """
    clean_u = (username or "").strip().lstrip("@")
    clean_msg = (message_text or "").strip()
    if not clean_u or not clean_msg:
        return {"ok": False, "error": "Username ou mensagem vazios."}

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "username": clean_u,
            "message_sent": clean_msg,
        }

    target_serial = resolve_target_serial(serial)
    sent_ok = False
    error_detail = ""
    try:
        import uiautomator2 as u2

        d = u2.connect(target_serial) if target_serial else u2.connect()
        d.screen_on()
        url = f"https://www.instagram.com/{clean_u}"
        d.shell(f'am start -a android.intent.action.VIEW -d "{url}" -p com.instagram.android')
        time.sleep(4.0)

        btn_msg = d(textMatches="(?i)^(Mensagem|Message|Enviar mensagem)$")
        if not btn_msg.exists:
            btn_msg = d(descriptionMatches="(?i)^(Mensagem|Message|Enviar mensagem)$")

        if btn_msg.exists:
            btn_msg.click()
            time.sleep(3.5)

            campo = d(resourceId="com.instagram.android:id/row_thread_composer_edittext")
            if not campo.exists:
                campo = d(className="android.widget.EditText")

            if campo.exists:
                campo.click()
                time.sleep(0.8)
                campo.set_text(clean_msg)
                time.sleep(1.2)

                btn_send = d(resourceId="com.instagram.android:id/row_thread_composer_button_send")
                if not btn_send.exists:
                    btn_send = d(textMatches="(?i)^(Enviar|Send)$")
                if not btn_send.exists:
                    btn_send = d(descriptionMatches="(?i)^(Enviar|Send)$")

                if btn_send.exists:
                    btn_send.click()
                    time.sleep(2.0)
                    sent_ok = True
                else:
                    error_detail = "Botão Enviar não encontrado na tela do Direct."
            else:
                error_detail = "Campo de texto (EditText) não encontrado no chat."
        else:
            error_detail = f"Botão Mensagem não encontrado no perfil @{clean_u}."
    except Exception as e:
        error_detail = f"Erro no disparo via uiautomator2: {e}"

    if sent_ok:
        init_crm_db()
        now_ms = int(time.time() * 1000)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT thread_folder FROM conversations WHERE LOWER(username) = ? LIMIT 1", (clean_u.lower(),))
        row = cur.fetchone()
        folder = row["thread_folder"] if row else f"adb_{clean_u.lower()}"
        if row:
            cur.execute(
                """
                UPDATE conversations
                SET funnel_stage = CASE WHEN funnel_stage = 'RESPONDER_AGORA' THEN 'QUALIFICANDO' ELSE funnel_stage END,
                    msg_count = msg_count + 1,
                    nicolas_msg_count = nicolas_msg_count + 1,
                    last_sender = 'OPERATOR',
                    last_message_text = ?,
                    last_message_dt = ?,
                    last_timestamp_ms = ?,
                    updated_at = ?
                WHERE thread_folder = ?
                """,
                (clean_msg, now_str, now_ms, now_str, folder),
            )
        cur.execute(
            """
            INSERT INTO messages (
                thread_folder, username, sender, sender_raw, timestamp_ms, dt_str,
                content, audio_uris, audio_transcription, share_link, reactions, source
            ) VALUES (?, ?, 'OPERATOR', ?, ?, ?, ?, '[]', '', '', '[]', 'ADB_AUTO_SENT')
            """,
            (folder, clean_u.lower(), SDR_OPERATOR_NAME, now_ms, now_str, clean_msg),
        )
        cur.execute(
            "INSERT INTO adb_sync_logs (sync_type, details, created_at) VALUES (?, ?, ?)",
            ("AUTO_RESPONDER", json.dumps({"username": clean_u, "msg": clean_msg}, ensure_ascii=False), now_str),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "username": clean_u, "message_sent": clean_msg}

    return {"ok": False, "username": clean_u, "error": error_detail}


def send_dm_reply_by_inbox_search(serial=DEFAULT_SERIAL, username="", display_title="", message_text="", dry_run=False):
    """Wrapper compatível com megazord_prospeccao.py para envio de DM de resposta."""
    return adb_send_dm(username=username or display_title, message_text=message_text, serial=serial, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Motor Híbrido ADB SDR Instagram")
    parser.add_argument("--status", action="store_true", help="Verifica status do celular ADB conectado")
    parser.add_argument("--scan-notifications", action="store_true", help="Lê notificações atuais do Instagram via dumpsys")
    parser.add_argument("--boot-catchup", action="store_true", help="Executa varredura completa de Inbox (Boot Catch-Up)")
    parser.add_argument("--open-chat", type=str, help="Abre o Direct de @username na tela do celular via ADB")
    parser.add_argument("--send-dm", nargs=2, metavar=("USERNAME", "TEXT"), help="Envia DM de texto para @username via ADB")
    parser.add_argument("--watch-daemon", action="store_true", help="Roda loop contínuo de leitura de notificações a cada 5s")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(get_adb_status(), indent=2, ensure_ascii=False))
    elif args.scan_notifications:
        print(json.dumps(scan_notifications_once(), indent=2, ensure_ascii=False))
    elif args.boot_catchup:
        print(json.dumps(run_boot_catchup(), indent=2, ensure_ascii=False))
    elif args.open_chat:
        print(json.dumps(adb_open_chat(args.open_chat), indent=2, ensure_ascii=False))
    elif args.send_dm:
        print(json.dumps(adb_send_dm(args.send_dm[0], args.send_dm[1]), indent=2, ensure_ascii=False))
    elif args.watch_daemon:
        print("[Notification Watcher] Monitorando notificações de com.instagram.android a cada 5s...")
        while True:
            res = scan_notifications_once()
            if res.get("new_messages"):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Novas mensagens capturadas: {res['new_messages']}")
            time.sleep(5)
    else:
        parser.print_help()
