#include <SPI.h>
#include <RadioLib.h>
#include <ArduinoBLE.h>

// -----------------------------------------------------------------------------
// Verified Hardware Pinout
// -----------------------------------------------------------------------------
static constexpr uint8_t PIN_MOD_A_CS   = 9;
static constexpr uint8_t PIN_MOD_A_GDO0 = 2;
static constexpr uint8_t PIN_MOD_A_GDO2 = 4;

static constexpr uint8_t PIN_MOD_B_CS   = 10;
static constexpr uint8_t PIN_MOD_B_GDO0 = 3;
static constexpr uint8_t PIN_MOD_B_GDO2 = 5;

static constexpr uint32_t RX_TIMEOUT_MS   = 2000;
static constexpr uint8_t  MAX_PAYLOAD_LEN = 20;

// CC1101 Hardware Command Strobes
static constexpr uint8_t CC1101_CMD_SIDLE = 0x36; // Exit RX/TX, turn off frequency synthesizer
static constexpr uint8_t CC1101_CMD_SFRX  = 0x3A; // Flush the RX FIFO buffer
static constexpr uint8_t CC1101_CMD_SFTX  = 0x3B; // Flush the TX FIFO buffer

// 1 MHz SPI clock for Nano 33 BLE Mbed OS stability
SPISettings cc1101Settings(1000000, MSBFIRST, SPI_MODE0);

Module modA(PIN_MOD_A_CS, PIN_MOD_A_GDO0, RADIOLIB_NC, PIN_MOD_A_GDO2, SPI, cc1101Settings);
Module modB(PIN_MOD_B_CS, PIN_MOD_B_GDO0, RADIOLIB_NC, PIN_MOD_B_GDO2, SPI, cc1101Settings);

CC1101 radioA(&modA);
CC1101 radioB(&modB);

// -----------------------------------------------------------------------------
// BLE Configuration
// -----------------------------------------------------------------------------
static const char BLE_DEVICE_NAME[] = "TPMS_Gateway";
static const char SERVICE_UUID[]    = "19B10000-E8F2-537E-4F6C-D104768A1214";
static const char WRITE_CHAR_UUID[] = "19B10002-E8F2-537E-4F6C-D104768A1214";
static const char NOTIFY_CHAR_UUID[]= "19B10001-E8F2-537E-4F6C-D104768A1214";

BLEService rfService(SERVICE_UUID);
BLECharacteristic writeChar(WRITE_CHAR_UUID, BLEWrite, 64);
BLECharacteristic notifyChar(NOTIFY_CHAR_UUID, BLERead | BLENotify, 64);

// 29-byte packed telemetry structure sent to Python over BLE
struct __attribute__((packed)) RFPacketFrame {
  uint32_t packetId;
  int16_t  rssi_x10;
  uint8_t  lqi;
  uint8_t  payloadLen;
  char     payload[MAX_PAYLOAD_LEN + 1];
};

volatile bool rxInterruptFired = false;
uint32_t globalTxCounter = 0;

char incomingBleMsg[MAX_PAYLOAD_LEN + 1] = {0};
volatile bool hasBleCommand = false;

void onModuleBPacketReceived() {
  rxInterruptFired = true;
}

inline void deselectBothCS() {
  digitalWrite(PIN_MOD_A_CS, HIGH);
  digitalWrite(PIN_MOD_B_CS, HIGH);
}

// Low-level hardware strobe to guarantee CC1101 FIFOs never lock up
void sendRawStrobe(uint8_t csPin, uint8_t strobe) {
  deselectBothCS();
  SPI.beginTransaction(cc1101Settings);
  digitalWrite(csPin, LOW);
  delayMicroseconds(5);
  SPI.transfer(strobe);
  delayMicroseconds(5);
  digitalWrite(csPin, HIGH);
  SPI.endTransaction();
}

void flushRadioA() {
  radioA.standby();
  sendRawStrobe(PIN_MOD_A_CS, CC1101_CMD_SIDLE);
  delay(2);
  sendRawStrobe(PIN_MOD_A_CS, CC1101_CMD_SFTX);
  delay(2);
}

