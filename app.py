import asyncio
import struct
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
from bleak import BleakScanner, BleakClient

WRITE_CHAR_UUID  = "19B10002-E8F2-537E-4F6C-D104768A1214"
NOTIFY_CHAR_UUID = "19B10001-E8F2-537E-4F6C-D104768A1214"

MAX_ALLOWED_LENGTH = 20

ble_client_instance = None
active_websockets = set()
main_loop = None
ble_write_lock = asyncio.Lock()

packet_history = []

latest_packet_data = {
    "packet_id": 0,
    "payload_text": "",
    "payload_len": 0,
    "rssi": "--",
    "lqi": "--",
    "raw_hex": "",
    "raw_bytes_list": [],
    "ble_connected": False
}

def ble_notification_handler(sender, data: bytearray):
    global latest_packet_data, packet_history
    if len(data) >= 29:
        pkt_id, rssi_x10, lqi, payload_len, raw_str = struct.unpack("<IhbB21s", data[:29])
        payload_len = min(payload_len, MAX_ALLOWED_LENGTH)
        decoded_text = raw_str[:payload_len].decode("utf-8", errors="replace")
        hex_dump = " ".join(f"{b:02X}" for b in data[:8 + payload_len])
        bytes_array = [f"0x{b:02X}" for b in data[:8 + payload_len]]

        entry = {
            "packet_id": pkt_id,
            "payload_text": decoded_text,
            "payload_len": payload_len,
            "rssi": f"{rssi_x10 / 10.0:.1f}",
            "lqi": lqi,
            "raw_hex": hex_dump,
            "raw_bytes_list": bytes_array,
            "ble_connected": True
        }
        latest_packet_data = entry
        packet_history.insert(0, entry)

        print(f"[RF CAPTURE] #{pkt_id}: '{decoded_text}' | RSSI: {rssi_x10 / 10.0:.1f} dBm | LQI: {lqi}")
        if main_loop:
            asyncio.run_coroutine_threadsafe(broadcast_state(), main_loop)

async def broadcast_state():
    dead = set()
    payload = json.dumps({
        "latest": latest_packet_data,
        "history": packet_history
    })
    for ws in list(active_websockets):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    active_websockets.difference_update(dead)

def on_disconnect(client):
    global ble_client_instance, latest_packet_data
    print("[BLE] Connection lost with TPMS_Gateway.")
    ble_client_instance = None
    latest_packet_data["ble_connected"] = False
    if main_loop:
        asyncio.run_coroutine_threadsafe(broadcast_state(), main_loop)

