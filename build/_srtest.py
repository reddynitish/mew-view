import sounddevice as sd
for sr in (48000,44100):
    try:
        sd.check_input_settings(device=1, samplerate=sr, channels=1)
        sd.check_output_settings(device=3, samplerate=sr, channels=1)
        print('OK', sr)
    except Exception as e:
        print('FAIL', sr, e)
