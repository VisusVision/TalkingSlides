import onnxruntime as ort
import warnings
warnings.filterwarnings('always')

print("Starting session...")
try:
    sess = ort.InferenceSession('/opt/liveportrait/pretrained_weights/insightface/models/buffalo_l/det_10g.onnx', providers=['CUDAExecutionProvider'])
    print("Session created. Active providers:", sess.get_providers())
except Exception as e:
    print("Error:", e)
