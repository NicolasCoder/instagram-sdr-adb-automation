#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEGAZORD PROSPECÇÃO DUAL-PHONE (OLHEIRO + SNIPER) — Instagram SDR Autônomo via ADB
==================================================================================
Arquitetura:
  - Celular 1 (OLHEIRO): Rola o feed de Reels, filtra anúncios/grades sugeridas,
    valida duração (>15s), detecta Talking Head masculino/empresa via YOLOv11 GPU + OpenCV,
    valida fala em Português via Faster-Whisper/Nemotron GPU e qualifica o perfil (seguidores, story, bio).
  - Celular 2 (SNIPER): Abre o perfil qualificado via deep-link ADB, verifica anti-duplicidade no chat,
    envia a DM personalizada e executa verificação pós-envio (Anti-Block Protection).
  - CRM SQLite & Dashboard Web (:4950): Integração automática com banco SQLite local.
"""
import os
import sys
import subprocess
from pathlib import Path

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

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import uiautomator2 as u2
import time
import random
import requests
import json
from datetime import datetime
import mss
import pygetwindow as gw
import cv2
import numpy as np
import torch
from ultralytics import YOLO
import re
import sqlite3
import argparse
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

try:
    from faster_whisper import WhisperModel
except Exception:
    WhisperModel = None

try:
    import snapinsta_api
except Exception:
    snapinsta_api = None

try:
    import insta_adb_sdr_engine as sdr_engine
except Exception:
    sdr_engine = None

# Força flush imediato nos logs em tempo real (compatível com pythonw.exe invisível)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass

# Carrega configurações do arquivo .env
load_dotenv(BASE_DIR / ".env")

PASTA_RAIZ = str(BASE_DIR)
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE_PATH = str(LOGS_DIR / "prospeccao_live.log")


def log_msg(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    linha = f"[{ts}] {msg}"
    print(linha, flush=True)
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


def _resolve_model_path(filename):
    cand_models = MODELS_DIR / filename
    if cand_models.exists():
        return str(cand_models)
    cand_root = BASE_DIR / filename
    if cand_root.exists():
        return str(cand_root)
    return str(cand_models)


log_msg("Inicializando redes neurais do OpenCV para detecção de Gênero...")
gender_net = None
GENDER_LIST = ["Male", "Female"]
MODEL_MEAN_VALUES = (78.4263377603, 87.7689143744, 114.895847746)
try:
    caminho_prototxt = _resolve_model_path("deploy_gender.prototxt")
    caminho_caffemodel = _resolve_model_path("gender_net.caffemodel")
    if os.path.exists(caminho_prototxt) and os.path.exists(caminho_caffemodel):
        gender_net = cv2.dnn.readNetFromCaffe(caminho_prototxt, caminho_caffemodel)
    else:
        log_msg("ℹ️ gender_net.caffemodel opcional não encontrado localmente (usando filtro de nomes BR + IA).")
except Exception as e:
    log_msg(f"⚠️ Aviso ao carregar modelo opcional de gênero: {e}")
    gender_net = None


# ==========================================
# 1. CONFIGURAÇÕES, CREDENCIAIS E AUTO-DETECÇÃO ADB
# ==========================================
def auto_detect_adb_serials():
    """
    Lê SERIAL_OLHEIRO e SERIAL_SNIPER do .env.
    Caso estejam em branco, detecta automaticamente os aparelhos conectados via `adb devices`.
    """
    olheiro = os.getenv("SERIAL_OLHEIRO", "").strip()
    sniper = os.getenv("SERIAL_SNIPER", "").strip()
    if olheiro and sniper:
        return olheiro, sniper

    adb_bin = sdr_engine.get_adb_exe() if sdr_engine else "adb"
    connected = []
    try:
        res = subprocess.run(
            [adb_bin, "devices"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        for ln in res.stdout.splitlines():
            parts = ln.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                connected.append(parts[0])
    except Exception:
        pass

    if not olheiro and connected:
        olheiro = connected[0]
    if not sniper and len(connected) >= 2:
        sniper = connected[1]
    elif not sniper and len(connected) == 1:
        sniper = connected[0]

    return olheiro, sniper


SERIAL_OLHEIRO, SERIAL_SNIPER = auto_detect_adb_serials()
TARGET = os.getenv("TARGET_MARKET", "BR").strip() or "BR"

ARQUIVO_PROSPECTADOS = str(DATA_DIR / "prospectados.txt")
BANCO_LOCAL_PATH = str(DATA_DIR / "banco_local.sqlite")

# Credenciais de IA (DeepSeek Opcional + Heurística Determinística Local 0ms)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

MIN_PROB_VENDA = float(os.getenv("MIN_PROB_VENDA", "0.55"))
MAX_PROB_MULHER = float(os.getenv("MAX_PROB_MULHER", "0.50"))

DEVICE_GPU = "cuda" if torch.cuda.is_available() else "cpu"
GPU_NAME = torch.cuda.get_device_name(0) if DEVICE_GPU == "cuda" else "CPU"
log_msg(f"Inicializando modelo YOLOv11 Nano em {DEVICE_GPU.upper()} ({GPU_NAME})...")
caminho_yolo = _resolve_model_path("yolo11n.pt")
if not os.path.exists(caminho_yolo):
    caminho_yolo = "yolo11n.pt"
modelo_yolo = YOLO(caminho_yolo)
try:
    if DEVICE_GPU == "cuda":
        modelo_yolo.to("cuda")
except Exception:
    pass

NEMO_SPEECH_BIN = os.getenv(
    "NEMO_SPEECH_BIN",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "NeMoSpeech", "bin", "nemo-speech.exe"),
)
if NEMO_SPEECH_BIN and os.path.exists(NEMO_SPEECH_BIN):
    log_msg(f"🎙️ Motor Oficial NVIDIA Nemotron 3.5 Transcribe GPU ativo: {NEMO_SPEECH_BIN}")
else:
    log_msg("ℹ️ Binário NeMo-Speech não encontrado, utilizando Faster-Whisper / Fallback...")

modelo_whisper_gpu = None
if (not NEMO_SPEECH_BIN or not os.path.exists(NEMO_SPEECH_BIN)) and WhisperModel is not None:
    try:
        log_msg(f"🎙️ Inicializando Fallback Faster-Whisper ({GPU_NAME})...")
        modelo_whisper_gpu = WhisperModel(
            "base",
            device=DEVICE_GPU,
            compute_type="float16" if DEVICE_GPU == "cuda" else "int8",
        )
    except Exception as e_wh:
        log_msg(f"⚠️ Aviso ao carregar Faster-Whisper ({e_wh})")

d_olheiro = None
d_sniper = None


def conectar_celulares():
    global d_olheiro, d_sniper, SERIAL_OLHEIRO, SERIAL_SNIPER
    SERIAL_OLHEIRO, SERIAL_SNIPER = auto_detect_adb_serials()
    if not SERIAL_OLHEIRO or not SERIAL_SNIPER:
        log_msg(
            "❌ Nenhum aparelho ADB detectado ou configurado! "
            "Conecte os celulares via USB com Depuração USB ativada ou preencha SERIAL_OLHEIRO e SERIAL_SNIPER no arquivo .env."
        )
        sys.exit(1)

    log_msg(f"Conectando aos celulares via ADB (Olheiro={SERIAL_OLHEIRO} | Sniper={SERIAL_SNIPER})...")
    try:
        d_olheiro = u2.connect(SERIAL_OLHEIRO)
        d_sniper = u2.connect(SERIAL_SNIPER)
        for _d in (d_olheiro, d_sniper):
            try:
                _d.jsonrpc.setConfigurator({
                    "waitForIdleTimeout": 0,
                    "actionAcknowledgmentTimeout": 100,
                    "scrollAcknowledgmentTimeout": 100,
                })
                _d.shell("svc power stayon true")
                _d.shell("settings put system screen_off_timeout 1800000")
            except Exception:
                pass
        log_msg(f"✅ Celulares conectados com sucesso! (Olheiro={SERIAL_OLHEIRO} | Sniper={SERIAL_SNIPER})")
    except Exception as e:
        log_msg(f"❌ Erro ao conectar nos celulares. Verifique o cabo USB e os seriais no .env. Erro: {e}")
        sys.exit(1)


def garantir_celular_desbloqueado(d, nome="Celular"):
    """
    Garante que a tela esteja acesa (via KEYCODE_WAKEUP 224),
    desativa Always-On Display (aod_mode=0) para não travar o UiAutomator2 e verifica se está em Bouncer/Keyguard.
    """
    try:
        res_win = d.shell("input keyevent 224; svc power stayon true; settings put system aod_mode 0; dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'")
        out_win = (res_win.output if hasattr(res_win, "output") else str(res_win)) or ""
        if "Bouncer" in out_win or "Keyguard" in out_win or "aodservice" in out_win:
            d.shell("am force-stop com.samsung.android.app.aodservice; wm dismiss-keyguard; input swipe 360 1100 360 200 220")
            time.sleep(0.35)
            res_win2 = d.shell("dumpsys window windows | grep -E 'mCurrentFocus'")
            out_win2 = (res_win2.output if hasattr(res_win2, "output") else str(res_win2)) or ""
            if "Bouncer" in out_win2:
                log_msg(f"🔒 [{nome} BLOQUEADO COM PADRÃO] Desbloqueie a tela na janela '{nome}' do scrcpy!")
                return False
        return True
    except Exception as e:
        log_msg(f"⚠️ Aviso ao verificar desbloqueio de {nome}: {e}")
        return True


# ==========================================
# 2. FUNÇÕES DE BANCO DE DADOS LOCAL
# ==========================================
def normalizar_username(arroba):
    if not arroba:
        return ""
    limpo = str(arroba).replace("\xa0", " ").strip().lstrip("@").strip().lower()
    if " " in limpo:
        limpo = limpo.split()[0].strip()
    return limpo


def inicializar_banco():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(BANCO_LOCAL_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            copy TEXT,
            data_envio TEXT,
            respondeu BOOLEAN
        )
        """
    )
    conn.commit()
    conn.close()
    if sdr_engine is not None and hasattr(sdr_engine, "init_crm_db"):
        sdr_engine.init_crm_db()


