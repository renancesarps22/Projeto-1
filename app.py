import io
import os
from datetime import date

import pandas as pd
import streamlit as st
import plotly.express as px

# Export PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

st.set_page_config(page_title="App Personal", layout="wide")

DEFAULT_XLSX_PATH = "APP PERSONAL.xlsx"  # keep in same folder on deploy
REGISTRO_PATH = "registro_treinos.csv"   # local persistence (works on Streamlit Cloud)
AVALIACOES_PATH = "avaliacoes_usuario.csv"  # avaliações adicionadas via app

# ---------------------------
# Helpers
# ---------------------------

def _load_workbook(uploaded_file: bytes | None):
    """Return dict of DataFrames for expected sheets."""
    if uploaded_file is not None:
        data = uploaded_file
        xls = pd.ExcelFile(io.BytesIO(data))
    else:
        if not os.path.exists(DEFAULT_XLSX_PATH):
            raise FileNotFoundError(
                f"Arquivo '{DEFAULT_XLSX_PATH}' nao encontrado. "
                "Envie o Excel pelo seletor acima ou coloque o arquivo junto do app."
            )
        xls = pd.ExcelFile(DEFAULT_XLSX_PATH)

    sheets = {name: xls.parse(name) for name in xls.sheet_names}
    return sheets


def _safe_to_datetime(s):
    return pd.to_datetime(s, errors="coerce")


def _read_registro() -> pd.DataFrame:
    if os.path.exists(REGISTRO_PATH):
        df = pd.read_csv(REGISTRO_PATH)
        # normalize types
        if "Data" in df.columns:
            df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date
        return df
    return pd.DataFrame(
        columns=[
            "Data", "Nome", "Grupo muscular", "Exercicio", "Series", "Repeticoes", "Carga (kg)", "Observacoes"
        ]
    )


def _append_registro(rows: list[dict]):
    df_old = _read_registro()
    df_new = pd.DataFrame(rows)
    # keep consistent column order
    for col in df_old.columns:
        if col not in df_new.columns:
            df_new[col] = None
    df_all = pd.concat([df_old, df_new[df_old.columns]], ignore_index=True)
    df_all.to_csv(REGISTRO_PATH, index=False)


def _read_avaliacoes_extra() -> pd.DataFrame:
    """Avaliações criadas pelo usuário via app (persistência local)."""
    if os.path.exists(AVALIACOES_PATH):
        df = pd.read_csv(AVALIACOES_PATH)
        if "Data" in df.columns:
            df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date
        return df
    return pd.DataFrame()


def _append_avaliacao(row: dict):
    df_old = _read_avaliacoes_extra()
    df_new = pd.DataFrame([row])
    # alinhar colunas
    if not df_old.empty:
        for col in df_old.columns:
            if col not in df_new.columns:
                df_new[col] = None
        for col in df_new.columns:
            if col not in df_old.columns:
                df_old[col] = None
        df_all = pd.concat([df_old[df_new.columns], df_new[df_new.columns]], ignore_index=True)
    else:
        df_all = df_new
    df_all.to_csv(AVALIACOES_PATH, index=False)


def _kpi_delta(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return None
    return float(s.iloc[-1] - s.iloc[0])


def _fmt_delta(value, unit=""):
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}{unit}"


