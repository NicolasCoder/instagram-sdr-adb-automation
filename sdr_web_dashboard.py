# -*- coding: utf-8 -*-
"""
Dashboard Web SDR & CRM Autônomo Instagram via ADB (Porta :4950)
Interface executiva completa para operar a prospecção:
  1. Fila de Áudios Prontos & Vídeo Teste (Zero-Read Handoff Cards com roteiro de 15s + 1-Clique Abrir Chat no Celular via ADB)
  2. Fila 'Responder Agora' com envio automático de texto via ADB
  3. Leads Quentes / Vídeo Teste Entregue / WhatsApp & Reativação
  4. Explorador de Conversas + Watcher de Notificações em Tempo Real
"""
import os
import sys
import subprocess

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

import json
import time
import sqlite3
import threading
import urllib.parse
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import insta_adb_sdr_engine as adb_engine

DB_PATH = adb_engine.DB_PATH
PORT = int(os.getenv("CRM_WEB_PORT", "4950"))

WATCHER_STATE = {
    "enabled": False,
    "interval_sec": 60,
    "last_run": None,
    "last_result": None,
    "total_scans": 0,
}


def watcher_background_loop():
    while True:
        if WATCHER_STATE["enabled"]:
            try:
                res = adb_engine.scan_notifications_once()
                WATCHER_STATE["last_run"] = datetime.now().strftime("%H:%M:%S")
                WATCHER_STATE["last_result"] = res
                WATCHER_STATE["total_scans"] += 1
            except Exception as e:
                WATCHER_STATE["last_result"] = {"ok": False, "error": str(e)}
        time.sleep(WATCHER_STATE["interval_sec"])


def get_db_conn():
    adb_engine.init_crm_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Instagram SDR & ADB Command Center (:4950)</title>