def carregar_prospectados():
    """
    Carrega e unifica todos os leads já prospectados de `data/prospectados.txt`
    e `data/banco_local.sqlite` para nunca enviar mensagem repetida.
    """
    inicializar_banco()
    vistos = set()

    if os.path.exists(ARQUIVO_PROSPECTADOS):
        try:
            with open(ARQUIVO_PROSPECTADOS, "r", encoding="utf-8", errors="ignore") as f:
                for linha in f:
                    u = normalizar_username(linha)
                    if u:
                        vistos.add(u)
        except Exception as e:
            log_msg(f"⚠️ Erro ao ler {ARQUIVO_PROSPECTADOS}: {e}")

    if os.path.exists(BANCO_LOCAL_PATH):
        try:
            conn = sqlite3.connect(BANCO_LOCAL_PATH)
            for (u_raw,) in conn.execute("SELECT username FROM leads"):
                u = normalizar_username(u_raw)
                if u:
                    vistos.add(u)
            conn.close()
        except Exception:
            pass

    lista_ordenada = sorted(vistos)
    try:
        with open(ARQUIVO_PROSPECTADOS, "w", encoding="utf-8") as f:
            for u in lista_ordenada:
                f.write(f"{u}\n")
    except Exception:
        pass

    log_msg(f"🛡️ Base de proteção carregada: {len(lista_ordenada)} perfis já prospectados bloqueados contra reenvio!")
    return lista_ordenada


def ja_foi_prospectado(arroba, prospectados=None):
    """Checa em memória e também direto no SQLite em tempo real se o @ já foi prospectado."""
    u = normalizar_username(arroba)
    if not u:
        return False
    if prospectados and u in prospectados:
        return True
    try:
        conn = sqlite3.connect(BANCO_LOCAL_PATH)
        row = conn.execute(
            "SELECT 1 FROM leads WHERE lower(replace(username, '@', '')) = ? LIMIT 1",
            (u,),
        ).fetchone()
        conn.close()
        if row:
            if prospectados is not None and u not in prospectados:
                prospectados.append(u)
            return True
    except Exception:
        pass
    return False


def salvar_prospectado(arroba):
    u = normalizar_username(arroba)
    if not u:
        return
    try:
        with open(ARQUIVO_PROSPECTADOS, "a", encoding="utf-8") as f:
            f.write(f"{u}\n")
    except Exception as e:
        log_msg(f"⚠️ Erro ao salvar em {ARQUIVO_PROSPECTADOS}: {e}")


