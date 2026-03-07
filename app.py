import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
import plotly.colors
from scipy.stats import gmean
import textwrap
import re

# ==========================================
# NASTAVENÍ STRÁNKY
# ==========================================

st.set_page_config(
    page_title="Analýza inflace",
    page_icon="📈",
    layout="wide"
)

# Funkce pro čištění a přípravu zdrojových dat z ČSÚ
def prep_timeseries(df, date_col):
    """
    Standardní cleanup pro časové řady:
    - Přejmenuje sloupec s datem na 'Datum'
    - Vyhází tečky (zástupný znak ČSÚ pro chybějící data) a nahradí je NaN
    - Převede textové datum (MM/RRRR) na opravdový datetime formát
    - Seřadí data podle času a nastaví je jako index
    - Převede všechna čísla (která jsou v Excelu často jako text) na floaty
    """
    df = df.rename(columns={date_col: "Datum"})
    df = df.replace('.', np.nan).dropna(subset=['Datum'])
    df['Datum'] = pd.to_datetime(df['Datum'], format='%m/%Y', errors='coerce')
    df = df.dropna(subset=['Datum']).sort_values('Datum').set_index('Datum')
    df = df.apply(pd.to_numeric, errors='coerce')
    df.columns = df.columns.str.replace('\n', ' ') # Odstranění zalomení řádků v názvech sloupců
    return df


# ==========================================
# 1. NAČÍTÁNÍ INFLAČNÍCH DAT (CPI A HICP)
# ==========================================

@st.cache_data # Cache, aby se Excel nenačítal při každém kliknutí v aplikaci
def load_inflation_data():
    try:
        # --- Národní inflace (CPI) ---
        # Musíme spojit dva soubory, protože ČSÚ v průběhu času změnil kódování (COICOP -> ECOICOP)
        df_cpi_old = pd.read_excel("CPI_1.xlsx")
        df_cpi_new = pd.read_excel("CPI_2.xlsx")
        
        df_cpi_old = df_cpi_old.rename(columns={"Oddíl COICOP": "Datum"})
        df_cpi_new = df_cpi_new.rename(columns={"Oddíl ECOICOP": "Datum"})
        
        # Merge obou řad do jednoho dataframe
        cpi_raw = pd.concat([df_cpi_old, df_cpi_new], ignore_index=True)
        cpi_raw = prep_timeseries(cpi_raw, "Datum")
        
        # Výpočet temp růstu (pct_change) - meziroční (12 m) a meziměsíční (1 m)
        cpi_yoy = cpi_raw.pct_change(12) * 100
        cpi_mom = cpi_raw.pct_change(1) * 100

        # --- Evropská inflace (HICP) ---
        hicp_raw = pd.read_excel("HICP.xlsx")
        hicp_raw = prep_timeseries(hicp_raw, "Oddíl COICOP")
        hicp_yoy = hicp_raw.pct_change(12) * 100

        # Oříznutí dat na rok 1995, od kdy jsou řady stabilní a kompletní
        start_date = '1995-01-01'
        return (
            cpi_raw.loc[start_date:], 
            cpi_yoy.loc[start_date:], 
            cpi_mom.loc[start_date:], 
            hicp_raw.loc[start_date:], 
            hicp_yoy.loc[start_date:]
        )

    except Exception as e:
        # Error pro případ, že soubory chybí nebo mají špatný formát
        st.error(f"Kritická chyba při načítání dat o inflaci: {e}")
        return None, None, None, None, None


# ==========================================
# 2. NAČÍTÁNÍ VAH SPOTŘEBNÍHO KOŠE
# ==========================================

@st.cache_data
def load_weights_data():
    # A) Podrobné váhy pro rok 2025 (pro Treemap graf)
    try:
        weights_tree = pd.read_excel('spot_kos2025_podrobne.xlsx')
        
        # ČSÚ má váhy v promile, my je chceme v procentech (děleno 10)
        if weights_tree['VÁHA v ‰'].dtype == 'O':
            weights_tree['VÁHA v ‰'] = weights_tree['VÁHA v ‰'].astype(str).str.replace(',', '.').astype(float)
        weights_tree['Weight_Pct'] = weights_tree['VÁHA v ‰'] / 10
        
        weights_tree['ECOICOP'] = weights_tree['ECOICOP'].astype(str).str.strip()
        
        # Slovník pro mapování kódů na názvy (hlavních 12 kategorií)
        category_names = {
            '01': 'Potraviny a nealko nápoje', '02': 'Alkohol a tabák', '03': 'Odívání a obuv',
            '04': 'Bydlení, voda, energie, paliva', '05': 'Bytové vybavení', '06': 'Zdraví',
            '07': 'Doprava', '08': 'Pošty a telekomunikace', '09': 'Rekreace a kultura',
            '10': 'Vzdělávání', '11': 'Stravování a ubytování', '12': 'Ostatní zboží a služby'
        }
        
        # Funkce na vytáhnutí prvních dvou čísel z kódu (např. 01.1.2 -> 01)
        def extract_main_category(code):
            base_code = str(code).strip().split('.')[0]
            return '0' + base_code if len(base_code) == 1 and base_code.isdigit() else base_code

        weights_tree['Main_Code'] = weights_tree['ECOICOP'].apply(extract_main_category)
        
        # Do stromové mapy bereme jen položky s tečkou (podkategorie), ne celkové úhrny
        subcategories_only = weights_tree[weights_tree['ECOICOP'].astype(str).str.contains('.', regex=False)].copy()
        subcategories_only['Main_Category_Name'] = subcategories_only['Main_Code'].map(category_names).fillna(subcategories_only['Main_Code'])
        
    except Exception:
        subcategories_only = None

    # B) Historie vah hlavních kategorií (jak se měnil koš v čase)
    try:
        weights_history = pd.read_excel('vahy_v_letech.xlsx')
        weights_history.columns = weights_history.columns.astype(str)
        
        # Nechceme řádek "ÚHRN", zajímají nás ty dílčí části
        weights_history = weights_history[weights_history['NAZEV'] != 'ÚHRN'].copy()
        
        # Projdeme všechny sloupce, co jsou roky, a převedeme promile na procenta
        year_columns = [col for col in weights_history.columns if col.isdigit()]
        for year in year_columns:
            weights_history[year] = weights_history[year].astype(str).str.replace(',', '.')
            weights_history[year] = pd.to_numeric(weights_history[year], errors='coerce') / 10
            
    except Exception as e:
        st.error(f"Chyba při zpracování historie vah spotřebního koše: {e}")
        weights_history = None

    return subcategories_only, weights_history


# Spuštění loadovacích funkcí
cpi_raw, cpi_yoy, cpi_mom, hicp_raw, hicp_yoy = load_inflation_data()
weights_tree, weights_history = load_weights_data()

# Pokud se nepodaří načíst CPI, aplikace se zastaví
if cpi_raw is None: 
    st.stop()


# ==========================================
# 3. SIDEBAR - NASTAVENÍ A FILTRY
# ==========================================

st.sidebar.title("⚙️ Nastavení")

# A) Globální filtr období (ořezává dataframe pro celou aplikaci)
st.sidebar.subheader("1. Načíst období dat")
st.sidebar.caption("Zvolte časové období, které vás zajímá. Data se následně načtou do aplikace, přepočítají se všechny statistiky (KPI) a upraví se rozsah grafů.")

min_date = cpi_raw.index.min().date()
max_date = cpi_raw.index.max().date()

filter_start = st.sidebar.date_input(
    "Načíst OD", 
    value=pd.to_datetime("1995-01-01").date(), 
    min_value=min_date, 
    max_value=max_date,
    format="DD.MM.YYYY" 
)

filter_end = st.sidebar.date_input(
    "Načíst DO", 
    value=max_date, 
    min_value=min_date, 
    max_value=max_date,
    format="DD.MM.YYYY"
)

# Kontrola, aby uživatel nezadal konec dřív než začátek
if filter_start > filter_end:
    st.error("Logická chyba: Počáteční datum ('Načíst OD') nesmí být po koncovém datu ('Načíst DO').")
    st.stop()

# Vytvoření odfiltrovaných datasetů pro výpočty v grafech a tabulkách
cpi_filtered = cpi_raw.loc[filter_start:filter_end]
hicp_filtered = hicp_raw.loc[filter_start:filter_end]
cpi_yoy_filtered = cpi_yoy.loc[filter_start:filter_end]
cpi_mom_filtered = cpi_mom.loc[filter_start:filter_end]
hicp_yoy_filtered = hicp_yoy.loc[filter_start:filter_end]

st.sidebar.markdown("---")

# B) Vizuální zoom (jen nastaví defaultní pohled Plotly grafu, neřeže data)
st.sidebar.subheader("2. Pohled grafu")
st.sidebar.caption("Nastavte počáteční výřez pro všechny grafy. Tato volba nemění výpočty, pouze určuje, jakou část dat uvidíte ihned po načtení. V grafech se pak můžete ručně posouvat v celém rozsahu zvoleném v kroku 1.")

filtered_min_date = cpi_filtered.index.min().date()
filtered_max_date = cpi_filtered.index.max().date()

# Defaultně zoomujeme na posledních 7 let
seven_years_offset = (pd.to_datetime(filtered_max_date) - pd.DateOffset(years=7)).date()
default_zoom = max(filtered_min_date, seven_years_offset)
default_zoom = min(default_zoom, filtered_max_date)

zoom_start = st.sidebar.date_input(
    "Zobrazit OD", 
    value=default_zoom, 
    min_value=filtered_min_date, 
    max_value=filtered_max_date,
    format="DD.MM.YYYY"
)

zoom_end = st.sidebar.date_input(
    "Zobrazit DO", 
    value=filtered_max_date, 
    min_value=filtered_min_date, 
    max_value=filtered_max_date,
    format="DD.MM.YYYY"
)

# Kontrola, aby uživatel nezadal konec dřív než začátek
if zoom_start > zoom_end:
    st.error("Logická chyba: Počáteční datum ('Načíst OD') nesmí být po koncovém datu ('Načíst DO').")
    st.stop()

# Seznam [start, end], který budeme posílat do layoutu grafu
axis_view_range = [str(zoom_start), str(zoom_end)]

st.sidebar.markdown("---")

# C) Volba roku báze (Index = 100)
st.sidebar.subheader("3. Bazický rok (Index = 100)")
st.sidebar.caption("Zvolte referenční rok, vůči kterému se bude porovnávat historický cenový vývoj. Toto nastavení ovlivňuje záložku bazického indexu (Vývoj cen od určitého roku) a sekci Osobní inflace.")

available_years = sorted(cpi_raw.index.year.unique(), reverse=True)
default_base_idx = available_years.index(2020) if 2020 in available_years else 0

base_year = st.sidebar.selectbox(
    "Vyberte rok báze:",
    options=available_years,
    index=default_base_idx
)

# ==========================================
# PŘEVOD BAZICKÝCH INDEXŮ (REBASING)
# ==========================================

# 1. Přepočet CPI
cpi_base_subset = cpi_raw[cpi_raw.index.year == base_year]

if cpi_base_subset.empty:
    st.error(f"Základní data pro výpočet bazického roku {base_year} zcela chybí.")
    st.stop()

# Vydělíme celou řadu průměrem dané kageorie bazického roku a vynásobíme 100
cpi_base_mean = cpi_base_subset.mean()
cpi_rebased = (cpi_filtered / cpi_base_mean) * 100

# 2. Přepočet HICP (pokud pro ten rok existují data)
hicp_base_subset = hicp_raw[hicp_raw.index.year == base_year]

if not hicp_base_subset.empty:
    hicp_base_mean = hicp_base_subset.mean()
    hicp_rebased = (hicp_filtered / hicp_base_mean) * 100
else:
    # Pokud pro HICP data v daném roce nejsou, vrátíme jen prázdná data (NaN)
    hicp_rebased = hicp_filtered * np.nan


# ==========================================
# HLAVNÍ OBSAH DASHBOARDU
# ==========================================

st.title("Interaktivní dashboard pro analýzu inflace v ČR")

# Zobrazení horních metrik (KPI)
total_months = len(cpi_filtered)
str_start_date = cpi_filtered.index[0].strftime("%m.%Y")
str_end_date = cpi_filtered.index[-1].strftime("%m.%Y")

metric_col1, metric_col2, metric_col3 = st.columns([2, 1, 1])
metric_col1.metric("📅 Vybrané období", f"{str_start_date} – {str_end_date}")
metric_col2.metric("∑ Počet měsíců", f"{total_months}")

st.markdown("---")

# Definice záložek
tab_uvod, tab_vahy, tab_bazicky, tab_mezirocni, tab_mezimesicni, tab_vlastni = st.tabs([
    "🏠 Úvod a manuál",
    "⚖️ Váhy spotřebního koše",
    "📈 Vývoj cen od určitého roku", 
    "🧮 Meziroční inflace", 
    "📉 Meziměsíční inflace",
    "👤 Osobní inflace"
])

# ==========================================
# 0. ZÁLOŽKA: ÚVOD A METODIKA
# ==========================================