def _current_value(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 1:
        return None
    return float(s.iloc[-1])


def _make_pdf_from_table(title: str, df: pd.DataFrame) -> bytes:
    """Simple PDF export for the current ficha."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    x0, y = 40, h - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x0, y, title)
    y -= 25

    c.setFont("Helvetica", 9)
    cols = list(df.columns)
    # Column widths (rough)
    col_w = [70, 70, 90, 120, 40, 55, 55]
    col_w = col_w[: len(cols)]
    if len(col_w) < len(cols):
        col_w += [80] * (len(cols) - len(col_w))

    # Header
    for i, col in enumerate(cols):
        c.drawString(x0 + sum(col_w[:i]), y, str(col)[:18])
    y -= 14

    # Rows
    for _, row in df.iterrows():
        if y < 60:
            c.showPage()
            y = h - 50
            c.setFont("Helvetica", 9)
        for i, col in enumerate(cols):
            val = row.get(col, "")
            c.drawString(x0 + sum(col_w[:i]), y, str(val)[:20])
        y -= 12

    c.showPage()
    c.save()
    return buf.getvalue()


def _to_float_or_none(x, treat_zero_as_none: bool = False):
    """Parse float; return None on blanks/invalid.

    Streamlit number_input retorna 0.0 por padrão. Para medidas corporais,
    normalmente 0 indica 'não preenchido'. Use treat_zero_as_none=True nesses casos.
    """
    try:
        if x is None:
            return None
        v = float(x)
        if treat_zero_as_none and abs(v) < 1e-12:
            return None
        return v
    except Exception:
        return None


def _calc_imc(peso: float | None, altura: float | None) -> float | None:
    if peso is None or altura is None:
        return None
    if altura <= 0:
        return None
    return peso / (altura ** 2)


def _classificacao_imc(imc: float | None) -> str | None:
    if imc is None:
        return None
    # Mantém nomenclatura comum (e bate com o exemplo da planilha)
    if imc < 18.5:
        return "Baixo peso"
    if imc < 25:
        return "Peso normal"
    if imc < 30:
        return "Sobrepeso"
    if imc < 35:
        return "Obesidade Grau I"
    if imc < 40:
        return "Obesidade Grau II"
    return "Obesidade Grau III"


def _calc_dc_jp3(sexo: str | None, idade: float | None, d_pe: float | None, d_ab: float | None,
                d_cx: float | None, d_si: float | None, d_tr: float | None) -> float | None:
    """Densidade corporal (Jackson & Pollock 3 dobras).

    Homem: Peitoral + Abdominal + Coxa
    Mulher: Tríceps + Supra-ilíaca + Coxa
    """
    if sexo not in {"Homem", "Mulher"}:
        return None
    if idade is None:
        return None

    if sexo == "Homem":
        vals = [d_pe, d_ab, d_cx]
        if any(v is None for v in vals):
            return None
        s = sum(vals)
        return 1.10938 - 0.0008267 * s + 0.0000016 * (s ** 2) - 0.0002574 * idade

    # Mulher
    vals = [d_tr, d_si, d_cx]
    if any(v is None for v in vals):
        return None
    s = sum(vals)
    return 1.0994921 - 0.0009929 * s + 0.0000023 * (s ** 2) - 0.0001392 * idade


def _calc_gordura_siri(dc: float | None) -> float | None:
    if dc is None or dc <= 0:
        return None
    return 495 / dc - 450


def _calc_rcq(cc: float | None, cq: float | None) -> float | None:
    if cc is None or cq is None:
        return None
    if cq <= 0:
        return None
    return cc / cq


def _classificacao_rcq(sexo: str | None, rcq: float | None) -> str | None:
    """Classificação de risco (padrão OMS, simplificado em 3 níveis)."""
    if rcq is None or sexo not in {"Homem", "Mulher"}:
        return None
    if sexo == "Homem":
        if rcq < 0.90:
            return "Baixo Risco"
        if rcq < 1.00:
            return "Risco Moderado"
        return "Alto Risco"
    # Mulher
    if rcq < 0.80:
        return "Baixo Risco"
    if rcq < 0.85:
        return "Risco Moderado"
    return "Alto Risco"


# ---------------------------
# Sidebar: Load file + filters
# ---------------------------

st.sidebar.title("App Personal")

# Tema (mudança de cor via CSS)
tema = st.sidebar.selectbox(
    "Tema do dashboard",
    ["Escuro (padrão)", "Claro", "Azul", "Verde"],
    index=0,
)

THEMES_CSS = {
    "Escuro (padrão)": "",
    "Claro": """
        <style>
        /* Tema claro com contraste alto */
        .stApp { background: #ffffff; color: #111111; }
        [data-testid="stHeader"] { background: rgba(255,255,255,0.85); }
        /* Textos e labels (evita sobrescrever cores de delta) */
        body, p, span, div, label, h1, h2, h3, h4, h5, h6 { color: #111111; }
        [data-testid="stMetricLabel"],
        [data-testid="stMetricValue"] { color: #111111 !important; }
        /* Mantém cor do delta (verde/vermelho) */
        [data-testid="stMetricDelta"] { filter: none; }
        /* Tabelas/DF */
        [data-testid="stDataFrame"] * { color: #111111 !important; }
        </style>
    """,
    "Azul": """
        <style>
        .stApp { background: #071526; }
        [data-testid="stHeader"] { background: rgba(7,21,38,0.6); }
        </style>
    """,
    "Verde": """
        <style>
        .stApp { background: #071f16; }
        [data-testid="stHeader"] { background: rgba(7,31,22,0.6); }
        </style>
    """,
}

# Plotly template por tema
PLOTLY_TEMPLATE = "plotly_white" if tema == "Claro" else "plotly_dark"

st.markdown(THEMES_CSS.get(tema, ""), unsafe_allow_html=True)

uploaded = st.sidebar.file_uploader(
    "Envie o Excel (APP PERSONAL.xlsx)",
    type=["xlsx"],
    help="Se voce nao enviar, o app tenta carregar o arquivo que estiver junto do app (APP PERSONAL.xlsx).",
)

try:
    sheets = _load_workbook(uploaded.getvalue() if uploaded else None)
except Exception as e:
    st.error(str(e))
    st.stop()

# Expected sheets
avaliacao = sheets.get("AVALIACAO_FISICA", pd.DataFrame()).copy()
dados_treinos = sheets.get("DADOS_TREINOS", pd.DataFrame()).copy()

# Normalize avaliacao
if not avaliacao.empty and "Data" in avaliacao.columns:
    avaliacao["Data"] = _safe_to_datetime(avaliacao["Data"]).dt.date

# Mescla avaliações extras criadas via app
av_extra = _read_avaliacoes_extra()
if not av_extra.empty:
    # alinhar colunas entre base e extra
    for col in avaliacao.columns:
        if col not in av_extra.columns:
            av_extra[col] = None
    for col in av_extra.columns:
        if col not in avaliacao.columns:
            avaliacao[col] = None
    avaliacao = pd.concat([avaliacao[av_extra.columns], av_extra[av_extra.columns]], ignore_index=True)
    if "Data" in avaliacao.columns:
        avaliacao["Data"] = pd.to_datetime(avaliacao["Data"], errors="coerce").dt.date

# Name filter
nomes = []
if not avaliacao.empty and "Nome" in avaliacao.columns:
    nomes = sorted([x for x in avaliacao["Nome"].dropna().unique().tolist() if str(x).strip()])

nome_sel = st.sidebar.selectbox("Nome", nomes if nomes else ["(sem nomes)"])

# Date range filter
if not avaliacao.empty and "Data" in avaliacao.columns and avaliacao["Data"].notna().any():
    dmin = avaliacao["Data"].min()
    dmax = avaliacao["Data"].max()
    dr = st.sidebar.date_input("Periodo", (dmin, dmax), min_value=dmin, max_value=dmax)
    if isinstance(dr, tuple) and len(dr) == 2:
        d_start, d_end = dr
    else:
        d_start, d_end = dmin, dmax
else:
    d_start = d_end = None


# Filtered avaliacao
av = avaliacao.copy()
if not av.empty:
    if "Nome" in av.columns and nomes:
        av = av[av["Nome"] == nome_sel]
    if d_start and d_end and "Data" in av.columns:
        av = av[(av["Data"] >= d_start) & (av["Data"] <= d_end)]
    av = av.sort_values("Data")


# ---------------------------
# Main UI (Tabs)
# ---------------------------

tab1, tab_av, tab2, tab3 = st.tabs(["Dashboard", "Avaliação Física", "Ficha de treino", "Registro de treinos"])

# ===============
# Tab 1: Dashboard
# ===============
with tab1:
    st.subheader("Dashboard")

    if av.empty:
        st.info("Nenhum dado encontrado em AVALIACAO_FISICA para os filtros atuais.")
    else:
        # Identify columns
        col_peso = "Peso" if "Peso" in av.columns else None
        col_gord = "G" if "G" in av.columns else ("% Gordura" if "% Gordura" in av.columns else None)
        col_mm = "MM" if "MM" in av.columns else ("% Massa Magra" if "% Massa Magra" in av.columns else None)

        # KPIs (valor atual em cima, variação embaixo)
        k1, k2, k3, k4, k5 = st.columns(5)

        if col_peso:
            peso_atual = _current_value(av[col_peso])
            delta_peso = _kpi_delta(av[col_peso])
            k1.metric("Peso (atual)", f"{peso_atual:.2f} kg" if peso_atual is not None else "-", _fmt_delta(delta_peso, " kg"))
        else:
            k1.metric("Peso (atual)", "-")

        if col_gord:
            g_atual = _current_value(av[col_gord])
            delta_g = _kpi_delta(av[col_gord])
            k2.metric("% Gordura (atual)", f"{g_atual:.2f} %" if g_atual is not None else "-", _fmt_delta(delta_g, " %"))
        else:
            k2.metric("% Gordura (atual)", "-")

        if col_mm:
            mm_atual = _current_value(av[col_mm])
            delta_mm = _kpi_delta(av[col_mm])
            k3.metric("% Massa magra (atual)", f"{mm_atual:.2f} %" if mm_atual is not None else "-", _fmt_delta(delta_mm, " %"))
        else:
            k3.metric("% Massa magra (atual)", "-")

        if "IMC" in av.columns:
            imc_atual = _current_value(av["IMC"])
            delta_imc = _kpi_delta(av["IMC"])
            k4.metric("IMC (atual)", f"{imc_atual:.2f}" if imc_atual is not None else "-", _fmt_delta(delta_imc, ""))
        else:
            k4.metric("IMC (atual)", "-")

        if "RCQ" in av.columns and av["RCQ"].notna().any():
            rcq_atual = float(pd.to_numeric(av["RCQ"], errors="coerce").dropna().iloc[-1])
            risco_atual = (
                av["RISCO"].dropna().iloc[-1]
                if "RISCO" in av.columns and av["RISCO"].notna().any()
                else "-"
            )
            k5.metric("RCQ (atual)", f"{rcq_atual:.2f}", str(risco_atual), delta_color="off")
        else:
            k5.metric("RCQ (atual)", "-")

        st.divider()

        # Gráficos em quadros
        g1, g2 = st.columns(2)
        with g1:
            with st.container(border=True):
                if col_peso:
                    fig = px.line(av, x="Data", y=col_peso, markers=True, title="Peso ao longo do tempo", template=PLOTLY_TEMPLATE)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Coluna de peso não encontrada.")

            with st.container(border=True):
                if col_gord:
                    fig = px.line(av, x="Data", y=col_gord, markers=True, title="% Gordura ao longo do tempo", template=PLOTLY_TEMPLATE)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Coluna de % gordura não encontrada.")

        with g2:
            with st.container(border=True):
                if col_mm:
                    fig = px.line(av, x="Data", y=col_mm, markers=True, title="% Massa magra ao longo do tempo", template=PLOTLY_TEMPLATE)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Coluna de % massa magra não encontrada.")

            with st.container(border=True):
                circ_cols = [c for c in ["CC", "CQ", "CA"] if c in av.columns]
                if circ_cols:
                    df_melt = av[["Data"] + circ_cols].melt(
                        id_vars=["Data"],
                        var_name="Circunferência",
                        value_name="Valor",
                    )
                    fig = px.line(
                        df_melt,
                        x="Data",
                        y="Valor",
                        color="Circunferência",
                        markers=True,
                        title="Circunferências ao longo do tempo",
                        template=PLOTLY_TEMPLATE,
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Circunferências (CC/CQ/CA) não encontradas.")

        g3, g4 = st.columns(2)
        with g3:
            with st.container(border=True):
                if "RCQ" in av.columns:
                    fig = px.line(av, x="Data", y="RCQ", markers=True, title="RCQ ao longo do tempo", template=PLOTLY_TEMPLATE)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Coluna RCQ não encontrada.")

        with g4:
            with st.container(border=True):
                st.markdown("#### Quadro RCQ e risco")
                rcq_cols = [c for c in ["Data", "RCQ", "RISCO"] if c in av.columns]
                if "RCQ" in rcq_cols:
                    df_rcq = av[rcq_cols].copy()
                    st.dataframe(df_rcq, use_container_width=True, hide_index=True)
                else:
                    st.info("Coluna RCQ/RISCO não encontrada.")


# =======================
# Tab Avaliação Física
# =======================
with tab_av:
    st.subheader("Avaliação Física")

    # ----- Criar nova avaliação -----
    if not avaliacao.empty and "Data" in avaliacao.columns and "Nome" in avaliacao.columns:
        with st.expander("Adicionar nova avaliação"):
            with st.form("form_nova_avaliacao", border=True):
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    nome_novo = st.text_input("Nome", value=nome_sel if nome_sel and nome_sel != "(sem nomes)" else "")
                with c2:
                    data_nova = st.date_input("Data", value=date.today())
                with c3:
                    sexo_col = "Sexo" if "Sexo" in avaliacao.columns else None
                    sexo_val = st.selectbox("Sexo", ["", "Homem", "Mulher"], index=0) if sexo_col else None

                # --- Entradas completas, seguindo a planilha ---
                # Dados pessoais / antropometria
                p1, p2, p3, p4 = st.columns(4)
                with p1:
                    idade = st.number_input("Idade", value=0, step=1) if "Idade" in avaliacao.columns else None
                with p2:
                    peso = st.number_input("Peso (kg)", value=0.0, step=0.1) if "Peso" in avaliacao.columns else None
                with p3:
                    altura = st.number_input("Altura (m)", value=0.0, step=0.01) if "Altura" in avaliacao.columns else None
                with p4:
                    cc = st.number_input("CC (cintura)", value=0.0, step=0.1) if "CC" in avaliacao.columns else None

                p5, p6, p7, p8 = st.columns(4)
                with p5:
                    cq = st.number_input("CQ (quadril)", value=0.0, step=0.1) if "CQ" in avaliacao.columns else None
                with p6:
                    ca = st.number_input("CA (abdômen)", value=0.0, step=0.1) if "CA" in avaliacao.columns else None
                with p7:
                    pass
                with p8:
                    pass

                st.markdown("**Dobras cutâneas (mm)**")
                d1, d2, d3, d4, d5 = st.columns(5)
                with d1:
                    d_pe = st.number_input("D PE (peitoral)", value=0.0, step=0.1) if "D PE" in avaliacao.columns else None
                with d2:
                    d_ab = st.number_input("D AB (abdominal)", value=0.0, step=0.1) if "D AB" in avaliacao.columns else None
                with d3:
                    d_cx = st.number_input("D CX (coxa)", value=0.0, step=0.1) if "D CX" in avaliacao.columns else None
                with d4:
                    d_si = st.number_input("D SI (supra-ilíaca)", value=0.0, step=0.1) if "D SI" in avaliacao.columns else None
                with d5:
                    d_tr = st.number_input("D TR (tríceps)", value=0.0, step=0.1) if "D TR" in avaliacao.columns else None

                # Prévia dos cálculos (opcional)
                sexo_calc = sexo_val if sexo_col else None
                idade_f = _to_float_or_none(idade, treat_zero_as_none=True)
                peso_f = _to_float_or_none(peso, treat_zero_as_none=True)
                altura_f = _to_float_or_none(altura, treat_zero_as_none=True)
                cc_f = _to_float_or_none(cc, treat_zero_as_none=True)
                cq_f = _to_float_or_none(cq, treat_zero_as_none=True)

                d_pe_f = _to_float_or_none(d_pe, treat_zero_as_none=True)
                d_ab_f = _to_float_or_none(d_ab, treat_zero_as_none=True)
                d_cx_f = _to_float_or_none(d_cx, treat_zero_as_none=True)
                d_si_f = _to_float_or_none(d_si, treat_zero_as_none=True)
                d_tr_f = _to_float_or_none(d_tr, treat_zero_as_none=True)

                imc_calc = _calc_imc(peso_f, altura_f)
                cls_imc = _classificacao_imc(imc_calc)
                dc_calc = _calc_dc_jp3(sexo_calc, idade_f, d_pe_f, d_ab_f, d_cx_f, d_si_f, d_tr_f)
                g_calc = _calc_gordura_siri(dc_calc)
                mm_calc = (100 - g_calc) if g_calc is not None else None
                rcq_calc = _calc_rcq(cc_f, cq_f)
                risco_calc = _classificacao_rcq(sexo_calc, rcq_calc)

                prev1, prev2, prev3, prev4, prev5 = st.columns(5)
                prev1.metric("IMC", f"{imc_calc:.2f}" if imc_calc is not None else "-")
                prev2.metric("Classificação", cls_imc or "-")
                prev3.metric("DC", f"{dc_calc:.6f}" if dc_calc is not None else "-")
                prev4.metric("% Gordura", f"{g_calc:.2f} %" if g_calc is not None else "-")
                prev5.metric("% Massa magra", f"{mm_calc:.2f} %" if mm_calc is not None else "-")

                st.caption("Obs.: DC e %G são calculados pelo protocolo Jackson & Pollock (3 dobras) + Siri, igual ao padrão usado na sua planilha.")

                submitted = st.form_submit_button("Salvar avaliação")

            if submitted:
                if not str(nome_novo).strip():
                    st.error("Informe o Nome.")
                elif sexo_col and not (sexo_val or "") .strip():
                    st.error("Selecione o Sexo para calcular DC, % gordura, % massa magra e risco do RCQ.")
                else:
                    # Monta linha com as mesmas colunas da planilha
                    row = {c: None for c in avaliacao.columns}
                    row["Nome"] = str(nome_novo).strip()
                    row["Data"] = data_nova
                    if sexo_col:
                        row[sexo_col] = sexo_val if sexo_val else None

                    # salva entradas brutas (seguindo as colunas existentes)
                    if "Idade" in avaliacao.columns:
                        row["Idade"] = idade_f
                    if "Peso" in avaliacao.columns:
                        row["Peso"] = peso_f
                    if "Altura" in avaliacao.columns:
                        row["Altura"] = altura_f
                    for c, v in [("CC", cc_f), ("CQ", cq_f), ("CA", _to_float_or_none(ca, treat_zero_as_none=True))]:
                        if c in avaliacao.columns:
                            row[c] = v

                    # Dobras
                    for c, v in [("D PE", d_pe_f), ("D AB", d_ab_f), ("D CX", d_cx_f), ("D SI", d_si_f), ("D TR", d_tr_f)]:
                        if c in avaliacao.columns:
                            row[c] = v

                    # Cálculos (igual planilha)
                    if "IMC" in avaliacao.columns:
                        row["IMC"] = imc_calc
                    if "Classificação" in avaliacao.columns:
                        row["Classificação"] = cls_imc
                    if "DC" in avaliacao.columns:
                        row["DC"] = dc_calc
                    if "G" in avaliacao.columns:
                        row["G"] = g_calc
                    if "MM" in avaliacao.columns:
                        row["MM"] = mm_calc

                    if "RCQ" in avaliacao.columns:
                        row["RCQ"] = rcq_calc
                    if "RISCO" in avaliacao.columns:
                        row["RISCO"] = risco_calc

                    _append_avaliacao(row)
                    st.success("Avaliação salva! Ela será combinada com sua planilha durante o uso do app.")
                    st.rerun()

    if av.empty:
        st.info("Nenhum dado encontrado em AVALIACAO_FISICA para os filtros atuais.")
    else:
        # Mostra uma tabela limpa (remove colunas muito técnicas se existirem)
        cols_preferidas = [
            c for c in [
                "Data",
                "Nome",
                "Peso",
                "G",
                "MM",
                "IMC",
                "CC",
                "CQ",
                "CA",
                "RCQ",
                "RISCO",
            ]
            if c in av.columns
        ]
        df_av = av[cols_preferidas] if cols_preferidas else av
        st.dataframe(df_av, use_container_width=True, hide_index=True)

        cexp1, cexp2 = st.columns([1, 3])
        with cexp1:
            # Export Excel
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as writer:
                df_av.to_excel(writer, index=False, sheet_name="avaliacao")
            st.download_button(
                "Baixar avaliação (Excel)",
                data=out.getvalue(),
                file_name="avaliacao_fisica_filtrada.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with cexp2:
            st.caption("A tabela respeita os filtros de Nome e Período da barra lateral.")


# ===================
# Tab 2: Ficha de treino
# ===================
with tab2:
    st.subheader("Ficha de treino")

    # Build group->exercise list from DADOS_TREINOS
    grupos = []
    ex_por_grupo = {}
    if not dados_treinos.empty:
        grupos = [c for c in dados_treinos.columns if str(c).strip()]
        for g in grupos:
            exs = dados_treinos[g].dropna().astype(str)
            exs = [e.strip() for e in exs.tolist() if e.strip() and e.strip().lower() != "nan"]
            ex_por_grupo[g] = exs

    if not grupos:
        st.warning("A aba DADOS_TREINOS nao foi encontrada (ou esta vazia). Sem ela, nao da para montar as listas de exercicios.")

    if "ficha_itens" not in st.session_state:
        st.session_state.ficha_itens = []  # list of dicts

    colA, colB, colC, colD = st.columns([1.2, 1.6, 1, 1])

    with colA:
        data_treino = st.date_input("Data do treino", value=date.today())
        nome_treino = st.text_input("Nome", value=nome_sel if nome_sel and nome_sel != "(sem nomes)" else "")

    with colB:
        grupo_sel = st.selectbox("Grupo muscular", grupos if grupos else ["-"])
        exercicios = ex_por_grupo.get(grupo_sel, [])
        exerc_sel = st.selectbox("Exercicio", exercicios if exercicios else ["-"])

    with colC:
        series = st.number_input("Séries", min_value=1, max_value=20, value=3, step=1)
        reps = st.number_input("Repetições", min_value=1, max_value=100, value=10, step=1)

    with colD:
        carga = st.number_input("Carga (kg)", min_value=0.0, max_value=500.0, value=0.0, step=0.5)
        obs = st.text_input("Observações", value="")

    b1, b2, b3 = st.columns([1, 1, 2])
    with b1:
        if st.button("Adicionar na ficha", use_container_width=True, type="primary"):
            if grupo_sel == "-" or exerc_sel == "-":
                st.warning("Selecione grupo e exercicio.")
            else:
                st.session_state.ficha_itens.append({
                    "Data": data_treino,
                    "Nome": nome_treino,
                    "Grupo muscular": grupo_sel,
                    "Exercicio": exerc_sel,
                    "Series": int(series),
                    "Repeticoes": int(reps),
                    "Carga (kg)": float(carga),
                    "Observacoes": obs.strip() if obs else "",
                })

    with b2:
        if st.button("Limpar ficha", use_container_width=True):
            st.session_state.ficha_itens = []

    with b3:
        if st.button("Salvar como treino realizado", use_container_width=True):
            if not st.session_state.ficha_itens:
                st.warning("A ficha esta vazia.")
            else:
                _append_registro(st.session_state.ficha_itens)
                st.success("Treino registrado!")
                st.session_state.ficha_itens = []

    st.markdown("#### Itens na ficha")
    df_ficha = pd.DataFrame(st.session_state.ficha_itens)
    st.dataframe(df_ficha, use_container_width=True, hide_index=True)

    # Exportar ficha
    if not df_ficha.empty:
        ex1, ex2, ex3 = st.columns([1.2, 1.2, 2])
        with ex1:
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as writer:
                df_ficha.to_excel(writer, index=False, sheet_name="ficha")
            st.download_button(
                "Exportar ficha (Excel)",
                data=out.getvalue(),
                file_name="ficha_treino.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with ex2:
            pdf_bytes = _make_pdf_from_table("Ficha de treino", df_ficha)
            st.download_button(
                "Exportar ficha (PDF)",
                data=pdf_bytes,
                file_name="ficha_treino.pdf",
                mime="application/pdf",
            )
        with ex3:
            st.caption("Exporta somente os itens atualmente listados na ficha.")


# =====================
# Tab 3: Registro de treinos
# =====================
with tab3:
    st.subheader("Registro de treinos")

    df_reg = _read_registro()

    # Filters
    f1, f2, f3 = st.columns([1.2, 1.2, 2])
    with f1:
        nome_f = st.text_input("Filtrar por nome", value="")
    with f2:
        grupo_f = st.text_input("Filtrar por grupo muscular", value="")
    with f3:
        st.caption("Dica: o registro fica salvo em 'registro_treinos.csv' no servidor do app.")

    df_show = df_reg.copy()
    if nome_f.strip():
        df_show = df_show[df_show["Nome"].astype(str).str.contains(nome_f.strip(), case=False, na=False)]
    if grupo_f.strip():
        df_show = df_show[df_show["Grupo muscular"].astype(str).str.contains(grupo_f.strip(), case=False, na=False)]

    st.dataframe(df_show, use_container_width=True, hide_index=True)

    # Download
    csv_bytes = df_show.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Baixar registro filtrado (CSV)",
        data=csv_bytes,
        file_name="registro_treinos_filtrado.csv",
        mime="text/csv",
    )

    if st.button("Apagar registro do servidor", type="secondary"):
        if os.path.exists(REGISTRO_PATH):
            os.remove(REGISTRO_PATH)
            st.success("Registro apagado.")
        else:
            st.info("Nao existe registro salvo ainda.")