def registrar_no_banco_local(username, copy):
    u = normalizar_username(username)
    try:
        conn = sqlite3.connect(BANCO_LOCAL_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO leads (username, copy, data_envio, respondeu)
            VALUES (?, ?, ?, ?)
            """,
            (f"@{u}", copy, datetime.now().isoformat(), False),
        )
        conn.commit()
        conn.close()
        log_msg(f"🗄️ Lead @{u} salvo no Banco Local ({BANCO_LOCAL_PATH}) com sucesso!")
    except Exception as e:
        log_msg(f"⚠️ Falha ao salvar no Banco Local: {e}")


# ==========================================
# 3. FUNÇÕES DO OLHEIRO (CAÇADOR - ARQUITETURA SINGLE-XML + GPU/YOLO + DEEPSEEK)
# ==========================================
PASTA_DEBUG_FRAMES = str(LOGS_DIR / "debug_frames")
os.makedirs(PASTA_DEBUG_FRAMES, exist_ok=True)
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

NOMES_MASCULINOS_BR = {
    "lucas", "thiago", "tiago", "geraldo", "joao", "joão", "pedro", "carlos", "rafael",
    "gabriel", "matheus", "mateus", "bruno", "rodrigo", "marcelo", "andre", "andré",
    "felipe", "guilherme", "gustavo", "daniel", "eduardo", "henrique", "leonardo", "leo",
    "ricardo", "vitor", "victor", "diego", "alex", "alexandre", "arthur", "artur",
    "fabio", "fábio", "paulo", "marcos", "marco", "leandro", "fernando", "caio", "igor",
    "renan", "vinicius", "vinícius", "diogo", "luiz", "luis", "luís", "jorge", "sergio",
    "sérgio", "cesar", "césar", "julio", "júlio", "roberto", "antonio", "antônio", "jose",
    "josé", "francisco", "danilo", "murilo", "otavio", "otávio", "samuel", "davi", "david",
    "bernardo", "heitor", "enzo", "lorenzo", "nicolas", "nícolas", "alan", "alberto",
    "aldo", "alessandro", "alfredo", "alvaro", "álvaro", "anderson", "angelo", "ângelo",
    "augusto", "breno", "cauã", "caua", "celso", "claudio", "cláudio", "cristiano",
    "denis", "dênis", "douglas", "edson", "elias", "emanuel", "emerson", "erick", "erik",
    "evandro", "everton", "fabiano", "fabricio", "fabrício", "flavio", "flávio", "fred",
    "frederico", "gilberto", "giovanni", "giovani", "glauber", "humberto", "hugo", "iago",
    "israel", "ivan", "jean", "jefferson", "jonas", "jonathan", "josue", "josué", "kaua",
    "kauã", "kleber", "laercio", "luan", "luciano", "maicon", "mauricio", "maurício",
    "mauro", "michel", "miguel", "milton", "moises", "moisés", "natan", "nathan", "nelson",
    "newton", "nilton", "norberto", "orlando", "osmar", "osvaldo", "pablo", "patrick",
    "plínio", "plinio", "ramon", "raul", "reginaldo", "reinaldo", "renato", "robinson",
    "robson", "rodolfo", "roger", "rogerio", "rogério", "romulo", "rômulo", "ronaldo",
    "rubens", "sandro", "saulo", "sidnei", "silvio", "sílvio", "tales", "thales", "tulio",
    "túlio", "ulisses", "valdir", "valter", "vanderlei", "agner", "wagner", "wallace",
    "wanderson", "washington", "wellington", "wesley", "willian", "william", "wilson", "yuri",
}

NOMES_FEMININOS_BR = {
    "ana", "maria", "julia", "júlia", "camila", "camilly", "fernanda", "patricia", "patrícia",
    "juliana", "bruna", "jessica", "jéssica", "leticia", "letícia", "amanda", "mariana",
    "carolina", "carol", "gabriela", "gabi", "larissa", "luana", "natália", "natalia",
    "beatriz", "bia", "priscila", "vanessa", "renata", "tatiana", "aline", "daniela",
    "claudia", "cláudia", "adriana", "luciana", "raquel", "débora", "debora", "isabela",
    "michele", "michelle", "ketlen", "bianca", "sabrina", "thais", "thaís", "pamela",
    "suelen", "flavia", "flávia", "viviane", "simone", "cristiane", "elaine", "monica",
    "mônica", "andressa", "jaqueline", "stephanie", "yasmin", "vitoria", "vitória",
    "eduarda", "duda", "manuela", "manu", "laura", "alice", "helena", "valentina",
    "heloisa", "heloísa", "cecilia", "cecília", "lorena", "livia", "lívia", "giovanna",
    "milena", "rayssa", "raissa", "samara", "taina", "tainá", "talita", "nathalia",
    "nayara", "maiara", "mayara", "marcela", "mirella", "nicole", "paola", "paloma",
    "poliana", "rafaela", "rebeca", "roberta", "rosana", "sara", "sarah", "silvana",
    "solange", "sonia", "sônia", "suzana", "tamires", "tania", "tânia", "tereza",
    "valeria", "valéria", "veronica", "verônica", "barbara", "bárbara", "cintia", "cíntia",
}


def autor_tem_nome_masculino(username_reels, nome_reels):
    texto = f"{nome_reels or ''} {(username_reels or '').replace('.', ' ').replace('_', ' ')}".lower()
    tokens = re.findall(r"[a-zà-öø-ÿ]+", texto)
    for t in tokens:
        if t in NOMES_MASCULINOS_BR:
            return True
    u_low = (username_reels or "").lower().lstrip("@")
    for prefix in ("dr.", "dr_", "o"):
        if u_low.startswith(prefix):
            resto = u_low[len(prefix):]
            for nome_m in NOMES_MASCULINOS_BR:
                if len(nome_m) >= 4 and resto.startswith(nome_m):
                    return True
    for nome_m in NOMES_MASCULINOS_BR:
        if len(nome_m) >= 5 and u_low.startswith(nome_m):
            return True
    return False


def autor_claramente_mulher(username_reels, nome_reels):
    if autor_tem_nome_masculino(username_reels, nome_reels):
        return False
    u_low = (username_reels or "").lower().lstrip("@")
    n_low = (nome_reels or "").lower()
    texto = f"{n_low} {u_low.replace('.', ' ').replace('_', ' ')}"
    if u_low.startswith(("dra.", "dra_")) or "dra " in f"{n_low} ":
        return True
    tokens = re.findall(r"[a-zà-öø-ÿ]+", texto)
    for t in tokens:
        if t in NOMES_FEMININOS_BR:
            return True
    for nome_f in NOMES_FEMININOS_BR:
        if len(nome_f) >= 5 and (u_low.startswith(nome_f) or u_low.startswith("dra" + nome_f)):
            return True
    return False


def classificar_genero(img_crop):
    if not gender_net or img_crop is None or img_crop.size == 0:
        return "Male", 0.0
    blob = cv2.dnn.blobFromImage(img_crop, 1.0, (227, 227), MODEL_MEAN_VALUES, swapRB=False)
    gender_net.setInput(blob)
    preds = gender_net.forward()
    gender = GENDER_LIST[preds[0].argmax()]
    conf = float(preds[0].max())
    return gender, conf


def capturar_tela_olheiro():
    """Captura a tela via uiautomator2 nativo ou janela do Scrcpy."""
    try:
        img = d_olheiro.screenshot(format="opencv")
        if img is not None and img.size > 0:
            return img
    except Exception:
        pass
    try:
        for w in gw.getAllWindows():
            if w.title and "OLHEIRO" in w.title.upper():
                if w.isMinimized:
                    w.restore()
                monitor = {"top": w.top, "left": w.left, "width": w.width, "height": w.height}
                with mss.MSS() as sct:
                    img = np.array(sct.grab(monitor))
                    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    except Exception:
        pass
    return None


def salvar_frame_inspecao(img, status_label, box=None, face_box=None):
    if os.getenv("SAVE_DEBUG_FRAMES", "0").strip() != "1":
        return
    try:
        vis = img.copy()
        if box is not None:
            x1, y1, x2, y2 = box
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 3)
        if face_box is not None:
            fx1, fy1, fx2, fy2 = face_box
            cv2.rectangle(vis, (fx1, fy1), (fx2, fy2), (255, 200, 0), 3)
        cv2.putText(vis, status_label, (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        cv2.imwrite(os.path.join(PASTA_DEBUG_FRAMES, "ultimo_frame.jpg"), vis)
    except Exception:
        pass


def _parse_bounds(bounds_str):
    m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", bounds_str or "")
    if not m:
        return None
    return tuple(map(int, m.groups()))


def inspecionar_tela_reels_xml():
    info = {
        "na_aba_reels": False,
        "aba_reels_selecionada": False,
        "eh_anuncio": False,
        "eh_grade_sugeridos": False,
        "username_reels": None,
        "author_click_xy": None,
        "nome_reels": None,
        "em_perfil": False,
        "popup_aberto": False,
        "app_instagram": False,
        "tem_scrubber": False,
        "audio_attribution": "",
        "legenda_curta": "",
        "share_xy": (990, 1229),
    }
    try:
        xml_str = d_olheiro.dump_hierarchy()
        root = ET.fromstring(xml_str)
    except Exception as e:
        log_msg(f"⚠️ Erro no dump XML do Olheiro: {e}")
        return info

    fallback_username = None
    for node in root.iter("node"):
        pkg = node.attrib.get("package", "")
        if pkg == "com.instagram.android":
            info["app_instagram"] = True

        rid = node.attrib.get("resource-id", "")
        cls = node.attrib.get("class", "")
        txt = (node.attrib.get("text") or "").replace("\xa0", " ").strip()
        desc = (node.attrib.get("content-desc") or "").replace("\xa0", " ").strip()
        sel = node.attrib.get("selected", "false") == "true"
        b = _parse_bounds(node.attrib.get("bounds", ""))
        txt_low = txt.lower()
        desc_low = desc.lower()

        if rid == "com.instagram.android:id/scrubber" or cls == "android.widget.SeekBar" or desc_low == "barra de pesquisa":
            info["tem_scrubber"] = True

        if rid == "com.instagram.android:id/direct_share_button" and b is not None:
            info["share_xy"] = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)

        if not rid and cls == "android.widget.Button" and desc and b is not None and 1540 <= b[1] <= 1635 and b[0] >= 120:
            info["audio_attribution"] = desc

        if not rid and desc and b is not None and 1615 <= b[1] <= 1715 and b[0] <= 100:
            info["legenda_curta"] = desc

        if rid == "com.instagram.android:id/clips_tab" and sel:
            info["aba_reels_selecionada"] = True

        if any(
            k in rid
            for k in (
                "context_menu_item",
                "layout_comment_thread",
                "direct_private_share",
                "bottom_sheet_container_view",
            )
        ):
            info["popup_aberto"] = True

        if any(k in rid for k in ("clips_video_container", "clips_media_component", "clips_viewer_view_pager", "root_clips_layout")):
            info["na_aba_reels"] = True

        if "row_profile_header" in rid or "profile_header_container" in rid or rid == "com.instagram.android:id/action_bar_title":
            info["em_perfil"] = True

        cy = ((b[1] + b[3]) // 2) if b is not None else -1
        visivel_reel = b is not None and 200 <= cy <= 1775
        visivel_inferior = b is not None and 600 <= b[1] <= 1775

        if rid == "com.instagram.android:id/clips_author_username" and txt and visivel_inferior:
            u = normalizar_username(txt)
            if u:
                info["username_reels"] = u
                info["author_click_xy"] = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)

        if visivel_reel:
            for prefix in ("foto do perfil de ", "profile picture of ", "reel de ", "reel by "):
                if desc_low.startswith(prefix):
                    resto = desc[len(prefix) :].split(".")[0].split()[0]
                    u = normalizar_username(resto)
                    if u:
                        if not fallback_username:
                            fallback_username = u
                        if rid == "com.instagram.android:id/clips_author_profile_pic" and visivel_inferior and not info["author_click_xy"]:
                            info["author_click_xy"] = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)

        if rid == "com.instagram.android:id/inline_follow_button" and desc and visivel_inferior:
            for prefix in ("seguir ", "follow "):
                if desc_low.startswith(prefix):
                    info["nome_reels"] = desc[len(prefix) :].strip()

        if visivel_reel and txt_low in ("patrocinado", "sponsored", "saiba mais", "learn more", "instalar agora", "comprar agora"):
            info["eh_anuncio"] = True
        if visivel_reel and ("patrocinado" in desc_low or "sponsored" in desc_low):
            info["eh_anuncio"] = True

        if any(k in txt_low for k in ("reels sugeridos", "mais reels", "suggested reels")):
            info["eh_grade_sugeridos"] = True

    if not info["username_reels"] and fallback_username:
        info["username_reels"] = fallback_username

    return info


def garantir_na_aba_reels():
    garantir_celular_desbloqueado(d_olheiro, "Olheiro")
    estado = inspecionar_tela_reels_xml()
    if (estado["na_aba_reels"] or estado["aba_reels_selecionada"]) and not estado["popup_aberto"] and not estado["em_perfil"]:
        return estado

    try:
        if not estado["app_instagram"]:
            app_atual = d_olheiro.app_current().get("package", "")
            if app_atual != "com.instagram.android":
                log_msg(f"🛡️ [INTERCEPTOR] Celular saiu do Instagram ({app_atual}). Reabrindo Instagram...")
                d_olheiro.app_start("com.instagram.android")
                time.sleep(3.0)
                estado = inspecionar_tela_reels_xml()

        if estado["popup_aberto"] or estado["em_perfil"]:
            log_msg("🛡️ [INTERCEPTOR] Voltando de perfil/modal para a tela de Reels...")
            d_olheiro.press("back")
            time.sleep(0.8)
            estado = inspecionar_tela_reels_xml()
            if (estado["na_aba_reels"] or estado["aba_reels_selecionada"]) and not estado["em_perfil"]:
                return estado

        if not estado["aba_reels_selecionada"] and not estado["na_aba_reels"]:
            aba_reels = d_olheiro(resourceId="com.instagram.android:id/clips_tab")
            if not aba_reels.exists:
                aba_reels = d_olheiro(descriptionMatches="(?i)Reels")
            if aba_reels.exists:
                log_msg("🛡️ [INTERCEPTOR] Clicando na aba Reels...")
                aba_reels.click()
                time.sleep(1.8)
                estado = inspecionar_tela_reels_xml()
    except Exception as e:
        log_msg(f"⚠️ [INTERCEPTOR] Aviso na verificação de tela: {e}")

    return estado


def rolar_proximo_reel(ultimo_username=None):
    try:
        d_olheiro.swipe(540, 1420, 540, 240, 0.10)
    except Exception:
        d_olheiro.shell("input swipe 540 1420 540 240 140")
    time.sleep(0.48)


def triagem_feed_yolo(estado_reels=None):
    try:
        if estado_reels is None:
            estado_reels = garantir_na_aba_reels()

        if estado_reels.get("eh_anuncio"):
            log_msg("[OLHEIRO] 📢 Anúncio/Patrocinado detectado! Pulando...")
            return False, True, estado_reels

        if estado_reels.get("eh_grade_sugeridos"):
            log_msg("[OLHEIRO] 🛑 Grade de Reels sugeridos detectada! Pulando...")
            return False, True, estado_reels

        img = capturar_tela_olheiro()
        if img is None:
            log_msg("⚠️ Não foi possível capturar a tela do Olheiro.")
            return False, False, estado_reels

        h_img, w_img = img.shape[:2]
        area_tela = float(h_img * w_img)

        resultados = modelo_yolo(img, verbose=False)

        pessoas_validas = []
        for r in resultados:
            boxes = r.boxes.xyxy.cpu().numpy()
            clss = r.boxes.cls.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            for box, cls, conf_yolo in zip(boxes, clss, confs):
                if int(cls) == 0 and float(conf_yolo) >= 0.42:
                    x1, y1, x2, y2 = map(int, box)
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w_img, x2), min(h_img, y2)
                    w_box, h_box = (x2 - x1), (y2 - y1)
                    area_ratio = (w_box * h_box) / area_tela
                    if area_ratio >= 0.10 and h_box >= int(h_img * 0.20):
                        pessoas_validas.append((area_ratio, (x1, y1, x2, y2), float(conf_yolo)))

        if not pessoas_validas:
            salvar_frame_inspecao(img, "SEM TALKING HEAD")
            return False, False, estado_reels

        if len(pessoas_validas) >= 3:
            log_msg(f"[OLHEIRO] 👥 Multidão/Cena com {len(pessoas_validas)} pessoas detectada. Pulando!")
            salvar_frame_inspecao(img, f"MULTIDAO ({len(pessoas_validas)} pessoas)")
            return False, True, estado_reels

        pessoas_validas.sort(key=lambda item: item[0], reverse=True)
        area_ratio, (x1, y1, x2, y2), conf_yolo = pessoas_validas[0]
        crop_pessoa = img[y1:y2, x1:x2]

        h_p, w_p = crop_pessoa.shape[:2]
        topo_pessoa = crop_pessoa[0 : max(1, int(h_p * 0.60)), :]
        crop_genero = topo_pessoa
        face_box_global = None

        try:
            gray_topo = cv2.cvtColor(topo_pessoa, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray_topo, scaleFactor=1.1, minNeighbors=4, minSize=(45, 45))
            if len(faces) > 0:
                fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                pad_x, pad_y = int(fw * 0.25), int(fh * 0.25)
                fx1 = max(0, fx - pad_x)
                fy1 = max(0, fy - pad_y)
                fx2 = min(w_p, fx + fw + pad_x)
                fy2 = min(topo_pessoa.shape[0], fy + fh + pad_y)
                crop_genero = topo_pessoa[fy1:fy2, fx1:fx2]
                face_box_global = (x1 + fx1, y1 + fy1, x1 + fx2, y1 + fy2)
        except Exception:
            pass

        u_reels = estado_reels.get("username_reels")
        n_reels = estado_reels.get("nome_reels")
        if face_box_global is not None and crop_genero.size > 0 and not autor_tem_nome_masculino(u_reels, n_reels):
            gender, conf = classificar_genero(crop_genero)
            if gender == "Female" and conf >= 0.78:
                log_msg(f"[OLHEIRO] 🚺 Mulher detectada no rosto pelo OpenCV (Confiança: {conf:.2f} | Área: {area_ratio:.0%}). Pulando!")
                salvar_frame_inspecao(img, f"SKIP FEMALE ({conf:.2f})", (x1, y1, x2, y2), face_box_global)
                return False, True, estado_reels

        salvar_frame_inspecao(img, f"TALKING HEAD OK (area={area_ratio:.0%})", (x1, y1, x2, y2), face_box_global)
        return True, True, estado_reels
    except Exception as e:
        log_msg(f"Erro na captura via YOLO: {e}")
        return False, False, estado_reels


def extrair_numero_seguidores(texto):
    if not texto:
        return 0
    texto = texto.replace("\xa0", " ").lower().strip()
    match = re.search(r"([\d\,\.]+)\s*(mil|mi|m|k)?\b", texto)
    if not match:
        return 0

    num_str = match.group(1)
    sufixo = match.group(2)

    try:
        if "," in num_str:
            num = float(num_str.replace(".", "").replace(",", "."))
        elif "." in num_str:
            if sufixo in ["mi", "m", "mil", "k"]:
                num = float(num_str)
            else:
                num = float(num_str.replace(".", ""))
        else:
            num = float(num_str)
    except Exception:
        return 0

    if sufixo in ["mi", "m"]:
        return int(num * 1000000)
    elif sufixo in ["mil", "k"]:
        return int(num * 1000)
    return int(num)


def extrair_primeiro_nome_heuristica(nome_exibicao, textos_perfil, username):
    ignorados = {
        "seguidores", "followers", "seguindo", "following", "publicações", "posts",
        "mensagem", "message", "seguir", "follow", "reels", "instagram", "voltar",
        "profissional", "painel", "editar", "compartilhar", "contato", "email",
        "dr", "dra", "prof", "ceo", "coach", "mentor", "oficial", "real", "canal",
        "podcast", "grupo", "agencia", "agência", "studio", "estúdio", "clinica", "clínica",
    }
    candidatos = []
    if nome_exibicao:
        candidatos.append(nome_exibicao)
    if textos_perfil:
        candidatos.extend([p.strip() for p in textos_perfil.split("|") if p.strip()])

    for parte in candidatos:
        if parte.lower() == (username or "").lower():
            continue
        palavras = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", parte)
        for p in palavras:
            if len(p) >= 3 and p.lower() not in ignorados:
                return p.capitalize()
    return (username or "Amigo").capitalize()


def avaliar_bio_heuristica(bio_text, nome_exibicao="", username=""):
    txt = f"{username} | {nome_exibicao} | {bio_text}".lower()

    palavras_femininas = [
        "mãe ", "mae ", "esposa", "empreendedora", "mentora", "fundadora", "advogada",
        "médica", "medica", "psicóloga", "psicologa", "nutricionista", "especialista em mulheres",
        "ajudo mulheres", "para mulheres", "ela/dela", "she/her", "consultora", "criadora",
    ]
    primeiro_token = ""
    tokens_nome = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", (nome_exibicao or "").lower())
    for t in tokens_nome:
        if t not in ("dr", "dra", "prof", "ceo"):
            primeiro_token = t
            break

    eh_mulher = (
        "dra." in txt
        or "dra " in txt
        or primeiro_token in NOMES_FEMININOS_BR
        or any(pf in txt for pf in palavras_femininas)
    )

    indicadores_venda = [
        "ceo", "fundador", "founder", "mentor", "mentoria", "consultoria", "consultor",
        "curso", "treinamento", "método", "metodo", "especialista", "ajudo", "ensino",
        "escala", "faturamento", "vendas", "marketing", "tráfego", "trafego", "gestão",
        "gestao", "empresa", "empresário", "empresario", "empreendedor", "negócios",
        "negocios", "advogado", "advocacia", "médico", "medico", "dr.", "dr ", "clínica", "clinica",
        "contador", "contabilidade", "investimento", "finanças", "financas", "imóveis",
        "imoveis", "corretor", "engenharia", "arquiteto", "arquitetura", "agência", "agencia", "link",
        "whatsapp", "wa.me", "linktr.ee", ".com", ".app", ".br", "contato", "clientes",
        "aluno", "alunos", "palestrante", "autor", "diretor", "sócio", "socio", "psicólogo",
        "psicologo", "treinador", "personal", "nutri", "dentista", "odonto", "consórcio",
        "consorcio", "veículos", "veiculos", "seminovos", "seguros", "assessoria", "escola",
        "instituto", "oficial", "profissional", "atendimento", "orçamento", "orcamento",
    ]
    tem_venda = any(ind in txt for ind in indicadores_venda)
    return tem_venda, (not eh_mulher)


def analisar_perfil_ia_unificada(bio_text, nome_exibicao, username):
    nome_heuristico = extrair_primeiro_nome_heuristica(nome_exibicao, bio_text, username)
    tem_venda_h, eh_homem_h = avaliar_bio_heuristica(bio_text, nome_exibicao, username)

    if eh_homem_h and tem_venda_h and autor_tem_nome_masculino(username, nome_exibicao):
        log_msg(f"⚡ [Fast-Path Local 0ms] tem_venda=True | eh_homem=True | nome='{nome_heuristico}'")
        return True, True, nome_heuristico

    if not eh_homem_h and not autor_tem_nome_masculino(username, nome_exibicao):
        log_msg(f"⚡ [Fast-Path Local 0ms] tem_venda={tem_venda_h} | eh_homem=False | nome='{nome_heuristico}'")
        return tem_venda_h, False, nome_heuristico

    if DEEPSEEK_API_KEY:
        prompt = f"""Analise os dados deste perfil do Instagram:
Username: @{username}
Nome de exibição: {nome_exibicao}
Textos da Bio/Perfil: {bio_text[:1000]}

Responda APENAS com um JSON válido contendo exatamente estas 3 chaves:
{{
  "tem_venda": true ou false (true APENAS se o perfil for do Brasil / em Português e for profissional, especialista, empresário, criador de conteúdo educativo/autoridade, prestador de serviço, infoprodutor, médico, advogado, consultor, mentor, empresa ou tiver link/oferta/posicionamento profissional; false se a bio estiver em inglês/espanhol/outro idioma estrangeiro, ou se for conta pessoal sem conteúdo profissional, meme ou fã-clube),
  "eh_homem": true ou false (true se for um homem ou empresa/marca liderada por homem; false se pertencer a uma mulher),
  "primeiro_nome": "Apenas o primeiro nome próprio da pessoa com inicial maiúscula (ex: Giovanni). Se for marca/empresa sem nome humano claro, use uma saudação adequada ou o nome curto"
}}"""
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "Você é um classificador comercial preciso. Responda exclusivamente em JSON puro."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 100,
                    "response_format": {"type": "json_object"},
                },
                timeout=3.5,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            dados = json.loads(content)
            tem_venda = bool(dados.get("tem_venda", False))
            eh_homem = bool(dados.get("eh_homem", True))
            p_nome = str(dados.get("primeiro_nome") or "").strip()
            palavras = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", p_nome)
            if palavras and len(palavras[0]) >= 2:
                primeiro_nome = palavras[0].capitalize()
            else:
                primeiro_nome = nome_heuristico
            log_msg(f"🧠 [DeepSeek AI] tem_venda={tem_venda} | eh_homem={eh_homem} | nome='{primeiro_nome}'")
            return tem_venda, eh_homem, primeiro_nome
        except Exception as e:
            log_msg(f"⚠️ Aviso DeepSeek ({e}), acionando fallback instantâneo...")

    log_msg(f"🧠 [Heurística Local] tem_venda={tem_venda_h} | eh_homem={eh_homem_h} | nome='{nome_heuristico}'")
    return tem_venda_h, eh_homem_h, nome_heuristico


def gerar_copy_padrao(primeiro_nome):
    custom_tpl = os.getenv("SDR_DEFAULT_OUTREACH_COPY", "").strip()
    if custom_tpl:
        return custom_tpl.replace("\\n", "\n").replace("{primeiro_nome}", primeiro_nome)
    return f"Oi {primeiro_nome} tudo bem?\nOlha, quantos videos seu editor de videos esta entregando atualmente?"


def olheiro_qualificar_perfil(prospectados=None, username_reels=None, nome_reels=None, author_click_xy=None):
    t0_qual = time.time()
    entrou = False

    if username_reels:
        try:
            d_olheiro.shell(f'am start -a android.intent.action.VIEW -d "http://instagram.com/_u/{username_reels}" -p com.instagram.android')
            entrou = True
        except Exception:
            pass

    if not entrou and author_click_xy:
        try:
            d_olheiro.click(author_click_xy[0], author_click_xy[1])
            entrou = True
        except Exception:
            pass

    if not entrou:
        info = d_olheiro.info
        d_olheiro.click(int(info["displayWidth"] * 0.25), int(info["displayHeight"] * 0.80))

    xml_str = ""
    root = None
    time.sleep(0.95)
    for tentativa in range(5):
        try:
            xml_str = d_olheiro.dump_hierarchy()
            if "row_profile_header" in xml_str or "profile_header_familiar_followers_value" in xml_str or "action_bar_title" in xml_str:
                root = ET.fromstring(xml_str)
                break
        except Exception:
            pass
        if tentativa == 1 and username_reels and "clips_video_container" in xml_str:
            d_olheiro.shell(f'am start -a android.intent.action.VIEW -d "http://instagram.com/_u/{username_reels}" -p com.instagram.android')
            time.sleep(0.9)
        else:
            time.sleep(0.35)

    if root is None:
        try:
            if not xml_str:
                xml_str = d_olheiro.dump_hierarchy()
            root = ET.fromstring(xml_str)
        except Exception as e:
            log_msg(f"⚠️ Erro ao ler XML do perfil: {e}")
            return False, "", None, None

    username = None
    nome_exibicao = nome_reels or ""
    seguidores = 0
    tem_story = False
    conta_privada = False
    tem_botao_msg = False
    textos_da_tela = []

    for node in root.iter("node"):
        rid = node.attrib.get("resource-id", "")
        txt = (node.attrib.get("text") or "").replace("\xa0", " ").strip()
        desc = (node.attrib.get("content-desc") or "").replace("\xa0", " ").strip()
        click = node.attrib.get("clickable", "false") == "true"
        txt_low = txt.lower()
        desc_low = desc.lower()

        if rid == "com.instagram.android:id/action_bar_title" and txt:
            username = normalizar_username(txt)

        if rid == "com.instagram.android:id/profile_header_full_name_above_vanity" and txt:
            nome_exibicao = txt

        if rid == "com.instagram.android:id/profile_header_familiar_followers_value" and txt:
            seguidores = extrair_numero_seguidores(txt)
        elif rid == "com.instagram.android:id/profile_header_followers_stacked_familiar" and desc and seguidores == 0:
            seguidores = extrair_numero_seguidores(desc)

        if rid == "com.instagram.android:id/reel_ring":
            tem_story = True
        elif rid == "com.instagram.android:id/row_profile_header_imageview" and "story" in desc_low:
            tem_story = True
        elif rid == "com.instagram.android:id/row_profile_header_imageview_frame_layout" and click:
            tem_story = True
        elif "story" in desc_low or "story" in rid.lower():
            tem_story = True

        if txt_low in ("mensagem", "message", "enviar mensagem") or desc_low in ("mensagem", "message", "enviar mensagem"):
            tem_botao_msg = True

        if "esta conta é privada" in txt_low or "this account is private" in txt_low:
            conta_privada = True

        if txt and "com.android.systemui" not in rid:
            textos_da_tela.append(txt)
        elif desc and "com.android.systemui" not in rid and any(k in desc_low for k in ("seguidores", "followers")):
            textos_da_tela.append(desc)

    if not username and username_reels:
        username = normalizar_username(username_reels)

    texto_bruto = " | ".join(textos_da_tela)

    if seguidores == 0:
        for i, t in enumerate(textos_da_tela):
            if "seguidores" in t.lower() or "followers" in t.lower():
                if any(c.isdigit() for c in t):
                    seguidores = extrair_numero_seguidores(t)
                elif i > 0:
                    seguidores = extrair_numero_seguidores(textos_da_tela[i - 1])
                if seguidores > 0:
                    break

    if "seguidores" not in texto_bruto.lower() and "followers" not in texto_bruto.lower() and "row_profile_header" not in xml_str:
        log_msg("❌ [ERRO] Tela de perfil não carregou (ou clique em anúncio). Voltando...")
        return False, "", None, None

    if username and ja_foi_prospectado(username, prospectados):
        log_msg(f"⚠️ Alvo @{username} já consta na lista de prospectados/avaliados. Pulando!")
        return False, texto_bruto, username, None

    if conta_privada:
        log_msg(f"❌ Perfil @{username} é Conta Privada. Pulando...")
        return False, texto_bruto, username, None

    tem_venda = False
    eh_homem = False
    primeiro_nome = extrair_primeiro_nome_heuristica(nome_exibicao, texto_bruto, username)

    if 0 < seguidores < 1000000 and tem_story:
        tem_venda, eh_homem, primeiro_nome = analisar_perfil_ia_unificada(texto_bruto, nome_exibicao, username)

    dt_qual = time.time() - t0_qual
    log_msg(
        f"📋 [QUALIFICAÇÃO @{username} em {dt_qual:.1f}s] "
        f"Seguidores={seguidores} ({'OK' if 0 < seguidores < 1000000 else 'X'}) | "
        f"Story={'OK' if tem_story else 'X'} | "
        f"Homem/Empresa={'OK' if eh_homem else 'X'} | "
        f"Comercial={'OK' if tem_venda else 'X'} | "
        f"BtnMsg={'OK' if tem_botao_msg else '?'} | "
        f"Nome='{primeiro_nome}'"
    )

    aprovado = (0 < seguidores < 1000000) and tem_story and tem_venda and eh_homem
    copy_gerada = gerar_copy_padrao(primeiro_nome) if aprovado else None
    return aprovado, texto_bruto, username, copy_gerada


def reiniciar_instagram_reels():
    log_msg("🔄 [RECUPERAÇÃO] Reiniciando Instagram no Olheiro para limpar estado...")
    d_olheiro.app_stop("com.instagram.android")
    time.sleep(1.5)
    d_olheiro.app_start("com.instagram.android")
    time.sleep(5.0)

    aba_reels = d_olheiro(resourceId="com.instagram.android:id/clips_tab")
    if not aba_reels.exists:
        aba_reels = d_olheiro(descriptionMatches="(?i)Reels")
    if aba_reels.exists:
        aba_reels.click()
        log_msg("✅ Instagram reiniciado e posicionado na aba Reels!")
    else:
        info = d_olheiro.info
        d_olheiro.click(int(info["displayWidth"] * 0.30), int(info["displayHeight"] * 0.93))
    time.sleep(2.5)


# ==========================================
# 4. FUNÇÕES DO SNIPER (ATIRADOR TURBO + ANTI-BLOCK)
# ==========================================
def _procurar_botao_msg_xml(xml_str):
    msg_xy = None
    opcoes_xy = None
    carregou_perfil = False
    try:
        root = ET.fromstring(xml_str)
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            txt = (node.attrib.get("text") or "").replace("\xa0", " ").strip().lower()
            desc = (node.attrib.get("content-desc") or "").replace("\xa0", " ").strip().lower()
            b = _parse_bounds(node.attrib.get("bounds", ""))
            if any(k in rid for k in ("row_profile_header", "profile_header_actions_top_row", "action_bar_title")):
                carregou_perfil = True
            if b is None:
                continue
            if txt in ("mensagem", "message", "enviar mensagem", "send message") or desc in ("mensagem", "message", "enviar mensagem", "send message"):
                msg_xy = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            elif desc in ("opções", "options", "mais opções") or "right_action_bar_buttons" in rid:
                opcoes_xy = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    except Exception:
        pass
    return carregou_perfil, msg_xy, opcoes_xy


FALHAS_ENTREGA_CONSECUTIVAS_SNIPER = 0


def detectar_bloqueio_ou_falha_entrega_dm(xml_pos_envio, copy_enviada):
    """
    Analisa o XML do chat do Sniper ~1.5s após clicar em Enviar para confirmar se a DM realmente chegou
    ou se o Instagram bloqueou/falhou o envio.
    Retorna: (entregue_ok: bool, bloqueio_critico: bool, motivo: str)
    """
    if not xml_pos_envio:
        return True, False, "XML vazio (fallback)"

    xml_low = xml_pos_envio.lower()

    frases_bloqueio_popup = (
        "tente novamente mais tarde",
        "try again later",
        "restringimos determinadas atividades",
        "we restrict certain activity",
        "ação bloqueada",
        "acao bloqueada",
        "action blocked",
        "proteger nossa comunidade",
        "protect our community",
        "você atingiu o limite",
        "voce atingiu o limite",
        "não é possível enviar mais convites",
        "nao e possivel enviar mais convites",
        "você não pode enviar mensagens",
        "voce nao pode enviar mensagens",
        "you can't send messages",
    )
    for frase in frases_bloqueio_popup:
        if frase in xml_low:
            return False, True, f"Popup de bloqueio do Instagram detectado ('{frase}')"

    rids_erro_dm = (
        "direct_message_failed",
        "message_status_error",
        "error_icon",
        "failed_to_send",
        "row_thread_item_failed_icon",
        "direct_thread_composer_unavailable",
    )
    for rid_err in rids_erro_dm:
        if rid_err in xml_low:
            return False, True, f"Ícone/status de falha de envio na DM ('{rid_err}')"

    frases_falha_balao = (
        "falha ao enviar",
        "não foi possível enviar",
        "nao foi possivel enviar",
        "failed to send",
        "couldn't send",
        "toque para tentar novamente",
        "tap to retry",
        "convite não enviado",
        "convite nao enviado",
        "mensagem não enviada",
    )
    for frase_f in frases_falha_balao:
        if frase_f in xml_low:
            return False, True, f"Mensagem não entregue no chat ('{frase_f}')"

    balao_na_thread = False
    texto_preso_no_composer = False
    trecho_busca = "editor de videos" if "editor de videos" in copy_enviada.lower() else copy_enviada[:20].strip().lower()
    try:
        root = ET.fromstring(xml_pos_envio)
        for node in root.iter("node"):
            rid = (node.attrib.get("resource-id") or "").lower()
            cls = (node.attrib.get("class") or "").lower()
            txt = (node.attrib.get("text") or "").strip().lower()
            desc = (node.attrib.get("content-desc") or "").strip().lower()
            eh_composer = "row_thread_composer_edittext" in rid or "edittext" in cls
            if eh_composer:
                if trecho_busca and trecho_busca in txt:
                    texto_preso_no_composer = True
            else:
                if trecho_busca and (trecho_busca in txt or trecho_busca in desc):
                    balao_na_thread = True
    except Exception:
        balao_na_thread = trecho_busca in xml_low

    if texto_preso_no_composer and not balao_na_thread:
        return False, False, "Texto permaneceu preso na caixa de digitação (não enviado)"

    if not balao_na_thread:
        return False, False, "Balão da mensagem enviada não apareceu na thread"

    return True, False, "Balão confirmado na thread sem erros e caixa de texto limpa"


def sniper_enviar_dm(arroba, copy):
    global FALHAS_ENTREGA_CONSECUTIVAS_SNIPER
    t0_sniper = time.time()
    arroba = normalizar_username(arroba)
    log_msg(f"🎯 [SNIPER] Mirando no alvo: @{arroba}")
    desbloqueado = garantir_celular_desbloqueado(d_sniper, "Sniper")
    if not desbloqueado:
        for _ in range(3):
            time.sleep(1.0)
            if garantir_celular_desbloqueado(d_sniper, "Sniper"):
                desbloqueado = True
                break
    if not desbloqueado:
        log_msg(
            f"🔒 [SNIPER BLOQUEADO COM PADRÃO] Salvando @{arroba} na fila do CRM (:4950) para disparo assim que desbloquear, sem parar o Olheiro!"
        )
        try:
            _salvar_card_fila_audio_crm(
                username=arroba,
                nome_lead=f"@{arroba} (Qualificado Olheiro)",
                ultima_msg_cliente="[NOVO LEAD QUALIFICADO PELO OLHEIRO — AGUARDANDO DISPARO SNIPER]",
                motivo="Sniper aguardando desbloqueio de padrão na tela — clique para enviar copy pronta!",
                roteiro_audio=copy,
                texto_alternativo=copy,
            )
        except Exception:
            pass
        return False

    link_perfil = f"http://instagram.com/_u/{arroba}"
    d_sniper.shell(f'am start -a android.intent.action.VIEW -d "{link_perfil}" -p com.instagram.android')

    msg_xy = None
    opcoes_xy = None
    carregou_perfil = False
    time.sleep(0.9)
    for _ in range(8):
        try:
            xml_s = d_sniper.dump_hierarchy()
            if "keyguard" in xml_s.lower():
                garantir_celular_desbloqueado(d_sniper, "Sniper")
                d_sniper.shell(f'am start -a android.intent.action.VIEW -d "{link_perfil}" -p com.instagram.android')
                time.sleep(0.9)
                continue
            carregou_perfil, msg_xy, opcoes_xy = _procurar_botao_msg_xml(xml_s)
            if msg_xy is not None:
                break
        except Exception:
            pass
        time.sleep(0.35)

    if msg_xy is None and carregou_perfil and opcoes_xy is not None:
        try:
            d_sniper.click(opcoes_xy[0], opcoes_xy[1])
            time.sleep(0.75)
            xml_menu = d_sniper.dump_hierarchy()
            _, msg_xy, _ = _procurar_botao_msg_xml(xml_menu)
            if msg_xy is None:
                d_sniper.press("back")
        except Exception:
            pass

    if msg_xy is None:
        log_msg(f"❌ [SNIPER] Botão 'Mensagem' não disponível no perfil @{arroba} ({time.time()-t0_sniper:.1f}s).")
        return False

    d_sniper.click(msg_xy[0], msg_xy[1])
    abriu_chat = False
    ja_enviado = False
    time.sleep(0.65)
    for _ in range(8):
        try:
            xml_chat = d_sniper.dump_hierarchy()
            xml_low = xml_chat.lower()
            if "editor de videos" in xml_low or "entregando atualmente" in xml_low:
                ja_enviado = True
                abriu_chat = True
                break
            if "row_thread_composer_edittext" in xml_chat or "android.widget.edittext" in xml_low:
                abriu_chat = True
                break
        except Exception:
            pass
        time.sleep(0.35)

    if ja_enviado:
        log_msg(f"🛑 [ANTI-DUPLICIDADE] Chat com @{arroba} já possui mensagem enviada anteriormente! Abortando envio duplicado.")
        d_sniper.press("back")
        return "JA_ENVIADO"

    if not abriu_chat:
        log_msg(f"❌ [SNIPER] Caixa de texto não encontrada no Direct de @{arroba}.")
        d_sniper.press("back")
        return False

    caixa_texto = d_sniper(resourceId="com.instagram.android:id/row_thread_composer_edittext")
    if not caixa_texto.exists:
        caixa_texto = d_sniper(className="android.widget.EditText")

    if not caixa_texto.exists:
        log_msg(f"❌ [SNIPER] Caixa de texto não localizada ao digitar para @{arroba}.")
        d_sniper.press("back")
        return False

    caixa_texto.set_text(copy)
    time.sleep(0.35)

    enviado = False
    for seletor_send in (
        d_sniper(resourceId="com.instagram.android:id/row_thread_composer_send_button_background"),
        d_sniper(resourceId="com.instagram.android:id/row_thread_composer_button_send"),
        d_sniper(descriptionMatches="(?i)^(Enviar|Send)$"),
        d_sniper(textMatches="(?i)^(Enviar|Send)$"),
    ):
        if seletor_send.exists:
            seletor_send.click()
            enviado = True
            break

    if not enviado:
        d_sniper.press("enter")
        enviado = True

    time.sleep(1.5)
    xml_pos = ""
    try:
        xml_pos = d_sniper.dump_hierarchy()
    except Exception:
        pass

    entregue_ok, bloqueio_critico, motivo_entrega = detectar_bloqueio_ou_falha_entrega_dm(xml_pos, copy)

    if not entregue_ok and not bloqueio_critico and "preso na caixa" in motivo_entrega:
        for seletor_send in (
            d_sniper(resourceId="com.instagram.android:id/row_thread_composer_button_send"),
            d_sniper(descriptionMatches="(?i)^(Enviar|Send)$"),
            d_sniper(textMatches="(?i)^(Enviar|Send)$"),
        ):
            if seletor_send.exists:
                seletor_send.click()
                break
        time.sleep(1.2)
        try:
            xml_pos = d_sniper.dump_hierarchy()
        except Exception:
            pass
        entregue_ok, bloqueio_critico, motivo_entrega = detectar_bloqueio_ou_falha_entrega_dm(xml_pos, copy)

    if not entregue_ok:
        FALHAS_ENTREGA_CONSECUTIVAS_SNIPER += 1
        if bloqueio_critico or FALHAS_ENTREGA_CONSECUTIVAS_SNIPER >= 2:
            log_msg(
                f"🛑 [ANTI-BLOCK SNIPER] O Instagram bloqueou o envio de mensagens (DM não entregue para @{arroba} — {motivo_entrega})! "
                f"Encerrando a prospecção IMEDIATAMENTE para proteger a conta!"
            )
            try:
                d_sniper.press("back")
            except Exception:
                pass
            return "BLOQUEIO_INSTAGRAM"
        else:
            log_msg(
                f"⚠️ [SNIPER] Mensagem para @{arroba} não confirmada ({motivo_entrega} | falha {FALHAS_ENTREGA_CONSECUTIVAS_SNIPER}/2). "
                f"Lead NÃO salvo no banco para não queimar!"
            )
            d_sniper.press("back")
            return False

    FALHAS_ENTREGA_CONSECUTIVAS_SNIPER = 0
    dt_sniper = time.time() - t0_sniper
    log_msg(f"✅ [SNIPER] HEADSHOT VERIFICADO em {dt_sniper:.1f}s! Mensagem entregue e confirmada no chat de @{arroba} ({motivo_entrega})!")
    d_sniper.press("back")
    return True


# ==========================================
# 4B. EXORCISTA ANTI-BRAINROT (GPU WHISPER PT-BR) + INBOX AUTO-RESPONDER / FILA ÁUDIO CRM (:4950)
# ==========================================
STOPWORDS_PT_BR = {
    "que", "não", "nao", "para", "pra", "com", "você", "voce", "uma", "mais", "como",
    "por", "seu", "sua", "isso", "mas", "está", "esta", "tem", "fazer", "quando", "muito",
    "nos", "das", "dos", "pelo", "pela", "ser", "são", "sao", "aqui", "então", "entao",
    "porque", "sobre", "quem", "tudo", "essa", "esse", "meu", "minha", "gente", "hoje",
    "vida", "empresa", "negócio", "negocio", "cliente", "clientes", "dinheiro", "vendas",
}

ULTIMO_URL_REEL_LIDO = ""
LEADS_INBOX_PROCESSADOS_SESSAO = set()
FILA_AUDIOS_JSON_PATH = str(DATA_DIR / "fila_audios_pendentes.json")


def _ler_url_clipboard_olheiro():
    try:
        adb_bin = sdr_engine.get_adb_exe() if sdr_engine else "adb"
        r = subprocess.run(
            [adb_bin, "-s", SERIAL_OLHEIRO, "shell", "service call clipboard 2 s16 com.android.shell"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2.5,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        chars = []
        for line in r.stdout.splitlines():
            line_clean = re.sub(r"^\s*0x[0-9a-fA-F]+:\s*", "", line)
            for word in re.findall(r"\b([0-9a-fA-F]{8})\b", line_clean):
                lo = int(word[4:8], 16)
                hi = int(word[0:4], 16)
                if 32 <= lo <= 126:
                    chars.append(chr(lo))
                if 32 <= hi <= 126:
                    chars.append(chr(hi))
        s = "".join(chars)
        m_url = re.search(r"https://www\.instagram\.com/(?:reel|reels|p)/[A-Za-z0-9_-]+", s)
        return m_url.group(0) + "/" if m_url else ""
    except Exception:
        return ""


def transcrever_e_validar_fala_reel_gpu(estado_reels):
    global ULTIMO_URL_REEL_LIDO
    if ((not NEMO_SPEECH_BIN or not os.path.exists(NEMO_SPEECH_BIN)) and modelo_whisper_gpu is None) or snapinsta_api is None:
        return True, 30.0, "", "Fallback (Transcrição local/Snapinsta indisponível)"

    t0_tr = time.time()
    share_x, share_y = estado_reels.get("share_xy") or (990, 1229)
    clip_antes = _ler_url_clipboard_olheiro()
    reel_url = ""
    try:
        d_olheiro.click(share_x, share_y)
        time.sleep(0.62)
        d_olheiro.click(1012, 1683)
        for _ in range(5):
            time.sleep(0.16)
            cand = _ler_url_clipboard_olheiro()
            if cand and cand != clip_antes:
                reel_url = cand
                break
        if not reel_url:
            xml_chk = d_olheiro.dump_hierarchy()
            if "direct_private_share" in xml_chk or "Copiar link" in xml_chk:
                clicou_copiar = False
                for node in ET.fromstring(xml_chk).iter("node"):
                    if (node.attrib.get("content-desc") or "") == "Copiar link":
                        b = _parse_bounds(node.attrib.get("bounds", ""))
                        if b:
                            d_olheiro.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
                            clicou_copiar = True
                            break
                if clicou_copiar:
                    for _ in range(5):
                        time.sleep(0.16)
                        cand = _ler_url_clipboard_olheiro()
                        if cand and cand != clip_antes:
                            reel_url = cand
                            break
                else:
                    d_olheiro.press("back")
                    time.sleep(0.25)
        if not reel_url:
            reel_url = _ler_url_clipboard_olheiro()
    except Exception as e:
        log_msg(f"⚠️ [TRANSCRITOR GPU] Erro ao copiar link do Reel: {e}")
        return True, 20.0, "", "Erro UI copiar link"

    if not reel_url:
        return True, 20.0, "", "URL não capturada no clipboard"

    ULTIMO_URL_REEL_LIDO = reel_url

    try:
        res_snap = snapinsta_api.call_snapinsta_api(reel_url, use_playwright_fallback=False)
        medias = res_snap.get("media") if isinstance(res_snap, dict) else None
        if not medias or not isinstance(medias, list) or not medias[0].get("url"):
            return True, 20.0, "", "Stream CDN não retornado"
        mp4_url = medias[0]["url"]
    except Exception as e:
        return True, 20.0, "", f"Snapinsta timeout ({e})"

    wav_path = str(LOGS_DIR / "temp_reel_audio.wav")
    dur_sec = 0.0
    no_win_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        r_ff = subprocess.run(
            [
                "ffmpeg", "-y", "-t", "9", "-i", mp4_url,
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", wav_path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=no_win_flag,
            timeout=6.0,
        )
        m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r_ff.stderr)
        if m_dur:
            dur_sec = int(m_dur.group(1)) * 3600 + int(m_dur.group(2)) * 60 + float(m_dur.group(3))
    except Exception as e:
        return True, 20.0, "", f"FFmpeg erro ({e})"

    if 0.0 < dur_sec <= 15.0:
        return False, dur_sec, "", f"Vídeo curto demais ({dur_sec:.1f}s <= 15.0s)"

    texto = ""
    lang = "pt"
    prob_lang = 0.99
    if NEMO_SPEECH_BIN and os.path.exists(NEMO_SPEECH_BIN):
        try:
            out_json_nemo = str(LOGS_DIR / "temp_reel_nemotron35.json")
            if os.path.exists(out_json_nemo):
                os.remove(out_json_nemo)
            subprocess.run(
                [
                    NEMO_SPEECH_BIN, "transcribe", wav_path,
                    "-m", "nemotron-3.5",
                    "--word-times", "-l", "pt-BR", "--verbatim",
                    "--device", "cuda",
                    "-f", "json", "-o", out_json_nemo, "--force",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                creationflags=no_win_flag,
                timeout=8.5,
            )
            if os.path.exists(out_json_nemo):
                with open(out_json_nemo, "r", encoding="utf-8") as fn:
                    n_data = json.load(fn)
                texto = (n_data.get("text") or "").strip()
                if not texto and isinstance(n_data.get("words"), list):
                    texto = " ".join(str(w.get("word", "")).strip() for w in n_data["words"]).strip()
        except Exception as e_nemo:
            log_msg(f"⚠️ [NEMOTRON 3.5 GPU] Aviso ({e_nemo}), usando fallback...")

    if not texto and modelo_whisper_gpu is not None:
        try:
            segments, info = modelo_whisper_gpu.transcribe(wav_path, beam_size=1, vad_filter=True)
            texto = " ".join(s.text.strip() for s in segments).strip()
            lang = getattr(info, "language", "") or ""
            prob_lang = float(getattr(info, "language_probability", 0.0) or 0.0)
        except Exception as e:
            return True, dur_sec, "", f"Erro transcrição ({e})"

    palavras = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", texto.lower())
    qtd_palavras = len(palavras)
    qtd_pt = sum(1 for p in palavras if p in STOPWORDS_PT_BR)
    dt_total = time.time() - t0_tr

    if qtd_palavras < 5:
        return False, dur_sec, texto, f"Só música/instrumental sem fala ({qtd_palavras} palavras em {dt_total:.1f}s)"

    eh_portugues = (lang == "pt" and prob_lang >= 0.45 and qtd_pt >= 1) or (qtd_pt >= 2)
    if not eh_portugues:
        return False, dur_sec, texto, f"Sem fala útil em Português (pt_words={qtd_pt} em {dt_total:.1f}s)"

    palavras_unicas = set(palavras)
    if qtd_palavras >= 6 and len(palavras_unicas) <= 2:
        return False, dur_sec, texto, f"Repetição musical/brainrot ({len(palavras_unicas)} palavras únicas)"

    return True, dur_sec, texto, f"Fala PT-BR confirmada ({qtd_palavras} palavras em {dt_total:.1f}s)"


def _salvar_card_fila_audio_crm(username, nome_lead, ultima_msg_cliente, motivo, roteiro_audio, texto_alternativo=""):
    username = re.sub(r"[^a-z0-9._]", "", (username or "").lower()).strip(".")
    if not username:
        username = "lead_inbox"
    try:
        if sdr_engine is not None and hasattr(sdr_engine, "DB_PATH"):
            sdr_engine.init_crm_db()
            conn = sqlite3.connect(str(sdr_engine.DB_PATH))
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "SELECT id FROM audio_alerts_queue WHERE username = ? AND status = 'PENDING'",
                (username,),
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    """UPDATE audio_alerts_queue
                       SET title = ?, client_last_msg = ?, context_one_liner = ?, audio_script_15s = ?, suggested_text_fallback = ?, created_at = ?
                       WHERE id = ?""",
                    (nome_lead or username, ultima_msg_cliente, motivo, roteiro_audio, texto_alternativo, now_str, row[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO audio_alerts_queue
                       (thread_folder, username, title, client_last_msg, context_one_liner, audio_script_15s, suggested_text_fallback, alert_type, priority_score, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'GRAVAR_AUDIO', 95, 'PENDING', ?)""",
                    (f"live_{username}", username, nome_lead or username, ultima_msg_cliente, motivo, roteiro_audio, texto_alternativo, now_str),
                )
            conn.commit()
            conn.close()
    except Exception as e_db:
        log_msg(f"⚠️ Aviso ao gravar na tabela audio_alerts_queue (:4950): {e_db}")

    try:
        item = {
            "username": username,
            "full_name": nome_lead or username,
            "instagram_url": f"https://www.instagram.com/{username}/",
            "client_last_message": ultima_msg_cliente,
            "reason": motivo,
            "audio_script_15s": roteiro_audio,
            "suggested_text_fallback": texto_alternativo,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "PENDING",
        }
        lista = []
        if os.path.exists(FILA_AUDIOS_JSON_PATH):
            try:
                with open(FILA_AUDIOS_JSON_PATH, "r", encoding="utf-8") as f:
                    lista = json.load(f)
            except Exception:
                lista = []
        lista = [x for x in lista if x.get("username") != username]
        lista.insert(0, item)
        with open(FILA_AUDIOS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==========================================
# 5. MOTOR PRINCIPAL DE PROSPECÇÃO
# ==========================================
def executar_rotina_de_prospeccao(prospectados, avaliados_sessao, ultimo_reel_visto=None):
    t0_ciclo = time.time()
    estado_reels = garantir_na_aba_reels()
    username_reels = estado_reels.get("username_reels")
    if username_reels and ultimo_reel_visto and username_reels == ultimo_reel_visto:
        log_msg(f"⚡ [ANTI-TRAVA] Reel ainda em @{username_reels}, aplicando swipe nativo reforçado...")
        try:
            d_olheiro.swipe(540, 1450, 540, 200, 0.10)
        except Exception:
            d_olheiro.shell("input swipe 540 1450 540 200 130")
        time.sleep(0.55)
        estado_reels = garantir_na_aba_reels()
        username_reels = estado_reels.get("username_reels")

    nome_reels = estado_reels.get("nome_reels")
    author_click_xy = estado_reels.get("author_click_xy")

    if username_reels:
        if username_reels in avaliados_sessao or ja_foi_prospectado(username_reels, prospectados):
            log_msg(f"[OLHEIRO] ⏭️ @{username_reels} já avaliado/prospectado. Pulando Reel ->")
            return False, True, username_reels
        if autor_claramente_mulher(username_reels, nome_reels):
            log_msg(f"[OLHEIRO] 🚺 Nome feminino detectado no Reel (@{username_reels} / '{nome_reels or ''}'). Pulando instantaneamente ->")
            avaliados_sessao.add(username_reels)
            return False, True, username_reels

    if estado_reels.get("na_aba_reels") and not estado_reels.get("eh_anuncio") and not estado_reels.get("tem_scrubber"):
        audio_attr = estado_reels.get("audio_attribution") or "sem barra >15s"
        log_msg(
            f"[OLHEIRO] ⏭️ [ANTI-BRAINROT <=15s] Vídeo curto demais sem barra de duração "
            f"(@{username_reels or 'desconhecido'} | áudio='{audio_attr}'). Pulando na hora ->"
        )
        if username_reels:
            avaliados_sessao.add(username_reels)
        return False, True, username_reels

    log_msg(f"[OLHEIRO] Triando frame com YOLOv11 GPU + OpenCV (autor={f'@{username_reels}' if username_reels else 'desconhecido'})...")
    valido, pessoa_detectada, estado_reels = triagem_feed_yolo(estado_reels)

    if not valido:
        if not pessoa_detectada:
            log_msg("[OLHEIRO] Nenhuma pessoa em destaque (Talking Head). Pulando Reel ->")
        if username_reels:
            avaliados_sessao.add(username_reels)
        return False, pessoa_detectada, username_reels

    log_msg(f"[OLHEIRO] 🎙️ Talking Head >15s detectado (@{username_reels or 'desconhecido'})! Validando fala em Português na GPU...")
    ok_fala, dur_sec, texto_falado, motivo_fala = transcrever_e_validar_fala_reel_gpu(estado_reels)
    if not ok_fala:
        log_msg(
            f"[OLHEIRO] 🗑️ [EXORCISTA ANTI-BRAINROT] @{username_reels or 'desconhecido'} REPROVADO: {motivo_fala} "
            f"| dur={dur_sec:.1f}s | transcr='{texto_falado[:75]}'. Exorcizando e rolando embora ->"
        )
        if username_reels:
            avaliados_sessao.add(username_reels)
        return False, True, username_reels

    log_msg(
        f"[OLHEIRO] 🏆 [FALA PT-BR CONFIRMADA NA GPU] @{username_reels or 'desconhecido'} "
        f"(dur={dur_sec:.1f}s): \"{texto_falado[:100]}\" -> Abrindo perfil para qualificar..."
    )

    aprovado, texto_perfil, username, copy_final = olheiro_qualificar_perfil(
        prospectados=prospectados,
        username_reels=username_reels,
        nome_reels=nome_reels,
        author_click_xy=author_click_xy,
    )

    d_olheiro.press("back")
    time.sleep(0.45)

    if not username:
        log_msg("⚠️ [INTERCEPTOR] Perfil não abriu (possível anúncio). Continuando nos Reels...")
        return False, True, username_reels

    username = normalizar_username(username)
    avaliados_sessao.add(username)
    if username_reels:
        avaliados_sessao.add(username_reels)

    if ja_foi_prospectado(username, prospectados):
        log_msg(f"⚠️ Alvo @{username} já prospectado antes. Ignorando...")
        return False, True, username

    if not aprovado or not copy_final:
        log_msg(f"❌ Perfil @{username} reprovado nas regras. Pulando para o próximo Reel ->")
        return False, True, username

    log_msg(f"🔥 Perfil @{username} APROVADO E INÉDITO! Disparando Sniper imediatamente (Reel retido em paralelo)...")
    log_msg(f"💬 Copy pronta para @{username}: {copy_final.replace(chr(10), ' / ')}")
    sucesso = sniper_enviar_dm(username, copy_final)

    if sucesso == "BLOQUEIO_INSTAGRAM":
        log_msg(f"🛑 [PARADA DE EMERGÊNCIA ANTI-BLOCK] Prospecção encerrada automaticamente após bloqueio de DM em @{username} (lead preservado para tentar outro dia).")
        sys.exit(0)

    if sucesso == "JA_ENVIADO":
        salvar_prospectado(username)
        if username not in prospectados:
            prospectados.append(username)
        return False, True, username

    if sucesso is True:
        salvar_prospectado(username)
        if username_reels and username_reels != username:
            salvar_prospectado(username_reels)
            if username_reels not in prospectados:
                prospectados.append(username_reels)
        if username not in prospectados:
            prospectados.append(username)
        registrar_no_banco_local(username, copy_final)
        log_msg(f"⚡ [TEMPO TOTAL DO LEAD @{username}] {time.time() - t0_ciclo:.1f}s do Reel até o envio da DM!")
        return True, True, username

    return False, True, username


def iniciar_fluxo_reels(ciclos=100, delay_min=15, delay_max=30):
    inicializar_banco()
    conectar_celulares()
    log_msg(f"🚀 Iniciando Motor Olheiro + Sniper TURBO 100% Focado em Prospecção (Meta: {ciclos} prospecções | Cooldown entre DMs: {delay_min}s-{delay_max}s)...")
    garantir_na_aba_reels()

    prospectados = carregar_prospectados()
    avaliados_sessao = set()
    mensagens_enviadas = 0
    falhas_consecutivas = 0
    reels_vistos_contador = 0
    ultimo_user_visto = None
    LIMITE_FALHAS = 15

    while mensagens_enviadas < ciclos:
        reels_vistos_contador += 1
        log_msg(f"\n==================== CAÇADA REELS (Sucessos: {mensagens_enviadas}/{ciclos}) ====================")
        sucesso, pessoa_detectada, ultimo_user = executar_rotina_de_prospeccao(
            prospectados, avaliados_sessao, ultimo_reel_visto=ultimo_user_visto
        )
        ultimo_user_visto = ultimo_user

        rolar_proximo_reel(ultimo_user)

        if sucesso:
            mensagens_enviadas += 1
            log_msg(f"🎉 [PLACAR] {mensagens_enviadas}/{ciclos} leads prospectados com sucesso nesta sessão!")
            if mensagens_enviadas < ciclos and delay_max > 0:
                delay = random.randint(delay_min, delay_max)
                log_msg(f"⏳ Pausa anti-spam de {delay}s antes do próximo disparo do Sniper...")
                time.sleep(delay)

        if not pessoa_detectada:
            falhas_consecutivas += 1
            if falhas_consecutivas >= LIMITE_FALHAS:
                log_msg(f"⚠️ Limite de {LIMITE_FALHAS} frames sem pessoas atingido. Reiniciando Instagram do Olheiro...")
                reiniciar_instagram_reels()
                falhas_consecutivas = 0
        else:
            falhas_consecutivas = 0

    log_msg(f"🏆 META ATINGIDA! {mensagens_enviadas}/{ciclos} prospecções concluídas com 100% de sucesso!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Megazord Prospecção Dual-Phone (Olheiro + Sniper)")
    parser.add_argument("--ciclos", type=int, default=100, help="Quantidade de DMs de sucesso para enviar")
    parser.add_argument("--delay-min", type=int, default=15, help="Delay mínimo em segundos após cada DM enviada")
    parser.add_argument("--delay-max", type=int, default=30, help="Delay máximo em segundos após cada DM enviada")
    parser.add_argument("--supervisionado", action="store_true", help="Modo rápido supervisionado (delay 6-10s) para validação ao vivo")
    args = parser.parse_args()

    if args.supervisionado:
        iniciar_fluxo_reels(ciclos=args.ciclos, delay_min=6, delay_max=10)
    else:
        iniciar_fluxo_reels(ciclos=args.ciclos, delay_min=args.delay_min, delay_max=args.delay_max)