<style>
  :root {
    --bg: #090b10;
    --surface: #11151f;
    --surface2: #171c29;
    --border: rgba(255,255,255,0.08);
    --text: #f1f5f9;
    --muted: #94a3b8;
    --accent: #6366f1;
    --accent-hover: #4f46e5;
    --green: #10b981;
    --amber: #f59e0b;
    --red: #ef4444;
    --pink: #ec4899;
    --cyan: #06b6d4;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
    line-height: 1.5;
    padding-bottom: 60px;
  }
  header {
    background: linear-gradient(180deg, rgba(23,28,41,0.98) 0%, rgba(17,21,31,0.95) 100%);
    border-bottom: 1px solid var(--border);
    padding: 16px 28px;
    position: sticky;
    top: 0;
    z-index: 50;
    backdrop-filter: blur(12px);
  }
  .top-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 14px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand h1 {
    font-size: 1.25rem;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .badge-adb {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
    background: rgba(16, 185, 129, 0.14);
    color: #34d399;
    border: 1px solid rgba(16, 185, 129, 0.3);
  }
  .badge-adb.offline {
    background: rgba(239, 68, 68, 0.14);
    color: #f87171;
    border-color: rgba(239, 68, 68, 0.3);
  }
  .top-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  button {
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 0.84rem;
    font-weight: 600;
    transition: all 0.15s ease;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  button:hover { background: #232a3d; border-color: rgba(255,255,255,0.18); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.primary:hover { background: var(--accent-hover); }
  button.success { background: rgba(16, 185, 129, 0.18); border-color: rgba(16, 185, 129, 0.4); color: #34d399; }
  button.success:hover { background: rgba(16, 185, 129, 0.28); }
  button.amber { background: rgba(245, 158, 11, 0.18); border-color: rgba(245, 158, 11, 0.4); color: #fbbf24; }
  button.amber:hover { background: rgba(245, 158, 11, 0.28); }

  .container { max-width: 1480px; margin: 0 auto; padding: 20px 28px; }
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px;
    margin-bottom: 22px;
  }
  .kpi-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
  }
  .kpi-card .label { font-size: 0.76rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .kpi-card .val { font-size: 1.65rem; font-weight: 800; margin-top: 4px; }
  .kpi-card .sub { font-size: 0.76rem; color: var(--muted); margin-top: 2px; }

  .tabs {
    display: flex;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 12px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }
  .tab-btn {
    padding: 9px 16px;
    border-radius: 8px;
    font-size: 0.88rem;
    background: var(--surface);
  }
  .tab-btn.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }

  .cards-list { display: flex; flex-direction: column; gap: 16px; }
  .lead-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 20px;
    transition: border-color 0.15s;
  }
  .lead-card:hover { border-color: rgba(99, 102, 241, 0.4); }
  .lead-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }
  .lead-title {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .lead-title h3 { font-size: 1.06rem; font-weight: 700; }
  .handle {
    color: var(--cyan);
    font-family: monospace;
    font-size: 0.9rem;
    background: rgba(6, 182, 212, 0.1);
    padding: 2px 8px;
    border-radius: 6px;
    text-decoration: none;
  }
  .tag {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 6px;
    text-transform: uppercase;
  }
  .tag.audio { background: rgba(236, 72, 153, 0.18); color: #f472b6; border: 1px solid rgba(236, 72, 153, 0.35); }
  .tag.vt { background: rgba(16, 185, 129, 0.18); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35); }
  .tag.urgent { background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); }

  .ctx-box {
    font-size: 0.88rem;
    color: #cbd5e1;
    margin-bottom: 10px;
    padding: 8px 12px;
    background: rgba(255,255,255,0.03);
    border-radius: 8px;
    border-left: 3px solid var(--cyan);
  }
  .script-box {
    background: rgba(236, 72, 153, 0.08);
    border: 1px solid rgba(236, 72, 153, 0.3);
    border-radius: 10px;
    padding: 13px 16px;
    margin-bottom: 12px;
  }
  .script-box .script-title {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #f472b6;
    font-weight: 700;
    margin-bottom: 5px;
  }
  .script-box .script-text {
    font-size: 1.02rem;
    font-weight: 500;
    color: #fff;
  }
  .text-reply-area {
    width: 100%;
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 0.88rem;
    font-family: inherit;
    margin-bottom: 10px;
    resize: vertical;
    min-height: 62px;
  }
  .card-actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
  }

  .filter-bar {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .filter-bar input, .filter-bar select {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 9px 14px;
    border-radius: 8px;
    font-size: 0.9rem;
  }
  .filter-bar input { flex: 1; min-width: 260px; }

  /* Modal */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.72);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 100;
    padding: 20px;
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    max-width: 820px;
    width: 100%;
    max-height: 88vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .modal-header {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .modal-body {
    padding: 18px 20px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .msg-bubble {
    max-width: 80%;
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 0.9rem;
  }
  .msg-operator {
    align-self: flex-end;
    background: rgba(99, 102, 241, 0.22);
    border: 1px solid rgba(99, 102, 241, 0.4);
  }
  .msg-client {
    align-self: flex-start;
    background: var(--surface2);
    border: 1px solid var(--border);
  }
  .msg-meta {
    font-size: 0.72rem;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .audio-badge {
    margin-top: 6px;
    padding: 8px 10px;
    background: rgba(236, 72, 153, 0.14);
    border-left: 3px solid var(--pink);
    border-radius: 6px;
    font-size: 0.85rem;
  }
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: #1e293b;
    border: 1px solid var(--accent);
    color: #fff;
    padding: 12px 18px;
    border-radius: 10px;
    box-shadow: 0 10px 25px rgba(0,0,0,0.5);
    display: none;
    z-index: 200;
    font-size: 0.9rem;
  }
</style>
</head>
<body>

<header>
  <div class="top-row">
    <div class="brand">
      <span style="font-size:1.6rem;">📲</span>
      <div>
        <h1>Instagram SDR & ADB Command Center</h1>
        <div style="font-size:0.78rem; color:var(--muted);">CRM Autônomo SQLite • Monitoramento de Notificações & Disparo ADB</div>
      </div>
    </div>
    <div class="top-actions">
      <div id="adbStatusBadge" class="badge-adb">Verificando ADB...</div>
      <button onclick="scanNotificationsNow()" title="Lê dumpsys notification --noredact sem tocar na tela">🔔 Ler Notificações Agora</button>
      <button class="amber" onclick="runBootCatchup()" title="Acorda o celular, abre a Inbox do Direct e lê conversas não lidas via UIAutomator">🚀 Boot Catch-Up (Varrer Inbox ADB)</button>
    </div>
  </div>
</header>

<div class="container">
  <div class="kpi-grid" id="kpiGrid"></div>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('audio_queue', this)">🎙️ Fila de Áudios & Vídeo Teste (<span id="cntAudio">0</span>)</button>
    <button class="tab-btn" onclick="switchTab('responder_agora', this)">🚨 LISTA 1: Responder Agora (<span id="cntResp">0</span>)</button>
    <button class="tab-btn" onclick="switchTab('quentes', this)">🔥 LISTA 2: Vídeo Teste & Quentes (<span id="cntHot">0</span>)</button>
    <button class="tab-btn" onclick="switchTab('reativacao', this)">🔄 LISTA 3: Reativação (<span id="cntReat">0</span>)</button>
    <button class="tab-btn" onclick="switchTab('all', this)">🗂️ Todas as Conversas</button>
  </div>

  <div class="filter-bar" id="filterBar" style="display:none;">
    <input type="text" id="searchInput" placeholder="Buscar por @username, nome ou palavra falada na conversa..." oninput="renderCurrentTab()">
    <select id="stageSelect" onchange="renderCurrentTab()">
      <option value="">Todas as Etapas</option>
      <option value="RESPONDER_AGORA">🚨 RESPONDER_AGORA</option>
      <option value="VIDEO_TESTE_ENVIADO">🎬 VIDEO_TESTE_ENVIADO</option>
      <option value="INTERESSE_QUENTE">🔥 INTERESSE_QUENTE</option>
      <option value="REATIVACAO">🔄 REATIVACAO</option>
      <option value="PROSPECTADO_SEM_RESPOSTA">📤 PROSPECTADO_SEM_RESPOSTA</option>
      <option value="DESINTERESSADO">❌ DESINTERESSADO</option>
      <option value="PESSOAL">👥 PESSOAL</option>
    </select>
  </div>

  <div id="cardsContainer" class="cards-list"></div>
</div>

<div class="modal-backdrop" id="chatModal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="modal-header">
      <div>
        <h3 id="modalTitle">Histórico da Conversa</h3>
        <div id="modalSub" style="font-size:0.8rem; color:var(--muted);"></div>
      </div>
      <button onclick="closeModal()">✖ Fechar</button>
    </div>
    <div class="modal-body" id="modalMessages"></div>
  </div>
</div>

<div class="toast" id="toastBox"></div>

<script>
let currentTab = 'audio_queue';
let dashboardData = { stats: {}, audio_alerts: [], conversations: [] };

function showToast(msg) {
  const t = document.getElementById('toastBox');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 4500);
}

async function loadAllData() {
  const res = await fetch('/api/dashboard');
  dashboardData = await res.json();
  renderHeaderAndKpis();
  renderCurrentTab();
}

function renderHeaderAndKpis() {
  const st = dashboardData.stats || {};
  const adb = dashboardData.adb;
  const badge = document.getElementById('adbStatusBadge');
  if (adb && adb.connected) {
    badge.className = 'badge-adb';
    badge.textContent = `📱 ADB ONLINE: ${adb.model} (${adb.serial})`;
  } else {
    badge.className = 'badge-adb offline';
    badge.textContent = `⚠️ ADB OFFLINE (Conecte o cabo USB)`;
  }

  document.getElementById('cntAudio').textContent = dashboardData.audio_alerts.filter(a => a.status !== 'DONE').length;
  document.getElementById('cntResp').textContent = st.RESPONDER_AGORA || 0;
  document.getElementById('cntHot').textContent = (st.VIDEO_TESTE_ENVIADO || 0) + (st.INTERESSE_QUENTE || 0);
  document.getElementById('cntReat').textContent = st.REATIVACAO || 0;

  document.getElementById('kpiGrid').innerHTML = `
    <div class="kpi-card">
      <div class="label">🎙️ Fila de Áudios / Vídeo Teste</div>
      <div class="val" style="color:#f472b6;">${dashboardData.audio_alerts.filter(a => a.status !== 'DONE').length}</div>
      <div class="sub">Prontos p/ gravar em 15s</div>
    </div>
    <div class="kpi-card">
      <div class="label">🚨 Responder Agora</div>
      <div class="val" style="color:#fbbf24;">${st.RESPONDER_AGORA || 0}</div>
      <div class="sub">Cliente respondeu por último</div>
    </div>
    <div class="kpi-card">
      <div class="label">🔥 Vídeo Teste & Quentes</div>
      <div class="val" style="color:#34d399;">${(st.VIDEO_TESTE_ENVIADO || 0) + (st.INTERESSE_QUENTE || 0)}</div>
      <div class="sub">${st.VIDEO_TESTE_ENVIADO || 0} testes entregues + ${st.INTERESSE_QUENTE || 0} negociações</div>
    </div>
    <div class="kpi-card">
      <div class="label">🔄 Follow-Up Reativação</div>
      <div class="val" style="color:#38bdf8;">${st.REATIVACAO || 0}</div>
      <div class="sub">Responderam/curtiram e esfriaram</div>
    </div>
    <div class="kpi-card">
      <div class="label">📊 Base Total no CRM</div>
      <div class="val">${st.total_conversations || 0}</div>
      <div class="sub">${st.total_messages || 0} mensagens registradas</div>
    </div>
  `;
}

function switchTab(tab, btn) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.getElementById('filterBar').style.display = (tab === 'all') ? 'flex' : 'none';
  renderCurrentTab();
}

function escapeHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderCurrentTab() {
  const container = document.getElementById('cardsContainer');
  if (currentTab === 'audio_queue') {
    const items = dashboardData.audio_alerts.filter(a => a.status !== 'DONE');
    if (!items.length) {
      container.innerHTML = `<div class="lead-card">🎉 Nenhum áudio pendente na fila!</div>`;
      return;
    }
    container.innerHTML = items.map(a => {
      const isVT = a.alert_type === 'LEAD_QUER_VIDEO_TESTE';
      return `
        <div class="lead-card">
          <div class="lead-header">
            <div class="lead-title">
              <span class="tag ${isVT ? 'vt' : 'audio'}">${isVT ? '🎬 LEAD QUER VÍDEO TESTE / FECHAMENTO' : '🎙️ GRAVAR ÁUDIO DE 15s AGORA'}</span>
              <h3>${escapeHtml(a.title)}</h3>
              <a class="handle" href="https://www.instagram.com/${escapeHtml(a.username)}" target="_blank">@${escapeHtml(a.username)}</a>
              <span style="font-size:0.78rem; color:var(--muted);">Prioridade ${a.priority_score}/100</span>
            </div>
            <span style="font-size:0.78rem; color:var(--muted);">Status: ${a.status}</span>
          </div>
          <div class="ctx-box">
            <strong>Resumo Mastigado (Sem precisar ler o chat):</strong> ${escapeHtml(a.context_one_liner)}<br>
            <span style="color:var(--muted);">Última fala do cliente:</span> <em>"${escapeHtml(a.client_last_msg)}"</em>
          </div>
          <div class="script-box">
            <div class="script-title">🎙️ ROTEIRO EXATO DE 15–20 SEGUNDOS (SÓ SEGURAR O MICROFONE E LER):</div>
            <div class="script-text">"${escapeHtml(a.audio_script_15s)}"</div>
          </div>
          <div class="card-actions">
            <button class="primary" onclick="openChatOnPhone('${escapeHtml(a.username)}')">📱 Abrir Chat de @${escapeHtml(a.username)} no Celular Agora (ADB)</button>
            <button class="success" onclick="markAlertDone(${a.id})">✅ Já Mandei o Áudio (Concluir)</button>
            <button onclick="sendTextViaAdbPrompt('${escapeHtml(a.username)}', \`${escapeHtml(a.suggested_text_fallback)}\`)">🤖 Ou Mandar em Texto via ADB</button>
            <button onclick="viewHistory('${escapeHtml(a.thread_folder)}', '${escapeHtml(a.title)}', '${escapeHtml(a.username)}')">💬 Ver Histórico Completo</button>
          </div>
        </div>
      `;
    }).join('');
    return;
  }

  let list = dashboardData.conversations;
  if (currentTab === 'responder_agora') {
    list = list.filter(c => c.funnel_stage === 'RESPONDER_AGORA');
  } else if (currentTab === 'quentes') {
    list = list.filter(c => c.funnel_stage === 'VIDEO_TESTE_ENVIADO' || c.funnel_stage === 'INTERESSE_QUENTE');
  } else if (currentTab === 'reativacao') {
    list = list.filter(c => c.funnel_stage === 'REATIVACAO');
  } else if (currentTab === 'all') {
    const q = (document.getElementById('searchInput').value || '').toLowerCase();
    const st = document.getElementById('stageSelect').value;
    if (st) list = list.filter(c => c.funnel_stage === st);
    if (q) {
      list = list.filter(c =>
        (c.username || '').toLowerCase().includes(q) ||
        (c.title || '').toLowerCase().includes(q) ||
        (c.last_message_text || '').toLowerCase().includes(q) ||
        (c.context_summary || '').toLowerCase().includes(q)
      );
    }
    list = list.slice(0, 120);
  }

  if (!list.length) {
    container.innerHTML = `<div class="lead-card">Nenhum lead encontrado nesta aba.</div>`;
    return;
  }

  container.innerHTML = list.map(c => `
    <div class="lead-card">
      <div class="lead-header">
        <div class="lead-title">
          <span class="tag urgent">${escapeHtml(c.funnel_stage)}</span>
          <h3>${escapeHtml(c.title)}</h3>
          <a class="handle" href="https://www.instagram.com/${escapeHtml(c.username)}" target="_blank">@${escapeHtml(c.username)}</a>
          ${c.whatsapp_phone ? `<a class="tag vt" href="https://wa.me/55${c.whatsapp_phone.replace(/\D/g,'')}" target="_blank">📱 WhatsApp: ${escapeHtml(c.whatsapp_phone)}</a>` : ''}
        </div>
        <span style="font-size:0.8rem; color:var(--muted);">Última msg (${c.last_sender}): ${escapeHtml(c.last_message_dt)} • ${c.msg_count} msgs</span>
      </div>
      <div class="ctx-box">
        <strong>Diagnóstico:</strong> ${escapeHtml(c.context_summary)}<br>
        <span style="color:var(--muted);">Última mensagem:</span> <em>"${escapeHtml(c.last_message_text)}"</em>
      </div>
      ${c.suggested_audio_script ? `
      <div class="script-box">
        <div class="script-title">🎙️ ROTEIRO DE ÁUDIO SUGERIDO (15s):</div>
        <div class="script-text">"${escapeHtml(c.suggested_audio_script)}"</div>
      </div>` : ''}
      ${c.suggested_text_reply ? `
      <textarea class="text-reply-area" id="txt_${c.id}">${escapeHtml(c.suggested_text_reply)}</textarea>
      ` : ''}
      <div class="card-actions">
        <button class="primary" onclick="openChatOnPhone('${escapeHtml(c.username)}')">📱 Abrir Chat no Celular (ADB)</button>
        ${c.suggested_text_reply ? `<button class="success" onclick="sendTextViaAdb(${c.id}, '${escapeHtml(c.username)}')">🤖 Disparar Texto Acima via ADB</button>` : ''}
        <button onclick="viewHistory('${escapeHtml(c.thread_folder)}', '${escapeHtml(c.title)}', '${escapeHtml(c.username)}')">💬 Ver Conversa (${c.msg_count} msgs)</button>
      </div>
    </div>
  `).join('');
}

