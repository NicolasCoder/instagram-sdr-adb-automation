# -*- coding: utf-8 -*-
"""
Monitor de Notificações Android (dumpsys notification) com Disparo de Oferta Rápida
"""
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
import uiautomator2 as u2

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SERIAL_SNIPER = os.getenv("SERIAL_SNIPER", "").strip()
TARGET = os.getenv("TARGET_MARKET", "BR").strip() or "BR"

OFERTA_BR = os.getenv(
    "SDR_OFFER_BR",
    "Cara, tô com um espaço na agenda essa semana. Topa um vídeo teste gratuito pra ver como fica a retenção na prática?",
)
OFERTA_US = os.getenv(
    "SDR_OFFER_US",
    "Listen, I have an open slot this week. How about a free test video so you can see the retention bump firsthand?",
)

print("Conectando ao celular Sniper...")
try:
    d = u2.connect(SERIAL_SNIPER) if SERIAL_SNIPER else u2.connect()
    print("✅ Sniper conectado!")
except Exception as e:
    print(f"❌ Erro ao conectar no Sniper. Verifique o serial no .env. Erro: {e}")
    sys.exit(1)


def checar_e_responder():
    print("\n[MONITOR] Checando notificações...")
    output = d.shell("dumpsys notification --noredact | grep android.text").output

    if "Instagram" in output and ("mensagem" in output.lower() or "message" in output.lower()):
        print("🚨 NOTIFICAÇÃO DE DM DETECTADA! 🚨")
        d.screen_on()
        d.open_notification()
        time.sleep(2)

        if d(textMatches="(?i).*Instagram.*").exists:
            d(textMatches="(?i).*Instagram.*").click()
            time.sleep(4)

            caixa_texto = d(className="android.widget.EditText")
            if caixa_texto.exists:
                oferta_atual = OFERTA_BR if TARGET == "BR" else OFERTA_US
                caixa_texto.set_text(oferta_atual)
                time.sleep(1)

                if d(description="Enviar").exists:
                    d(description="Enviar").click()
                elif d(description="Send").exists:
                    d(description="Send").click()
                else:
                    d.press("enter")

                print("✅ Oferta enviada! Pausando por 10 minutos para atendimento humano.")
                d.shell("service call notification 1")
                time.sleep(600)
                d.press("home")
            else:
                print("❌ Caixa de texto não encontrada. Retornando ao vigia.")
                d.press("home")
        else:
            print("Notificação clicável não encontrada. Fechando painel.")
            d.press("back")
    else:
        print("Nenhuma nova DM. Aguardando próximo ciclo...")


def iniciar_monitor():
    print("=== MONITOR DE NOTIFICAÇÕES ===")
    while True:
        try:
            checar_e_responder()
        except Exception as e:
            print(f"Erro no ciclo: {e}")
        time.sleep(30)


if __name__ == "__main__":
    iniciar_monitor()
