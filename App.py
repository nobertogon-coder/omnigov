import os
import csv
import io
import hashlib
from datetime import datetime, date
import uvicorn
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel, Field

# ==============================================================================
# 1. BANCO DE DADOS E PERSISTÊNCIA ROBUSTA (Anti-Sono do Render)
# =SOURCE: SQLite Local / Persistente
# ==============================================================================
DB_FILE = "./omnigov_enterprise.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_FILE}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UsuarioDB(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String)
    matricula = Column(String, unique=True, index=True)
    senha = Column(String)  # Senha simples para demonstração/piloto
    perfil = Column(String)  # "ADMIN" ou "FISCAL"
    secretaria_atribuida = Column(String, default="TODas")
    ativo = Column(Boolean, default=True)

class ContratoDB(Base):
    __tablename__ = "contratos"
    id = Column(Integer, primary_key=True, index=True)
    secretaria = Column(String, index=True)
    processo_sei = Column(String, index=True)
    numero_contrato = Column(String)
    competencia = Column(String)
    empresa = Column(String)
    cnpj = Column(String)
    
    valor_bruto = Column(Float)
    aliquota_inss = Column(Float, default=11.0)
    aliquota_iss = Column(Float, default=5.0)
    aliquota_irrf = Column(Float, default=0.0)
    retencao_inss = Column(Float)
    retencao_iss = Column(Float)
    retencao_irrf = Column(Float)
    valor_liquido = Column(Float)
    
    # 4 Portões do Art. 121
    cnd_regular = Column(Boolean, default=True)
    cnd_validade = Column(String)
    fgts_regular = Column(Boolean, default=True)
    fgts_validade = Column(String)
    folha_regular = Column(Boolean, default=True)
    beneficios_regular = Column(Boolean, default=True)
    
    doc_nome = Column(String)
    doc_hash_arquivo = Column(String, nullable=True)
    justificativa = Column(String, nullable=True)
    status = Column(String)  # PENDENTE, ATESTADO, GLOSADO, ARQUIVADO
    
    fiscal_nome = Column(String)
    fiscal_matricula = Column(String)
    hash_sha256 = Column(String, nullable=True, index=True)
    data_atesto = Column(String, nullable=True)
    ativo = Column(Boolean, default=True)

class AuditoriaDB(Base):
    __tablename__ = "auditoria"
    id = Column(Integer, primary_key=True, index=True)
    data_hora = Column(String)
    usuario_nome = Column(String)
    usuario_matricula = Column(String)
    perfil = Column(String)
    secretaria = Column(String)
    processo = Column(String)
    acao = Column(String)
    detalhes = Column(String)

Base.metadata.create_all(bind=engine)

# ==============================================================================
# 2. POPULAR DADOS INICIAIS E USUÁRIO ADMINISTRADOR PEDIDO
# ==============================================================================
def popular_dados_iniciais():
    db = SessionLocal()
    # Cria o Administrador padrão solicitado: AdmOmniGov / GovOmniAdm
    admin_existente = db.query(UsuarioDB).filter(UsuarioDB.matricula == "AdmOmniGov").first()
    if not admin_existente:
        adm = UsuarioDB(
            nome="Controlador Geral (Admin)",
            matricula="AdmOmniGov",
            senha="GovOmniAdm",
            perfil="ADMIN",
            secretaria_atribuida="TODAS",
            ativo=True
        )
        db.add(adm)
        db.commit()

    if db.query(ContratoDB).count() == 0:
        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        c1 = ContratoDB(
            secretaria="Secretaria de Saúde",
            processo_sei="SEI-040003.000182/2026",
            numero_contrato="CT-019/2026",
            competencia="10/2026",
            empresa="MEDCARE SERVIÇOS HOSPITALARES LTDA",
            cnpj="14.882.910/0001-43",
            valor_bruto=125000.00,
            aliquota_inss=11.0, aliquota_iss=5.0, aliquota_irrf=0.0,
            retencao_inss=13750.00, retencao_iss=6250.00, retencao_irrf=0.00,
            valor_liquido=105000.00,
            cnd_regular=True, cnd_validade="2026-12-31",
            fgts_regular=True, fgts_validade="2026-11-30",
            folha_regular=True, beneficios_regular=True,
            doc_nome="Dossie_Certidoes_Medcare_Out2026.pdf",
            doc_hash_arquivo="E3B0C44298FC1C149AFBF4C8996FB924",
            justificativa="Conferência integral dos 4 portões do Art. 121. Regularidade validada.",
            status="ATESTADO",
            fiscal_nome="Carlos Eduardo", fiscal_matricula="MAT-44910",
            hash_sha256="OMNIGOV-SHA256-9F86D081884C7D659A2FEAA0C55AD015",
            data_atesto=agora, ativo=True
        )
        db.add(c1)
        db.commit()
    db.close()