void flushRadioB() {
  radioB.standby();
  sendRawStrobe(PIN_MOD_B_CS, CC1101_CMD_SIDLE);
  delay(2);
  sendRawStrobe(PIN_MOD_B_CS, CC1101_CMD_SFRX);
  delay(2);
}

void onBleDisconnect(BLEDevice central) {
  Serial.println("[BLE] Laptop disconnected. Restarting advertising...");
  BLE.advertise();
}

void onWriteReceived(BLEDevice central, BLECharacteristic characteristic) {
  int len = writeChar.valueLength();
  if (len > 0 && len <= MAX_PAYLOAD_LEN && !hasBleCommand) {
    memset(incomingBleMsg, 0, sizeof(incomingBleMsg));
    writeChar.readValue((uint8_t*)incomingBleMsg, len);
    incomingBleMsg[len] = '\0';
    hasBleCommand = true;
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2500);

  Serial.println("\n==============================================");
  Serial.println("  PRODUCTION CC1101 + BLE DUAL GATEWAY        ");
  Serial.println("==============================================");

  pinMode(PIN_MOD_A_CS, OUTPUT);
  pinMode(PIN_MOD_B_CS, OUTPUT);
  deselectBothCS();

  pinMode(PIN_MOD_A_GDO0, INPUT);
  pinMode(PIN_MOD_A_GDO2, INPUT);
  pinMode(PIN_MOD_B_GDO0, INPUT);
  pinMode(PIN_MOD_B_GDO2, INPUT);

  SPI.begin();
  delay(100);

  // 1. Initialize Module A (TX on D9)
  deselectBothCS();
  int16_t resA = radioA.begin(433.0);
  if (resA == RADIOLIB_ERR_NONE) {
    radioA.setOutputPower(-10);
    flushRadioA();
    Serial.println("[INIT] Module A (TX on D9): READY");
  } else {
    Serial.print("[INIT] Module A FAILED, code: ");
    Serial.println(resA);
  }

  deselectBothCS();
  delay(50);

  // 2. Initialize Module B (RX on D10)
  deselectBothCS();
  int16_t resB = radioB.begin(433.0);
  if (resB == RADIOLIB_ERR_NONE) {
    flushRadioB();
    radioB.setPacketReceivedAction(onModuleBPacketReceived);
    Serial.println("[INIT] Module B (RX on D10): READY");
  } else {
    Serial.print("[INIT] Module B FAILED, code: ");
    Serial.println(resB);
  }

  deselectBothCS();
  delay(50);

  // 3. Initialize BLE Peripheral
  if (!BLE.begin()) {
    Serial.println("[FATAL] BLE Hardware initialization failed!");
    while (1);
  }

  BLE.setLocalName(BLE_DEVICE_NAME);
  BLE.setDeviceName(BLE_DEVICE_NAME);
  BLE.setAdvertisedService(rfService);
  rfService.addCharacteristic(writeChar);
  rfService.addCharacteristic(notifyChar);
  BLE.addService(rfService);

  BLE.setEventHandler(BLEDisconnected, onBleDisconnect);
  writeChar.setEventHandler(BLEWritten, onWriteReceived);

  BLE.advertise();
  Serial.println("[BLE] Advertising as 'TPMS_Gateway'. Ready for laptop.");
}