with tab_uvod:
    st.markdown("""
    Vítejte v aplikaci zaměřené na analýzu spotřebitelských cen. Nabízí detailní 
    přehled o vývoji cen zboží a služeb, stejně jako o struktuře výdajů průměrných českých domácností.
    

    Aplikace umožňuje sledovat dlouhodobý vývoj jednotlivých kategorií spotřebního koše, přičemž
    poskytuje informace o průměrném růstu, volatilitě či sezónním chování cen. Nabízí také
    možnost výpočtu osobní míry inflace podle zvolených parametrů.
    """)
    
    st.markdown("---")
    
    col_info1, col_info2 = st.columns(2)
    
    with col_info1:
        st.subheader("S jakými daty se pracuje?")
        st.markdown("""
        Veškerá data pocházejí z oficiálních a veřejně dostupných zdrojů Českého statistického úřadu.
        
        * **Data indexu spotřebitelských cen (CPI):** Aplikace primárně pracuje s národním CPI, členěným podle klasifikace ECOICOP do 12 kategorií spotřebního koše, což umožňuje sledovat cenový vývoj v jednotlivých oblastech výdajů.
        * **Srovnání s HICP:** Pro mezinárodní kontext jsou k dispozici také data harmonizovaného indexu spotřebitelských cen (HICP), který je používán v rámci EU a metodicky se liší zejména nezahrnutím imputovaného nájemného, zahrnutím tržeb z nákupů cizinců a každoroční aktualizací vah.
        * **Váhy spotřebního koše:** Výpočty vycházejí z výdajové struktury průměrné české domácnosti dle dat ČSÚ, váhy určují vliv jednotlivých položek na celkovou inflaci.
        """)
        
    with col_info2:
        st.subheader("Jak dashboard ovládat?")
        st.markdown("""
        Nastavení najdete v levém postranním panelu. Pokud panel není zobrazen při načtení, klikněte na tlačítko >> v levém horním rohu. Vaše nastavení se okamžitě projeví do všech grafů:
        
        1. **Načíst data (Období):** Zde si filtrujete fyzický rozsah dat. Pokud vás nezajímá určité období, jednoduše vyberte jiné datum v kalendáři. Znamená to, že v grafech uvidíte pouze zvolené období a zároveň nevybraná data nebudou zahrnuta do vypočtených statistik.
        2. **Pohled grafu:** Toto nastavení nemění samotná data ani výpočty, pouze vizuálně přiblíží grafy na vámi vybraný úsek času.
        3. **Bazický rok (Index = 100):** Toto je klíčové pro 3. záložku (Vývoj cen od určitého roku). Vyberete si rok, který bude sloužit jako základní rok (jeho hodnota je stanovena na 100). Pokud si vyberete rok 2015 a křivka dnes ukazuje 150, znamená to, že cenová hladina od té doby stoupla o 50 %.
        4. **Záložky:** V horní části dashboardu se nacházejí záložky s jednotlivými tématy. Více informací o jednotlivých záložkách se nachází níže.
        
        💡 *Tip k ovládání grafů: Všechny grafy jsou interaktivní. Tažením myší lze vybrat oblast pro přiblížení, najetím na křivku zobrazit přesné hodnoty v informačním okně, posouvat zobrazení, nebo kliknutím na položku v legendě skrýt konkrétní kategorii. Dvojklikem do grafu nebo použitím tlačítka Domů v pravém horním rohu se zobrazení vrátí do výchozího stavu.*
        """)

    st.markdown("---")
    
    st.subheader("Průvodce obsahem: Co se kde dozvíte?")
    
    guide_col1, guide_col2 = st.columns(2)
    
    with guide_col1:
        st.warning("""
        **⚖️ 1. Váhy spotřebního koše (struktura útrat)**
        * **Co tu najdete:** Anatomii české peněženky. Za co domácnosti vydávají nejvíce peněz?
        * **Co zjistíte:** Přehledná dlaždicová mapa vám ukáže detailní složení koše pro rok 2025. Pomocí historických grafů můžete zkoumat, jak se priority českých spotřebitelů v průběhu let vyvíjejí.
        """)

        st.info("""
        **📈 2. Vývoj cen od určitého roku (bazický index)**
        * **Co tu najdete:** Dlouhodobý pohled na to, jak rostou ceny vůči vámi zvolenému výchozímu roku. Vyjádřeno bazickým indexem.
        * **Co zjistíte:** O kolik procent vše podražilo od vašeho referenčního bodu a kolik ze své reálné kupní síly ztratila 1000 Kč bankovka. Najdete zde i detailní pohled na skokové zdražování bydlení.
        """)

        st.success("""
        **🧮 3. Meziroční inflace (meziroční index)**
        * **Co tu najdete:** Klasický ukazatel – srovnání se stejným měsícem loňského roku.
        * **Co zjistíte:** Prozkoumáte historické inflační vlny pomocí přehledné teplotní mapy. Zjistíte také, které konkrétní položky táhly inflaci nahoru ve vybraném měsíci a podíváte se na rozdíl mezi českou a evropskou metodikou.
        """)

    with guide_col2:
        st.error("""
        **📉 4. Meziměsíční inflace (meziměsíční index)**
        * **Co tu najdete:** Analýzu změn měsíc po měsíci. Ve kterých měsících se nejvíce zdražuje a kdy naopak zlevňuje?
        * **Co zjistíte:** Odhalíte skrytou sezónnost (např. že oblečení zlevňuje ve výprodejích v lednu). Dále zjistíte, jak velká nestabilita se týká jednotlivých roků a kategorií.
        """)

        st.markdown("""
        <div style="background-color: #f3e8ff; padding: 1rem; border-radius: 0.5rem; margin-bottom: 1rem; color: #5b21b6;">
            <div style="font-size: 1.05em; font-weight: bold; margin-bottom: 0.5rem;">👤 5. Moje inflace (osobní kalkulačka)</div>
            <ul style="margin-top: 0; margin-bottom: 0;">
                <li style="margin-bottom: 0.2rem;"><strong>Co tu najdete:</strong> Nástroj pro výpočet inflace přesně na míru dle vašich výdajů.</li>
                <li><strong>Co zjistíte:</strong> Neutrácíte za alkohol a nejezdíte autem? Zadejte si do formuláře nuly a upravte hodnoty podle svého života. Dashboard vám okamžitě ukáže, zda na vás inflace dopadá silněji, nebo naopak slaběji než na průměrného Čecha.</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

# --- ZÁPATÍ ---
st.markdown("<br><br>", unsafe_allow_html=True) 
st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: #808080; font-size: 0.85em; line-height: 1.6; padding-bottom: 30px;'>
        © 2026 Štěpán Kříž<br>
        Bakalářská práce: <em>Inflace v datech: vizualizace vývoje spotřebního koše</em><br>
        VŠE FIS | Studijní program: Matematické metody v ekonomii | Specializace: Datové analýzy a modelování<br>
        <br>
        Zdrojové kódy a data: 
        <a href='https://github.com/stepakriz/Bakalarska_prace' target='_blank' style='color: #808080; text-decoration: underline;'>
            GitHub repozitář
        </a>
    </div>
    """,
    unsafe_allow_html=True
)

