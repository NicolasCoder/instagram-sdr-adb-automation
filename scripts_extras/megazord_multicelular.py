# -*- coding: utf-8 -*-
"""
Modo Alternativo Multi-Celular com Gemini Vision + Whisper Local
Configurado via arquivo .env na raiz do projeto.
"""
import os
import sys
import time
import random
from pathlib import Path
from dotenv import load_dotenv
import uiautomator2 as u2
import yt_dlp
import whisper
from google import genai

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SERIAL_OLHEIRO = os.getenv("SERIAL_OLHEIRO", "").strip()
SERIAL_SNIPER = os.getenv("SERIAL_SNIPER", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if not GEMINI_API_KEY:
    print("❌ Defina GEMINI_API_KEY no arquivo .env antes de executar este modo.")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

print("Carregando modelo Whisper...")
modelo_whisper = whisper.load_model("base")

print("Conectando aos celulares...")
try:
    d_olheiro = u2.connect(SERIAL_OLHEIRO) if SERIAL_OLHEIRO else u2.connect()
    d_sniper = u2.connect(SERIAL_SNIPER) if SERIAL_SNIPER else d_olheiro
    print("✅ Celulares conectados com sucesso!")
except Exception as e:
    print(f"❌ Erro ao conectar nos celulares. Verifique os seriais no .env. Erro: {e}")
    sys.exit(1)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ARQUIVO_PROSPECTADOS = str(DATA_DIR / "prospectados.txt")


def carregar_prospectados():
    if not os.path.exists(ARQUIVO_PROSPECTADOS):
        return []
    with open(ARQUIVO_PROSPECTADOS, "r", encoding="utf-8") as f:
        return [linha.strip().lower() for linha in f.readlines() if linha.strip()]


def salvar_prospectado(arroba):
    with open(ARQUIVO_PROSPECTADOS, "a", encoding="utf-8") as f:
        f.write(f"{arroba.lower()}\n")


def olheiro_analisar_tela():
    caminho_imagem = str(BASE_DIR / "logs" / "tela_temp.png")
    os.makedirs(os.path.dirname(caminho_imagem), exist_ok=True)
    d_olheiro.shell("screencap -p /sdcard/tela.png")
    d_olheiro.pull("/sdcard/tela.png", caminho_imagem)
    d_olheiro.shell("rm /sdcard/tela.png")

    if not os.path.exists(caminho_imagem) or os.path.getsize(caminho_imagem) == 0:
        return False

    prompt = "Existe uma pessoa no centro da tela falando diretamente para a câmera ensinando algo? Responda APENAS com SIM ou NAO."
    try:
        imagem = client.files.upload(file=caminho_imagem)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=[imagem, prompt])
        client.files.delete(name=imagem.name)
        return "SIM" in response.text.strip().upper()
    except Exception:
        return False


def olheiro_extrair_link():
    if d_olheiro(descriptionMatches="(?i)Compartilhar.*|(?i)Share.*").exists:
        d_olheiro(descriptionMatches="(?i)Compartilhar.*|(?i)Share.*").click()
        time.sleep(2)
        if d_olheiro(textMatches="(?i)Copiar link|(?i)Copy link").exists:
            d_olheiro(textMatches="(?i)Copiar link|(?i)Copy link").click()
            time.sleep(1.5)
            d_olheiro.click(500, 150)
            time.sleep(1)
            return d_olheiro.clipboard
        else:
            d_olheiro.press("back")
    return None


