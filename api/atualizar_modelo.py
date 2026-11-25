with open("/opt/liric-api/main.py", "r") as f:
    codigo = f.read()

busca_auto = '''def obter_modelo_flash():
    for m in client.models.list():
        nome = m.name.replace("models/", "")
        if "flash" in nome and "lite" not in nome and "audio" not in nome and "image" not in nome:
            return nome
    return "gemini-flash-latest"

'''

codigo = codigo.replace("client = genai.Client()", "client = genai.Client()\n\n" + busca_auto)
codigo = codigo.replace('model="gemini-3.0-flash"', 'model=obter_modelo_flash()')

with open("/opt/liric-api/main.py", "w") as f:
    f.write(codigo)
