
#include <math.h>

// ===== PIN CONFIGURATION =====
const byte LM35_PIN = A1;
const byte LDR_PIN = A0;
const byte MOTION_BUTTON = 8;
const byte EMERGENCY_BUTTON = 11;

const byte HEATER_RELAY = 13;
const byte MOTOR_RELAY = 12;
const byte LIGHT_RELAY = 6;

const byte GREEN_LED = 2;
const byte RED_LED = 3;

// Heater and motor: active LOW
const byte RELAY_ON = LOW;
const byte RELAY_OFF = HIGH;

// Light relay: active HIGH
const byte LIGHT_ON = HIGH;
const byte LIGHT_OFF = LOW;

// ===== TIMERS =====
const unsigned long SENSOR_INTERVAL = 1000UL;
const unsigned long DATA_INTERVAL = 20000UL;
const unsigned long LIGHT_HOLD = 10000UL;
const unsigned long ALERT_INTERVAL = 10000UL;
const unsigned long DEBOUNCE = 40UL;

// ===== CLIMATE =====
enum Climate {
  IDLE,
  HEATING,
  COOLING
};

Climate climate = IDLE;

bool tempAuto = true;
bool lightAuto = true;
bool secureMode = false;
bool manualLight = false;
bool emergencyMode = false;

bool heaterOn = false;
bool motorOn = false;
bool lightOn = false;

float temperature = 0;
int lightPercent = 0;

bool tempOK = false;

// ===== MOTION =====
bool motionRaw = HIGH;
bool motionStable = HIGH;

unsigned long motionChanged = 0;
unsigned long lastMotion = 0;

bool motionSeen = false;

unsigned long motionCount = 0;
unsigned long lastAlert = 0;
bool alertSent = false;

// ===== EMERGENCY BUTTON =====
bool emergencyRaw = HIGH;
bool emergencyStable = HIGH;

unsigned long emergencyChanged = 0;
bool secureBeforeEmergency = false;

// ===== SYSTEM TIMERS =====
unsigned long lastSensor = 0;
unsigned long lastData = 0;
unsigned long lastPrint = 0;

// ===== CLIMATE OUTPUTS =====
void setClimateOutputs() {

  bool heat = !emergencyMode &&
              tempOK &&
              climate == HEATING;

  bool cool = !emergencyMode &&
              tempOK &&
              climate == COOLING;

  // Never operate heater and motor together
  if (heat && cool) {
    heat = false;
    cool = false;
  }

  if (heat != heaterOn || cool != motorOn) {

    digitalWrite(HEATER_RELAY, RELAY_OFF);
    digitalWrite(MOTOR_RELAY, RELAY_OFF);

    delay(40);

    if (heat) {
      digitalWrite(HEATER_RELAY, RELAY_ON);
    }

    if (cool) {
      digitalWrite(MOTOR_RELAY, RELAY_ON);
    }

    heaterOn = heat;
    motorOn = cool;
  }
}

// ===== LIGHT OUTPUT =====
void setLightOutput(unsigned long now) {

  bool state = false;

  if (secureMode) {

    state = motionSeen &&
            now - lastMotion < LIGHT_HOLD;
  }

  else if (lightAuto) {

    state = lightPercent < 30;
  }

  else {

    state = manualLight;
  }

  digitalWrite(
    LIGHT_RELAY,
    state ? LIGHT_ON : LIGHT_OFF
  );

  lightOn = state;
}

// ===== STATUS INDICATORS =====
void setStatusLEDs() {

  digitalWrite(
    GREEN_LED,
    emergencyMode ? LOW : HIGH
  );

  digitalWrite(
    RED_LED,
    emergencyMode ? HIGH : LOW
  );
}

// ===== UPDATE ALL OUTPUTS =====
void updateOutputs() {

  setClimateOutputs();
  setLightOutput(millis());
  setStatusLEDs();
}

// ===== ACTIVATE EMERGENCY =====
void activateEmergency() {

  if (emergencyMode) return;

  // Immediately disconnect climate loads
  digitalWrite(HEATER_RELAY, RELAY_OFF);
  digitalWrite(MOTOR_RELAY, RELAY_OFF);

  heaterOn = false;
  motorOn = false;

  climate = IDLE;
  tempAuto = false;

  secureBeforeEmergency = secureMode;

  emergencyMode = true;
  secureMode = true;

  motionSeen = false;
  alertSent = false;

  updateOutputs();

  Serial.println("EVENT,EMERGENCY");
  Serial.println("EMERGENCY MODE ON");
}

