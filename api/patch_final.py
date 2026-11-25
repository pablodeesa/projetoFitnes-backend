with open("/opt/liric-api/main.py", "r") as f:
    code = f.read()

# 1. Libera tanto Câmera quanto Galeria em qualquer Android/iPhone
code = code.replace('accept="image/*" capture="environment"', 'accept="image/*"')

# 2. Adiciona retentativa automática (até 3x) se o modelo der 503 High Demand
old_call = '''        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[
                types.Part.from_bytes(data=imagem_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )'''

new_call = '''        import time
        response = None
        for tentativa in range(3):
            try:
                response = client.models.generate_content(
                    model="gemini-3.8-flash",
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
            except Exception as err_ia:
                if "503" in str(err_ia) and tentativa < 2:
                    time.sleep(2)
                    continue
                raise err_ia'''

code = code.replace(old_call, new_call)

with open("/opt/liric-api/main.py", "w") as f:
    f.write(code)