async function openChatOnPhone(username) {
  showToast(`📱 Acordando celular e abrindo conversa de @${username} via ADB...`);
  const res = await fetch('/api/adb/open-chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username})
  });
  const data = await res.json();
  if (data.ok) {
    showToast(`✅ ${data.message}`);
    loadAllData();
  } else {
    showToast(`❌ Erro ADB: ${data.error}`);
  }
}

async function sendTextViaAdb(convId, username) {
  const el = document.getElementById('txt_' + convId);
  const text = el ? el.value.trim() : '';
  if (!text) return;
  if (!confirm(`Confirmar envio automático via ADB no celular para @${username}?\n\n"${text}"`)) return;
  showToast(`🤖 Enviando DM para @${username} via ADB no celular...`);
  const res = await fetch('/api/adb/send-dm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, text})
  });
  const data = await res.json();
  if (data.ok) {
    showToast(`✅ Mensagem enviada com sucesso para @${username}!`);
    loadAllData();
  } else {
    showToast(`❌ Falha ao enviar via ADB: ${data.error}`);
  }
}

async function sendTextViaAdbPrompt(username, defaultText) {
  const text = prompt(`Editar/Confirmar mensagem de texto para enviar via ADB para @${username}:`, defaultText);
  if (!text) return;
  showToast(`🤖 Enviando DM para @${username} via ADB...`);
  const res = await fetch('/api/adb/send-dm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, text})
  });
  const data = await res.json();
  if (data.ok) {
    showToast(`✅ Mensagem enviada para @${username}!`);
    loadAllData();
  } else {
    showToast(`❌ Erro: ${data.error}`);
  }
}

