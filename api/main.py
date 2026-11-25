import os
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from pwdlib import PasswordHash
import jwt
import psycopg
from psycopg.types.json import Jsonb
from google import genai
from google.genai import types
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Carrega variáveis do .env
if os.path.exists("/opt/liric-api/.env"):
    with open("/opt/liric-api/.env") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v.strip('"').strip("'")

app = FastAPI(title="Liric API - Blindada (Argon2 + JWT + Vision)", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_URL = "postgresql://liric_admin:Liric%402026Db%21@127.0.0.1:5432/liric_db"
JWT_SECRET = os.environ.get("JWT_SECRET", "liric-fallback-secret-key-2026")
JWT_ALGORITHM = "HS256"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
JWT_EXPIRA_HORAS = 24 * 7

password_hash = PasswordHash.recommended()
security = HTTPBearer()
client = genai.Client()

class NovoUsuario(BaseModel):
    nome: str
    email: str
    senha: str
    meta_diaria: float = 2000.0
    meta_semanal: float = 14000.0

class LoginRequest(BaseModel):
    email: str
    senha: str

class GoogleLoginRequest(BaseModel):
    credential: str

class NovaRefeicao(BaseModel):
    calorias_totais: float
    proteinas_g: float = 0
    carboidratos_g: float = 0
    gorduras_g: float = 0
    itens_ia: list = [{"item": "Prato manual", "kcal": 500}]

def gerar_token_jwt(usuario_id: int, email: str) -> str:
    payload = {
        "sub": str(usuario_id),
        "email": email.decode("utf-8") if isinstance(email, bytes) else str(email),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRA_HORAS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def obter_usuario_autenticado(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        usuario_id = int(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão expirada. Faça login novamente.")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de autenticação inválido.")

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, nome, email, meta_calorica_semanal, status_assinatura, trial_expira_em, assinatura_expira_em
                FROM usuarios WHERE id = %s;
                """,
                (usuario_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado.")

    agora = datetime.now(timezone.utc)
    status_sub = row[4] or "trial"
    trial_exp = row[5]
    ass_exp = row[6]

    acesso_liberado = False
    if status_sub == "ativa" and (ass_exp is None or ass_exp > agora):
        acesso_liberado = True
    elif status_sub == "trial" and trial_exp is not None and trial_exp > agora:
        acesso_liberado = True

    return {
        "id": row[0],
        "nome": row[1],
        "email": row[2],
        "meta_semanal": float(row[3]),
        "status_assinatura": status_sub,
        "trial_expira_em": trial_exp.isoformat() if trial_exp else None,
        "acesso_liberado": acesso_liberado,
    }

def exigir_assinatura_ativa(user: dict = Depends(obter_usuario_autenticado)) -> dict:
    if not user["acesso_liberado"]:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Seu período de degustação de 7 dias encerrou. Assine o plano mensal de R$ 16,90 para continuar."
        )
    return user

@app.get("/")
def health_check():
    return {"status": "online", "app": "Liric API v3 (Argon2 + JWT + Vision)"}

@app.post("/auth/registro")
@app.post("/usuarios")
def criar_usuario(dados: NovoUsuario):
    email_limpo = dados.email.strip().lower()
    if len(dados.senha) < 6:
        raise HTTPException(status_code=400, detail="A senha deve ter no mínimo 6 caracteres.")
    senha_criptografada = password_hash.hash(dados.senha)
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO usuarios (nome, email, senha_hash, meta_calorica_diaria, meta_calorica_semanal, status_assinatura, trial_expira_em)
                    VALUES (%s, %s, %s, %s, %s, 'trial', CURRENT_TIMESTAMP + INTERVAL '7 days')
                    RETURNING id, nome, email, meta_calorica_semanal, trial_expira_em;
                    """,
                    (dados.nome.strip(), email_limpo, senha_criptografada, dados.meta_diaria, dados.meta_semanal)
                )
                user = cur.fetchone()
                conn.commit()
        token = gerar_token_jwt(user[0], user[2])
        return {
            "id": user[0],
            "nome": user[1],
            "email": user[2],
            "meta_semanal": float(user[3]),
            "trial_expira_em": str(user[4]),
            "access_token": token,
            "token_type": "bearer"
        }
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=400, detail="Este e-mail já está cadastrado.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/auth/login")
def login_usuario(dados: LoginRequest):
    email_limpo = dados.email.strip().lower()
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nome, email, senha_hash FROM usuarios WHERE email = %s;", (email_limpo,))
            user = cur.fetchone()

    if not user or not user[3] or not user[3].startswith("$argon2"):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")

    if not password_hash.verify(dados.senha, user[3]):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")

    token = gerar_token_jwt(user[0], user[2])
    return {
        "id": user[0],
        "nome": user[1],
        "email": user[2],
        "access_token": token,
        "token_type": "bearer"
    }

@app.post("/auth/google")
def login_google(dados: GoogleLoginRequest):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID ausente no .env")
    try:
        info = id_token.verify_oauth2_token(dados.credential, google_requests.Request(), GOOGLE_CLIENT_ID)
    except Exception:
        raise HTTPException(status_code=401, detail="Token Google invalido")
    gsub, email = info["sub"], info["email"].strip().lower()
    nome = info.get("name") or email.split("@")[0]
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nome, email, google_id FROM usuarios WHERE google_id = %s OR email = %s;", (gsub, email))
            u = cur.fetchone()
            if u:
                if not u[3]:
                    cur.execute("UPDATE usuarios SET google_id = %s WHERE id = %s;", (gsub, u[0]))
                    conn.commit()
                uid, unome, uemail = u[0], u[1], u[2]
            else:
                sh = password_hash.hash(os.urandom(24).hex())
                cur.execute("INSERT INTO usuarios (nome, email, senha_hash, google_id, meta_calorica_diaria, meta_calorica_semanal, status_assinatura, trial_expira_em) VALUES (%s, %s, %s, %s, 2000.0, 14000.0, 'trial', CURRENT_TIMESTAMP + INTERVAL '7 days') RETURNING id, nome, email;", (nome, email, sh, gsub))
                n = cur.fetchone()
                conn.commit()
                uid, unome, uemail = n[0], n[1], n[2]
    return {"id": uid, "nome": unome, "email": uemail, "access_token": gerar_token_jwt(uid, uemail), "token_type": "bearer"}

@app.post("/refeicoes")
def registrar_refeicao(dados: NovaRefeicao, user: dict = Depends(exigir_assinatura_ativa)):
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO refeicoes (usuario_id, calorias_totais, proteinas_g, carboidratos_g, gorduras_g, analise_ia_json)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, registrado_em;
                    """,
                    (user["id"], dados.calorias_totais, dados.proteinas_g, dados.carboidratos_g, dados.gorduras_g, Jsonb(dados.itens_ia))
                )
                ref = cur.fetchone()
                conn.commit()
        return {"refeicao_id": ref[0], "registrado_em": str(ref[1])}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/refeicoes/analisar-foto")
async def analisar_foto_refeicao(
    foto: UploadFile = File(...),
    user: dict = Depends(exigir_assinatura_ativa)
):
    try:
        imagem_bytes = await foto.read()
        mime_type = foto.content_type or "image/jpeg"

        prompt = (
            "Você é um nutricionista especialista em visão computacional. "
            "Analise esta foto de refeição, identifique cada alimento visível, estime o peso aproximado em gramas "
            "e calcule as calorias totais, proteínas (g), carboidratos (g) e gorduras (g). "
            "Responda ESTRITAMENTE em JSON válido neste formato exato, sem markdown:\n"
            '{"calorias_totais": 0, "proteinas_g": 0, "carboidratos_g": 0, "gorduras_g": 0, '
            '"itens": [{"item": "nome do alimento", "gramas": 0, "kcal": 0}]}'
        )

        modelos_disponiveis = ["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-flash-latest"]
        response = None
        ultimo_erro = None

        for modelo in modelos_disponiveis:
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=[
                        types.Part.from_bytes(data=imagem_bytes, mime_type=mime_type),
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                break
            except Exception as err:
                ultimo_erro = err
                continue

        if response is None:
            raise ultimo_erro

        resultado = json.loads(response.text)
        kcal = float(resultado.get("calorias_totais", 0))
        prot = float(resultado.get("proteinas_g", 0))
        carb = float(resultado.get("carboidratos_g", 0))
        gord = float(resultado.get("gorduras_g", 0))
        itens = resultado.get("itens", [])

        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO refeicoes (usuario_id, calorias_totais, proteinas_g, carboidratos_g, gorduras_g, analise_ia_json)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, registrado_em;
                    """,
                    (user["id"], kcal, prot, carb, gord, Jsonb(itens))
                )
                ref = cur.fetchone()
                conn.commit()

        return {
            "refeicao_id": ref[0],
            "calorias_totais": kcal,
            "proteinas_g": prot,
            "carboidratos_g": carb,
            "gorduras_g": gord,
            "itens": itens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na análise da IA: {str(e)}")

@app.get("/usuarios/me/resumo-semanal")
def resumo_semanal(user: dict = Depends(obter_usuario_autenticado)):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(calorias_totais), 0),
                       COALESCE(SUM(proteinas_g), 0),
                       COALESCE(SUM(carboidratos_g), 0),
                       COALESCE(SUM(gorduras_g), 0),
                       COUNT(id)
                FROM refeicoes
                WHERE usuario_id = %s
                  AND registrado_em >= date_trunc('week', CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo');
                """,
                (user["id"],)
            )
            soma_kcal, soma_prot, soma_carb, soma_gord, total_refeicoes = cur.fetchone()
    meta = user["meta_semanal"]
    consumido = float(soma_kcal)
    return {
        "usuario": user["nome"],
        "status_assinatura": user["status_assinatura"],
        "acesso_liberado": user["acesso_liberado"],
        "meta_semanal_kcal": meta,
        "consumido_semana_kcal": consumido,
        "saldo_restante_kcal": meta - consumido,
        "macros_semana": {
            "proteinas_g": float(soma_prot),
            "carboidratos_g": float(soma_carb),
            "gorduras_g": float(soma_gord)
        },
        "refeicoes_na_semana": total_refeicoes
    }

@app.get("/app", response_class=HTMLResponse)
def interface_visual():
    html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Liric AI — Contabilidade Calórica Semanal</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen font-sans p-4 md:p-8">
  <div class="max-w-xl mx-auto space-y-6">
    <header class="flex items-center justify-between border-b border-slate-800 pb-4">
      <div>
        <h1 class="text-2xl font-bold text-emerald-400">LIRIC AI</h1>
        <p class="text-xs text-slate-400">Visão Nutricional & Contabilidade Semanal</p>
      </div>
      <div class="flex items-center gap-2">
        <span id="badge-status" class="px-3 py-1 text-xs rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">7 Dias Grátis</span>
        <button id="btn-sair" onclick="sair()" class="hidden text-xs text-slate-400 hover:text-white underline">Sair</button>
      </div>
    </header>

    <!-- Tela de Login / Cadastro -->
    <section id="tela-auth" class="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
      <div class="flex gap-2 border-b border-slate-800 pb-3">
        <button id="tab-login" onclick="alternarModo('login')" class="flex-1 py-2 rounded-lg text-sm font-semibold bg-emerald-500 text-slate-950">Entrar</button>
        <button id="tab-cadastro" onclick="alternarModo('cadastro')" class="flex-1 py-2 rounded-lg text-sm font-semibold bg-slate-800 text-slate-300">Criar Conta (7 Dias Grátis)</button>
      </div>

      <div id="campo-nome" class="hidden">
        <label class="text-xs text-slate-400">Seu Nome</label>
        <input id="auth-nome" type="text" placeholder="Ex: Pablo" class="w-full mt-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm">
      </div>
      <div>
        <label class="text-xs text-slate-400">E-mail</label>
        <input id="auth-email" type="email" placeholder="seu@email.com" class="w-full mt-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm">
      </div>
      <div>
        <label class="text-xs text-slate-400">Senha (mín. 6 caracteres)</label>
        <input id="auth-senha" type="password" placeholder="••••••" class="w-full mt-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm">
      </div>
<button id="btn-auth" onclick="enviarAuth()" class="w-full bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-semibold py-2.5 rounded-xl transition">Entrar na Conta</button>
      <div id="g_id_onload" data-client_id="__GID__" data-context="signin" data-ux_mode="redirect" data-login_uri="https://remained-various-doctor-estimate.trycloudflare.com/auth/google/redirect" data-callback="handleGoogleCredential" data-auto_prompt="false"></div>
      <div class="flex justify-center pt-2"><div class="g_id_signin" data-type="standard" data-shape="rectangular" data-theme="filled_black" data-text="continue_with" data-size="large"></div></div>
      <p class="text-[11px] text-center text-slate-400">Experimente 7 dias grátis. Depois apenas R$ 16,90/ms.</p>
    </section>

    <!-- Dashboard Logado -->
    <div id="tela-app" class="hidden space-y-6">
      <section class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-3">
        <div class="flex justify-between items-end">
          <span id="nome-usuario" class="text-sm text-slate-400">Saldo da Semana</span>
          <span id="saldo-txt" class="text-xl font-bold text-white">-- kcal</span>
        </div>
        <div class="w-full bg-slate-800 rounded-full h-3 overflow-hidden">
          <div id="barra-progresso" class="bg-emerald-500 h-3 transition-all duration-500" style="width: 0%"></div>
        </div>
        <div class="flex justify-between text-xs text-slate-400">
          <span id="consumido-txt">Consumido: 0 kcal</span>
          <span id="meta-txt">Meta: 14000 kcal</span>
        </div>
        <div class="grid grid-cols-3 gap-2 pt-2 border-t border-slate-800 text-center text-xs">
          <div class="bg-slate-950/60 p-2 rounded-lg"><span class="text-slate-400 block">Proteínas</span><strong id="prot-sem" class="text-emerald-400">0g</strong></div>
          <div class="bg-slate-950/60 p-2 rounded-lg"><span class="text-slate-400 block">Carbos</span><strong id="carb-sem" class="text-sky-400">0g</strong></div>
          <div class="bg-slate-950/60 p-2 rounded-lg"><span class="text-slate-400 block">Gorduras</span><strong id="gord-sem" class="text-amber-400">0g</strong></div>
        </div>
      </section>

      <section class="bg-slate-900 border border-slate-800 rounded-2xl p-5 space-y-4">
        <h2 class="text-base font-semibold text-slate-200">Analisar Prato por Foto (IA)</h2>
        <div class="grid grid-cols-2 gap-3">
          <label class="flex flex-col items-center justify-center h-32 border-2 border-dashed border-slate-700 hover:border-emerald-500 rounded-xl cursor-pointer bg-slate-950/50 transition p-2 text-center">
            <span class="text-sm text-emerald-400 font-semibold">Tirar Foto</span>
            <span class="text-[11px] text-slate-400 mt-1">Abrir câmera agora</span>
            <input id="foto-camera" type="file" accept="image/*" capture="environment" class="hidden" onchange="selecionarOrigem(this)">
          </label>
          <label class="flex flex-col items-center justify-center h-32 border-2 border-dashed border-slate-700 hover:border-sky-500 rounded-xl cursor-pointer bg-slate-950/50 transition p-2 text-center">
            <span class="text-sm text-sky-400 font-semibold">Galeria</span>
            <span class="text-[11px] text-slate-400 mt-1">Escolher foto salva</span>
            <input id="foto-galeria" type="file" accept="image/*" class="hidden" onchange="selecionarOrigem(this)">
          </label>
        </div>
        <p id="nome-arquivo" class="text-xs text-center text-slate-400">Nenhuma foto selecionada</p>

        <button id="btn-enviar" onclick="enviarFotoIA()" class="w-full bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-semibold py-3 rounded-xl transition">
          Calcular Calorias com IA
        </button>

        <div id="resultado-ia" class="hidden bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-2 text-sm">
          <p class="font-semibold text-emerald-400" id="resumo-prato"></p>
          <ul id="lista-itens" class="text-xs text-slate-300 space-y-1 list-disc pl-4"></ul>
        </div>
      </section>
    </div>
  </div>

  <script>
    let modoAuth = 'login';
    let arquivoAtual = null;

    function alternarModo(modo) {
      modoAuth = modo;
      document.getElementById('campo-nome').classList.toggle('hidden', modo === 'login');
      document.getElementById('tab-login').className = modo === 'login'
        ? 'flex-1 py-2 rounded-lg text-sm font-semibold bg-emerald-500 text-slate-950'
        : 'flex-1 py-2 rounded-lg text-sm font-semibold bg-slate-800 text-slate-300';
      document.getElementById('tab-cadastro').className = modo === 'cadastro'
        ? 'flex-1 py-2 rounded-lg text-sm font-semibold bg-emerald-500 text-slate-950'
        : 'flex-1 py-2 rounded-lg text-sm font-semibold bg-slate-800 text-slate-300';
      document.getElementById('btn-auth').innerText = modo === 'login' ? 'Entrar na Conta' : 'Criar Conta Grátis (7 Dias)';
    }

async function handleGoogleCredential(r) {
      const res = await fetch("/auth/google", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({credential: r.credential})});
      const d = await res.json();
      if (!res.ok) { alert(d.detail || "Erro Google"); return; }
      localStorage.setItem("liric_token", d.access_token);
      window.location.reload();
    }
    async function enviarAuth() {
      const nome = document.getElementById('auth-nome').value;
      const email = document.getElementById('auth-email').value;
      const senha = document.getElementById('auth-senha').value;
      const rota = modoAuth === 'login' ? '/auth/login' : '/auth/registro';
      const corpo = modoAuth === 'login' ? { email, senha } : { nome, email, senha };

      const res = await fetch(rota, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(corpo)
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || 'Erro na autenticação');
        return;
      }
      localStorage.setItem('liric_token', data.access_token);
      window.location.reload();
    }

    function sair() {
      localStorage.removeItem('liric_token');
      window.location.reload();
    }

    async function verificarSessao() {
      const token = localStorage.getItem('liric_token');
      if (!token) {
        document.getElementById('tela-auth').classList.remove('hidden');
        document.getElementById('tela-app').classList.add('hidden');
        document.getElementById('btn-sair').classList.add('hidden');
        return;
      }
      const res = await fetch('/usuarios/me/resumo-semanal', {
        headers: { 'Authorization': 'Bearer ' + token }
      });
      if (!res.ok) {
        sair();
        return;
      }
      const d = await res.json();
      document.getElementById('tela-auth').classList.add('hidden');
      document.getElementById('tela-app').classList.remove('hidden');
      document.getElementById('btn-sair').classList.remove('hidden');
      document.getElementById('nome-usuario').innerText = 'Olá, ' + d.usuario;
      document.getElementById('badge-status').innerText = d.status_assinatura === 'ativa' ? 'Plano Pro R$ 16,90' : 'Trial 7 Dias';
      document.getElementById('saldo-txt').innerText = d.saldo_restante_kcal.toFixed(0) + ' kcal restantes';
      document.getElementById('consumido-txt').innerText = 'Consumido: ' + d.consumido_semana_kcal.toFixed(0) + ' kcal';
      document.getElementById('meta-txt').innerText = 'Meta: ' + d.meta_semanal_kcal.toFixed(0) + ' kcal';
      document.getElementById('prot-sem').innerText = d.macros_semana.proteinas_g.toFixed(0) + 'g';
      document.getElementById('carb-sem').innerText = d.macros_semana.carboidratos_g.toFixed(0) + 'g';
      document.getElementById('gord-sem').innerText = d.macros_semana.gorduras_g.toFixed(0) + 'g';
      const pct = Math.min(100, (d.consumido_semana_kcal / d.meta_semanal_kcal) * 100);
      document.getElementById('barra-progresso').style.width = pct + '%';
    }

    function selecionarOrigem(input) {
      if (input.files && input.files.length > 0) {
        arquivoAtual = input.files[0];
        document.getElementById('nome-arquivo').innerText = 'Selecionado: ' + arquivoAtual.name;
      }
    }

    async function enviarFotoIA() {
      const token = localStorage.getItem('liric_token');
      if (!arquivoAtual) {
        alert('Selecione ou tire uma foto do prato primeiro.');
        return;
      }
      const btn = document.getElementById('btn-enviar');
      btn.disabled = true;
      btn.innerText = 'Analisando alimento com IA...';

      const formData = new FormData();
      formData.append('foto', arquivoAtual);

      try {
        const res = await fetch('/refeicoes/analisar-foto', {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + token },
          body: formData
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Erro ao analisar foto');

        document.getElementById('resumo-prato').innerText =
          `Total: ${data.calorias_totais} kcal (P: ${data.proteinas_g}g | C: ${data.carboidratos_g}g | G: ${data.gorduras_g}g)`;
        const ul = document.getElementById('lista-itens');
        ul.innerHTML = '';
        data.itens.forEach(i => {
          const li = document.createElement('li');
          li.innerText = `${i.item} (${i.gramas}g) — ${i.kcal} kcal`;
          ul.appendChild(li);
        });
        document.getElementById('resultado-ia').classList.remove('hidden');
        verificarSessao();
      } catch (err) {
        alert('Falha: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.innerText = 'Calcular Calorias com IA';
      }
    }

    verificarSessao();
  </script>
</body>
</html>
"""
    return html.replace("__GID__", GOOGLE_CLIENT_ID)