// ===== RESET EMERGENCY =====
void resetEmergency() {

  if (!emergencyMode) return;

  emergencyMode = false;
  secureMode = secureBeforeEmergency;

  motionSeen = false;
  tempAuto = false;
  climate = IDLE;

  updateOutputs();

  Serial.println("EMERGENCY MODE OFF");
}

// ===== READ EMERGENCY BUTTON =====
void checkEmergencyButton(unsigned long now) {

  bool reading = digitalRead(EMERGENCY_BUTTON);

  if (reading != emergencyRaw) {
    emergencyChanged = now;
  }

  if (now - emergencyChanged >= DEBOUNCE &&
      reading != emergencyStable) {

    emergencyStable = reading;

    if (emergencyStable == LOW) {
      activateEmergency();
    }
  }

  emergencyRaw = reading;
}

// ===== READ LM35 =====
float readTemperature() {

  int values[9];

  for (byte i = 0; i < 9; i++) {

    values[i] = analogRead(LM35_PIN);
    delay(3);
  }

  // Median filter
  for (byte i = 1; i < 9; i++) {

    int value = values[i];
    int j = i;

    while (j > 0 && values[j - 1] > value) {

      values[j] = values[j - 1];
      j--;
    }

    values[j] = value;
  }

  return values[4] * 5.0 / 1023.0 * 100.0;
}

// ===== SENSOR MEASUREMENTS =====
void measureSensors() {

  temperature = readTemperature();

  // This is the simplified classroom test mode
  tempOK = true;

  long total = 0;

  for (int i = 0; i < 10; i++) {
    total += analogRead(LDR_PIN);
  }

  lightPercent = (int)(
    total / 10.0 * 100.0 / 1023.0 + 0.5
  );

  lightPercent = constrain(
    lightPercent, 0, 100
  );

  if (!tempAuto || emergencyMode) return;

  // Heating below 17 C, until 23 C
  // Cooling above 23 C, until 17 C
  if (climate == IDLE) {

    if (temperature < 17.0) {
      climate = HEATING;
    }

    else if (temperature > 23.0) {
      climate = COOLING;
    }
  }

  else if (climate == HEATING) {

    if (temperature >= 23.0) {
      climate = IDLE;
    }
  }

  else if (climate == COOLING) {

    if (temperature <= 17.0) {
      climate = IDLE;
    }
  }
}

// ===== MOTION BUTTON =====
void checkMotionButton(unsigned long now) {

  bool reading = digitalRead(MOTION_BUTTON);

  if (reading != motionRaw) {
    motionChanged = now;
  }

  if (now - motionChanged >= DEBOUNCE &&
      reading != motionStable) {

    motionStable = reading;

    if (secureMode && motionStable == LOW) {

      motionCount++;

      Serial.println("MOTION DETECTED");

      if (!alertSent ||
          now - lastAlert >= ALERT_INTERVAL) {

        Serial.println("EVENT,SECURITY_ALERT");

        lastAlert = now;
        alertSent = true;
      }
    }
  }

  motionRaw = reading;

  if (secureMode && motionStable == LOW) {

    motionSeen = true;
    lastMotion = now;
  }
}

// ===== THINGSPEAK TELEMETRY =====
void sendThingSpeakData() {

  // DATA plus eight ThingSpeak fields
  Serial.print("DATA,");

  Serial.print(temperature, 2);
  Serial.print(",");

  Serial.print(lightPercent);
  Serial.print(",");

  Serial.print(motionCount);
  Serial.print(",");

  Serial.print(heaterOn ? 1 : 0);
  Serial.print(",");

  Serial.print(motorOn ? 1 : 0);
  Serial.print(",");

  Serial.print(lightOn ? 1 : 0);
  Serial.print(",");

  Serial.print(secureMode ? 1 : 0);
  Serial.print(",");

  Serial.println(lightAuto ? 1 : 0);
}