# ==========================================
# 1. ZÁLOŽKA: VÁHY SPOTŘEBNÍHO KOŠE
# ==========================================
with tab_vahy:
    st.caption("Analýza složení spotřebního koše. Ten stanovuje strukturu výdajů průměrné české domácnosti. Na jejím základě je kategoriím přiřazena váha v % pro výpočet celkové inflace. Pokud je vaše osobní spotřeba jiná, může se vaše individuální inflace od té oficiální lišit. Zároveň uvidíte, jak se tyto priority v čase vyvíjejí.")

    # --- SEKCE 0: KONTROLA DAT A VÝPOČET KPI ---
    if weights_tree is not None:
        # Základní počty pro metriky
        total_main_categories = 12
        total_subcategories = len(weights_tree)
        
        # Nalezení řádku s nejvyšší vahou (dominantní položka)
        dominant_item_row = weights_tree.loc[weights_tree['Weight_Pct'].idxmax()]
        dominant_item_name = dominant_item_row['NAZEV']
        dominant_item_weight = dominant_item_row['Weight_Pct']
        
        # Horní karty s hlavními čísly
        w_col1, w_col2, w_col3 = st.columns(3)
        w_col1.metric(
            "📂 Počet hlavních kategorií", 
            f"{total_main_categories}",
            help="Hlavní kategorie klasifikace ECOICOP. Podle této klasifikace je spotřební koš rozdělen na 12 základních oddílů."
        )
        w_col2.metric(
            "🛒 Počet podkategorií", 
            f"{total_subcategories}",
            help="Jednotlivé hlavní kategorie mají další, podrobnější dělení. Toto je celkový počet těchto nižších úrovní."
        )
        w_col3.metric(
            "🏠 Největší výdaj", 
            f"{dominant_item_name}", 
            delta=f"{dominant_item_weight:.1f}".replace('.', ',') + " % koše", 
            delta_color="off",
            help="Jedná se o podkategorii. Je zde zobrazena proto, že si pod ní lze představit už mnohem konkrétnější výdaj než u obecné hlavní kategorie."
        )
        
        st.markdown("---")

    # Kontrola, jestli se data načetla
    if weights_tree is None or weights_history is None:
        st.error("Chyba: Datové soubory obsahující váhy spotřebního koše nejsou dostupné.")
    else:
        # --- GLOBÁLNÍ PŘÍPRAVA PRO VIZUALIZACE ---
        # Definice labelů a barev pro konzistentní vzhled všech grafů
        ecoicop_labels = {
            'E01': 'Potraviny a nealko nápoje', 'E02': 'Alkohol a tabák', 'E03': 'Odívání a obuv',
            'E04': 'Bydlení, voda, energie, paliva', 'E05': 'Bytové vybavení', 'E06': 'Zdraví',
            'E07': 'Doprava', 'E08': 'Pošty a telekomunikace', 'E09': 'Rekreace a kultura',
            'E10': 'Vzdělávání', 'E11': 'Stravování a ubytování', 'E12': 'Ostatní zboží a služby'
        }
        
        # Paleta barev pro 12 kategorií
        distinct_palette = [
            "#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462",
            "#b3de69", "#fccde5", "#d9d9d9", "#bc80bd", "#ccebc5", "#ffed6f"
        ]
        category_color_map = {name: distinct_palette[i] for i, name in enumerate(ecoicop_labels.values())}

        # --- SEKCE 1: DLAŽDICOVÝ GRAF (TREEMAP) ---
        st.subheader("1. Detailní struktura spotřebního koše (2025)")
        st.markdown("Hierarchický pohled na aktuální složení koše. Velikost obdélníku odpovídá váze dané kategorie ve výdajích domácností. Čím je plocha větší, tím více peněz průměrná domácnost za danou položku utratí. Hlavní kategorie jsou navíc barevně odlišeny.") 

        MAIN_FONT_SIZE = "18px" 

        def wrap_text_labels(text, max_width=18):
            """Zalamování dlouhých názvů v obdélnících přes textwrap."""
            if not isinstance(text, str): return text
            return "<br>".join(textwrap.wrap(text, width=max_width))

        def parse_main_category_code(code_str):
            """Regex/String parsování hlavního kódu (E01 atd.) pro barvení a legendu."""
            base_code = str(code_str).strip().split('.')[0] 
            if len(base_code) == 1 and base_code.isdigit(): return 'E0' + base_code
            if len(base_code) == 2 and base_code.isdigit(): return 'E' + base_code
            return base_code
            
        def get_representative_examples(ecoicop_code):
            """Slovník příkladů pro hover okno v grafu (očištěno od prefixů)."""
            code = str(ecoicop_code).strip()
            if code.startswith('E'): code = code[1:]
            if code.startswith('0') and len(code) > 1: code = code[1:]

            # Logika startswith pro přiřazení popisků kódům
            if code.startswith('1.1'): return "Rýže, mouka, pečivo, maso a uzeniny, ryby, mléčné výrobky, vejce, oleje, ovoce, zelenina, cukrovinky, koření..."
            if code.startswith('1.2'): return "Minerální vody, limonády, ovocné džusy, káva, čaj, kakao..."
            if code.startswith('2.1'): return "Destiláty (vodka, rum), víno, sekt, pivo (výčepní, ležák, v restauraci)..."
            if code.startswith('2.2'): return "Cigarety, tabák pro ruční balení, zahřívaný tabák..."
            if code.startswith('3.1'): return "Pánské, dámské a dětské oděvy, jeansy, trička, spodní prádlo, čištění oděvů..."
            if code.startswith('3.2'): return "Vycházková obuv (pánská, dámská, dětská), sportovní boty, opravy obuvi..."
            if code.startswith('4.1'): return "Čisté nájemné v nájemních a družstevních bytech..."
            if code.startswith('4.2'): return "Náklady na pořízení domu/bytu <br>(nezahrnují ceny pozemků, nákupy starších nemovitostí ani splátky a úroky z hypoték),<br> rekonstrukce, stavební práce..."
            if code.startswith('4.3'): return "Malířské a instalatérské práce, obkladačky, umyvadla, barvy, tmely..."
            if code.startswith('4.4'): return "Vodné, stočné, poplatky za odvoz odpadu, ostatní služby související s bydlením..."
            if code.startswith('4.5'): return "Elektřina, zemní plyn, propan-butan, uhlí, palivové dřevo, dálkové vytápění..."
            if code.startswith('5.1'): return "Nábytek (skříně, postele), matrace, osvětlení, koberce..."
            if code.startswith('5.2'): return "Povlečení, záclony, ručníky..."
            if code.startswith('5.3'): return "Chladničky, pračky, myčky, sporáky, vysavače..."
            if code.startswith('5.4'): return "Talíře, hrnky, příbory, hrnce..."
            if code.startswith('5.5'): return "Nářadí pro kutily (vrtačky), zahradní technika..."
            if code.startswith('5.6'): return "Prací prášky, aviváže, saponáty, úklidové služby..."
            if code.startswith('6.1'): return "Léčiva, vitamíny, doplatky na recepty, kontaktní čočky, brýle..."
            if code.startswith('6.2'): return "Služby zubaře, vyšetření u specialistů, fyzioterapie..."
            if code.startswith('6.3'): return "Lázeňská péče..."
            if code.startswith('7.1'): return "Nová a ojetá osobní auta, motocykly, jízdní kola..."
            if code.startswith('7.2'): return "Pohonné hmoty (Benzin, Nafta, LPG), pneu, servis, dálniční známky, parkovné..."
            if code.startswith('7.3'): return "Dopravní služby: jízdenky na vlak a autobus, MHD, letenky, taxi..."
            if code.startswith('8.1'): return "Poštovní známky, balíkové služby, doručení zásilek..."
            if code.startswith('8.2'): return "Mobilní telefony, nabíječky, příslušenství..."
            if code.startswith('8.3'): return "Mobilní tarify, internet na doma..."
            if code.startswith('9.1'): return "Televizory, notebooky, tiskárny, tablety..."
            if code.startswith('9.2'): return "Velká zařízení (karavany, lodě, velká sportovní výbava)..."
            if code.startswith('9.3'): return "Hračky, Lego, sportovní potřeby, krmivo pro zvířata, rostliny..."
            if code.startswith('9.4'): return "Kino, divadlo, vstupné na sport, rozhlasové a TV poplatky..."
            if code.startswith('9.5'): return "Knihy, noviny, časopisy, papírnictví..."
            if code.startswith('9.6'): return "Dovolená se službami: zájezdy k moři, pobyty na horách..."
            if code.startswith('10.1'): return "Úhrada v mateřské škole (školkovné)..."
            if code.startswith('10.2'): return "Školné na soukromých gymnáziích a středních školách..."
            if code.startswith('10.3'): return "Školné za pomaturitní studium..."
            if code.startswith('10.4'): return "Školné na soukromých a veřejných VŠ, školné na VOŠ, poplatek za přijímací řízení..."
            if code.startswith('10.5'): return "Výuka cizích jazyků, kurzy v ZUŠ, úhrada za školní družinu, rekvalifikační kurzy..."
            if code.startswith('11.1'): return "Obědy v restauracích, fast food, kavárny, školní/závodní jídelny..."
            if code.startswith('11.2'): return "Ubytovací služby: hotely, penziony, koleje..."
            if code.startswith('12.1'): return "Kadeřník, kosmetika, drogerie, parfémy..."
            if code.startswith('12.3'): return "Šperky, hodinky, kočárky, deštníky..."
            if code.startswith('12.4'): return "Domovy důchodců, jesle, pečovatelská služba..."
            if code.startswith('12.5'): return "Povinné ručení, havarijní, domácnosti, úrazové..."
            if code.startswith('12.6'): return "Bankovní poplatky, vedení účtu..."
            if code.startswith('12.7'): return "Správní poplatky, pasy, právní služby, poplatky za psy..."

            return "Specifické položky této kategorie"

        # Příprava dat pro Treemap (parsování a formátování názvů)
        weights_tree['Parsed_Main_Code'] = weights_tree['ECOICOP'].apply(parse_main_category_code)
        is_subcategory = weights_tree['ECOICOP'].astype(str).str.contains('.', regex=False)
        treemap_subcategories = weights_tree[is_subcategory].copy()

        treemap_subcategories['Main_Cat_Name'] = treemap_subcategories['Parsed_Main_Code'].map(ecoicop_labels).fillna(treemap_subcategories['Parsed_Main_Code'])
        treemap_subcategories['Main_Cat_Formatted'] = f"<span style='font-size:{MAIN_FONT_SIZE}'><b>" + treemap_subcategories['Main_Cat_Name'] + "</b></span>"

        treemap_subcategories['Subcat_Name_Wrapped'] = treemap_subcategories['NAZEV'].apply(
            lambda x: "<b>" + wrap_text_labels(x, max_width=18) + "</b>"
        )

        treemap_subcategories['Main_Cat_Total_Weight'] = treemap_subcategories.groupby('Main_Cat_Name')['Weight_Pct'].transform('sum')
        treemap_subcategories['Examples'] = treemap_subcategories['ECOICOP'].apply(get_representative_examples)

        # Plotly Treemap graf
        formatted_color_map = {f"<span style='font-size:{MAIN_FONT_SIZE}'><b>{k}</b></span>": v for k, v in category_color_map.items()}

        fig_treemap = px.treemap(
            treemap_subcategories,
            path=['Main_Cat_Formatted', 'Subcat_Name_Wrapped'], 
            values='Weight_Pct', 
            color='Main_Cat_Formatted',                   
            color_discrete_map=formatted_color_map,              
            custom_data=['Main_Cat_Name', 'Main_Cat_Total_Weight', 'NAZEV', 'Examples']
        )

        # Úprava popisků při najetí (hovertemplate)
        fig_treemap.update_traces(
            root_color="lightgrey",
            textinfo="label+value",
            textposition="middle center",
            texttemplate="%{label}<br>%{value:.1f} %",
            hovertemplate=(
                "<b>Hlavní kategorie: %{customdata[0]}</b><br>"
                "Celkové zastoupení: %{customdata[1]:.2f} %<br>"
                "--------------------------------------<br>"
                "<b>Podkategorie: %{customdata[2]}</b><br>"
                "Zastoupení podkategorie: %{value:.2f} %<br>"
                "<br>"
                "<i>Typičtí zástupci v koši:</i><br>"
                "<i>%{customdata[3]}</i>"
                "<extra></extra>"
            )
        )

        fig_treemap.update_layout(
            margin=dict(t=30, l=5, r=5, b=5),
            font=dict(family="Arial"), 
            height=800,
            separators=", "
        )

        st.plotly_chart(fig_treemap, use_container_width=True)
        st.markdown("---")

        # --- SEKCE 2: STACKED AREA (VÝVOJ VAH V ČASE) ---
        st.subheader("2. Historický vývoj: Jak se měnily priority domácností?")
        st.markdown("""
        Stoprocentní plošný graf zobrazuje změnu váhy jednotlivých kategorií v průběhu let. 
        Spotřební koš je standardně fixován pro dvouletá období (např. hodnoty pro roky 2014 a 2015 jsou shodné). 
        Kategorie jsou seřazeny od nejvýznamnějších (spodní část grafu) po méně významné.
        """)

        def hex_to_rgba_transparent(color, opacity):
            """Převede hexadecimální barvu na formát RGBA s definovanou průhledností."""
            if color.startswith('#'):
                color = color.lstrip('#')
                r, g, b = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
                return f"rgba({r}, {g}, {b}, {opacity})"
            return color

        # Extrakce dostupných let z hlaviček sloupců (v datech jsou typicky jen "liché" roky definující koš)
        available_year_cols = [col for col in weights_history.columns if str(col).isdigit()]
        basket_years = sorted(available_year_cols, key=int)

        # Matematická duplikace let pro vytvoření "schodového" efektu platnosti koše
        timeline_years = []
        for year in basket_years:
            timeline_years.append(int(year) - 1) # Sudý rok (kdy platí stejný koš z předchozího roku)
            timeline_years.append(int(year))     # Lichý rok (nově stanovený koš)

        weights_history_sorted = weights_history.copy()
        
        # Funkce pro sjednocení různorodých historických názvů do 12 hlavních kategorií ECOICOP
        def normalize_history_category_names(hist_name):
            hist_name = str(hist_name).lower()
            if 'potrav' in hist_name: return ecoicop_labels['E01']
            if 'alkohol' in hist_name or 'tabák' in hist_name: return ecoicop_labels['E02']
            if 'odívání' in hist_name or 'obuv' in hist_name: return ecoicop_labels['E03']
            if 'bydlení' in hist_name or 'voda' in hist_name or 'energi' in hist_name: return ecoicop_labels['E04']
            if 'vybavení' in hist_name or 'zařízení' in hist_name or 'opravy' in hist_name: return ecoicop_labels['E05']
            if 'zdraví' in hist_name: return ecoicop_labels['E06']
            if 'doprav' in hist_name: return ecoicop_labels['E07']
            if 'pošt' in hist_name or 'telekom' in hist_name: return ecoicop_labels['E08']
            if 'rekreac' in hist_name or 'kultur' in hist_name: return ecoicop_labels['E09']
            if 'vzděláv' in hist_name: return ecoicop_labels['E10']
            if 'stravování' in hist_name or 'ubytování' in hist_name or 'restaurace' in hist_name: return ecoicop_labels['E11']
            if 'ostatní' in hist_name: return ecoicop_labels['E12']
            return hist_name

        weights_history_sorted['Mapped_Name'] = weights_history_sorted['NAZEV'].apply(normalize_history_category_names)
        
        # Seřazení kategorií podle jejich celkové historické váhy (největší vykreslujeme jako první/dole)
        weights_history_sorted['Aggregate_Weight'] = weights_history_sorted[basket_years].sum(axis=1)
        weights_history_sorted = weights_history_sorted.sort_values(by='Aggregate_Weight', ascending=False)
        
        fig_area = go.Figure()

        for row in weights_history_sorted.itertuples():
            original_values = weights_history_sorted.loc[row.Index, basket_years].values
            
            # Duplikace datových bodů pro každý rok z dvouletého období koše
            expanded_values = []
            for val in original_values:
                expanded_values.extend([val, val])
            
            base_color = category_color_map.get(row.Mapped_Name, '#888888')
            fill_color = hex_to_rgba_transparent(base_color, 0.7) 

            # Parametr shape='hv' vykreslí rovnou čáru a pak kolmý skok, čímž věrně reprezentuje skokovou změnu koše
            fig_area.add_trace(go.Scatter(
                x=timeline_years,
                y=expanded_values,
                name=row.NAZEV,
                mode='lines',
                line=dict(width=0.5, color='rgba(255,255,255,0.8)', shape='hv'),
                stackgroup='one',
                groupnorm='percent',
                fillcolor=fill_color,
                hovertemplate="<b>%{y:.2f} %</b>", 
            ))

        fig_area.update_layout(
            template='plotly_white', height=600,
            xaxis_title="Rok",
            yaxis_title="Podíl na spotřebním koši (%)",
            xaxis=dict(type='linear', dtick=1, showgrid=True, gridcolor='rgba(0,0,0,0.1)', tickangle=0),
            yaxis=dict(range=[0, 100], ticksuffix=' %', dtick=10, showgrid=True, gridcolor='rgba(0,0,0,0.1)'),
            hovermode="x unified",
            margin=dict(l=50, r=50, t=20, b=50),
            legend=dict(traceorder="reversed"),
            separators=", "
        )

        st.plotly_chart(fig_area, use_container_width=True)
        st.markdown("---")

        # --- SEKCE 3: SROVNÁVACÍ GRAF (LOLLIPOP CHART) ---
        st.subheader("3. Srovnání spotřebních košů ve dvou zvolených obdobích")
        st.markdown("""
        Porovnání vah pomocí lízátkového grafu. 
        Tenká linka a bod umožňují velmi přesně sledovat i zlomkové změny v procentních bodech.
        Červené body značí, že daná kategorie na významu nabrala, modré body indikují její pokles.
        """)

        # Příprava textových popisků pro uživatelský výběr (např. z roku "2015" udělá "2014–2015")
        period_labels = [f"{int(year)-1}–{year}" for year in basket_years]
        label_to_column_map = dict(zip(period_labels, basket_years))

        # Uživatelské vstupy pro volbu srovnávaných období
        col_old, col_new = st.columns(2)
        with col_old:
            selected_base_period = st.selectbox("Výchozí období (starší koš):", period_labels, index=0, key="vahy_period_old")
        with col_new:
            selected_target_period = st.selectbox("Srovnávací období (novější koš):", period_labels, index=len(period_labels)-1, key="vahy_period_new")

        base_year_col = label_to_column_map[selected_base_period]
        target_year_col = label_to_column_map[selected_target_period]

        # Výpočet dat pro lízátkový graf
        if base_year_col and target_year_col:
            comparison_df = weights_history.copy()
            comparison_df['Weight_Diff'] = comparison_df[target_year_col] - comparison_df[base_year_col]
             
            def simplify_category_name(name):
                """Zkrátí dlouhé názvy pro lepší čitelnost na ose Y grafu."""
                name = str(name).strip()
                replacements = {
                    'Potraviny a nealkoholické nápoje': 'Potraviny a nealko',
                    'Alkoholické nápoje, tabák': 'Alkohol a tabák',
                    'Bydlení, voda, energie, paliva': 'Bydlení a energie',
                    'Bytové vybavení, zařízení domácnosti; opravy': 'Vybavení bytu'
                }
                for old, new in replacements.items():
                    name = name.replace(old, new)
                # Odstranění počátečních čísel
                name = re.sub(r'^\d+\s*', '', name)
                return name

            comparison_df['Display_Name'] = comparison_df['NAZEV'].apply(simplify_category_name)
            comparison_df = comparison_df.sort_values('Weight_Diff', ascending=True)

            # --- Vizuální styling grafu ---
            COLOR_INCREASE = '#d32f2f'        # Červená pro nárůst váhy
            COLOR_DECREASE = '#1f77b4'       # Modrá pro pokles váhy
            COLOR_STEM_INC = '#f4bcbc'       # Světle červená pro spojovací linku 
            COLOR_STEM_DEC = '#a8cce8'       # Světle modrá pro spojovací linku 

            marker_colors = [COLOR_INCREASE if val > 0 else COLOR_DECREASE for val in comparison_df['Weight_Diff']]
            stem_colors = [COLOR_STEM_INC if val > 0 else COLOR_STEM_DEC for val in comparison_df['Weight_Diff']]

            fig_lollipop = go.Figure()

            # Vykreslení spojovacích linek 
            for i in range(len(comparison_df)):
                fig_lollipop.add_shape(
                    type='line',
                    x0=0, y0=i,
                    x1=comparison_df['Weight_Diff'].iloc[i], y1=i,
                    line=dict(color=stem_colors[i], width=2),
                    layer='below'
                )

            # Vykreslení koncových bodů
            fig_lollipop.add_trace(go.Scatter(
                y=comparison_df['Display_Name'],
                x=comparison_df['Weight_Diff'],
                mode='markers',
                marker=dict(
                    color=marker_colors, 
                    size=14, 
                    line=dict(width=2, color='white') # Bílé ohraničení oddělí bod od mřížky pozadí
                ),
                customdata=comparison_df[[base_year_col, target_year_col, 'NAZEV']].values,
                hovertemplate=(
                    "<b>Změna: %{x:.2f} p. b.</b><br>"
                    f"Podíl {selected_target_period}: %{{customdata[1]:.2f}} %<br>"
                    f"Podíl {selected_base_period}: %{{customdata[0]:.2f}} %<extra></extra>"
                )
            ))

            # Nastavení dynamického rozsahu osy X tak, aby body nebyly nalepené na okraj grafu
            x_min, x_max = comparison_df['Weight_Diff'].min(), comparison_df['Weight_Diff'].max()
            x_padding = max(abs(x_min), abs(x_max)) * 0.4
            
            # Středová osa reprezentující nulovou změnu
            fig_lollipop.add_vline(x=0, line_color="#333333", line_width=0.8, opacity=0.5)

            fig_lollipop.update_layout(
                template='plotly_white', height=650,
                xaxis_title="Změna váhy v procentních bodech (p. b.)",
                xaxis=dict(
                    zeroline=False, 
                    range=[x_min - x_padding, x_max + x_padding],
                    showgrid=True, gridcolor='#eaeaea', 
                    tickfont=dict(color='#7f7f7f') 
                ),
                yaxis=dict(
                    showgrid=False, 
                    type='category',
                    tickfont=dict(color='#7f7f7f', size=12) 
                ),
                margin=dict(l=10, t=30, r=10, b=40),
                showlegend=False,
                separators=", "
            )

            st.plotly_chart(fig_lollipop, use_container_width=True)
         