async def ble_worker():
    global ble_client_instance, latest_packet_data
    while True:
        if ble_client_instance is None or not ble_client_instance.is_connected:
            try:
                print("[BLE] Scanning for 'TPMS_Gateway'...")
                device = await BleakScanner.find_device_by_name("TPMS_Gateway", timeout=4.0)
                if device:
                    print(f"[BLE] Connecting to {device.name} [{device.address}]...")
                    client = BleakClient(device, disconnected_callback=on_disconnect)
                    await client.connect(timeout=8.0)
                    if client.is_connected:
                        ble_client_instance = client
                        latest_packet_data["ble_connected"] = True
                        await client.start_notify(NOTIFY_CHAR_UUID, ble_notification_handler)
                        print("[BLE] Connected and subscribed to notifications.")
                        await broadcast_state()
            except Exception as e:
                print(f"[BLE Worker Retry] {e}")
                ble_client_instance = None
                latest_packet_data["ble_connected"] = False
                await broadcast_state()
        await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()
    task = asyncio.create_task(ble_worker())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>433 MHz RF Packet Inspector & History</title>
    <style>
        :root {
            --bg: #090d16;
            --card: #111827;
            --border: #1f2937;
            --accent: #38bdf8;
            --accent-green: #10b981;
            --danger: #ef4444;
            --text-dim: #9ca3af;
            --text: #f9fafb;
        }
        body {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 24px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .container { width: 100%; max-width: 840px; }
        h1 { font-size: 20px; font-weight: 700; margin: 0 0 4px 0; color: var(--accent); }
        .sub { font-size: 12px; color: var(--text-dim); margin-bottom: 20px; }
        
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            background: #1f2937;
            color: var(--danger);
            margin-bottom: 20px;
            transition: all 0.3s;
        }
        .badge.active { background: #064e3b; color: #34d399; }
        .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }

        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 18px;
        }
        .card-title {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-dim);
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
        }

        .input-group { display: flex; gap: 8px; }
        input[type="text"] {
            flex: 1;
            background: #030712;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 10px 14px;
            border-radius: 8px;
            font-family: inherit;
            font-size: 14px;
            outline: none;
        }
        input[type="text"]:focus { border-color: var(--accent); }
        input[type="text"].invalid { border-color: var(--danger); }
        
        button {
            background: var(--accent);
            color: #030712;
            font-weight: 700;
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-family: inherit;
            transition: all 0.2s;
        }
        button:hover { opacity: 0.9; }
        button:disabled { background: #374151; color: #9ca3af; cursor: not-allowed; }

        .char-counter { font-size: 11px; color: var(--text-dim); }
        .char-counter.limit { color: var(--danger); font-weight: bold; }

        .alert-box {
            display: none;
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid var(--danger);
            color: #fca5a5;
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 12px;
            margin-top: 8px;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 10px;
            margin-bottom: 12px;
        }
        .metric-box {
            background: #030712;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px;
            text-align: center;
        }
        .metric-box .label { font-size: 10px; color: var(--text-dim); margin-bottom: 4px; }
        .metric-box .value { font-size: 16px; font-weight: 700; color: var(--accent-green); }

        .raw-dump {
            background: #030712;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            font-size: 13px;
            line-height: 1.5;
            color: #38bdf8;
            word-break: break-all;
        }
        .byte-pill {
            display: inline-block;
            background: #1e293b;
            color: #f1f5f9;
            padding: 2px 6px;
            border-radius: 4px;
            margin: 2px;
            font-size: 11px;
        }

        .history-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
            margin-top: 8px;
        }
        .history-table th, .history-table td {
            padding: 8px 10px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }
        .history-table th { color: var(--text-dim); font-size: 11px; font-weight: 600; }
        .history-table tr:hover td { background: #1e293b; }
        .mono-hex { color: #38bdf8; font-size: 11px; font-family: inherit; }
    </style>
</head>
<body>
    <div class="container">
        <h1>433 MHz RF Packet Inspector & History</h1>
        <div class="sub">Web UI &rarr; BLE Write &rarr; CC1101 TX (D9) &rarr; CC1101 RX (D10) &rarr; BLE Notify &rarr; Web UI</div>
        
        <div class="badge" id="status-badge">
            <div class="dot"></div>
            <span id="status-text">Connecting to Gateway...</span>
        </div>

        <div class="card">
            <div class="card-title">
                <span>1. Transmit Message (Max 20 Characters)</span>
                <span id="char-count" class="char-counter">5 / 20</span>
            </div>
            <div class="input-group">
                <input type="text" id="msg-input" placeholder="Type message (max 20 chars)..." value="Hello" oninput="checkInput()">
                <button id="send-btn" onclick="sendMessage()">Transmit</button>
            </div>
            <div class="alert-box" id="warning-box">
                Warning: Message exceeds the safe 20-byte CC1101 packet limit and cannot be sent!
            </div>
            <div id="tx-status" style="font-size: 12px; margin-top: 8px; color: var(--text-dim);">Ready</div>
        </div>

        <div class="card">
            <div class="card-title">2. Latest Decoded Message</div>
            <div class="metrics-grid">
                <div class="metric-box">
                    <div class="label">FRAME ID</div>
                    <div class="value" id="val-id">#0</div>
                </div>
                <div class="metric-box">
                    <div class="label">PAYLOAD BYTES</div>
                    <div class="value" id="val-len">0 B</div>
                </div>
                <div class="metric-box">
                    <div class="label">SIGNAL RSSI</div>
                    <div class="value" id="val-rssi">-- dBm</div>
                </div>
                <div class="metric-box">
                    <div class="label">LINK QUALITY</div>
                    <div class="value" id="val-lqi">--</div>
                </div>
            </div>
            <div style="font-size: 12px; margin-bottom: 6px; color: var(--text-dim);">PAYLOAD TEXT:</div>
            <div class="raw-dump" id="val-text" style="color: #34d399; font-weight: 700; font-size: 16px;">
                Waiting for transmission...
            </div>
            <div style="font-size: 12px; margin: 12px 0 6px 0; color: var(--text-dim);">RAW PACKET BYTES (HEADER + PAYLOAD):</div>
            <div id="val-pills"></div>
        </div>

        <div class="card">
            <div class="card-title">3. Packet History Log</div>
            <div style="overflow-x: auto; max-height: 300px; overflow-y: auto;">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th style="width: 60px;">ID</th>
                            <th style="width: 150px;">Payload</th>
                            <th style="width: 65px;">Bytes</th>
                            <th style="width: 90px;">RSSI</th>
                            <th style="width: 55px;">LQI</th>
                            <th>Raw Hex Dump</th>
                        </tr>
                    </thead>
                    <tbody id="history-body">
                        <tr><td colspan="6" style="text-align: center; color: var(--text-dim);">No packets logged yet.</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        const ws = new WebSocket(`ws://${location.host}/ws`);
        const statusBadge = document.getElementById("status-badge");
        const statusText = document.getElementById("status-text");
        const txStatus = document.getElementById("tx-status");
        const sendBtn = document.getElementById("send-btn");
        const inputEl = document.getElementById("msg-input");
        const charCountEl = document.getElementById("char-count");
        const warningBox = document.getElementById("warning-box");
        const historyBody = document.getElementById("history-body");

        function checkInput() {
            const len = inputEl.value.length;
            charCountEl.innerText = `${len} / 20`;
            if (len > 20) {
                charCountEl.classList.add("limit");
                inputEl.classList.add("invalid");
                warningBox.style.display = "block";
            } else {
                charCountEl.classList.remove("limit");
                inputEl.classList.remove("invalid");
                warningBox.style.display = "none";
            }
        }

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const latest = data.latest;
            const history = data.history;

            if (latest.ble_connected) {
                statusBadge.classList.add("active");
                statusText.innerText = "BLE Link Active";
            } else {
                statusBadge.classList.remove("active");
                statusText.innerText = "Connecting to Gateway...";
            }

            if (latest.packet_id > 0) {
                document.getElementById("val-id").innerText = "#" + latest.packet_id;
                document.getElementById("val-len").innerText = latest.payload_len + " B";
                document.getElementById("val-rssi").innerText = latest.rssi + " dBm";
                document.getElementById("val-lqi").innerText = latest.lqi;
                document.getElementById("val-text").innerText = latest.payload_text;

                const pillsContainer = document.getElementById("val-pills");
                pillsContainer.innerHTML = "";
                latest.raw_bytes_list.forEach(b => {
                    const span = document.createElement("span");
                    span.className = "byte-pill";
                    span.innerText = b;
                    pillsContainer.appendChild(span);
                });
            }

            if (history && history.length > 0) {
                historyBody.innerHTML = "";
                history.forEach(item => {
                    const row = document.createElement("tr");
                    row.innerHTML = `
                        <td><strong>#${item.packet_id}</strong></td>
                        <td style="color: #34d399; font-weight: 600;">${item.payload_text}</td>
                        <td>${item.payload_len} B</td>
                        <td>${item.rssi} dBm</td>
                        <td>${item.lqi}</td>
                        <td class="mono-hex">${item.raw_hex}</td>
                    `;
                    historyBody.appendChild(row);
                });
            }
        };

        async function sendMessage() {
            const text = inputEl.value.trim();
            if (!text) return;

            if (text.length > 20) {
                warningBox.style.display = "block";
                txStatus.style.color = "#ef4444";
                txStatus.innerText = "Blocked: Message exceeds 20 characters.";
                return;
            }

            sendBtn.disabled = true;
            txStatus.style.color = "#38bdf8";
            txStatus.innerText = "Transmitting over BLE & 433 MHz RF...";

            try {
                const res = await fetch(`/send?msg=${encodeURIComponent(text)}`);
                const result = await res.json();
                
                if (result.status === "sent") {
                    txStatus.style.color = "#34d399";
                    txStatus.innerText = "Transmitted via 433 MHz RF!";
                } else {
                    txStatus.style.color = "#ef4444";
                    txStatus.innerText = "Error: " + (result.detail || result.status);
                }
            } catch (err) {
                txStatus.style.color = "#ef4444";
                txStatus.innerText = "Network Error.";
            } finally {
                setTimeout(() => { sendBtn.disabled = false; }, 350);
            }
        }

        inputEl.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !sendBtn.disabled) sendMessage();
        });

        checkInput();
    </script>
