# -*- coding: utf-8 -*-
"""
Treinador de Algoritmo do Feed de Reels (Retenção + Like em Talking Heads via Gemini Vision)
"""
import os
import sys
import time
import random
import shutil
from pathlib import Path
from dotenv import load_dotenv
from google import genai

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

ADB_CMD = os.getenv("ADB_PATH", "").strip() or shutil.which("adb") or "adb"
SERIAL_OLHEIRO = os.getenv("SERIAL_OLHEIRO", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if not GEMINI_API_KEY:
    print("❌ Defina GEMINI_API_KEY no arquivo .env antes de executar o treinador de Reels.")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)


def _adb_prefix():
    if SERIAL_OLHEIRO:
        return f'"{ADB_CMD}" -s {SERIAL_OLHEIRO}'
    return f'"{ADB_CMD}"'


def tirar_print_adb():
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    caminho_imagem = str(logs_dir / "tela_atual.png")
    pref = _adb_prefix()
    os.system(f"{pref} shell screencap -p /sdcard/tela_temp_reels.png")
    os.system(f'{pref} pull /sdcard/tela_temp_reels.png "{caminho_imagem}"')
    os.system(f"{pref} shell rm /sdcard/tela_temp_reels.png")
    return caminho_imagem


def pular_video():
    print("[Ação] Pulando vídeo...")
    os.system(f"{_adb_prefix()} shell input swipe 500 1500 500 300 200")


def curtir_video():
    print("[Ação] Dando Like no vídeo!")
    pref = _adb_prefix()
    os.system(f"{pref} shell input tap 500 1000")
    time.sleep(0.1)
    os.system(f"{pref} shell input tap 500 1000")


def assistir_ate_o_fim():
    tempo_assistindo = random.uniform(15.0, 30.0)
    print(f"[Ação] Assistindo por {tempo_assistindo:.1f} segundos...")
    time.sleep(tempo_assistindo)


def analisar_imagem(caminho_imagem):
    prompt = """
    Analise esta captura de tela do Instagram Reels.
    Existe uma pessoa no centro da tela falando diretamente para a câmera (formato especialista/talking head ensinando algo)?
    Responda APENAS com a palavra SIM ou NAO.
    """
    try:
        imagem = client.files.upload(file=caminho_imagem)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=[imagem, prompt])
        client.files.delete(name=imagem.name)
        resultado = response.text.strip().upper()
        print(f"[IA] Resultado da análise: {resultado}")
        return "SIM" in resultado
    except Exception as e:
        print(f"[Erro na API] {e}")
        return False


def rodar_treinamento(ciclos=50):
    print("Iniciando o Treinador de Algoritmo... Abra a aba REELS no celular.")
    time.sleep(2)

    for i in range(ciclos):
        print(f"\n--- Analisando Reel {i+1}/{ciclos} ---")
        time.sleep(random.uniform(1.0, 1.5))

        img_path = tirar_print_adb()
        if not os.path.exists(img_path) or os.path.getsize(img_path) == 0:
            print("⚠️ Erro no ADB: Imagem não gerada. Tentando o próximo...")
            pular_video()
            continue

        try:
            if analisar_imagem(img_path):
                print("🏆 Talking Head detectado! Retendo vídeo para treinar algoritmo.")
                assistir_ate_o_fim()
                if random.random() < 0.3:
                    curtir_video()
                pular_video()
            else:
                print("❌ Fora do perfil. Swipe imediato!")
                pular_video()
        except Exception as e:
            print(f"Erro na execução: {e}. Pulando...")
            pular_video()


if __name__ == "__main__":
    rodar_treinamento()
