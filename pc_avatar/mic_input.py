import speech_recognition as sr

recognizer = sr.Recognizer()
mic = sr.Microphone()

def listen_for_speech():
    with mic as source:
        print("🎧 Listening...")
        audio = recognizer.listen(source)
    try:
        text = recognizer.recognize_google(audio)
        print("You:", text)
        return text
    except sr.UnknownValueError:
        return None