async function markAlertDone(alertId) {
  await fetch('/api/alerts/done', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: alertId})
  });
  showToast('✅ Card de áudio marcado como concluído!');
  loadAllData();
}

async function scanNotificationsNow() {
  showToast('🔔 Lendo notificações do Instagram no Android via dumpsys...');
  const res = await fetch('/api/adb/scan-notifications', {method: 'POST'});
  const data = await res.json();
  showToast(`🔔 Bandeja lida: ${data.found_in_tray || 0} notificações do Insta (${(data.new_messages || []).length} novas salvas)!`);
  loadAllData();
}

async function runBootCatchup() {
  showToast('🚀 Iniciando Boot Catch-Up: abrindo Inbox do Direct no celular e lendo conversas recentes...');
  const res = await fetch('/api/adb/boot-catchup', {method: 'POST'});
  const data = await res.json();
  showToast(`✅ Catch-Up concluído: ${data.inbox_rows_scanned || 0} conversas varridas na Inbox (${(data.new_messages_synced || []).length} novas)!`);
  loadAllData();
}

async function viewHistory(folder, title, username) {
  document.getElementById('modalTitle').textContent = `${title} (@${username})`;
  document.getElementById('modalSub').textContent = `Pasta: ${folder}`;
  document.getElementById('modalMessages').innerHTML = 'Carregando mensagens...';
  document.getElementById('chatModal').classList.add('open');

  const res = await fetch('/api/messages?folder=' + encodeURIComponent(folder));
  const msgs = await res.json();
  document.getElementById('modalMessages').innerHTML = msgs.map(m => `
    <div class="msg-bubble ${m.sender !== 'CLIENT' ? 'msg-operator' : 'msg-client'}">
      <div class="msg-meta"><strong>${escapeHtml(m.sender_raw)}</strong> • ${escapeHtml(m.dt_str)} • <span style="opacity:0.75">${m.source}</span></div>
      ${m.content ? `<div>${escapeHtml(m.content).replace(/\n/g,'<br>')}</div>` : ''}
      ${m.audio_transcription ? `<div class="audio-badge">🎙️ <strong>Áudio Transcrito (GPU):</strong> "${escapeHtml(m.audio_transcription)}"</div>` : ''}
      ${m.share_link ? `<div style="margin-top:4px;font-size:0.8rem;"><a href="${escapeHtml(m.share_link)}" target="_blank" style="color:var(--cyan);">🔗 ${escapeHtml(m.share_link)}</a></div>` : ''}
    </div>
  `).join('');
}