</body>
</html>
"""

@app.get("/")
async def get_index():
    return HTMLResponse(HTML_PAGE)

@app.get("/send")
async def send_command(msg: str):
    global ble_client_instance
    if len(msg) > MAX_ALLOWED_LENGTH:
        return {"status": "error", "detail": f"Message too long ({len(msg)} chars). Max allowed is {MAX_ALLOWED_LENGTH}."}

    if not ble_client_instance or not ble_client_instance.is_connected:
        return {"status": "error", "detail": "BLE Gateway is not connected"}
    
    async with ble_write_lock:
        try:
            raw_bytes = msg.encode("utf-8")
            await ble_client_instance.write_gatt_char(WRITE_CHAR_UUID, raw_bytes, response=True)
            print(f"[BLE WRITE] Sent '{msg}' ({len(raw_bytes)} bytes)")
            await asyncio.sleep(0.25)
            return {"status": "sent", "message": msg}
        except Exception as e:
            print(f"[BLE WRITE ERROR] {e}")
            return {"status": "error", "detail": str(e)}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        await websocket.send_text(json.dumps({
            "latest": latest_packet_data,
            "history": packet_history
        }))
        while True:
            await asyncio.sleep(1)
    except Exception:
        pass
    finally:
        active_websockets.discard(websocket)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