def olheiro_raspar_perfil():
    entrou = False
    if d_olheiro(textMatches="(?i)Seguir|(?i)Follow").exists:
        try:
            bounds = d_olheiro(textMatches="(?i)Seguir|(?i)Follow").info["bounds"]
            d_olheiro.click(bounds["left"] - 150, (bounds["top"] + bounds["bottom"]) // 2)
            entrou = True
        except Exception:
            pass

    if not entrou:
        info = d_olheiro.info
        d_olheiro.click(info["displayWidth"] * 0.15, info["displayHeight"] * 0.80)

    time.sleep(4)
    textos_da_tela = []
    for elemento in d_olheiro(className="android.widget.TextView"):
        try:
            texto = elemento.info.get("text", "")
            if texto:
                textos_da_tela.append(texto)
        except Exception:
            pass

    texto_bruto = " | ".join(textos_da_tela)
    return texto_bruto if len(texto_bruto) > 50 else None


def baixar_e_transcrever(url):
    arquivo_saida = str(BASE_DIR / "logs" / "audio_temp.mp3")
    for ext in ["mp3", "m4a", "mp4", "webm"]:
        cand = str(BASE_DIR / "logs" / f"audio_temp.{ext}")
        if os.path.exists(cand):
            try:
                os.remove(cand)
            except Exception:
                pass

    opcoes_ydl = {
        "format": "bestaudio/best",
        "outtmpl": str(BASE_DIR / "logs" / "audio_temp.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opcoes_ydl) as ydl:
            ydl.download([url])
        if os.path.exists(arquivo_saida):
            res = modelo_whisper.transcribe(arquivo_saida)
            return res["text"]
    except Exception:
        return None
    return None


def cerebro_gemini(textos_perfil, transcricao):
    prompt = f"""
    Abaixo estão os textos do perfil de um cliente no Instagram: {textos_perfil}
    Abaixo está o que ele falou no último Reel: {transcricao}

    TAREFA 1: Encontre o "username" (o @ exato) desse cliente no meio dos textos do perfil.
    TAREFA 2: Crie uma copy curta elogiando a fala dele e a bio dele, oferecendo edição dinâmica de Reels.

    FORMATO OBRIGATÓRIO DE SAÍDA:
    Linha 1: [Apenas o username, sem o símbolo de @]
    Linha 2 em diante: [A Copy]
    """
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        linhas = response.text.strip().split("\n")
        username = linhas[0].replace("@", "").strip()
        copy = "\n".join(linhas[1:]).strip()
        return username, copy
    except Exception:
        return None, None


def sniper_enviar_dm(arroba, copy):
    print(f"\n[SNIPER] Mirando no alvo: @{arroba}")
    d_sniper.screen_on()

    link_perfil = f"https://www.instagram.com/{arroba}"
    comando_magico = f'am start -a android.intent.action.VIEW -d "{link_perfil}" -p com.instagram.android'
    d_sniper.shell(comando_magico)
    time.sleep(5)

    if d_sniper(textMatches="(?i)Mensagem|(?i)Message").exists:
        d_sniper(textMatches="(?i)Mensagem|(?i)Message").click()
        time.sleep(3)
        caixa_texto = d_sniper(className="android.widget.EditText")

        if caixa_texto.exists:
            caixa_texto.set_text(copy)
            time.sleep(1)

            if d_sniper(description="Enviar").exists:
                d_sniper(description="Enviar").click()
            elif d_sniper(description="Send").exists:
                d_sniper(description="Send").click()
            else:
                d_sniper.press("enter")

            print(f"✅ [SNIPER] Mensagem entregue para @{arroba}.")
            time.sleep(2)
            d_sniper.press("home")
            return True
        else:
            print("❌ [SNIPER] Caixa de texto não encontrada.")
    else:
        print(f"❌ [SNIPER] Botão 'Mensagem' bloqueado no perfil @{arroba}.")

    d_sniper.press("home")
    return False


def iniciar_megazord(ciclos=100):
    print("\n=== MEGAZORD MULTI-CELULAR ATIVADO ===")
    print("Celular Olheiro: Deixe na aba REELS.")
    print("Celular Sniper: Deixe com a tela desbloqueada na HOME.")
    time.sleep(5)

    prospectados = carregar_prospectados()

    for i in range(ciclos):
        print(f"\n--- Caçada {i+1}/{ciclos} ---")
        time.sleep(2)

        print("[OLHEIRO] Analisando frame...")
        eh_talking_head = olheiro_analisar_tela()

        if not eh_talking_head:
            print("[OLHEIRO] Sem Talking Head. Pulando...")
            d_olheiro.shell("input swipe 500 1500 500 300 200")
            continue

        print("[OLHEIRO] 🏆 Especialista detectado! Retendo vídeo...")
        time.sleep(random.uniform(10.0, 15.0))

        link = olheiro_extrair_link()
        if not link:
            d_olheiro.shell("input swipe 500 1500 500 300 200")
            continue

        texto_perfil = olheiro_raspar_perfil()
        if not texto_perfil:
            d_olheiro.shell("input swipe 500 1500 500 300 200")
            continue

        transcricao = baixar_e_transcrever(link)
        if not transcricao:
            d_olheiro.press("back")
            time.sleep(1)
            d_olheiro.shell("input swipe 500 1500 500 300 200")
            continue

        username, copy_final = cerebro_gemini(texto_perfil, transcricao)

        d_olheiro.press("back")
        time.sleep(1)
        d_olheiro.shell("input swipe 500 1500 500 300 200")

        if not username or not copy_final:
            print("⚠️ IA falhou em extrair a copy/username.")
            continue

        if username.lower() in prospectados:
            print(f"⚠️ Alvo @{username} já prospectado antes. Ignorando.")
            continue

        print(f"\n🧠 [CÉREBRO] Copy gerada para @{username}:\n{copy_final}\n")

        sucesso = sniper_enviar_dm(username, copy_final)

        if sucesso:
            salvar_prospectado(username)
            prospectados.append(username.lower())

            delay = random.randint(300, 600)
            print(f"⏳ Pausa anti-ban de {delay/60:.1f} minutos para proteger o Sniper.")
            time.sleep(delay)


if __name__ == "__main__":
    iniciar_megazord()