// ===== SERIAL COMMANDS =====
void handleCommand(String command) {

  command.trim();
  command.toUpperCase();

  // During emergency only remote reset
  // and read-only status are accepted
  if (emergencyMode) {

    if (command == "REMOTE EMERGENCY OFF") {
      resetEmergency();
    }

    else if (command == "STATUS") {
      sendThingSpeakData();
    }

    else {
      Serial.println("EMERGENCY LOCK ACTIVE");
    }

    return;
  }

  // Remote-only emergency reset command
  if (command == "REMOTE EMERGENCY OFF") {
    Serial.println("EMERGENCY NOT ACTIVE");
    return;
  }

  if (command == "LIGHT ON") {

    secureMode = false;
    lightAuto = false;
    manualLight = true;
  }

  else if (command == "LIGHT OFF") {

    secureMode = false;
    lightAuto = false;
    manualLight = false;
  }

  else if (command == "LIGHT AUTO ON") {

    secureMode = false;
    lightAuto = true;
  }

  else if (command == "LIGHT AUTO OFF") {

    lightAuto = false;
    manualLight = false;
  }

  else if (command == "SECURE ON") {

    secureMode = true;
    motionSeen = false;
    alertSent = false;
  }

  else if (command == "SECURE OFF") {

    secureMode = false;
    motionSeen = false;
  }

  else if (
    command == "AUTO TEMP ON" ||
    command == "AUTO"
  ) {

    tempAuto = true;
    climate = IDLE;
  }

  else if (command == "AUTO TEMP OFF") {

    tempAuto = false;
    climate = IDLE;
  }

  else if (command == "HEAT ON") {

    tempAuto = false;
    climate = tempOK ? HEATING : IDLE;
  }

  else if (command == "HEAT OFF") {

    if (climate == HEATING) {
      climate = IDLE;
    }
  }

  else if (command == "COOL ON") {

    tempAuto = false;
    climate = tempOK ? COOLING : IDLE;
  }

  else if (command == "COOL OFF") {

    if (climate == COOLING) {
      climate = IDLE;
    }
  }

  else if (command == "STOP") {

    tempAuto = false;
    climate = IDLE;

    secureMode = false;
    lightAuto = false;
    manualLight = false;
  }

  else if (command == "STATUS") {

    updateOutputs();
    sendThingSpeakData();
    return;
  }

  else if (command.length() > 0) {

    Serial.println("UNKNOWN COMMAND");
    return;
  }

  updateOutputs();

  Serial.print("CMD: ");
  Serial.println(command);
}

// ===== SETUP =====
void setup() {

  // Set inactive output levels before OUTPUT mode
  digitalWrite(HEATER_RELAY, RELAY_OFF);
  digitalWrite(MOTOR_RELAY, RELAY_OFF);
  digitalWrite(LIGHT_RELAY, LIGHT_OFF);

  digitalWrite(GREEN_LED, LOW);
  digitalWrite(RED_LED, LOW);

  pinMode(HEATER_RELAY, OUTPUT);
  pinMode(MOTOR_RELAY, OUTPUT);
  pinMode(LIGHT_RELAY, OUTPUT);

  pinMode(GREEN_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);

  pinMode(MOTION_BUTTON, INPUT_PULLUP);
  pinMode(EMERGENCY_BUTTON, INPUT_PULLUP);

  Serial.begin(9600);
  Serial.setTimeout(50);

  measureSensors();

  updateOutputs();

  Serial.println("SMART HOME FINAL PROJECT");
  Serial.println("Gmail + ThingSpeak + Emergency");
  Serial.println("Commands enabled");

  sendThingSpeakData();

  lastSensor = millis();
  lastData = millis();
}

// ===== MAIN LOOP =====
void loop() {

  unsigned long now = millis();

  // Emergency has priority
  checkEmergencyButton(now);

  // Check simulated motion sensor
  checkMotionButton(now);

  // Process incoming serial commands
  if (Serial.available()) {

    String command = Serial.readStringUntil('\n');

    handleCommand(command);
  }

  // Measure sensors once per second
  if (now - lastSensor >= SENSOR_INTERVAL) {

    lastSensor = now;

    measureSensors();
  }

  updateOutputs();

  now = millis();

  // Print status once per second
  if (now - lastPrint >= 1000UL) {

    lastPrint = now;

    Serial.print("T=");
    Serial.print(temperature, 2);
    Serial.print("C");

    Serial.print(" | Light=");
    Serial.print(lightPercent);
    Serial.print("%");

    Serial.print(" | HEATER ");
    Serial.print(heaterOn ? "ON" : "OFF");

    Serial.print(" | MOTOR ");
    Serial.print(motorOn ? "ON" : "OFF");

    Serial.print(" | LED ");
    Serial.print(lightOn ? "ON" : "OFF");

    Serial.print(" | SECURE ");
    Serial.print(secureMode ? "ON" : "OFF");

    Serial.print(" | EMERGENCY ");
    Serial.println(emergencyMode ? "ON" : "OFF");
  }

  // Send telemetry every 20 seconds
  if (now - lastData >= DATA_INTERVAL) {

    lastData = now;

    sendThingSpeakData();
  }
}
