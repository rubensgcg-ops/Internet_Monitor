from flask import Flask, render_template, jsonify, request
import sqlite3
import threading
import time
from datetime import datetime, timedelta
import subprocess
import pandas as pd
import json
import os

app = Flask(__name__)

DB_FILE = "internet.db"
MEASURE_INTERVAL = 60  # segundos (5 minutos)

# === Banco de dados ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ping_avg REAL,
            download_mbps REAL,
            upload_mbps REAL,
            jitter REAL,
            packet_loss REAL
        )
    """)
    conn.commit()
    conn.close()
    print("[INFO] Banco de dados inicializado:", DB_FILE)

# === Executa o speedtest oficial ===
def executar_speedtest():
    """Executa o speedtest oficial da Ookla e retorna ping, download, upload, jitter e packet loss."""
    try:
        result = subprocess.run(
            ["speedtest", "--accept-license", "--accept-gdpr", "-f", "json"],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            print(f"[ERRO] Speedtest falhou: {result.stderr.strip()}")
            return None, None, None, None, None

        data = json.loads(result.stdout)

        ping = data["ping"]["latency"]
        jitter = data["ping"].get("jitter", 0)
        download = data["download"]["bandwidth"] * 8 / 1e6  # bits/s → Mbps
        upload = data["upload"]["bandwidth"] * 8 / 1e6
        packet_loss = data.get("packetLoss", 0)

        return ping, download, upload, jitter, packet_loss

    except FileNotFoundError:
        print("[ERRO] O executável 'speedtest' não foi encontrado.")
        print("Instale com:")
        print("  curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash")
        print("  sudo apt install speedtest -y")
    except json.JSONDecodeError as e:
        print(f"[ERRO EXECUTAR_SPEEDTEST] Erro ao interpretar JSON: {e}")
        print("Saída recebida:", result.stdout[:200], "...")
    except Exception as e:
        print(f"[ERRO EXECUTAR_SPEEDTEST] {e}")

    return None, None, None, None, None

# === Coletor de dados (usando Ookla) ===
def collect_metrics():
    while True:
        try:
            print("[INFO] Executando speedtest oficial...")
            ping, download, upload, jitter, packet_loss = executar_speedtest()

            if ping is not None and download is not None and upload is not None:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO metrics (timestamp, ping_avg, download_mbps, upload_mbps, jitter, packet_loss) VALUES (?, ?, ?, ?, ?, ?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ping, download, upload, jitter, packet_loss)
                )
                conn.commit()
                conn.close()
                print(f"[OK] Registro salvo: ping={ping:.2f} ms | jitter={jitter:.2f} ms | ↓ {download:.2f} Mbps | ↑ {upload:.2f} Mbps | perda={packet_loss:.2f}%")
            else:
                print("[WARN] Speedtest retornou dados incompletos.")

        except Exception as e:
            print(f"[ERRO COLETA] {e}")

        time.sleep(MEASURE_INTERVAL)

# === Rota principal ===
@app.route("/")
def index():
    return render_template("index.html")

# === API de dados para o dashboard ===
@app.route("/data")
def data():
    time_range = request.args.get("range", "1h")
    now = datetime.now()

    ranges = {
        "1h": now - timedelta(hours=1),
        "4h": now - timedelta(hours=4),
        "12h": now - timedelta(hours=12),
        "1d": now - timedelta(days=1),
        "7d": now - timedelta(days=7),
        "total": datetime(1970, 1, 1)
    }
    start_time = ranges.get(time_range, ranges["1h"])

    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query(
        "SELECT * FROM metrics WHERE timestamp >= ? ORDER BY timestamp ASC",
        conn,
        params=(start_time.strftime("%Y-%m-%d %H:%M:%S"),)
    )
    conn.close()

    if df.empty:
        return jsonify({
            "timestamps": [], 
            "ping": [], 
            "download": [], 
            "upload": [],
            "jitter": [],
            "packet_loss": [],
            "stats": {
                "download": {"min": 0, "max": 0},
                "upload": {"min": 0, "max": 0},
                "ping": {"min": 0, "max": 0},
                "jitter": {"min": 0, "max": 0},
                "packet_loss": {"min": 0, "max": 0}
            }
        })

    # Calcular estatísticas
    stats = {
        "download": {
            "min": float(df["download_mbps"].min()),
            "max": float(df["download_mbps"].max())
        },
        "upload": {
            "min": float(df["upload_mbps"].min()),
            "max": float(df["upload_mbps"].max())
        },
        "ping": {
            "min": float(df["ping_avg"].min()),
            "max": float(df["ping_avg"].max())
        },
        "jitter": {
            "min": float(df["jitter"].min()) if "jitter" in df.columns else 0,
            "max": float(df["jitter"].max()) if "jitter" in df.columns else 0
        },
        "packet_loss": {
            "min": float(df["packet_loss"].min()) if "packet_loss" in df.columns else 0,
            "max": float(df["packet_loss"].max()) if "packet_loss" in df.columns else 0
        }
    }

    return jsonify({
        "timestamps": df["timestamp"].tolist(),
        "ping": df["ping_avg"].tolist(),
        "download": df["download_mbps"].tolist(),
        "upload": df["upload_mbps"].tolist(),
        "jitter": df["jitter"].tolist() if "jitter" in df.columns else [],
        "packet_loss": df["packet_loss"].tolist() if "packet_loss" in df.columns else [],
        "stats": stats
    })

# === Inicialização ===
if __name__ == "__main__":
    init_db()

    # Inicia coleta em background
    collector_thread = threading.Thread(target=collect_metrics, daemon=True)
    collector_thread.start()

    print("[INFO] Servidor Flask iniciado em http://0.0.0.0:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
