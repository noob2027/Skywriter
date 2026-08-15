/*
  Teensy 4.0 Hello World

  Prints a message to the Arduino Serial Monitor and blinks
  the built-in LED once per second.
*/

const int LED_PIN = LED_BUILTIN;
bool ledState = false;

void setup() {
  pinMode(LED_PIN, OUTPUT);

  Serial.begin(115200);

  // Give the USB Serial connection a moment to appear,
  // but do not wait forever when running without a computer.
  unsigned long waitStart = millis();
  while (!Serial && millis() - waitStart < 2000) {
    // Wait up to two seconds.
  }

  Serial.println("Hello, world from Teensy 4.0!");
}

void loop() {
  ledState = !ledState;
  digitalWrite(LED_PIN, ledState);

  Serial.println("Hello, world from Teensy 4.0!");
  delay(1000);
}