# ==========================================
# 2. ZÁLOŽKA: BAZICKÝ INDEX
# ==========================================
with tab_bazicky:
    # Úvodní vysvětlení logiky bazického indexu pro uživatele
    st.caption(f"Bazický index zobrazuje vývoj cenové hladiny v čase. Hodnota 100 je fixována k průměru roku {base_year}. Pokud křivka vystoupá na 120, znamená to, že ceny jsou o 20 % vyšší než v roce {base_year}.")

    TOTAL_COL = 'Úhrn'
    cpi_total = cpi_rebased[TOTAL_COL]
    
    # --- VÝPOČET KLÍČOVÝCH UKAZATELŮ (KPI) ---
    val_start = cpi_total.iloc[0]
    val_end = cpi_total.iloc[-1]
    
    # Procentuální nárůst za celé vybrané období
    total_growth_pct = ((val_end / val_start) - 1) * 100
    
    # Výpočet ztráty kupní síly pro referenční částku 1 000 Kč
    purchasing_power_1k = 1000 * (val_start / val_end)
    purchasing_power_loss = 1000 - purchasing_power_1k
    
    # Vykreslení informačních panelů (KPI karet)
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    
    kpi1.metric(
        label="📈 Celkový nárůst cen za období",
        value=f"{total_growth_pct:+.1f}".replace('.', ',') + " %",
        help=f"Celková procentuální změna cenové hladiny mezi {str_start_date} a {str_end_date}."
    )
    
    kpi2.metric(
        label="📉 Kupní síla 1 000 Kč",
        value=f"{purchasing_power_1k:.0f} Kč", 
        delta=f"-{purchasing_power_loss:.0f} Kč (Ztráta)",
        help=f"Reálná hodnota tisícikoruny z počátku období {str_start_date} přepočtená na cenovou hladinu v měsíci {str_end_date}."
    )
    
    kpi3.metric(
        label="📅 Bazický rok (Index = 100)",
        value=base_year,
        help="Referenční rok, jehož průměrná cenová hladina představuje hodnotu 100."
    )

    kpi4.metric(
        label=f"🏁 Aktuální index ({base_year}=100)",
        value=f"{val_end:.1f}".replace('.', ','),
        help=f"Nejnovější hodnota indexu vztažená k průměru roku {base_year}."
    )
    
    st.markdown("---")

    # --- POMOCNÉ FUNKCE PRO GRAFY ---
    # Centrální definice formátování časových os na český standard pro popisky (hover texty)
    CZ_MONTHS = {
        1: 'Leden', 2: 'Únor', 3: 'Březen', 4: 'Duben', 5: 'Květen', 6: 'Červen',
        7: 'Červenec', 8: 'Srpen', 9: 'Září', 10: 'Říjen', 11: 'Listopad', 12: 'Prosinec'
    }

    def format_dates_cz(date_index):
        """Převede časový index Pandas na seznam českých popisků (např. 'Leden 2023')."""
        return [f"{CZ_MONTHS[d.month]} {d.year}" for d in date_index]

    standard_hover_fmt = "<b>%{customdata}</b><br>Index: %{y:.2f}<extra></extra>"

    # --- GRAF 1: HLAVNÍ PŘEHLED VÝVOJE ---
    rebasing_help_text = ("Metodická poznámka k výpočtům: Aplikace provádí přebázování indexů na vámi zvolený rok pomocí průměrů z již publikovaných (a na jedno desetinné místo zaokrouhlených) dat. "
            "Český statistický úřad však pro tvorbu dlouhodobých řad využívá k řetězení indexů přesné interní konstanty a nezaokrouhlená mikrodata. "
            "Vlivem postupného zaokrouhlování se tak mohou zobrazené hodnoty bazických indexů lišit od oficiálních výstupů ČSÚ v řádu 0,1 až 0,2 bodu. "
    ) 

    st.subheader(f"1. Celkový vývoj cenové hladiny (báze {base_year}=100)", help=rebasing_help_text)
    st.markdown("Základní pohled na úhrnnou inflaci. Červená část křivky zvýrazňuje vývoj od vámi zvoleného referenčního roku do současnosti.")

    fig_main = go.Figure()
    
    # Rozdělení dat na historický kontext a sledované období (od 1. ledna bazického roku)
    tracking_start_date = pd.to_datetime(f"{base_year}-01-01")
    tracked_mask = cpi_rebased.index >= tracking_start_date
    
    cpi_tracked = cpi_rebased.loc[tracked_mask]
    
    # Vykreslení historické části křivky (šedá, potlačená do pozadí)
    fig_main.add_trace(go.Scatter(
        x=cpi_rebased.index, 
        y=cpi_total, 
        mode='lines', 
        name=f'Historický kontext (do r. {base_year})', 
        line=dict(color='#b0b0b0', width=2),
        customdata=format_dates_cz(cpi_rebased.index), 
        hovertemplate=standard_hover_fmt
    ))
    
    # Vykreslení sledované části křivky (červená a dominantní), pokud data pro toto období existují
    if not cpi_tracked.empty:
        fig_main.add_trace(go.Scatter(
            x=cpi_tracked.index, 
            y=cpi_tracked[TOTAL_COL], 
            mode='lines', 
            name=f'Sledovaný vývoj (od r. {base_year})', 
            line=dict(color='#d62728', width=4),
            customdata=format_dates_cz(cpi_tracked.index),
            hovertemplate=standard_hover_fmt
        ))
    
    # Zvýraznění referenční hranice 100 pro snazší orientaci
    fig_main.add_hline(
        y=100, line_color='black', line_width=0.8, line_dash="dash", opacity=0.5, 
        annotation_text=f"Průměr {base_year} = 100"
    )
    
    # Přidání výrazného bodu na samotný konec sledované řady
    fig_main.add_trace(go.Scatter(
        x=[cpi_rebased.index[-1]], 
        y=[val_end], 
        mode='markers+text', 
        marker=dict(color='#d62728', size=10),
        textposition="top left",
        textfont=dict(color='#d62728', size=12, weight='bold'),
        showlegend=False, 
        hoverinfo='skip'
    ))

    fig_main.update_layout(
        template='plotly_white', height=500, 
        xaxis_title="Rok",
        yaxis_title=f"Index ({base_year} = 100)", 
        xaxis=dict(tickformat="%Y", dtick="M12", showgrid=True, range=axis_view_range), 
        separators=", ",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=30)
    )
    st.plotly_chart(fig_main, use_container_width=True)
    st.markdown("---")

    # --- SEKCE 2: SMALL MULTIPLES (DETAIL KATEGORIÍ) ---
    st.subheader("2. Detailní vývoj jednotlivých kategorií")
    st.markdown("Rozpad indexu do 12 hlavních oddílů spotřebního koše. Zde vidíte, jak se vyvíjejí ceny důležitějších položek (oranžová) a těch méně důležitých (modré). Pro lepší srovnání jsou v pozadí světle šedou barvou vykresleny křivky ostatních kategorií.")

    categories = [col for col in cpi_rebased.columns if col != TOTAL_COL]
    n_cols = 3
    n_rows = (len(categories) + n_cols - 1) // n_cols
    
    category_titles = [cat.replace('\n', ' ') for cat in categories]
    formatted_dates_all = format_dates_cz(cpi_rebased.index)
    
    # Vytvoření mřížky grafů (subplots) s globálními titulky os pro čistší vizuál
    fig_categories = make_subplots(
        rows=n_rows, cols=n_cols, 
        subplot_titles=category_titles, 
        shared_xaxes=False,  
        shared_yaxes=False,  
        vertical_spacing=0.1,
        horizontal_spacing=0.06,
        x_title="Rok",
        y_title=f"Index ({base_year}=100)"
    )
    
    for i, cat in enumerate(categories):
        row = (i // n_cols) + 1
        col = (i % n_cols) + 1
        
        # Vykreslení šedých křivek ostatních kategorií na pozadí pro zachování kontextu celku
        for other_cat in categories:
            fig_categories.add_trace(go.Scatter(
                x=cpi_rebased.index, 
                y=cpi_rebased[other_cat], 
                mode='lines', 
                line=dict(color='#e0e0e0', width=0.5), 
                hoverinfo='skip', 
                showlegend=False
            ), row=row, col=col)
        
        # Barevné rozlišení: kritické položky (Bydlení, Potraviny) oranžově, zbytek standardně modře
        is_critical = any(keyword in cat for keyword in ['Bydlení', 'Potraviny'])
        line_color = '#d95f02' if is_critical else '#1f77b4'
        
        # Vykreslení hlavní křivky pro iterovanou kategorii
        fig_categories.add_trace(go.Scatter(
            x=cpi_rebased.index, 
            y=cpi_rebased[cat], 
            mode='lines', 
            line=dict(color=line_color, width=2), 
            showlegend=False,
            customdata=formatted_dates_all,
            hovertemplate=standard_hover_fmt
        ), row=row, col=col)
        
        fig_categories.add_hline(y=100, line_color='black', line_width=0.8, line_dash="dash", opacity=0.5, row=row, col=col)
        
        # Textové označení úplně poslední hodnoty na konci křivky
        cat_final_val = cpi_rebased[cat].iloc[-1]
        fig_categories.add_trace(go.Scatter(
            x=[cpi_rebased.index[-1]], 
            y=[cat_final_val], 
            mode='markers+text', 
            text=[f"{int(cat_final_val)}"], 
            textposition="middle right", 
            marker=dict(color=line_color, size=6), 
            textfont=dict(color=line_color, size=11, weight="bold"), 
            showlegend=False,
            hoverinfo='skip'
        ), row=row, col=col)

    fig_categories.update_annotations(font_size=12) 
    
    # Zvětšení okrajů zajišťuje, že Streamlit neořízne globální textové nadpisy os
    fig_categories.update_layout(
        template='plotly_white', 
        height=300 * n_rows,
        margin=dict(l=80, r=20, t=30, b=60), 
        separators=", "
    )
    
    fig_categories.update_xaxes(range=axis_view_range, tickformat="%Y", dtick="M12", showgrid=True)
    fig_categories.update_yaxes(showgrid=True, dtick=20) 
    st.plotly_chart(fig_categories, use_container_width=True)
    st.markdown("---")

    # --- SEKCE 3: BYDLENÍ (SPECIFICKÝ SCHODOVÝ GRAF) ---
    st.subheader("3. Specifikum: skokový růst cen bydlení a energií")
    st.markdown("Kategorie bydlení je velmi diskutovaná. Schodový graf ukazuje, že změny cen nejsou publikovány neustále, ale měsíčně. Díky schodům lze snadněji identifikovat znatelnější zdražení.")

    # Bezpečné dynamické vyhledání sloupce obsahujícího slovo 'Bydlení'
    housing_col = next((c for c in cpi_rebased.columns if 'Bydlení' in c), None)
    
    if housing_col:
        housing_series = cpi_rebased[housing_col]
        housing_latest_val = housing_series.iloc[-1]
        
        fig_housing = go.Figure()
        
        # Vykreslení referenční osy 100 jako základní linky
        fig_housing.add_trace(go.Scatter(
            x=housing_series.index, 
            y=[100] * len(housing_series), 
            mode='lines', 
            line=dict(color='rgba(0, 0, 0, 0.5)', width=0.8, dash='dash'), 
            hoverinfo='skip'
        ))
        
        # Parametr shape='hv' vykreslí křivku jako schody, což detailněji ukazuje jednorázové měsíční cenové skoky
        fig_housing.add_trace(go.Scatter(
            x=housing_series.index, 
            y=housing_series, 
            mode='lines', 
            name='Index bydlení', 
            line=dict(color='#d95f02', width=3.5, shape='hv'),
            fill='tonexty', 
            fillcolor='rgba(217, 95, 2, 0.1)',
            customdata=format_dates_cz(housing_series.index),
            hovertemplate=standard_hover_fmt
        ))
        
        # Zvýraznění posledního datového bodu
        fig_housing.add_trace(go.Scatter(
            x=[housing_series.index[-1]], 
            y=[housing_latest_val], 
            mode='markers', 
            marker=dict(color='#d95f02', size=12), 
            showlegend=False,
            hoverinfo='skip'
        ))

        fig_housing.update_layout(
            template='plotly_white', height=450,
            xaxis_title="Rok",
            yaxis_title=f"Index ({base_year}=100)",
            yaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.1)'),
            xaxis=dict(tickformat="%Y", dtick="M12", showgrid=True, gridcolor='rgba(0,0,0,0.1)', range=axis_view_range),
            showlegend=False,
            margin=dict(t=20),
            separators=", "
        )
        st.plotly_chart(fig_housing, use_container_width=True)
    else:
        st.warning("Data pro kategorii Bydlení nebyla nalezena.")
        
    st.markdown("---")

    # --- SEKCE 4: METODIKA CPI VS HICP ---
    hicp_help_text = (
        "Pokud zvolíte bazický rok 1999 a starší, křivka evropské metodiky (HICP) se v grafu nevykreslí. "
        "Je to dáno tím, že harmonizovaná data pro jsou k dispozici až od roku 2000."
    )
    st.subheader("4. Metodické srovnání: národní (CPI) vs. evropský (HICP) index", help=hicp_help_text)
    st.markdown("Graf ukazuje rozdíl mezi úhrnným národním indexem (CPI) a harmonizovaným indexem EU (HICP). Zásadními faktory odlišnosti jsou započítávání nákladů na vlastnické bydlení (imputovaného nájemného) v českém indexu a naopak zahrnutí útrat cizinců spolu s každoroční aktualizací vah u evropského indexu.")
    
    # Nalezení společného časového průniku obou indexů pro matematicky korektní porovnání
    common_timeline = cpi_rebased.index.intersection(hicp_rebased.index)
    
    if not common_timeline.empty:
        cpi_common = cpi_rebased.loc[common_timeline, TOTAL_COL]
        hicp_common = hicp_rebased.loc[common_timeline, TOTAL_COL]

        dates_common_cz = format_dates_cz(common_timeline)

        # Předvýpočet přesných rozdílů mezi indexy, které se zobrazí v interaktivním informačním okně (hover text)
        diff_cpi_hicp = cpi_common - hicp_common
        diff_hicp_cpi = hicp_common - cpi_common
        
        # Příprava vícerozměrných dat pro Plotly customdata (spojení data a vypočtené hodnoty)
        custom_data_cpi = np.column_stack((dates_common_cz, diff_cpi_hicp))
        custom_data_hicp = np.column_stack((dates_common_cz, diff_hicp_cpi))

        fig_methodology = go.Figure()
        
        # Vykreslení evropského HICP (čárkovaně, protože se jedná o sekundární pohled)
        fig_methodology.add_trace(go.Scatter(
            x=hicp_common.index, 
            y=hicp_common, 
            mode='lines', 
            name='HICP (EU)', 
            line=dict(color='#d95f02', dash='dash'),
            customdata=custom_data_hicp,
            hovertemplate="<b>%{customdata[0]}</b><br>HICP: %{y:.2f}<br>Rozdíl (HICP - CPI): %{customdata[1]:.2f} b.<extra></extra>"
        ))
        
        # Vykreslení národního CPI s šedou výplní mezi oběma čarami pro vizuální zdůraznění rozdílu
        fig_methodology.add_trace(go.Scatter(
            x=cpi_common.index, 
            y=cpi_common, 
            mode='lines', 
            name='CPI (ČR)', 
            line=dict(color='#1f77b4'), 
            fill='tonexty', 
            fillcolor='rgba(204,204,204,0.3)', 
            customdata=custom_data_cpi,
            hovertemplate="<b>%{customdata[0]}</b><br>CPI: %{y:.2f}<br>Rozdíl (CPI - HICP): %{customdata[1]:.2f} b.<extra></extra>"
        ))
        
        fig_methodology.add_hline(y=100, line_color='black', line_dash="dash", line_width=0.8, opacity=0.5)
        
        fig_methodology.update_layout(
            template='plotly_white', height=450, 
            xaxis_title="Rok",
            yaxis_title=f"Index ({base_year}=100)", 
            xaxis=dict(
                range=axis_view_range, 
                tickformat="%Y", 
                dtick="M12",
                showgrid=True, 
                gridcolor='rgba(0,0,0,0.1)' 
            ), 
            legend=dict(x=0.01, y=0.99, bgcolor='rgba(255,255,255,0.8)'),
            margin=dict(t=20),
            separators=", "
        )
        st.plotly_chart(fig_methodology, use_container_width=True)
    else:
        st.warning("Nedostatek společných dat pro srovnání metodiky CPI a HICP.")


