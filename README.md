# 433 MHz Dual-CC1101 RF Loopback & BLE Telemetry Gateway

A hardware-in-the-loop sub-GHz RF packet transceiver, protocol inspector, and telemetry gateway built on the **Arduino Nano 33 BLE (nRF52840)** and **two TI CC1101 433 MHz modules**. 

This project bridges sub-GHz 433 MHz RF communications with a real-time web application over Bluetooth Low Energy (BLE). Users can type custom payloads on a web dashboard, transmit them over Bluetooth to the microcontroller, broadcast them over 433 MHz RF from Module A to Module B, and inspect the decoded payload, Signal Strength (RSSI), Link Quality Indicator (LQI), and raw hexadecimal byte frames in real time.

---

## System Architecture

```text
+-----------------------------------------------------------------------+
|                        HOST MACHINE (Laptop)                          |
|                                                                       |
|  [ Web Browser UI ] <----(WebSockets)----> [ FastAPI + Bleak Server ] |
+-------------------------------------------------------+---------------+
                                                        |
                                            Bluetooth Low Energy (GATT)
                                         Write: 19B10002 | Notify: 19B10001
                                                        |
+-------------------------------------------------------v---------------+
|                   ARDUINO NANO 33 BLE (nRF52840)                      |
|                                                                       |
|           Shared 1 MHz SPI Bus (D11 MOSI, D12 MISO, D13 SCK)          |
|                     +---------------------------+                     |
|                     |                           |                     |
|             (CSN: Pin D9)               (CSN: Pin D10)                |
|                     v                           v                     |
|            [ CC1101 Module A ]         [ CC1101 Module B ]            |
|               (Transmitter)                (Receiver)                 |
+---------------------+---------------------------^---------------------+
                      |                           |
                      +--- 433.0 MHz RF Air Link -+
```

### Key Engineering Features
* **Direct Hardware Strobe FIFO Management:** Uses raw SPI command strobes (`SIDLE 0x36`, `SFRX 0x3A`, `SFTX 0x3B`) before and after every packet cycle to prevent CC1101 RX/TX FIFO overflow lockups on a shared SPI bus.
* **Mbed OS RTOS & BLE Decoupling:** Decouples BLE GATT write callbacks from SPI bus transactions to prevent nRF52840 Mbed OS thread deadlocks and interrupt starvation.
* **Serialized Asyncio BLE Queue:** Implements an asynchronous mutex lock (`asyncio.Lock`) on the Python host to serialize outgoing GATT writes and prevent Windows Bluetooth stack I/O cancellation errors during rapid transmissions.
* **Hardware-Protected Payload Limits:** Enforces a strict 20-byte payload ceiling across the UI, backend, and firmware to guarantee single-frame transmission stability without FIFO underflow.

---

## Hardware Requirements

* **Microcontroller:** 1x Arduino Nano 33 BLE (3.3V logic; do **not** use 5V boards without level shifters)
* **RF Transceivers:** 2x TI CC1101 433 MHz Wireless Modules (with SMA antennas attached)
* **Host Computer:** Windows 10/11, macOS, or Linux machine with built-in Bluetooth 4.0+ (BLE)
* **Accessories:** Breadboard and jumper wires

---

## Hardware Wiring & Pinout

Both CC1101 modules are powered directly from the Arduino Nano 33 BLE's onboard regulated **`+3V3`** pin and share the hardware SPI bus (`D11`, `D12`, `D13`). Separate Chip Select (`CSN`) and General Digital Output (`GDO0`, `GDO2`) pins isolate the two radios.

### Master Wiring Table

| CC1101 Pin | Pin Label | Module A (Transmitter) | Module B (Receiver) | Function / Description |
| :---: | :---: | :---: | :---: | :--- |
| **1** | **GND** | Nano **GND** | Nano **GND** | Common Ground |
| **2** | **VCC** | Nano **+3V3** | Nano **+3V3** | Onboard Regulated 3.3V Supply |
| **3** | **GDO0** | Nano **D2** | Nano **D3** | Interrupt / Sync Word & Packet Detection |
| **4** | **CSN** | Nano **D9** | Nano **D10** | SPI Chip Select (Active LOW) |
| **5** | **SCK** | Nano **D13** | Nano **D13** | Shared SPI Clock |
| **6** | **MOSI** | Nano **D11** | Nano **D11** | Shared SPI Master Out Slave In |
| **7** | **MISO / GDO1** | Nano **D12** | Nano **D12** | Shared SPI Master In Slave Out |
| **8** | **GDO2** | Nano **D4** | Nano **D5** | General Digital Output / Chip Status |

> **Important Hardware Note:** Ensure both 433 MHz antennas are screwed onto the CC1101 modules before powering the board. Keep the modules at least 10-15 cm apart on your desk; Firmware TX power is set to `-10 dBm` to prevent near-field receiver saturation.

---

## Installation & Setup

### Step 1: Flash the Arduino Firmware
1. Install the [Arduino IDE](https://www.arduino.cc/en/software).
2. Open **Tools > Board > Boards Manager**, search for **Arduino Mbed OS Nano Boards**, and install it.
3. Open **Tools > Manage Libraries** and install the following libraries:
   * **RadioLib** (by Jan Gromes)
   * **ArduinoBLE** (by Arduino)
4. Open `firmware/RF_TPMS_Gateway/RF_TPMS_Gateway.ino` in the Arduino IDE.
5. Select **Arduino Nano 33 BLE** under **Tools > Board** and select your active COM port.
6. Click **Upload**.
7. *(Optional)* Open the Serial Monitor at **115200 baud** to view live hardware-level RF and BLE logs.

### Step 2: Set Up the Python Backend Server
1. Ensure **Python 3.10+** is installed on your computer and Bluetooth is turned **ON**.
2. Clone this repository and navigate into the project directory:
   ```bash
   git clone https://github.com/Ritwik-Mallavarapu/433-Mhz-RF-Transmission-using-2-CC1101-and-Arduino-Nano-33-BLE.git
   cd cc1101-ble-rf-gateway
   ```
3. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows (Command Prompt):
   .venv\Scripts\activate
   # Windows (PowerShell):
   .\.venv\Scripts\Activate.ps1
   # macOS / Linux:
   source .venv/bin/activate
   ```
4. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Start the server:
   ```bash
   python server/app.py
   ```

### Step 3: Launch the Web Inspector
1. Once the terminal prints `[BLE] Connected and subscribed to notifications.`, open your browser and go to:
   ```text
   http://localhost:8000
   ```
2. Verify the top status badge is green (**`BLE Link Active`**).
3. Type any message up to 20 characters in the input box and click **Transmit** (or press `Enter`).
4. View the decoded payload, RSSI, LQI, raw byte breakdown, and chronological history log in real time.

---

## Over-The-Air & BLE Frame Format

When Module B captures an RF packet, the Arduino packs a 29-byte binary struct (`RFPacketFrame`) and sends it via BLE Notification (`<IhbB21s` little-endian format):

| Byte Offset | Data Type | Field Name | Description |
| :---: | :---: | :--- | :--- |
| `0 - 3` | `uint32_t` | `packetId` | Sequential transmission counter |
| `4 - 5` | `int16_t` | `rssi_x10` | Signal strength multiplied by 10 (e.g., `-310` = `-31.0 dBm`) |
| `6` | `uint8_t` | `lqi` | Link Quality Indicator (lower values indicate cleaner demodulation) |
| `7` | `uint8_t` | `payloadLen` | Number of active payload characters (`1` to `20`) |
| `8 - 28` | `char[21]` | `payload` | Null-terminated UTF-8 payload string |

---

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