void executeLoopback(const char* payload) {
  globalTxCounter++;
  size_t len = strlen(payload);
  if (len > MAX_PAYLOAD_LEN) len = MAX_PAYLOAD_LEN;

  Serial.println("----------------------------------------------");
  Serial.print("[TX Module A] Sending packet #");
  Serial.print(globalTxCounter);
  Serial.print(" | Payload: ");
  Serial.println(payload);

  // Step 1: Explicitly flush both TX and RX FIFOs so neither chip can overflow
  flushRadioA();
  flushRadioB();

  // Step 2: Arm Receiver (Module B)
  rxInterruptFired = false;
  int16_t rxArmState = radioB.startReceive();
  deselectBothCS();

  if (rxArmState != RADIOLIB_ERR_NONE) {
    Serial.print("[RX ERROR] startReceive failed: ");
    Serial.println(rxArmState);
    flushRadioB();
    return;
  }

  // Allow 25ms for Module B PLL frequency synthesizer lock
  delay(25);

  // Step 3: Transmit via Module A using proven non-blocking startTransmit
  int16_t txStart = radioA.startTransmit((uint8_t*)payload, len);
  deselectBothCS();

  if (txStart != RADIOLIB_ERR_NONE) {
    Serial.print("[TX ERROR] startTransmit failed: ");
    Serial.println(txStart);
    flushRadioA();
    flushRadioB();
    return;
  }

  // Wait for over-the-air transmission to finish (85ms covers up to 20 bytes safely)
  delay(85);
  radioA.finishTransmit();
  flushRadioA();
  Serial.println("[TX Module A] Transmitted successfully.");

  // Step 4: Wait for Module B interrupt on D3
  uint32_t waitStart = millis();
  while (!rxInterruptFired && (millis() - waitStart < RX_TIMEOUT_MS)) {
    if (digitalRead(PIN_MOD_B_GDO0) == HIGH) {
      rxInterruptFired = true;
      break;
    }
    BLE.poll();
    delay(1);
  }

  // Allow 10ms for final CRC bytes to settle in FIFO
  delay(10);

  // Step 5: Read Packet from Module B
  deselectBothCS();
  size_t rxLen = radioB.getPacketLength();

  if (rxLen == 0) {
    rxLen = len;
  }
  if (rxLen > MAX_PAYLOAD_LEN) {
    rxLen = MAX_PAYLOAD_LEN;
  }

  uint8_t rxBuffer[32] = {0};
  int16_t readState = radioB.readData(rxBuffer, (uint8_t)rxLen);
  deselectBothCS();

  if (readState == RADIOLIB_ERR_NONE && rxBuffer[0] != 0) {
    rxBuffer[rxLen] = '\0';
    String recStr = String((char*)rxBuffer);

    float rssi = radioB.getRSSI();
    uint8_t lqi = radioB.getLQI();

    Serial.print("[RX Module B] Received: ");
    Serial.println(recStr);
    Serial.print("[SIGNAL INFO] RSSI: ");
    Serial.print(rssi);
    Serial.print(" dBm | LQI: ");
    Serial.println(lqi);

    RFPacketFrame frame;
    memset(&frame, 0, sizeof(RFPacketFrame));
    frame.packetId = globalTxCounter;
    frame.rssi_x10 = (int16_t)(rssi * 10);
    frame.lqi = lqi;
    frame.payloadLen = (uint8_t)strlen((char*)rxBuffer);
    memcpy(frame.payload, rxBuffer, frame.payloadLen);

    notifyChar.writeValue((const uint8_t*)&frame, sizeof(RFPacketFrame));
    Serial.println("[BLE] Forwarded frame to laptop.");
  } else {
    Serial.print("[RX ERROR] Packet missed or corrupted (readData code: ");
    Serial.print(readState);
    Serial.println(")");
  }

  // Step 6: Cleanly flush both modules back to idle
  flushRadioB();
  flushRadioA();
  deselectBothCS();

  // Step 7: Pump Mbed BLE stack so notification transmits cleanly
  for (int i = 0; i < 10; i++) {
    BLE.poll();
    delay(5);
  }
}

void loop() {
  BLE.poll();

  if (hasBleCommand) {
    char currentPayload[MAX_PAYLOAD_LEN + 1];
    noInterrupts();
    strncpy(currentPayload, incomingBleMsg, sizeof(currentPayload));
    currentPayload[MAX_PAYLOAD_LEN] = '\0';
    hasBleCommand = false;
    interrupts();

    executeLoopback(currentPayload);
  }

  delay(2);
}
