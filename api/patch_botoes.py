with open("/opt/liric-api/main.py", "r") as f:
    code = f.read()

bloco_antigo = '''      <label class="flex flex-col items-center justify-center w-full h-36 border-2 border-dashed border-slate-700 hover:border-emerald-500 rounded-xl cursor-pointer bg-slate-950/50 transition">
        <div class="text-center px-4">
          <p class="text-sm text-emerald-400 font-medium">Tirar foto ou escolher da galeria</p>
          <p id="nome-arquivo" class="text-xs text-slate-400 mt-1">JPG, PNG ou WEBP</p>
        </div>
        <input id="foto-input" type="file" accept="image/*" class="hidden" onchange="mostrarArquivo()">
      </label>'''

bloco_novo = '''      <div class="grid grid-cols-2 gap-3">
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
      <p id="nome-arquivo" class="text-xs text-center text-slate-400">Nenhuma foto selecionada (JPG, PNG ou WEBP)</p>'''

if bloco_antigo in code:
    code = code.replace(bloco_antigo, bloco_novo)
else:
    # Caso o bloco tenha vindo com accept="image/*" capture="environment"
    code = code.replace(
        '<input id="foto-input" type="file" accept="image/*" class="hidden" onchange="mostrarArquivo()">',
        '<input id="foto-camera" type="file" accept="image/*" capture="environment" class="hidden" onchange="selecionarOrigem(this)"><input id="foto-galeria" type="file" accept="image/*" class="hidden" onchange="selecionarOrigem(this)">'
    )

js_antigo = '''    function mostrarArquivo() {
      const input = document.getElementById('foto-input');
      if (input.files.length > 0) {
        document.getElementById('nome-arquivo').innerText = 'Selecionado: ' + input.files[0].name;
      }
    }'''

js_novo = '''    let arquivoAtual = null;
    function selecionarOrigem(input) {
      if (input.files && input.files.length > 0) {
        arquivoAtual = input.files[0];
        document.getElementById('nome-arquivo').innerText = 'Selecionado: ' + arquivoAtual.name;
      }
    }'''

code = code.replace(js_antigo, js_novo)
code = code.replace("const input = document.getElementById('foto-input');", "const input = { files: arquivoAtual ? [arquivoAtual] : [] };")

with open("/opt/liric-api/main.py", "w") as f:
    f.write(code)
