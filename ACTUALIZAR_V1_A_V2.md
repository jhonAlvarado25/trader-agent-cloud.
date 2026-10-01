# Actualizar Trader Agent V1 a V2 desde iPhone

1. Descarga y descomprime `Trader_Agent_Cloud_V2.zip`.
2. En GitHub abre el repositorio donde tienes V1.
3. Reemplaza los archivos de la raíz por los archivos de V2:
   - app.py
   - config.py
   - market.py
   - indicators.py
   - setups.py
   - backtest.py
   - risk.py
   - engine.py
   - requirements.txt
   - README.md
4. Mantén/actualiza `.streamlit/config.toml`.
5. Haz Commit changes.
6. Streamlit debería detectar el commit.
7. Si no lo hace: Streamlit Cloud → Manage app → Reboot app.
8. Espera 1–3 minutos y recarga Safari.

No debes crear API keys.
