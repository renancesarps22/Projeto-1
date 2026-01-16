import io
import os
from datetime import date

import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="App Personal", layout="wide")

DEFAULT_XLSX_PATH = "APP PERSONAL.xlsx"  # keep in same folder on deploy
REGISTRO_PATH = "registro_treinos.csv"   # local persistence (works on Streamlit Cloud)

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


# ---------------------------
# Sidebar: Load file + filters
# ---------------------------

st.sidebar.title("App Personal")

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

tab1, tab2, tab3 = st.tabs(["Dashboard", "Ficha de treino", "Registro de treinos"])

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

        # KPIs
        k1, k2, k3, k4 = st.columns(4)
        if col_peso:
            delta_peso = _kpi_delta(av[col_peso])
            k1.metric("Variação de peso", _fmt_delta(delta_peso, " kg"))
        else:
            k1.metric("Variação de peso", "-")

        if col_gord:
            delta_g = _kpi_delta(av[col_gord])
            k2.metric("Variação % gordura", _fmt_delta(delta_g, " p.p."))
        else:
            k2.metric("Variação % gordura", "-")

        if col_mm:
            delta_mm = _kpi_delta(av[col_mm])
            k3.metric("Variação % massa magra", _fmt_delta(delta_mm, " p.p."))
        else:
            k3.metric("Variação % massa magra", "-")

        # RCQ atual
        if "RCQ" in av.columns and av["RCQ"].notna().any():
            rcq_atual = float(pd.to_numeric(av["RCQ"], errors="coerce").dropna().iloc[-1])
            risco_atual = av["RISCO"].dropna().iloc[-1] if "RISCO" in av.columns and av["RISCO"].notna().any() else "-"
            k4.metric("RCQ (atual)", f"{rcq_atual:.2f}", risco_atual)
        else:
            k4.metric("RCQ (atual)", "-")

        # Charts grid
        c1, c2 = st.columns(2)
        with c1:
            if col_peso:
                fig = px.line(av, x="Data", y=col_peso, markers=True, title="Peso ao longo do tempo")
                st.plotly_chart(fig, use_container_width=True)
            if col_gord:
                fig = px.line(av, x="Data", y=col_gord, markers=True, title="% Gordura ao longo do tempo")
                st.plotly_chart(fig, use_container_width=True)

        with c2:
            if col_mm:
                fig = px.line(av, x="Data", y=col_mm, markers=True, title="% Massa magra ao longo do tempo")
                st.plotly_chart(fig, use_container_width=True)

            # IMC
            if "IMC" in av.columns:
                fig = px.line(av, x="Data", y="IMC", markers=True, title="IMC ao longo do tempo")
                st.plotly_chart(fig, use_container_width=True)

        c3, c4 = st.columns(2)
        with c3:
            # Bar: MM vs Gordura (ultima data)
            if col_mm and col_gord:
                last = av.dropna(subset=[col_mm, col_gord]).tail(1)
                if not last.empty:
                    df_bar = pd.DataFrame({
                        "Medida": ["% Massa Magra", "% Gordura"],
                        "Valor": [float(last[col_mm].iloc[0]), float(last[col_gord].iloc[0])],
                    })
                    fig = px.bar(df_bar, x="Medida", y="Valor", title="% Massa magra x % gordura (atual)")
                    st.plotly_chart(fig, use_container_width=True)

            # Circunferencias (CC, CQ, CA) se existirem
            circ_cols = [c for c in ["CC", "CQ", "CA"] if c in av.columns]
            if circ_cols:
                df_melt = av[["Data"] + circ_cols].melt(id_vars=["Data"], var_name="Circunferência", value_name="Valor")
                fig = px.line(df_melt, x="Data", y="Valor", color="Circunferência", markers=True, title="Circunferências ao longo do tempo")
                st.plotly_chart(fig, use_container_width=True)

        with c4:
            st.markdown("#### Quadro RCQ e risco")
            rcq_cols = [c for c in ["Data", "RCQ", "RISCO", "Sexo"] if c in av.columns]
            if "RCQ" in rcq_cols:
                df_rcq = av[rcq_cols].copy()
                st.dataframe(df_rcq, use_container_width=True, hide_index=True)
            else:
                st.info("Coluna RCQ/RISCO nao encontrada.")

        st.markdown("#### Dados filtrados")
        st.dataframe(av, use_container_width=True, hide_index=True)


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
