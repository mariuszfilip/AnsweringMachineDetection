from audio import *

# print("heelo")
learn = load_learner('models')
learn.data.x.config.cache = False

def predict_from_file(wav_file, expected_result, verbose=False):
    my_item = AudioItem(path=wav_file)
    print(my_item)
    y, pred, raw_pred = audio_predict(learn,my_item)
    print(my_item)
    if verbose: print(y)
    if verbose: print(pred.item())
    if verbose: print(raw_pred)
    if verbose: print("\n")

    # if pred.item()in expected_result:
    #     return True

    # return False

predict_from_file("sample_files/beep.wav", [0,1], verbose=True)
predict_from_file("sample_files/speech.wav", [2], verbose=True)
