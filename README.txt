DASHBOARD: ANALÝZA INFLACE A SPOTŘEBNÍHO KOŠE

Tento dashboard obsahuje statistiky a interaktivní vizualizace týkající se inflace. Umožňuje uživatelům nastavit vstupní data, zobrazení v grafech, bazický rok a váhy pro výpočet osobní inflace. Aplikace je strukturována do 6 tematických sekcí:

- Úvod
- Bazický index
- Meziroční index 
- Mezimesíční index 
- Váhy spotřebního koše 
- Osobní inflace

Detailnější návod k použití naleznete přímo v aplikaci.


ONLINE SPUŠTĚNÍ

Aplikace je vytvořena ve frameworku Streamlit a je hostována na jeho serverech.
Online verze dashboardu je dostupná zde: 

https://inflace.streamlit.app


STAŽENÍ APLIKACE, DAT A DALŠÍCH SOUBORŮ

Jak stáhnout aplikaci do počítače:

   1. Otevřete si v prohlížeči stránku s repozitářem: https://github.com/stepakriz/Bakalarska_prace

   2. Vpravo nahoře nad seznamem souborů najděte zelené tlačítko s nápisem <> Code a klikněte na něj.

   3. Otevře se malá nabídka. Úplně dole vyberte možnost Download ZIP.

   4. Na stažený soubor klikněte pravým tlačítkem myši a zvolte Extrahovat vše.

   5. Tím získáte složku se všemi potřebnými soubory (kód app0.py, data i requirements.txt), se kterou budete dále pracovat.


LOKÁLNÍ SPUŠTĚNÍ

Pro spuštění aplikace u vás na počítači potřebujete stáhnout zdrojový kód a příslušná data. Všechny následující soubory musí být umístěny společně v jedné složce:

- app.py (hlavní skript)
- CPI_1.xlsx (datový soubor)
- CPI_2.xlsx (datový soubor)
- HICP.xlsx (datový soubor)
- spot_kos2025_podrobne.xlsx (datový soubor)
- vahy_v_letech.xlsx (datový soubor)
- requirements.txt (soubor pro instalaci knihoven)

Požadavky na systém:
Musíte mít nainstalovaný programovací jazyk Python ze stránky https://www.python.org/downloads/ (ideálně verze 3.13) a potřebné knihovny (Pandas, NumPy, SciPy, Streamlit, Plotly).

Postup instalace a spuštění:

  1.  Otevřete Příkazový řádek (ve vyhledávání ve Windows napište "cmd" nebo "Příkazový řádek" a spusťte jej).

  2.  Pomocí příkazu "cd" se přesuňte do složky, kde máte stažené všechny soubory. Například:
      cd "C:\Cesta\K\Vasi\Slozce"

  3.  Pro instalaci všech potřebných knihoven zadejte tento příkaz a potvrďte klávesou Enter:
      pip install -r requirements.txt


  4.  Samotnou aplikaci pak spustíte zadáním tohoto příkazu:
      python -m streamlit run app.py