function closeModal() {
  document.getElementById('chatModal').classList.remove('open');
}

loadAllData();
setInterval(loadAllData, 10000);
</script>
</body>
</html>
"""


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class SDRDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/stats":
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT funnel_stage, COUNT(*) FROM conversations GROUP BY funnel_stage")
            stats = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM conversations")
            stats["total_conversations"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM messages")
            stats["total_messages"] = cur.fetchone()[0]
            conn.close()
            self._send_json({"ok": True, "stats": stats, "adb": adb_engine.get_adb_status(), "watcher": WATCHER_STATE})
            return

        if parsed.path == "/api/dashboard":
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT funnel_stage, COUNT(*) FROM conversations GROUP BY funnel_stage")
            stats = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM conversations")
            stats["total_conversations"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM messages")
            stats["total_messages"] = cur.fetchone()[0]

            cur.execute("SELECT * FROM audio_alerts_queue ORDER BY priority_score DESC, id DESC")
            alerts = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT id, thread_folder, username, title, funnel_stage, priority_score,
                       msg_count, audio_count, last_sender, last_message_text, last_message_dt,
                       whatsapp_phone, drive_links, recommended_action_type,
                       suggested_text_reply, suggested_audio_script, context_summary
                FROM conversations
                ORDER BY priority_score DESC, last_timestamp_ms DESC
                """
            )
            convs = [dict(r) for r in cur.fetchall()]
            conn.close()
            self._send_json(
                {
                    "ok": True,
                    "stats": stats,
                    "adb": adb_engine.get_adb_status(),
                    "watcher": WATCHER_STATE,
                    "audio_alerts": alerts,
                    "conversations": convs,
                }
            )
            return

        if parsed.path == "/api/messages":
            qs = urllib.parse.parse_qs(parsed.query)
            folder = (qs.get("folder") or [""])[0]
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT * FROM messages WHERE thread_folder = ? ORDER BY timestamp_ms ASC", (folder,))
            msgs = [dict(r) for r in cur.fetchall()]
            conn.close()
            self._send_json(msgs)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except Exception:
            payload = {}

        if self.path == "/api/adb/open-chat":
            res = adb_engine.adb_open_chat(payload.get("username", ""))
            self._send_json(res)
            return

        if self.path == "/api/adb/send-dm":
            res = adb_engine.adb_send_dm(payload.get("username", ""), payload.get("text", ""))
            self._send_json(res)
            return

        if self.path == "/api/adb/scan-notifications":
            res = adb_engine.scan_notifications_once()
            self._send_json(res)
            return

        if self.path == "/api/adb/boot-catchup":
            res = adb_engine.run_boot_catchup(max_scrolls=2)
            self._send_json(res)
            return

        if self.path == "/api/alerts/done":
            alert_id = payload.get("id")
            conn = get_db_conn()
            conn.execute("UPDATE audio_alerts_queue SET status = 'DONE' WHERE id = ?", (alert_id,))
            conn.commit()
            conn.close()
            self._send_json({"ok": True})
            return

        self._send_json({"error": "Not found"}, status=404)


if __name__ == "__main__":
    adb_engine.init_crm_db()
    t = threading.Thread(target=watcher_background_loop, daemon=True)
    t.start()
    server = ThreadedHTTPServer(("127.0.0.1", PORT), SDRDashboardHandler)
    print("==================================================================")
    print("🚀 INSTAGRAM SDR & ADB COMMAND CENTER RODANDO!")
    print(f"🌐 Acesse: http://127.0.0.1:{PORT}")
    print(f"📱 Celular ADB Monitorado: {adb_engine.resolve_target_serial() or 'Auto-Detect ADB'}")
    print("==================================================================")
    server.serve_forever()