popular_dados_iniciais()

# ==============================================================================
# 3. CAMADA DE API (FastAPI)
# ==============================================================================
app = FastAPI(title="OmniGov Enterprise", version="6.0.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verificar_data_vencida(data_str: str) -> bool:
    if not data_str:
        return True
    try:
        dt_val = datetime.strptime(data_str, "%Y-%m-%d").date()
        return dt_val < date.today()
    except Exception:
        return False

# Pydantic Schemas
class LoginInput(BaseModel):
    matricula: str
    senha: str

class UsuarioCreate(BaseModel):
    nome: str
    matricula: str
    senha: str
    perfil: str # ADMIN ou FISCAL
    secretaria_atribuida: str

class ContratoInput(BaseModel):
    secretaria: str
    processo_sei: str
    numero_contrato: str
    competencia: str
    empresa: str
    cnpj: str
    valor_bruto: float = Field(..., gt=0)
    aliquota_inss: float = 11.0
    aliquota_iss: float = 5.0
    aliquota_irrf: float = 0.0
    cnd_regular: bool
    cnd_validade: str
    fgts_regular: bool
    fgts_validade: str
    folha_regular: bool
    beneficios_regular: bool
    doc_nome: str
    doc_hash_arquivo: str = ""
    fiscal_nome: str
    fiscal_matricula: str

class AuditarInput(BaseModel):
    cnd_regular: bool
    cnd_validade: str
    fgts_regular: bool
    fgts_validade: str
    folha_regular: bool
    beneficios_regular: bool
    justificativa: str
    fiscal_nome: str
    fiscal_matricula: str

class AdminActionInput(BaseModel):
    motivo: str
    usuario_nome: str
    usuario_matricula: str

@app.post("/api/auth/login")
def fazer_login(payload: LoginInput, db: Session = Depends(get_db)):
    u = db.query(UsuarioDB).filter(UsuarioDB.matricula == payload.matricula.strip(), UsuarioDB.senha == payload.senha.strip(), UsuarioDB.ativo == True).first()
    if not u:
        raise HTTPException(status_code=401, detail="Matrícula ou senha inválidas.")
    return {
        "ok": True,
        "nome": u.nome,
        "matricula": u.matricula,
        "perfil": u.perfil,
        "secretaria": u.secretaria_atribuida
    }

@app.get("/api/usuarios")
def listar_usuarios(db: Session = Depends(get_db)):
    usuarios = db.query(UsuarioDB).all()
    return [{"id": u.id, "nome": u.nome, "matricula": u.matricula, "perfil": u.perfil, "secretaria": u.secretaria_atribuida} for u in usuarios]

@app.post("/api/usuarios")
def cadastrar_usuario(payload: UsuarioCreate, db: Session = Depends(get_db)):
    existente = db.query(UsuarioDB).filter(UsuarioDB.matricula == payload.matricula).first()
    if existente:
        raise HTTPException(status_code=400, detail="Já existe um usuário cadastrado com esta matrícula.")
    
    novo = UsuarioDB(
        nome=payload.nome.strip(),
        matricula=payload.matricula.strip(),
        senha=payload.senha.strip(),
        perfil=payload.perfil.strip(),
        secretaria_atribuida=payload.secretaria_atribuida,
        ativo=True
    )
    db.add(novo)
    db.commit()
    return {"ok": True, "mensagem": "Usuário cadastrado com sucesso."}

@app.get("/api/dados")
def obter_dados(secretaria: str = "TODAS", incluir_arquivados: bool = False, db: Session = Depends(get_db)):
    query = db.query(ContratoDB)
    if not incluir_arquivados:
        query = query.filter(ContratoDB.ativo == True)
    if secretaria != "TODAS":
        query = query.filter(ContratoDB.secretaria == secretaria)
    
    contratos = query.order_by(ContratoDB.id.desc()).all()
    ativos = [c for c in contratos if c.ativo]
    
    orcamento = round(sum(c.valor_bruto for c in ativos), 2)
    retencao = round(sum(c.retencao_inss + c.retencao_iss + c.retencao_irrf for c in ativos), 2)
    risco = round(sum(c.valor_bruto for c in ativos if c.status == "GLOSADO"), 2)
    liberado = round(sum(c.valor_liquido for c in ativos if c.status == "ATESTADO"), 2)

    resumo_secretarias = {}
    for c in ativos:
        if c.secretaria not in resumo_secretarias:
            resumo_secretarias[c.secretaria] = {"total": 0, "bruto": 0.0, "glosados": 0, "atestados": 0}
        resumo_secretarias[c.secretaria]["total"] += 1
        resumo_secretarias[c.secretaria]["bruto"] += c.valor_bruto
        if c.status == "GLOSADO":
            resumo_secretarias[c.secretaria]["glosados"] += 1
        elif c.status == "ATESTADO":
            resumo_secretarias[c.secretaria]["atestados"] += 1

    logs = db.query(AuditoriaDB).order_by(AuditoriaDB.id.desc()).limit(40).all()

    return {
        "kpis": {"orcamento": orcamento, "retencao": retencao, "risco": risco, "liberado": liberado},
        "resumo_secretarias": resumo_secretarias,
        "contratos": contratos,
        "auditoria": logs
    }

@app.post("/api/contratos")
def cadastrar_contrato(dados: ContratoInput, db: Session = Depends(get_db)):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    inss = round(dados.valor_bruto * (dados.aliquota_inss / 100.0), 2)
    iss = round(dados.valor_bruto * (dados.aliquota_iss / 100.0), 2)
    irrf = round(dados.valor_bruto * (dados.aliquota_irrf / 100.0), 2)
    liquido = round(dados.valor_bruto - (inss + iss + irrf), 2)

    cnd_vencida = verificar_data_vencida(dados.cnd_validade)
    fgts_vencido = verificar_data_vencida(dados.fgts_validade)

    cnd_ok = dados.cnd_regular and not cnd_vencida
    fgts_ok = dados.fgts_regular and not fgts_vencido

    motivos = []
    if not cnd_ok: motivos.append("CNDT Irregular ou Vencida")
    if not fgts_ok: motivos.append("CRF-FGTS Irregular ou Vencido")
    if not dados.folha_regular: motivos.append("Folha Salarial/eSocial Pendente")
    if not dados.beneficios_regular: motivos.append("Vale-Transporte/Alimentação Pendente")

    if motivos:
        status_ini = "GLOSADO"
        just_ini = f"TRAVA AUTOMÁTICA ART. 121: {', '.join(motivos)}."
    else:
        status_ini = "PENDENTE"
        just_ini = "Pré-validação documental concluída. Aguardando atesto final."

    novo = ContratoDB(
        secretaria=dados.secretaria,
        processo_sei=dados.processo_sei.strip(),
        numero_contrato=dados.numero_contrato.strip(),
        competencia=dados.competencia.strip(),
        empresa=dados.empresa.strip().upper(),
        cnpj=dados.cnpj.strip(),
        valor_bruto=dados.valor_bruto,
        aliquota_inss=dados.aliquota_inss, aliquota_iss=dados.aliquota_iss, aliquota_irrf=dados.aliquota_irrf,
        retencao_inss=inss, retencao_iss=iss, retencao_irrf=irrf, valor_liquido=liquido,
        cnd_regular=cnd_ok, cnd_validade=dados.cnd_validade,
        fgts_regular=fgts_ok, fgts_validade=dados.fgts_validade,
        folha_regular=dados.folha_regular, beneficios_regular=dados.beneficios_regular,
        doc_nome=dados.doc_nome or "Certidoes_SEI.pdf",
        doc_hash_arquivo=dados.doc_hash_arquivo or "HASH-PADRAO-SEI",
        justificativa=just_ini, status=status_ini,
        fiscal_nome=dados.fiscal_nome, fiscal_matricula=dados.fiscal_matricula, ativo=True
    )
    db.add(novo)
    db.add(AuditoriaDB(
        data_hora=agora, usuario_nome=dados.fiscal_nome, usuario_matricula=dados.fiscal_matricula,
        perfil="OPERADOR", secretaria=dados.secretaria, processo=dados.processo_sei,
        acao=f"CADASTRO DE FATURA ({status_ini})", detalhes=f"Empresa: {novo.empresa} | Bruto: R$ {dados.valor_bruto:,.2f}"
    ))
    db.commit()
    return {"ok": True, "status": status_ini, "motivos": motivos}

@app.post("/api/contratos/{contrato_id}/auditar")
def auditar_contrato(contrato_id: int, payload: AuditarInput, db: Session = Depends(get_db)):
    c = db.query(ContratoDB).filter(ContratoDB.id == contrato_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Contrato não localizado.")

    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cnd_vencida = verificar_data_vencida(payload.cnd_validade)
    fgts_vencido = verificar_data_vencida(payload.fgts_validade)

    c.cnd_validade = payload.cnd_validade
    c.fgts_validade = payload.fgts_validade
    c.cnd_regular = payload.cnd_regular and not cnd_vencida
    c.fgts_regular = payload.fgts_regular and not fgts_vencido
    c.folha_regular = payload.folha_regular
    c.beneficios_regular = payload.beneficios_regular
    c.fiscal_nome = payload.fiscal_nome
    c.fiscal_matricula = payload.fiscal_matricula

    falhas = []
    if cnd_vencida: falhas.append("CNDT vencida")
    elif not payload.cnd_regular: falhas.append("CNDT irregular")
    if fgts_vencido: falhas.append("CRF-FGTS vencido")
    elif not payload.fgts_regular: falhas.append("CRF-FGTS irregular")
    if not payload.folha_regular: falhas.append("Folha salarial pendente")
    if not payload.beneficios_regular: falhas.append("VT/VA pendente")

    if not falhas:
        c.status = "ATESTADO"
        c.justificativa = payload.justificativa
        semente = f"{c.processo_sei}|{c.cnpj}|{c.valor_bruto}|{c.fiscal_matricula}|{agora}"
        c.hash_sha256 = "OMNIGOV-SHA256-" + hashlib.sha256(semente.encode()).hexdigest()[:32].upper()
        c.data_atesto = agora
        acao_log = "ATESTO EMITIDO COM BLINDAGEM SHA-256"
    else:
        c.status = "GLOSADO"
        c.hash_sha256 = None
        c.data_atesto = agora
        c.justificativa = f"[BLOQUEIO ART. 121: {', '.join(falhas)}] - Parecer: {payload.justificativa}"
        acao_log = "FATURA GLOSADA (RETENÇÃO CAUTELAR ART. 121)"

    db.add(AuditoriaDB(
        data_hora=agora, usuario_nome=payload.fiscal_nome, usuario_matricula=payload.fiscal_matricula,
        perfil="OPERADOR", secretaria=c.secretaria, processo=c.processo_sei,
        acao=acao_log, detalhes=c.justificativa
    ))
    db.commit()
    return {"ok": True, "status": c.status, "falhas": falhas, "hash": c.hash_sha256}

@app.post("/api/contratos/{contrato_id}/arquivar")
def arquivar_contrato(contrato_id: int, payload: AdminActionInput, db: Session = Depends(get_db)):
    c = db.query(ContratoDB).filter(ContratoDB.id == contrato_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Contrato não encontrado.")
    
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    c.ativo = False
    c.status = "ARQUIVADO"
    c.justificativa = f"[ARQUIVADO PELA CONTROLADORIA: {payload.motivo}]"

    db.add(AuditoriaDB(
        data_hora=agora, usuario_nome=payload.usuario_nome, usuario_matricula=payload.usuario_matricula,
        perfil="ADMIN", secretaria=c.secretaria, processo=c.processo_sei,
        acao="ARQUIVAMENTO ADMINISTRATIVO", detalhes=payload.motivo
    ))
    db.commit()
    return {"ok": True}

@app.get("/api/validar/{codigo_hash}")
def validar_hash(codigo_hash: str, db: Session = Depends(get_db)):
    limpo = codigo_hash.strip().upper()
    c = db.query(ContratoDB).filter(ContratoDB.hash_sha256 == limpo).first()
    if not c:
        return {"valido": False, "mensagem": "ALERTA: Nenhum dossiê oficial corresponde a este Hash SHA-256."}
    return {
        "valido": True, "processo_sei": c.processo_sei, "numero_contrato": c.numero_contrato,
        "secretaria": c.secretaria, "empresa": c.empresa, "cnpj": c.cnpj, "valor_liquido": c.valor_liquido,
        "fiscal": f"{c.fiscal_nome} ({c.fiscal_matricula})", "data_atesto": c.data_atesto, "doc_nome": c.doc_nome
    }

@app.get("/api/exportar/csv")
def exportar_csv(db: Session = Depends(get_db)):
    contratos = db.query(ContratoDB).all()
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["ID", "Secretaria", "Processo SEI", "Contrato", "Empresa", "CNPJ", "Bruto", "Liquido", "Status", "Fiscal", "Hash"])
    for c in contratos:
        writer.writerow([c.id, c.secretaria, c.processo_sei, c.numero_contrato, c.empresa, c.cnpj, f"{c.valor_bruto:.2f}".replace('.',','), f"{c.valor_liquido:.2f}".replace('.',','), c.status, c.fiscal_nome, c.hash_sha256 or "GLOSADO"])
    output.seek(0)
    headers = {"Content-Disposition": f"attachment; filename=Relatorio_OmniGov_TCE_{date.today()}.csv"}
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

@app.get("/api/admin/backup-db")
def baixar_backup():
    if os.path.exists(DB_FILE):
        return FileResponse(path=DB_FILE, filename=f"backup_omnigov_{date.today()}.db", media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="Banco de dados não encontrado.")

# ==============================================================================
# 4. FRONTEND INTEGRADO (OmniGov Enterprise UI)
# ==============================================================================
HTML_INTERFACE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OmniGov Enterprise | Compliance e Blindagem Contratual</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; }
        .font-mono-num { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; }
        @media print {
            body * { visibility: hidden; }
            #conteudoDossie, #conteudoDossie * { visibility: visible; }
            #conteudoDossie { position: absolute; left: 0; top: 0; width: 100%; border: none !important; box-shadow: none !important; }
            .nao-imprimir { display: none !important; }
        }
    </style>
</head>
<body class="bg-slate-100 text-slate-900 min-h-screen flex flex-col">

    <!-- TELA DE LOGIN (EXIGIDA NO INÍCIO) -->
    <div id="telaLogin" class="fixed inset-0 bg-[#071D41] z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-3xl max-w-md w-full p-8 shadow-2xl space-y-6 border border-slate-200">
            <div class="text-center space-y-2">
                <span class="px-3 py-1 bg-blue-100 text-blue-900 rounded-full text-xs font-extrabold uppercase">OmniGov Enterprise v6.0</span>
                <h1 class="text-2xl font-extrabold text-slate-900">Acesso Restrito ao Sistema</h1>
                <p class="text-xs text-slate-500">Informe suas credenciais corporativas ou acesse com a conta de Administrador.</p>
            </div>
            <div class="space-y-4 text-xs">
