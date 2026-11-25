with open("/opt/liric-api/main.py", "r") as f:
    code = f.read()

# Garante seleção de Câmera ou Galeria em qualquer telefone
code = code.replace('accept="image/*" capture="environment"', 'accept="image/*"')

# Substitui a chamada única por tentativa em múltiplos modelos Flash
inicio = code.find("        imagem_bytes = await foto.read()")
fim = code.find("        resultado = json.loads(response.text)")

novo_bloco = '''        imagem_bytes = await foto.read()
        mime_type = foto.content_type or "image/jpeg"

        prompt = (
            "Você  um nutricionista especialista em visão computacional. "
            "Analise esta foto de refeição, identifique cada alimento visível, estime o peso aproximado em gramas "
            "e calcule as calorias totais, proteínas (g), carboidratos (g) e gorduras (g). "
            "Responda ESTRITAMENTE em JSON válido neste formato exato, sem markdown:\\n"
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

'''

if inicio != -1 and fim != -1:
    code = code[:inicio] + novo_bloco + code[fim:]

with open("/opt/liric-api/main.py", "w") as f:
    f.write(code)