# ==========================================
# 3. ZÁLOŽKA: MEZIROČNÍ INFLACE
# ==========================================
with tab_mezirocni:
    st.caption("Analýza vývoje aktuální inflace. Meziroční index srovnává daný měsíc vůči stejnému měsíci v minulém roce. Pokud určitá komodita v lednu 2024 stojí 40 Kč a v lednu 2025 cena vzrostla na 44 Kč, meziroční inflace je 10 %.")
    
    # --- SEKCE 0: KLÍČOVÉ UKAZATELE (KPI) ---
    cpi_yoy_total = cpi_yoy_filtered['Úhrn']
    
    # Výpočet klouzavého průměru inflace (metodika ČSÚ)
    # Porovnáváme sumu indexů posledních 12 měsíců vůči předchozím 12 měsícům
    latest_date = cpi_yoy_total.index[-1]
    current_pos = cpi_raw.index.get_loc(latest_date)

    if current_pos >= 23:
        # Nastavení oken pro klouzavý výpočet
        window_curr_end = cpi_raw.index[current_pos]
        window_curr_start = cpi_raw.index[current_pos - 11]
        window_prev_end = cpi_raw.index[current_pos - 12]
        window_prev_start = cpi_raw.index[current_pos - 23]
        
        period_now_str = f"{CZ_MONTHS[window_curr_start.month]} {window_curr_start.year} až {CZ_MONTHS[window_curr_end.month]} {window_curr_end.year}"
        period_prev_str = f"{CZ_MONTHS[window_prev_start.month]} {window_prev_start.year} až {CZ_MONTHS[window_prev_end.month]} {window_prev_end.year}"
        
        # Matematická sumace indexů
        sum_recent_12 = cpi_raw['Úhrn'].iloc[current_pos-11 : current_pos+1].sum()
        sum_previous_12 = cpi_raw['Úhrn'].iloc[current_pos-23 : current_pos-11].sum()
        
        avg_inflation_val = (sum_recent_12 / sum_previous_12 * 100) - 100
        help_text_avg = f"Index porovnává součet posledních 12 měsíců ({period_now_str}) vůči součtu předchozích 12 měsíců ({period_prev_str}). Slouží k eliminaci krátkodobých výkyvů."
    else:
        # Fallback pro krátké řady (prostý průměr)
        avg_inflation_val = cpi_yoy_total.mean()
        help_text_avg = "Prostý průměr zobrazených hodnot (nedostatečná historie pro klouzavý index ČSÚ)." 

    # Výpočet extrémů (MAX a MIN) v aktuálním filtru
    max_inf_val = cpi_yoy_total.max()
    max_inf_date = cpi_yoy_total.idxmax()
    max_inf_str = f"{CZ_MONTHS[max_inf_date.month]} {max_inf_date.year}"

    min_inf_val = cpi_yoy_total.min()
    min_inf_date = cpi_yoy_total.idxmin()
    min_inf_str = f"{CZ_MONTHS[min_inf_date.month]} {min_inf_date.year}"

    # Aktuální inflace a výpočet delty vůči loňsku
    curr_inf_val = cpi_yoy_total.iloc[-1]
    curr_inf_str = f"{CZ_MONTHS[latest_date.month]} {latest_date.year}"
    prev_year_date = latest_date - pd.DateOffset(years=1)

    if prev_year_date in cpi_yoy_total.index:
        prev_year_inf_val = cpi_yoy_total.loc[prev_year_date]
        delta_yoy = curr_inf_val - prev_year_inf_val
        comparison_text = f"Před rokem ({CZ_MONTHS[prev_year_date.month]} {prev_year_date.year}) byla inflace {prev_year_inf_val:.1f}".replace('.', ',') + " %."
    else:
        prev_inf_val = cpi_yoy_total.iloc[-2] if len(cpi_yoy_total) > 1 else curr_inf_val
        delta_yoy = curr_inf_val - prev_inf_val
        comparison_text = "Nedostatek dat pro srovnání s loňským rokem, zobrazena změna oproti předchozímu měsíci."

    curr_help_text = (
        f"Zobrazuje meziroční inflaci za měsíc {curr_inf_str}.\n\n"
        f"Spodní hodnota (delta) ukazuje změnu v procentních bodech (p. b.) "
        f"oproti stejnému měsíci předchozího roku.\n\n{comparison_text}"
    )

    # Vykreslení horní řady metrik
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)

    m_col1.metric("📊 Průměrná roční inflace", f"{avg_inflation_val:.1f}".replace('.', ',') + " %", help=help_text_avg)
    m_col2.metric("🚀 Vrchol (MAX)", f"{max_inf_val:.1f}".replace('.', ',') + " %", delta=max_inf_str, delta_color="off")
    m_col3.metric("❄️ Dno (MIN)", f"{min_inf_val:.1f}".replace('.', ',') + " %", delta=min_inf_str, delta_color="off")

    delta_formatted = f"{delta_yoy:+.2f}".replace('.', ',') + " p. b."
    m_col4.metric("🎯 Aktuální inflace", f"{curr_inf_val:.1f}".replace('.', ',') + " %", delta=delta_formatted, delta_color="inverse", help=curr_help_text)

    st.markdown("---")
    
    # --- SEKCE 1: TEPLOTNÍ MAPA (HEATMAP) ---
    st.subheader("1. Meziroční inflace v čase")
    st.markdown("Rychlý pohled na inflační vlny v historii. Tmavě červená pole značí období vysoké inflace, bílá až modrá pole značí období stability nebo deflace. Osa Y představuje roky, osa X měsíce.")

    if not cpi_yoy_filtered.empty:
        # Transformace dat na matici [Rok x Měsíc]
        heatmap_df = cpi_yoy_filtered.copy()
        heatmap_df['Year'] = heatmap_df.index.year
        heatmap_df['Month'] = heatmap_df.index.month
        
        heatmap_matrix = heatmap_df.pivot(index='Year', columns='Month', values='Úhrn')
        
        # Příprava českých popisků pro hover
        hover_matrix = [[f"{CZ_MONTHS[m]} {y}" for m in heatmap_matrix.columns] for y in heatmap_matrix.index]

        # Přejmenování sloupců na zkratky měsíců
        cz_short_months = {1:'Led', 2:'Úno', 3:'Bře', 4:'Dub', 5:'Kvě', 6:'Čvn', 7:'Čvc', 8:'Srp', 9:'Zář', 10:'Říj', 11:'Lis', 12:'Pro'}
        heatmap_matrix = heatmap_matrix.rename(columns=cz_short_months).sort_index()
        
        zoom_start_year = pd.to_datetime(zoom_start).year
        zoom_end_year = pd.to_datetime(zoom_end).year

        fig_heat = go.Figure(go.Heatmap(
            z=heatmap_matrix.values, 
            x=heatmap_matrix.columns, 
            y=heatmap_matrix.index, 
            colorscale='RdBu_r', 
            zmid=0, 
            text=heatmap_matrix.values, 
            texttemplate="%{z:.1f}", 
            textfont=dict(size=14),
            xgap=1, ygap=1,
            customdata=hover_matrix,
            hovertemplate="<b>%{customdata}</b><br>Inflace: %{z:.2f} %<extra></extra>"
        ))
        
        fig_heat.update_layout(
            template='plotly_white', height=600, xaxis_title="Měsíc", yaxis_title="Rok",
            xaxis=dict(side='top', tickfont=dict(size=14)), 
            yaxis=dict(dtick=1, range=[zoom_start_year - 0.5, zoom_end_year + 0.5], tickfont=dict(size=14)),
            margin=dict(t=80), separators=", "
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    st.markdown("---")

    # --- SEKCE 2: SMALL MULTIPLES PRO KATEGORIE ---
    st.subheader("2. Detailní vývoj jednotlivých kategorií")
    st.markdown("Porovnání vývoje cen v jednotlivých oddílech spotřebního koše. Oranžově jsou zvýrazněny klíčové kategorie (Bydlení, Potraviny), které mají největší dopad na peněženky domácností. Pro lepší srovnání jsou v pozadí světle šedou barvou vykresleny křivky ostatních kategorií.")
    
    hover_fmt_yoy = "<b>%{customdata}</b><br>Inflace: %{y:.2f} %<extra></extra>"
    yoy_categories = [col for col in cpi_yoy_filtered.columns if col != 'Úhrn']
    
    n_cols = 3
    n_rows = (len(yoy_categories) + n_cols - 1) // n_cols
    yoy_titles = [c.replace('\n', ' ') for c in yoy_categories]
    
    fig_grid_yoy = make_subplots(
        rows=n_rows, cols=n_cols, 
        subplot_titles=yoy_titles, 
        shared_xaxes=False,
        shared_yaxes=False,
        vertical_spacing=0.1,
        horizontal_spacing=0.06,
        x_title="Rok",
        y_title="Meziroční inflace (%)"
    )
    
    dates_text_yoy = format_dates_cz(cpi_yoy_filtered.index)

    for i, cat in enumerate(yoy_categories):
        row, col = (i // n_cols) + 1, (i % n_cols) + 1
        
        # Šedé pozadí ostatních kategorií pro kontext
        for other_cat in yoy_categories:
            fig_grid_yoy.add_trace(go.Scatter(
                x=cpi_yoy_filtered.index, 
                y=cpi_yoy_filtered[other_cat], 
                mode='lines', 
                line=dict(color='#e0e0e0', width=0.5), 
                hoverinfo='skip', 
                showlegend=False
            ), row=row, col=col)
        
        # Zvýraznění dominantních položek koše
        is_critical = any(keyword in cat for keyword in ['Bydlení', 'Potraviny'])
        line_color = '#d95f02' if is_critical else '#1f77b4'
        
        fig_grid_yoy.add_trace(go.Scatter(
            x=cpi_yoy_filtered.index, 
            y=cpi_yoy_filtered[cat], 
            mode='lines', 
            line=dict(color=line_color, width=2), 
            name=cat, 
            showlegend=False,
            customdata=dates_text_yoy,  
            hovertemplate=hover_fmt_yoy 
        ), row=row, col=col)
        
        fig_grid_yoy.add_hline(y=0, line_color='black', line_width=0.7, row=row, col=col, opacity=0.4)
        
        # Textový štítek poslední hodnoty
        cat_latest_val = cpi_yoy_filtered[cat].iloc[-1]
        fig_grid_yoy.add_trace(go.Scatter(
            x=[cpi_yoy_filtered.index[-1]], 
            y=[cat_latest_val], 
            mode='markers+text', 
            text=[f"{cat_latest_val:.1f}".replace('.', ',') + "%"], 
            textposition="middle right", 
            marker=dict(color=line_color, size=6), 
            textfont=dict(color=line_color, size=10, weight='bold'), 
            showlegend=False,
            hoverinfo='skip'
        ), row=row, col=col)

    fig_grid_yoy.update_annotations(font_size=12)
    fig_grid_yoy.update_layout(
        template='plotly_white', 
        height=300 * n_rows, 
        separators=", ",
        margin=dict(l=80, r=20, t=30, b=60)
    )
    fig_grid_yoy.update_xaxes(range=axis_view_range, tickformat="%Y", dtick="M12", showgrid=True)
    fig_grid_yoy.update_yaxes(dtick=5, zeroline=False)
    st.plotly_chart(fig_grid_yoy, use_container_width=True)

    st.markdown("---")

    # --- SEKCE 3: ODCHÝLENÍ OD PRŮMĚRNÉ INFLACE (LOLLIPOP CHART) ---
    st.subheader("3. Které kategorie rostou rychleji než celkový průměr?")
    st.markdown("Detailní pohled na vybraný měsíc. Graf ukazuje inflaci jednotlivých kategorií a jejich odlišnost od celkového průměru. Červené body v grafu značí nadprůměrný růst cen, modré naopak podprůměrný (nebo pokles).")
    
    # Custom CSS pro Streamlit slider (červená barva)
    st.markdown(
        """
        <style>
        div[data-baseweb="slider"] > div > div > div:first-child { background-color: #e6e6e6 !important; }
        div[data-baseweb="slider"] div[role="slider"] { background-color: rgb(255, 75, 75) !important; border-color: rgb(255, 75, 75) !important; }
        </style>
        """,
        unsafe_allow_html=True
    )

    # Očištění dat o počáteční NaN (první rok řady)
    valid_cpi_data = cpi_yoy_filtered.dropna(subset=['Úhrn'])
    available_dates = valid_cpi_data.index 
    
    if len(available_dates) > 0:
        selected_date = st.select_slider(
            "Vyberte měsíc a rok pro detailní analýzu:",
            options=available_dates,
            value=available_dates[-1],
            format_func=lambda x: x.strftime("%m.%Y")
        )
        
        if selected_date:
            monthly_data = valid_cpi_data.loc[selected_date]
            monthly_total = monthly_data['Úhrn']
            monthly_categories = monthly_data.drop('Úhrn').sort_values()
            
            # Upravení názvů kategorií (odstranění kódů a zkrácení)
            clean_labels = monthly_categories.index.str.replace(r'^\d+\s', '', regex=True)
            clean_labels = clean_labels.str.replace('Bydlení, voda, energie, paliva', 'Bydlení a energie').str.replace('Potraviny a nealkoholické nápoje', 'Potraviny')
            monthly_categories.index = clean_labels

            # Podmíněné barvení podle vzdálenosti od průměru
            lollipop_colors = ['#d62728' if val > monthly_total else '#1f77b4' for val in monthly_categories.values]
            lollipop_customdata = [f"{val - monthly_total:+.2f}".replace('.', ',') for val in monthly_categories.values]
            
            fig_lollipop = go.Figure()
            
            # Horizontální linky 
            fig_lollipop.add_trace(go.Bar(
                y=monthly_categories.index, 
                x=monthly_categories.values - monthly_total, 
                base=monthly_total, 
                orientation='h', 
                marker=dict(color=lollipop_colors, opacity=0.4), 
                width=0.05, 
                hoverinfo='skip', 
                showlegend=False
            ))
            
            # Datové body 
            fig_lollipop.add_trace(go.Scatter(
                x=monthly_categories.values, 
                y=monthly_categories.index, 
                mode='markers',  
                marker=dict(color=lollipop_colors, size=14), 
                customdata=lollipop_customdata, 
                hovertemplate="<b>Inflace: %{x:.2f} %</b><br>Vzdálenost od průměru: %{customdata} p. b.<extra></extra>",
                showlegend=False 
            ))
            
            # Svislá osa celkového průměru
            fig_lollipop.add_vline(x=monthly_total, line_dash="dash", line_color="#333333", opacity=0.8, line_width=1)
            fig_lollipop.add_annotation(
                x=monthly_total, y=1.02, yref="paper",      
                text=f"Průměr: {monthly_total:.2f} %".replace('.', ','),
                showarrow=False, font=dict(color="#333333", weight="bold"),
                bgcolor="rgba(255, 255, 255, 0.8)"
            )
            
            title_month_str = f"{CZ_MONTHS.get(selected_date.month)} {selected_date.year}"
            
            fig_lollipop.update_layout(
                template='plotly_white', 
                height=700, 
                title=dict(text=f"<b>Struktura inflace v měsíci: {title_month_str}</b>", font=dict(size=16)),
                xaxis_title="Meziroční inflace (%)", 
                xaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.1)', dtick=1), 
                margin=dict(l=20),
                showlegend=False,
                separators=", "
            )
            st.plotly_chart(fig_lollipop, use_container_width=True)
            st.markdown("---")

    # --- SEKCE 4: METODIKA CPI vs HICP (ROZDÍL V P. B.) ---
    st.subheader("4. Metodický rozdíl: CPI (ČR) minus HICP (EU)")
    st.markdown("O kolik procentních bodů se liší česká metodika od evropské? Modré sloupce ukazují, kdy česká inflace převyšuje tu harmonizovanou. Zásadními faktory odlišnosti jsou započítávání nákladů na vlastnické bydlení (imputovaného nájemného) v českém indexu a naopak zahrnutí útrat cizinců spolu s každoroční aktualizací vah u evropského indexu.")
    
    # Průnik indexů pro srovnatelné období
    common_timeline_yoy = cpi_yoy_filtered.index.intersection(hicp_yoy_filtered.index)
    method_diff = cpi_yoy_filtered.loc[common_timeline_yoy, 'Úhrn'] - hicp_yoy_filtered.loc[common_timeline_yoy, 'Úhrn']
    
    diff_dates_cz = format_dates_cz(method_diff.index)
    diff_values_str = [f"{val:+.2f}".replace('.', ',') for val in method_diff]
    diff_customdata = np.column_stack((diff_dates_cz, diff_values_str))
    
    diff_colors = ['#1f77b4' if val > 0 else '#d95f02' for val in method_diff]

    fig_diff = go.Figure()

    fig_diff.add_trace(go.Bar(
        x=method_diff.index, 
        y=method_diff, 
        marker_color=diff_colors, 
        name='Rozdíl (p. b.)', 
        showlegend=False,
        customdata=diff_customdata,
        hovertemplate="<b>%{customdata[0]}</b><br>Rozdíl: %{customdata[1]} p. b.<extra></extra>"
    ))

    # Pomocné stopy pro vytvoření legendy
    fig_diff.add_trace(go.Bar(x=[None], y=[None], marker_color='#1f77b4', name='Národní CPI je vyšší', hoverinfo='skip'))
    fig_diff.add_trace(go.Bar(x=[None], y=[None], marker_color='#d95f02', name='Evropské HICP je vyšší', hoverinfo='skip'))

    fig_diff.update_layout(
        template='plotly_white', height=600,
        xaxis_title="Rok",
        yaxis_title="Rozdíl (p. b.)",
        yaxis=dict(range=[-5, 5], showgrid=True, gridcolor='rgba(0,0,0,0.1)', zeroline=True, zerolinecolor='rgba(51, 51, 51, 0.5)', zerolinewidth=0.7),
        xaxis=dict(range=axis_view_range, tickformat="%Y", dtick="M12", showgrid=True, gridcolor='rgba(0,0,0,0.1)'),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.01),
        margin=dict(l=50, r=50, t=50, b=50),
        separators=", "
    )
    st.plotly_chart(fig_diff, use_container_width=True)
    st.markdown("---")

    # --- SEKCE 5: DETAIL BYDLENÍ ---
    st.subheader("5. Odlišnost metodik CPI a HICP u bydlení")
    st.markdown("Český index (modrá) zahrnuje do složky bydlení také imputované nájemné, zatímco harmonizovaný evropský index (oranžová) vychází z odlišné metodiky a pracuje zejména s čistými nájmy a energiemi. Zvýrazněná plocha znázorňuje rozdíl mezi oběma přístupy v čase.")
    
    # Nalezení patřičných sloupců v obou datasetech
    col_cpi_housing = next((col for col in cpi_yoy_filtered.columns if '04' in col or 'Bydlení' in col), None)
    col_hicp_housing = next((col for col in hicp_yoy_filtered.columns if '04' in col or 'Bydlení' in col), None)

    if col_cpi_housing and col_hicp_housing:
        cpi_house_series = cpi_yoy_filtered.loc[common_timeline_yoy, col_cpi_housing]
        hicp_house_series = hicp_yoy_filtered.loc[common_timeline_yoy, col_hicp_housing]
        
        diff_cpi_vs_hicp = cpi_house_series - hicp_house_series
        diff_hicp_vs_cpi = hicp_house_series - cpi_house_series
        
        custom_data_cpi_h = np.column_stack((diff_dates_cz, diff_cpi_vs_hicp))
        custom_data_hicp_h = np.column_stack((diff_dates_cz, diff_hicp_vs_cpi))

        fig_house = go.Figure()

        fig_house.add_trace(go.Scatter(
            x=hicp_house_series.index,
            y=hicp_house_series,
            mode='lines',
            name='HICP Bydlení (EU metodika)',
            line=dict(color='#d95f02', width=3, dash='dash'), 
            customdata=custom_data_hicp_h,
            hovertemplate="<b>%{customdata[0]}</b><br>HICP: %{y:.2f} %<br>Rozdíl (HICP - CPI): %{customdata[1]:.2f} p. b.<extra></extra>"
        ))

        fig_house.add_trace(go.Scatter(
            x=cpi_house_series.index,
            y=cpi_house_series,
            mode='lines',
            name='CPI Bydlení (ČR metodika)',
            line=dict(color='#1f77b4', width=3), 
            fill='tonexty', 
            fillcolor='rgba(204, 204, 204, 0.3)', 
            customdata=custom_data_cpi_h,
            hovertemplate="<b>%{customdata[0]}</b><br>CPI: %{y:.2f} %<br>Rozdíl (CPI - HICP): %{customdata[1]:.2f} p. b.<extra></extra>"
        ))

        fig_house.add_hline(y=0, line_color='black', line_width=0.5, opacity=0.5)

        fig_house.update_layout(
            template='plotly_white', height=600,
            xaxis_title="Rok", yaxis_title="Meziroční inflace (%)",
            xaxis=dict(range=axis_view_range, tickformat="%Y", dtick="M12", showgrid=True, gridcolor='rgba(0,0,0,0.1)'),
            yaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.1)', zeroline=False),
            legend=dict(x=0.01, y=0.99, bgcolor='rgba(255, 255, 255, 0.8)', bordercolor='#dddddd', borderwidth=1),
            margin=dict(l=50, r=50, t=50, b=50),
            separators=", "
        )
        st.plotly_chart(fig_house, use_container_width=True)
    else:
        st.warning("Data pro porovnání metodiky v kategorii Bydlení nebyla nalezena.")
        
