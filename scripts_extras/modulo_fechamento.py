# -*- coding: utf-8 -*-
"""
Módulo de Fechamento Single-Phone para Reel Selecionado
Extrai o link do Reel atual, transcreve com Whisper local, lê a Bio e envia copy personalizada via Gemini.
"""
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
import uiautomator2 as u2
import yt_dlp
import whisper
from google import genai

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
SERIAL_SNIPER = os.getenv("SERIAL_SNIPER", "").strip()

if not GEMINI_API_KEY:
    print("❌ Defina GEMINI_API_KEY no arquivo .env antes de executar este módulo.")
    sys.exit(1)

print("Carregando o modelo Whisper local...")
modelo_whisper = whisper.load_model("base")

client = genai.Client(api_key=GEMINI_API_KEY)

print("Conectando ao celular...")
d = u2.connect(SERIAL_SNIPER) if SERIAL_SNIPER else u2.connect()
print("✅ Celular conectado!")


def extrair_link_do_reel():
    print("[1] Copiando link do Reel...")
    if d(descriptionMatches="(?i)Compartilhar.*|(?i)Share.*").exists:
        d(descriptionMatches="(?i)Compartilhar.*|(?i)Share.*").click()
        time.sleep(2)

        if d(textMatches="(?i)Copiar link|(?i)Copy link").exists:
            d(textMatches="(?i)Copiar link|(?i)Copy link").click()
            time.sleep(1.5)
            d.click(500, 150)
            time.sleep(1)
            return d.clipboard
        else:
            print("Botão de Copiar Link não encontrado no menu.")
            d.press("back")
    return None


def baixar_audio_mp3(url):
    print(f"[2] Baixando áudio do Reel: {url}")
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    arquivo_saida = str(logs_dir / "audio_temp.mp3")

    for ext in ["mp3", "m4a", "mp4", "webm"]:
        cand = str(logs_dir / f"audio_temp.{ext}")
        if os.path.exists(cand):
            try:
                os.remove(cand)
            except Exception:
                pass

    opcoes_ydl = {
        "format": "bestaudio/best",
        "outtmpl": str(logs_dir / "audio_temp.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(opcoes_ydl) as ydl:
            ydl.download([url])

        if not os.path.exists(arquivo_saida):
            print("❌ Erro: O yt-dlp não conseguiu converter para MP3.")
            return None

        return arquivo_saida
    except Exception as e:
        print(f"Erro no download ou conversão: {e}")
        return None


def ir_para_perfil_e_raspar_dados():
    print("[4] Entrando no perfil do cliente para ler a Bio...")
    entrou = False

    if d(textMatches="(?i)Seguir|(?i)Follow").exists:
        try:
            bounds = d(textMatches="(?i)Seguir|(?i)Follow").info["bounds"]
            click_x = bounds["left"] - 150
            click_y = (bounds["top"] + bounds["bottom"]) // 2
            d.click(click_x, click_y)
            entrou = True
        except Exception as e:
            print(f"Erro no cálculo do clique: {e}")

    if not entrou and d(descriptionMatches="(?i).*foto do perfil.*|(?i).*profile photo.*").exists:
        d(descriptionMatches="(?i).*foto do perfil.*|(?i).*profile photo.*").click()
        entrou = True

    if not entrou:
        info = d.info
        d.click(info["displayWidth"] * 0.15, info["displayHeight"] * 0.80)

    time.sleep(4)

    textos_da_tela = []
    for elemento in d(className="android.widget.TextView"):
        try:
            texto = elemento.info.get("text", "")
            if texto:
                textos_da_tela.append(texto)
        except Exception:
            pass

    texto_bruto_perfil = " | ".join(textos_da_tela)
    if len(texto_bruto_perfil) < 50:
        return None

    print(f"Textos extraídos do perfil: {texto_bruto_perfil[:100]}...")
    return texto_bruto_perfil


def gerar_copy_gemini(texto_perfil, transcricao):
    print("[5] Gerando Copy Hyper-Personalizada via Gemini...")
    prompt = f"""
    Você é um editor de vídeos premium (estilo minimalista e alta retenção) prospectando clientes.

    Abaixo estão os textos extraídos da tela do perfil do Instagram do cliente (nome, métricas e BIO):
    {texto_perfil}

    Abaixo está a transcrição de um trecho do último vídeo (Reels) que ele postou:
    {transcricao}

    TAREFA: Escreva uma DM (mensagem direta) para ele.
    REGRAS DA COPY:
    1. Seja natural, pareça um humano digitando (sem emojis exagerados, sem formalidade extrema).
    2. Comece chamando pelo nome (se encontrar no texto do perfil).
    3. Elogie um PONTO ESPECÍFICO do que ele falou na transcrição do vídeo (prove que você assistiu).
    4. Conecte com algo da Bio dele (se fizer sentido).
    5. Ofereça escalar a retenção dele com uma edição dinâmica.
    6. MÁXIMO DE 4 LINHAS. Seja direto. Termine com uma pergunta rápida.
    """

    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        copy_gerada = response.text.strip()
        print("\n--- COPY GERADA ---")
        print(copy_gerada)
        print("-------------------\n")
        return copy_gerada
    except Exception as e:
        print(f"Erro no Gemini: {e}")
        return None


def enviar_mensagem_e_voltar(mensagem):
    print("[6] Enviando DM e voltando para os Reels...")

    if d(textMatches="(?i)Mensagem|(?i)Message").exists:
        d(textMatches="(?i)Mensagem|(?i)Message").click()
        time.sleep(3)

        caixa_texto = d(className="android.widget.EditText")
        if caixa_texto.exists:
            caixa_texto.set_text(mensagem)
            time.sleep(1)

            if d(description="Enviar").exists:
                d(description="Enviar").click()
            elif d(description="Send").exists:
                d(description="Send").click()
            else:
                d.press("enter")

            print("✅ Cliente prospectado com sucesso.")
            time.sleep(2)
            d.press("back")
            time.sleep(1)
            d.press("back")
            time.sleep(1)
            d.press("back")
            time.sleep(1)
        else:
            print("❌ Caixa de texto não encontrada.")
            d.press("back")
            time.sleep(1)
            d.press("back")
    else:
        print("❌ Botão 'Mensagem' não encontrado neste perfil.")
        d.press("back")


def executar_modulo_fechamento():
    print("=== INICIANDO MÓDULO DE FECHAMENTO ===")
    print("Deixe um Reel aprovado rodando na tela do celular agora.")
    time.sleep(4)

    link = extrair_link_do_reel()
    if not link:
        print("Falha ao pegar o link. Abortando módulo.")
        return

    arquivo_mp3 = baixar_audio_mp3(link)
    if not arquivo_mp3:
        print("Falha ao criar o MP3. Abortando módulo.")
        return

    print("[3] Whisper está transcrevendo o áudio localmente...")
    try:
        resultado_whisper = modelo_whisper.transcribe(arquivo_mp3)
        transcricao = resultado_whisper["text"]
        print(f"Transcrição: {transcricao[:100]}...")
    except Exception as e:
        print(f"Erro na transcrição: {e}")
        return

    texto_perfil = ir_para_perfil_e_raspar_dados()
    if not texto_perfil:
        print("⚠️ Perfil não acessado. Cancelando a copy para não bugar a navegação.")
        return

    copy_final = gerar_copy_gemini(texto_perfil, transcricao)
    if copy_final:
        enviar_mensagem_e_voltar(copy_final)


if __name__ == "__main__":
    executar_modulo_fechamento()
