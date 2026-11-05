#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Utilitário de Download Automático dos Modelos de Visão Computacional:
  1. deploy_gender.prototxt (Arquitetura Caffe OpenCV DNN)
  2. gender_net.caffemodel (Pesos Caffe OpenCV DNN para filtro de gênero)
  3. yolo11n.pt (YOLOv11 Nano via Ultralytics para detecção de Talking Head)
"""
import shutil
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent
MODELS_DIR.mkdir(parents=True, exist_ok=True)

GENDER_PROTOTXT_URL = "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_deploy.prototxt"
GENDER_CAFFEMODEL_URL = "https://github.com/GilLevi/AgeGenderDeepLearning/raw/master/models/gender_net.caffemodel"


def download_file(url: str, dest: Path):
    if dest.exists() and dest.stat().st_size > 100:
        print(f"[OK] Modelo já existe: {dest.name} ({dest.stat().st_size / 1024:.1f} KB)")
        return
    print(f"[DOWNLOAD] Baixando {dest.name}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        print(f"[OK] {dest.name} salvo com sucesso ({dest.stat().st_size / (1024*1024):.2f} MB)!")
    except Exception as e:
        print(f"[AVISO] Não foi possível baixar {dest.name} automaticamente ({e}). O sistema usará o fallback heurístico + IA.")


def main():
    download_file(GENDER_PROTOTXT_URL, MODELS_DIR / "deploy_gender.prototxt")
    download_file(GENDER_CAFFEMODEL_URL, MODELS_DIR / "gender_net.caffemodel")
    try:
        from ultralytics import YOLO
        yolo_path = MODELS_DIR / "yolo11n.pt"
        if not yolo_path.exists():
            print("[DOWNLOAD] Inicializando YOLOv11n (yolo11n.pt)...")
            YOLO(str(yolo_path))
        print(f"[OK] Modelo YOLOv11n pronto em: {yolo_path}")
    except Exception as e:
        print(f"[AVISO] Ultralytics YOLO será inicializado na primeira execução ({e}).")


if __name__ == "__main__":
    main()
