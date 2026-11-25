import os
import json
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import psycopg
from psycopg.types.json import Jsonb
from google import genai
from google.genai import types

if os.path.exists("/opt/liric-api/.env"):
    with open("/opt/liric-api/.env") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v.strip('"').strip("'")

app = FastAPI(title="Liric API - Visão Nutricional", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_URL = "postgresql://liric_admin:Liric@2026Db!@127.0.0.1:5432/liric_db"
client = genai.Client()

def obter_modelo_flash():
    for m in client.models.list():
        nome = m.name.replace("models/", "")
        if "flash" in nome and "lite" not in nome and "audio" not in nome and "image" not in nome:
            return nome
    return "gemini-flash-latest"



class NovoUsuario(BaseModel):
    nome: str
    email: str
    senha: str
    meta_diaria: float = 2000.0
    meta_semanal: float = 14000.0

class NovaRefeicao(BaseModel):
    usuario_id: int
    calorias_totais: float
    proteinas_g: float = 0
    carboidratos_g: float = 0
    gorduras_g: float = 0
    itens_ia: list = [{"item": "Prato manual", "kcal": 500}]

@app.get("/")
def health_check():
    return {"status": "online", "app": "Liric API v2 (Gemini Vision)"}

@app.post("/usuarios")
def criar_usuario(dados: NovoUsuario):
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO usuarios (nome, email, senha_hash, meta_calorica_diaria, meta_calorica_semanal)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id, nome, meta_calorica_semanal;
                    """,
                    (dados.nome, dados.email, dados.senha, dados.meta_diaria, dados.meta_semanal)
                )
                user = cur.fetchone()
                conn.commit()
        return {"id": user[0], "nome": user[1], "meta_semanal": float(user[2])}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/refeicoes")
def registrar_refeicao(dados: NovaRefeicao):
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO refeicoes (usuario_id, calorias_totais, proteinas_g, carboidratos_g, gorduras_g, analise_ia_json)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, registrado_em;
                    """,
                    (dados.usuario_id, dados.calorias_totais, dados.proteinas_g, dados.carboidratos_g, dados.gorduras_g, Jsonb(dados.itens_ia))
                )
                ref = cur.fetchone()
                conn.commit()
        return {"refeicao_id": ref[0], "registrado_em": str(ref[1])}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/refeicoes/analisar-foto")
async def analisar_foto_refeicao(
    usuario_id: int = Form(1),
    foto: UploadFile = File(...)
):
    try:
        imagem_bytes = await foto.read()
        mime_type = foto.content_type or "image/jpeg"

        prompt = (
            "Você  um nutricionista especialista em visão computacional. "
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
                    (usuario_id, kcal, prot, carb, gord, Jsonb(itens))
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

@app.get("/usuarios/{usuario_id}/resumo-semanal")
def resumo_semanal(usuario_id: int):
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT nome, meta_calorica_semanal FROM usuarios WHERE id = %s;", (usuario_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="Usuário não encontrado")
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
                (usuario_id,)
            )
            soma_kcal, soma_prot, soma_carb, soma_gord, total_refeicoes = cur.fetchone()
    meta = float(user[1])
    consumido = float(soma_kcal)
    return {
        "usuario": user[0],
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
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Liric AI — Contabilidade Calórica Semanal</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen font-sans p-4 md:p-8">
  <div class="max-w-xl mx-auto space-y-6">
    <header class="flex items-center justify-between border-b border-slate-800 pb-4">
      <div>
        <h1 class="text-2xl font-bold text-emerald-400">LIRIC AI</h1>
        <p class="text-xs text-slate-400">Visão Nutricional & Contabilidade Semanal</p>
      </div>
      <span class="px-3 py-1 text-xs rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Gemini Vision Ativo</span>
    </header>

    <section class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-3">
      <div class="flex justify-between items-end">
        <span class="text-sm text-slate-400">Saldo da Semana (Usuário #1)</span>
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
      <p id="nome-arquivo" class="text-xs text-center text-slate-400">Nenhuma foto selecionada (JPG, PNG ou WEBP)</p>

      <button id="btn-enviar" onclick="enviarFotoIA()" class="w-full bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-semibold py-3 rounded-xl transition">
        Calcular Calorias com IA
      </button>

      <div id="resultado-ia" class="hidden bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-2 text-sm">
        <p class="font-semibold text-emerald-400" id="resumo-prato"></p>
        <ul id="lista-itens" class="text-xs text-slate-300 space-y-1 list-disc pl-4"></ul>
      </div>
    </section>
  </div>

  <script>
    async function inicializar() {
      await fetch('/usuarios', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({nome: 'Pablo Teste', email: 'pablo@liric.app', senha: '123'})
      }).catch(() => {});
      atualizarResumo();
    }

    let arquivoAtual = null;
    function selecionarOrigem(input) {
      if (input.files && input.files.length > 0) {
        arquivoAtual = input.files[0];
        document.getElementById('nome-arquivo').innerText = 'Selecionado: ' + arquivoAtual.name;
      }
    }

    async function atualizarResumo() {
      const res = await fetch('/usuarios/1/resumo-semanal');
      if (!res.ok) return;
      const d = await res.json();
      document.getElementById('saldo-txt').innerText = d.saldo_restante_kcal.toFixed(0) + ' kcal restantes';
      document.getElementById('consumido-txt').innerText = 'Consumido: ' + d.consumido_semana_kcal.toFixed(0) + ' kcal';
      document.getElementById('meta-txt').innerText = 'Meta: ' + d.meta_semanal_kcal.toFixed(0) + ' kcal';
      document.getElementById('prot-sem').innerText = d.macros_semana.proteinas_g.toFixed(0) + 'g';
      document.getElementById('carb-sem').innerText = d.macros_semana.carboidratos_g.toFixed(0) + 'g';
      document.getElementById('gord-sem').innerText = d.macros_semana.gorduras_g.toFixed(0) + 'g';
      const pct = Math.min(100, (d.consumido_semana_kcal / d.meta_semanal_kcal) * 100);
      document.getElementById('barra-progresso').style.width = pct + '%';
    }

    async function enviarFotoIA() {
      const input = { files: arquivoAtual ? [arquivoAtual] : [] };
      if (!input.files.length) {
        alert('Selecione ou tire uma foto do prato primeiro.');
        return;
      }
      const btn = document.getElementById('btn-enviar');
      btn.disabled = true;
      btn.innerText = 'Analisando alimento com IA...';

      const formData = new FormData();
      formData.append('usuario_id', '1');
      formData.append('foto', input.files[0]);

      try {
        const res = await fetch('/refeicoes/analisar-foto', { method: 'POST', body: formData });
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
        atualizarResumo();
      } catch (err) {
        alert('Falha: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.innerText = 'Calcular Calorias com IA';
      }
    }
    inicializar();
  </script>
</body>
</html>
"""
