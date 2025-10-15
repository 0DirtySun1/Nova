class SafetyToggle:
    def __init__(self):
        self.vision_enabled = True
        self.mic_enabled = True

    def toggle_vision(self):
        self.vision_enabled = not self.vision_enabled
        print("Vision:", "ON" if self.vision_enabled else "OFF")

    def toggle_mic(self):
        self.mic_enabled = not self.mic_enabled
        print("Mic:", "ON" if self.mic_enabled else "OFF")
