CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(120) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    senha_hash TEXT NOT NULL,
    meta_calorica_diaria NUMERIC(7,2) NOT NULL DEFAULT 2000.00,
    meta_calorica_semanal NUMERIC(8,2) NOT NULL DEFAULT 14000.00,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS refeicoes (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    foto_url TEXT,
    calorias_totais NUMERIC(7,2) NOT NULL,
    proteinas_g NUMERIC(6,2) DEFAULT 0,
    carboidratos_g NUMERIC(6,2) DEFAULT 0,
    gorduras_g NUMERIC(6,2) DEFAULT 0,
    analise_ia_json JSONB NOT NULL,
    registrado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_refeicoes_usuario_data ON refeicoes (usuario_id, registrado_em);