# ==========================================
# 4. ZÁLOŽKA: MEZIMĚSÍČNÍ INFLACE
# ==========================================
with tab_mezimesicni:
    st.caption("Analýza krátkodobých výkyvů. Zobrazuje, jak se cenová hladina mění z měsíce na měsíc a jakou roli v tom hraje sezónnost.")
    
    # --- POMOCNÁ FUNKCE ---
    def geo_mean_pct(series):
        """
        Výpočet průměrného tempa růstu (geometrický průměr).
        Postup: převod % na koeficienty (1.02), výpočet průměru a převod zpět na %.
        """
        clean_series = series.dropna()
        if len(clean_series) == 0: 
            return np.nan
        growth_factors = 1 + (clean_series / 100)
        return (gmean(growth_factors) - 1) * 100

    # --- SEKCE 0: KLÍČOVÉ UKAZATELE (KPI) ---
    mom_total_series = cpi_mom_filtered['Úhrn']
    
    # Průměrné měsíční tempo růstu cen za zvolené období
    avg_mom_growth = geo_mean_pct(mom_total_series)
    
    # Počet měsíců, kdy ceny meziměsíčně klesaly
    deflation_months_count = (mom_total_series < 0).sum()
    
    # Vyhledání historického maxima meziměsíčního skoku
    max_jump_val = mom_total_series.max()
    max_jump_date = mom_total_series.idxmax()
    max_jump_str = f"{CZ_MONTHS[max_jump_date.month]} {max_jump_date.year}"
    
    start_mom_date = mom_total_series.index[0]
    end_mom_date = mom_total_series.index[-1]
    period_mom_str = f"{CZ_MONTHS[start_mom_date.month]} {start_mom_date.year} až {CZ_MONTHS[end_mom_date.month]} {end_mom_date.year}"

    # Zobrazení metrik v horní části záložky
    mm_col1, mm_col2, mm_col3 = st.columns(3)
    
    mm_col1.metric(
        "⚡ Průměrné meziměsíční tempo",
        f"{avg_mom_growth:.2f}".replace('.', ',') + " %",
        help=f"Průměrná změna cenové hladiny za jeden měsíc (vypočtená geometrickým průměrem za období {str_start_date} až {str_end_date})."
    )
    
    mm_col2.metric(
        "📉 Měsíce v deflaci",
        f"{deflation_months_count}",
        help="Absolutní počet měsíců ve vybraném období, kdy meziměsíční index klesl pod nulu."
    )
    
    mm_col3.metric(
        "🎢 Největší zdražení (MAX)",
        f"+{max_jump_val:.1f}".replace('.', ',') + " %",
        delta=max_jump_str,
        delta_color="off",
        help="Historicky nejvyšší zaznamenaný skok cen v rámci jednoho měsíce."
    )
    
    st.markdown("---")

    # --- SEKCE 1: TEPLOTNÍ MAPA SEZÓNNOSTI ---
    st.subheader("1. Průměrná měsíční změna: Kdy se zdražuje nejvíce?")
    st.markdown("Graf analyzuje průměrnou meziměsíční změnu cen pro jednotlivé kalendářní měsíce. Tmavě červená značí období pravidelného přeceňování (typicky leden). Modrá barva indikuje měsíce, kdy daná kategorie zlevňuje (např. sezónní výprodeje oděvů).")
    
    # Seskupení dat podle měsíců (1-12) pro odhalení sezónních vzorců
    seasonality_df = cpi_mom_filtered.copy()
    seasonality_df['Calendar_Month'] = seasonality_df.index.month
    seasonality_matrix = seasonality_df.groupby('Calendar_Month').agg(geo_mean_pct).T

    if 'Úhrn' in seasonality_matrix.index:
        seasonality_matrix = seasonality_matrix.drop('Úhrn')

    cz_short_months = {1: 'Led', 2: 'Úno', 3: 'Bře', 4: 'Dub', 5: 'Kvě', 6: 'Čvn', 7: 'Čvc', 8: 'Srp', 9: 'Zář', 10: 'Říj', 11: 'Lis', 12: 'Pro'}
    seasonality_matrix = seasonality_matrix.rename(columns=cz_short_months)

    # Funkce pro vyčištění a sjednocení názvů kategorií (odstranění kódů a zkrácení)
    def shorten_category_names(index_obj):
        idx = index_obj.str.replace(r'^\d+\.?\s*', '', regex=True)
        idx = idx.str.replace('\n', ' ')
        
        replacement_map = {
            'Potraviny a nealkoholické nápoje': 'Potraviny a nealko',
            'Bydlení, voda, energie, paliva': 'Bydlení a energie',
            'Bytové vybavení, zařízení domácnosti; opravy': 'Vybavení bytu',
            'Bytové vybavení, zařízení domácnosti, opravy': 'Vybavení bytu',
            'Stravování a ubytování': 'Restaurace a hotely',
            'Alkoholické nápoje, tabák': 'Alkohol a tabák',
            'Odívání a obuv': 'Oblečení a obuv',
            'Pošty a telekomunikace': 'Telekomunikace',
            'Ostatní zboží a služby': 'Ostatní'
        }
        for old_val, new_val in replacement_map.items():
            idx = idx.str.replace(old_val, new_val, regex=False)
        return idx

    seasonality_matrix.index = shorten_category_names(seasonality_matrix.index)

    # Seřazení kategorií podle fixního pořadí (váhy v koši)
    importance_order = [
        'Bydlení a energie', 'Potraviny a nealko', 'Doprava', 'Alkohol a tabák', 
        'Restaurace a hotely', 'Rekreace a kultura', 'Vybavení bytu', 'Ostatní', 
        'Oblečení a obuv', 'Telekomunikace', 'Zdraví', 'Vzdělávání'
    ]
    present_cols = [col for col in importance_order if col in seasonality_matrix.index]
    missing_cols = [col for col in seasonality_matrix.index if col not in present_cols]
    seasonality_matrix = seasonality_matrix.reindex(present_cols + missing_cols)

    # Vykreslení heatmapy sezónnosti
    fig_seasonality = go.Figure()
    fig_seasonality.add_trace(go.Heatmap(
        z=seasonality_matrix.values, 
        x=seasonality_matrix.columns, 
        y=seasonality_matrix.index,
        colorscale='RdBu_r', 
        zmid=0,
        text=seasonality_matrix.values, 
        texttemplate="%{z:.1f}", 
        textfont={"size": 14},
        xgap=1, ygap=1,
        hovertemplate="Kategorie: %{y}<br>Měsíc: %{x}<br>Prům. změna: <b>%{z:.2f} %</b><extra></extra>"
    ))
    
    fig_seasonality.update_layout(
        template='plotly_white', height=700,
        xaxis_title="Měsíc", 
        xaxis=dict(side='top', tickfont=dict(size=14)),
        yaxis=dict(tickfont=dict(size=14), autorange='reversed'),
        margin=dict(t=80, l=150),
        separators=", "
    )
    st.plotly_chart(fig_seasonality, use_container_width=True)

    st.markdown("---")

    # --- SEKCE 2: ANALÝZA VOLATILITY V LETECH ---
    st.subheader("2. Volatilita cen: rozpětí meziměsíční inflace v letech")
    st.markdown("Graf ukazuje roční stabilitu cen. Délka vertikální čáry značí absolutní rozdíl mezi nejvyšší a nejnižší meziměsíční změnou v daném roce. Oranžově jsou zvýrazněny roky s extrémní nestabilitou.")
    
    total_col_name = 'Úhrn' if 'Úhrn' in cpi_mom_filtered.columns else cpi_mom_filtered.columns[0]
    
    # Výpočet ročních statistik (min, max, geo průměr) pro měření kolísavosti
    volatility_stats = cpi_mom_filtered.groupby(cpi_mom_filtered.index.year)[total_col_name].agg(
        min='min', 
        max='max', 
        mean=geo_mean_pct
    )
    volatility_stats['spread'] = volatility_stats['max'] - volatility_stats['min']
    
    zoom_year_start = pd.to_datetime(zoom_start).year
    zoom_year_end = pd.to_datetime(zoom_end).year
    
    hover_template_volatility = (
        "<b>Rok %{x}</b><br>"
        "Průměr: %{customdata[3]:.2f} %<br>"
        "Maximální skok: %{customdata[1]:.2f} %<br>"
        "Minimální skok: %{customdata[0]:.2f} %<br>"
        "<b>Rozpětí (Volatilita): %{customdata[2]:.2f} p. b.</b><extra></extra>"
    )

    # Rozdělení dat na stabilní a nestabilní roky pro barevné odlišení v grafu
    x_stable, y_stable, custom_stable = [], [], []
    x_unstable, y_unstable, custom_unstable = [], [], []

    for year, metrics in volatility_stats.iterrows():
        v_min, v_max, v_spread, v_mean = metrics['min'], metrics['max'], metrics['spread'], metrics['mean']
        c_data = [v_min, v_max, v_spread, v_mean]
        
        # Roky s rozpětím nad 3 p. b. označujeme jako nestabilní
        if v_spread >= 3.0:
            x_unstable.extend([year, year, None])
            y_unstable.extend([v_min, v_max, None])
            custom_unstable.extend([c_data, c_data, None])
        else:
            x_stable.extend([year, year, None])
            y_stable.extend([v_min, v_max, None])
            custom_stable.extend([c_data, c_data, None])

    fig_volatility = go.Figure()
    
    # Vykreslení čar rozpětí (svislé úsečky)
    fig_volatility.add_trace(go.Scatter(
        x=x_stable, y=y_stable, customdata=custom_stable, mode='lines', 
        line=dict(color='#cccccc', width=3), name='Stabilní', hovertemplate=hover_template_volatility
    ))
    
    fig_volatility.add_trace(go.Scatter(
        x=x_unstable, y=y_unstable, customdata=custom_unstable, mode='lines', 
        line=dict(color='#d95f02', width=5), name='Nestabilní', hovertemplate=hover_template_volatility
    ))
    
    # Přidání bodů pro roční průměry
    stats_data_matrix = volatility_stats[['min', 'max', 'spread', 'mean']].values
    fig_volatility.add_trace(go.Scatter(
        x=volatility_stats.index, y=volatility_stats['mean'], mode='markers', 
        marker=dict(color='#1f77b4', size=8), name='Průměr', 
        customdata=stats_data_matrix, hovertemplate=hover_template_volatility 
    ))

    fig_volatility.update_layout(
        template='plotly_white', height=500,
        xaxis_title="Rok",
        yaxis_title="Meziměsíční změna (%)",
        yaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.1)', zeroline=True, zerolinewidth=0.5, zerolinecolor='rgba(51, 51, 51, 0.5)'),
        xaxis=dict(
            tickmode='linear', dtick=1, tickangle=-45, showgrid=False, 
            range=[zoom_year_start - 0.5, zoom_year_end + 0.5]
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.01),
        margin=dict(t=50),
        separators=", "
    )
    st.plotly_chart(fig_volatility, use_container_width=True)

    st.markdown("---")

    # --- SEKCE 3: MAPA RIZIKA (BUBLINOVÝ GRAF) ---
    st.subheader("3. Mapa rizika: stabilita vs. růst cen")
    st.markdown("""
    Tento graf člení spotřební kategorie do čtyř kvadrantů dle jejich aktuálního chování.
    * **Osa X (růst)**: Svislá čára odpovídá měsíčnímu ekvivalentu inflačního cíle ČNB (0,165 %), který je odvozen z meziročního přírůstku indexu spotřebitelských cen ve výši 2 %. Odděluje nízký růst od nadměrného zdražování.
    * **Osa Y (volatilita)**: Měřena jako mezikvartilové rozpětí (IQR), které odolává extrémům. Vodorovná čára ukazuje medián napříč všemi kategoriemi.
    * **Velikost bublin**: Znázorňuje váhu (významnost) položky ve spotřebním koši.
    """)
    
    st.caption("Pro převod na měsíční cíl se používá geometrický průměr (12. odmocnina z 1,02 - 1) * 100, což po zaokrouhlení činí 0,165 %.")

    # Odstranění celkového úhrnu pro srovnání čistě dílčích subkategorií
    risk_dataset = cpi_mom_filtered.drop(columns=['Úhrn'], errors='ignore') if 'Úhrn' in cpi_mom_filtered.columns else cpi_mom_filtered.copy()
    
    # --- POSUVNÍK PRO VÝBĚR DYNAMICKÉHO ČASOVÉHO OKNA ---
    WINDOW_MONTHS = 36 
    
    if len(risk_dataset) < WINDOW_MONTHS:
        st.warning(f"Zvolené období obsahuje méně než {WINDOW_MONTHS} měsíců. Vykresluji mapu pro celou dostupnou historii.")
        recent_window_data = risk_dataset
        selected_period_desc = "Celá dostupná historie"
    else:
        #Generování textových popisků pro posuvník ve formátu MM.YYYY
        all_dates_str = [idx.strftime("%m.%Y") for idx in risk_dataset.index]
        available_endpoints = all_dates_str[WINDOW_MONTHS - 1:]
        
        selected_endpoint_str = st.select_slider(
            "Vyberte koncový měsíc pro výpočet 3letého průměru:",
            options=available_endpoints,
            value=available_endpoints[-1]
        )
        
        endpoint_idx = all_dates_str.index(selected_endpoint_str)
        recent_window_data = risk_dataset.iloc[endpoint_idx - WINDOW_MONTHS + 1 : endpoint_idx + 1]
        
        start_window_str = all_dates_str[endpoint_idx - WINDOW_MONTHS + 1]
        selected_period_desc = f"Analyzované období (3 roky): {start_window_str} až {selected_endpoint_str}"
        
    st.markdown(f"**{selected_period_desc}**")
    
    # Metodika: Výpočet IQR (Mezikvartilového rozpětí) jakožto robustní míry volatility
    def robust_volatility_iqr(series):
        return series.quantile(0.75) - series.quantile(0.25)
    
    # Agregace dat do metrik pro osy X a Y
    risk_metrics = recent_window_data.agg([geo_mean_pct, robust_volatility_iqr]).T
    risk_metrics.columns = ['Avg_Growth', 'Volatility_IQR']

    # PÁROVÁNÍ VAH Z HISTORICKÝCH DAT PRO VELIKOST BUBLIN
    def extract_category_key(category_text):
        text_l = str(category_text).lower()
        if 'potrav' in text_l: return '01'
        if 'alkohol' in text_l or 'tabák' in text_l: return '02'
        if 'odíván' in text_l or 'obuv' in text_l: return '03'
        if 'bydlen' in text_l or 'voda' in text_l or 'energi' in text_l: return '04'
        if 'vybaven' in text_l or 'zařízen' in text_l or 'oprav' in text_l: return '05'
        if 'zdrav' in text_l: return '06'
        if 'doprav' in text_l: return '07'
        if 'pošt' in text_l or 'telekom' in text_l: return '08'
        if 'rekreac' in text_l or 'kultur' in text_l: return '09'
        if 'vzděláv' in text_l: return '10'
        if 'stravov' in text_l or 'ubytov' in text_l or 'restaurac' in text_l: return '11'
        if 'ostatn' in text_l: return '12'
        return str(category_text).strip()

    weights_dict = {}
    if weights_history is not None and '2025' in weights_history.columns and 'NAZEV' in weights_history.columns:
        weights_dict = {
            extract_category_key(name): val 
            for name, val in zip(weights_history['NAZEV'], weights_history['2025'])
        }

    # Přiřazení vah s ochranným zamezením neviditelně malých bodů (clip)
    risk_metrics['Weight'] = risk_metrics.index.map(lambda x: weights_dict.get(extract_category_key(x), 5.0))
    risk_metrics['Weight'] = risk_metrics['Weight'].fillna(1.0).clip(lower=1.0)
    
    risk_metrics.index = shorten_category_names(risk_metrics.index)

    # Výpočet kombinovaného "skóre rizika" pro aplikaci barevného spektra (červená = špatné, zelená = dobré)
    cnb_monthly_target = round(((1.02 ** (1/12)) - 1) * 100, 3) 
    median_volatility = risk_metrics['Volatility_IQR'].median()

    rank_growth = risk_metrics['Avg_Growth'].rank(pct=True)
    rank_volatility = risk_metrics['Volatility_IQR'].rank(pct=True)
    risk_metrics['Risk_Score'] = rank_growth + rank_volatility 

    # --- VYKRESLENÍ MAPY RIZIKA ---
    fig_risk = go.Figure()
    
    max_bubble_weight = max(risk_metrics['Weight']) if max(risk_metrics['Weight']) > 0 else 1

    fig_risk.add_trace(go.Scatter(
        x=risk_metrics['Avg_Growth'], 
        y=risk_metrics['Volatility_IQR'],
        mode='markers+text', 
        text=risk_metrics.index, 
        textposition='top center',
        textfont=dict(size=11, color='#333333'), 
        marker=dict(
            size=risk_metrics['Weight'], 
            sizemode='area',
            sizeref=2. * max_bubble_weight / (50. ** 2), 
            sizemin=5,
            color=risk_metrics['Risk_Score'], 
            colorscale='RdYlGn', 
            reversescale=True, 
            line=dict(width=0.8, color='#333333'), 
            opacity=0.8, 
            showscale=False
        ),
        hovertemplate="<b>%{text}</b><br>Prům. růst: %{x:.3f} %<br>Volatilita (IQR): %{y:.3f}<br>Váha v koši: %{marker.size:.1f} %<extra></extra>"
    ))

    # Cílová hodnota ČNB (Osa X) a Medián Volatility (Osa Y)
    fig_risk.add_vline(
        x=cnb_monthly_target, line_width=1.5, line_dash="dash", line_color="#1f77b4", opacity=0.8,
        annotation_text="Cíl ČNB (0,165 %)", annotation_position="bottom right",
        annotation_font_size=12, annotation_font_color="#1f77b4"
    )
    fig_risk.add_hline(y=median_volatility, line_width=1, line_dash="dash", line_color="#555555", opacity=0.6)

    fig_risk.update_layout(
        template='plotly_white', height=700,
        xaxis_title="Průměrná měsíční změna (%)", 
        yaxis_title="Volatilita (Mezikvartilové rozpětí - IQR)", 
        xaxis=dict(
            showgrid=True, gridcolor='rgba(0,0,0,0.1)', zeroline=True, zerolinewidth=0.5, zerolinecolor='rgba(0,0,0,0.2)',
            range=[risk_metrics['Avg_Growth'].min() - 0.2, risk_metrics['Avg_Growth'].max() + 0.2]
        ),
        yaxis=dict(
            showgrid=True, gridcolor='rgba(0,0,0,0.1)', zeroline=False,
            range=[max(0, risk_metrics['Volatility_IQR'].min() - 0.1), risk_metrics['Volatility_IQR'].max() + (risk_metrics['Volatility_IQR'].max() * 0.15)]
        ),
        margin=dict(t=60, r=20, l=20, b=50),
        separators=", "
    )
    
    # Automatizované umístění anotací do 4 kvadrantů
    annotations_config = [
        dict(x=0.99, y=0.98, text="VYSOKÁ VOLATILITA<br>& VYSOKÝ RŮST<br>(Rizikové)", color="#d62728", xanchor="right", yanchor="top"),
        dict(x=0.01, y=0.98, text="VYSOKÁ VOLATILITA<br>& NÍZKÝ RŮST<br>(Nepředvídatelné)", color="#d95f02", xanchor="left", yanchor="top"),
        dict(x=0.99, y=0.02, text="NÍZKÁ VOLATILITA<br>& VYSOKÝ RŮST<br>(Trvale zdražující)", color="#ff7f0e", xanchor="right", yanchor="bottom"),
        dict(x=0.01, y=0.02, text="NÍZKÁ VOLATILITA<br>& NÍZKÝ RŮST<br>(Stabilní a bezpečné)", color="#2ca02c", xanchor="left", yanchor="bottom")
    ]
    
    for ann in annotations_config:
        fig_risk.add_annotation(
            x=ann['x'], y=ann['y'], xref="paper", yref="paper",
            text=f"<b>{ann['text']}</b>",
            showarrow=False, xanchor=ann['xanchor'], yanchor=ann['yanchor'],
            font=dict(color=ann['color'], size=12), bgcolor="rgba(255,255,255,0.7)"
        )

    st.plotly_chart(fig_risk, use_container_width=True)

# ==========================================
# 5. ZÁLOŽKA: VLASTNÍ INFLACE
# ==========================================
with tab_vlastni:
    st.caption("Spočítejte si vlastní osobní inflaci. Každá domácnost má jiné výdaje. Zadejte své přibližné měsíční útraty a zjistěte, jaká byla vaše skutečná inflace ve srovnání s oficiálními daty ČSÚ.")

    # 1. Extrakce názvů kategorií (bez celkového úhrnu)
    category_columns = [col for col in cpi_raw.columns if col != 'Úhrn']
    
    # 2. Mapování kódů na uživatelsky srozumitelné názvy a definice nápověd
    coicop_names_map = {
        '01': 'Potraviny a nealko', '02': 'Alkohol a tabák', '03': 'Oblečení a obuv',
        '04': 'Bydlení a energie', '05': 'Vybavení bytu', '06': 'Zdraví',
        '07': 'Doprava', '08': 'Telekomunikace', '09': 'Rekreace a kultura',
        '10': 'Vzdělávání', '11': 'Restaurace a hotely', '12': 'Ostatní zboží a služby'
    }

    coicop_help_texts = {
        '01': "Maso, pečivo, mléčné výrobky, zelenina, káva, čaje, nápoje atd.",
        '02': "Pivo, víno, lihoviny, cigarety, tabák.",
        '03': "Veškeré oděvy, boty, látky.",
        '04': "Nájemné, vlastnické náklady, elektřina, plyn, voda, teplo, odvoz odpadu, údržba bytu (náklady na hypotéku se nezapočítávají).",
        '05': "Nábytek, koberce, spotřebiče, nádobí, mycí prostředky.",
        '06': "Léky, doplatky u lékaře, péče u zubaře, zdravotnické pomůcky.",
        '07': "Nákup aut, benzín/nafta, opravy aut, jízdenky na vlak/bus, letenky.",
        '08': "Mobilní tarify, nákup telefonu, internet, poštovní služby.",
        '09': "Knihy, hračky, kina, divadla, zájezdy, sportovní vybavení, poplatky za TV/rozhlas.",
        '10': "Školkovné, školné, jazykové kurzy.",
        '11': "Obědy v restauracích, kavárny, kantýny, ubytování na dovolené.",
        '12': "Kadeřnictví, kosmetika, pojištění, finanční služby, hodinky, šperky."
    }

    # Identifikace COICOP kódu z názvu sloupce pro spárování s vahami
    def map_to_coicop_code(text_label):
        text_lower = str(text_label).lower()
        if 'potravin' in text_lower: return '01'
        if 'alkohol' in text_lower or 'tabák' in text_lower: return '02'
        if 'odíván' in text_lower or 'obuv' in text_lower: return '03'
        if 'bydlen' in text_lower or 'voda' in text_lower or 'paliv' in text_lower: return '04'
        if 'vybaven' in text_lower or 'domácnost' in text_lower: return '05'
        if 'zdrav' in text_lower: return '06'
        if 'doprav' in text_lower: return '07'
        if 'pošt' in text_lower or 'telekom' in text_lower: return '08'
        if 'rekreac' in text_lower or 'kultur' in text_lower: return '09'
        if 'vzděláv' in text_lower: return '10'
        if 'stravov' in text_lower or 'ubytov' in text_lower: return '11'
        if 'ostatn' in text_lower: return '12'
        return None

    # 3. Příprava referenčních vah ČSÚ pro srovnání (rok 2025)
    csu_weights_2025 = {}
    if weights_history is not None and '2025' in weights_history.columns:
        for _, row in weights_history.iterrows():
            weight_val = row['2025']
            if pd.isna(weight_val): 
                continue
            
            mapped_code = map_to_coicop_code(row['NAZEV'])
            if mapped_code:
                csu_weights_2025[mapped_code] = float(weight_val)

    total_csu_weight = sum(csu_weights_2025.values())

    # Inicializační hodnoty pro uživatelský formulář
    default_monthly_expenses = {
        '01': 7000, '02': 3500, '03': 1600, '04': 10500, '05': 2400, '06': 1100,
        '07': 4200, '08': 1200, '09': 3400, '10': 300, '11': 2500, '12': 2300
    }

    # 4. Sestavení kompletních metadat pro každou kategorii
    category_metadata = {}
    for col in category_columns:
        coicop_code = map_to_coicop_code(col)
        
        cat_display_name = coicop_names_map.get(coicop_code, str(col)[:30])
        cat_help = coicop_help_texts.get(coicop_code, "Položky spadající do této kategorie.")
        cat_default_spend = default_monthly_expenses.get(coicop_code, 1000)
        
        # Normalizace vah ČSÚ na 100 % pro korektní srovnání
        if coicop_code and coicop_code in csu_weights_2025 and total_csu_weight > 0:
            cat_pct_weight = (csu_weights_2025[coicop_code] / total_csu_weight) * 100.0
        else:
            cat_pct_weight = 100.0 / len(category_columns)
            
        category_metadata[col] = {
            'name': cat_display_name, 
            'help': cat_help, 
            'spend': cat_default_spend, 
            'csu_weight': cat_pct_weight
        }

    # --- UŽIVATELSKÉ ROZHRANÍ PRO ZADÁVÁNÍ VÝDAJŮ ---
    disclaimer_text = (
    "Poznámka: ČSÚ pro výpočet inflace využívá tzv. Laspeyresův vzorec, "
    "který pracuje s fixními vahami historického základního období. "
    "Tato kalkulačka pro uživatelskou přívětivost modeluje inflaci "
    "na základě vaší aktuální struktury výdajů."
)
    st.subheader("1. Nastavení osobních výdajů", help=disclaimer_text)
    st.markdown("Zadejte svou odhadovanou měsíční útratu v Kč pro jednotlivé kategorie (pro zobrazení příkladů položek najeďte myší na ikonu otazníku). Aplikace tyto částky automaticky přepočítá na procentuální váhy vašeho osobního spotřebního koše.")
    
    user_input_spends = {}
    
    # Formulář pro hromadné odeslání vstupů (optimalizace výkonu)
    with st.form("form_personal_inflation"):
        input_cols = st.columns(3)
        
        for idx, col_name in enumerate(category_columns):
            meta = category_metadata[col_name]
            
            with input_cols[idx % 3]:
                user_input_spends[col_name] = st.number_input(
                    f"{meta['name']} (Kč)",
                    min_value=0, 
                    value=int(meta['spend']),
                    step=500,
                    help=meta['help'],
                    key=f"user_input_{idx}"
                )
                
        submit_calc_btn = st.form_submit_button("Vypočítat vlastní inflaci", type="secondary", use_container_width=True)
            
    total_monthly_spend = sum(user_input_spends.values())
    
    st.metric("Vaše celkové měsíční výdaje", f"{total_monthly_spend:,.0f} Kč".replace(',', ' '))
    st.markdown("---")
    
    # Ošetření nulového vstupu pro zamezení chyb v následných výpočtech
    if total_monthly_spend == 0:
        st.error("⚠️ Vaše celková útrata činí 0 Kč. Pro výpočet inflace zadejte prosím reálné hodnoty alespoň v jedné kategorii.")
    else:
        # --- MATEMATICKÝ MODEL VLASTNÍ INFLACE ---
        # 1. Výpočet vah osobního koše na základě nominálních útrat
        personal_weights_pct = {cat: (spend / total_monthly_spend) * 100.0 for cat, spend in user_input_spends.items()}

        # 2. Agregace osobního indexu
        personal_index_rebased = pd.Series(0.0, index=cpi_raw.index)
        
        for cat in category_columns:
            # KROK A: Přebázování konkrétní kategorie na base_year
            cat_base_subset = cpi_raw[cat][cpi_raw.index.year == base_year]
            
            if not cat_base_subset.empty:
                cat_base_mean = cat_base_subset.mean()
                cat_rebased = (cpi_raw[cat] / cat_base_mean) * 100
            else:
                # Ošetření pro případ, že chybí data pro referenční rok
                cat_rebased = cpi_raw[cat] * np.nan
                
            # KROK B: Aplikace osobní váhy na srovnaný index a přičtení do celku
            personal_index_rebased += cat_rebased * (personal_weights_pct[cat] / 100.0)
            
        # 3. Výpočet meziroční dynamiky (YoY) z již sestaveného a přebázovaného osobního indexu
        personal_yoy = personal_index_rebased.pct_change(12) * 100
        
        # 4. Filtrování vypočtených řad dle uživatelského období
        my_index_filtered = personal_index_rebased.loc[filter_start:filter_end]
        my_yoy_filtered = personal_yoy.loc[filter_start:filter_end]
        
        formatted_dates_my = format_dates_cz(my_index_filtered.index)
        
        # --- VIZUALIZACE 1: BAZICKÝ INDEX ---
        st.subheader("2. Dlouhodobý vývoj (bazický index)")
        st.markdown(f"Graf ukazuje změnu cenové hladiny vůči vybranému základnímu roku ({base_year}=100). Zde můžete sledovat, zda vaše konkrétní struktura výdajů v dlouhodobém horizontu zdražila více než celostátní průměr.")
        
        csu_index_plot = cpi_rebased['Úhrn']
        
        # Příprava dat pro srovnávací tooltipy
        diff_base_csu = csu_index_plot - my_index_filtered
        diff_base_my = my_index_filtered - csu_index_plot
        
        custom_data_base_csu = np.column_stack((formatted_dates_my, diff_base_csu))
        custom_data_base_my = np.column_stack((formatted_dates_my, diff_base_my))

        fig_personal_base = go.Figure()
        
        # Trace pro oficiální průměr ČSÚ
        fig_personal_base.add_trace(go.Scatter(
            x=csu_index_plot.index, y=csu_index_plot, mode='lines', 
            name='ČSÚ (Průměr ČR)', line=dict(color='#1f77b4', width=2),
            customdata=custom_data_base_csu, 
            hovertemplate="<b>%{customdata[0]}</b><br>ČSÚ: %{y:.2f}<br>Rozdíl (ČSÚ - Moje): %{customdata[1]:.2f} b.<extra></extra>"
        ))
        
        # Trace pro simulovanou osobní inflaci
        fig_personal_base.add_trace(go.Scatter(
            x=my_index_filtered.index, y=my_index_filtered, mode='lines', 
            name='Moje inflace', line=dict(color='#d62728', width=3),
            customdata=custom_data_base_my, 
            hovertemplate="<b>%{customdata[0]}</b><br>Moje: %{y:.2f}<br>Rozdíl (Moje - ČSÚ): %{customdata[1]:.2f} b.<extra></extra>"
        ))
        
        fig_personal_base.add_hline(y=100, line_dash="dash", line_color="black", line_width=0.7, opacity=0.5)
        
        fig_personal_base.update_layout(
            template='plotly_white', height=500, 
            xaxis_title="Rok",
            yaxis_title=f"Index ({base_year} = 100)", 
            xaxis=dict(range=axis_view_range, tickformat="%Y", dtick="M12", showgrid=True),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=30, b=20),
            separators=", "
        )
        st.plotly_chart(fig_personal_base, use_container_width=True)
        st.markdown("---")
        
        # --- VIZUALIZACE 2: MEZIROČNÍ INFLACE ---
        st.subheader("3. Tempo zdražování (meziroční inflace)")
        st.markdown("Porovnání procentuálního růstu cen oproti stejnému měsíci předchozího roku. Graf odhaluje období, kdy inflace nejvíce ovlivňovala váš osobní rozpočet v porovnání s oficiálními daty.")

        csu_yoy_plot = cpi_yoy_filtered['Úhrn']
        
        # Kalkulace YoY rozdílů pro tooltipy
        diff_yoy_csu = csu_yoy_plot - my_yoy_filtered
        diff_yoy_my = my_yoy_filtered - csu_yoy_plot
        
        custom_data_yoy_csu = np.column_stack((formatted_dates_my, diff_yoy_csu))
        custom_data_yoy_my = np.column_stack((formatted_dates_my, diff_yoy_my))

        fig_personal_yoy = go.Figure()
        
        fig_personal_yoy.add_trace(go.Scatter(
            x=csu_yoy_plot.index, y=csu_yoy_plot, mode='lines', 
            name='ČSÚ (Průměr ČR)', line=dict(color='#1f77b4', width=2),
            customdata=custom_data_yoy_csu, 
            hovertemplate="<b>%{customdata[0]}</b><br>ČSÚ: %{y:.2f} %<br>Rozdíl (ČSÚ - Moje): %{customdata[1]:.2f} p. b.<extra></extra>"
        ))
        
        fig_personal_yoy.add_trace(go.Scatter(
            x=my_yoy_filtered.index, y=my_yoy_filtered, mode='lines', 
            name='Moje inflace', line=dict(color='#d62728', width=3),
            customdata=custom_data_yoy_my, 
            hovertemplate="<b>%{customdata[0]}</b><br>Moje: %{y:.2f} %<br>Rozdíl (Moje - ČSÚ): %{customdata[1]:.2f} p. b.<extra></extra>"
        ))
        
        fig_personal_yoy.add_hline(y=0, line_color='black', line_width=0.5, opacity=0.5)
        
        fig_personal_yoy.update_layout(
            template='plotly_white', height=500, 
            xaxis_title="Rok", 
            yaxis_title="Meziroční změna (%)", 
            xaxis=dict(range=axis_view_range, tickformat="%Y", dtick="M12", showgrid=True),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=30, b=20),
            separators=", "
        )
        st.plotly_chart(fig_personal_yoy, use_container_width=True)
        st.markdown("---")
        
        # --- VIZUALIZACE 3: STRUKTURNÍ POROVNÁNÍ KOŠE ---
        st.subheader("4. Srovnání struktury koše (průměr ČR vs. vaše výdaje)")
        st.markdown("Graf ukazuje strukturní rozdíly v rozložení výdajů. Poskytuje detailní pohled na to, u kterých kategorií se vaše osobní útraty (převedené na procentní podíl) nejvíce odchylují od celostátního průměru.")
        
        # Příprava dat pro horizontální sloupcový graf
        plot_categories = []
        plot_weights_csu = []
        plot_weights_user = []
        
        for cat in category_columns:
            plot_categories.append(category_metadata[cat]['name'])
            plot_weights_csu.append(category_metadata[cat]['csu_weight'])
            plot_weights_user.append(personal_weights_pct[cat])
        
        fig_weights_comparison = go.Figure()
        
        fig_weights_comparison.add_trace(go.Bar(
            y=plot_categories, x=plot_weights_csu, 
            name='Průměr (ČSÚ 2025)', orientation='h', marker_color='#1f77b4',
            hovertemplate="<b>%{y}</b><br>ČSÚ: %{x:.1f} %<extra></extra>"
        ))
        
        fig_weights_comparison.add_trace(go.Bar(
            y=plot_categories, x=plot_weights_user, 
            name='Moje výdaje', orientation='h', marker_color='#d62728',
            hovertemplate="<b>%{y}</b><br>Moje: %{x:.1f} %<extra></extra>"
        ))
        
        fig_weights_comparison.update_layout(
            template='plotly_white', barmode='group', height=700,
            xaxis_title="Zastoupení výdajů (%)",
            yaxis=dict(
                autorange='reversed', 
                title=None, 
                tickfont=dict(size=13),
                tickmode='linear', 
                dtick=1
            ), 
            xaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.1)'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=150, t=30, b=20),
            separators=", "
        )
        st.plotly_chart(fig_weights_comparison, use_container_width=True)