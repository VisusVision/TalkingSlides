"""Quick diagnostic: check UNet dtype, CUDA fp16 speed, and LD_LIBRARY_PATH."""
import sys, os, time

print("=== MuseTalk UNet Diagnostic ===")
print(f"LD_LIBRARY_PATH = {os.environ.get('LD_LIBRARY_PATH', '(not set)')}")

import torch
print(f"torch version   = {torch.__version__}")
print(f"CUDA available  = {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA device     = {torch.cuda.get_device_name(0)}")
    props = torch.cuda.get_device_properties(0)
    print(f"VRAM total      = {props.total_memory/1024**3:.2f} GB")

# --- fp16 conv benchmark ---
print("\n--- fp16 conv2d benchmark (50 iters, 8x4x32x32 input) ---")
x  = torch.randn(8, 4, 32, 32, dtype=torch.float16, device="cuda")
w  = torch.randn(4, 4, 3, 3,  dtype=torch.float16, device="cuda")
torch.cuda.synchronize()
t0 = time.monotonic()
for _ in range(50):
    torch.nn.functional.conv2d(x, w, padding=1)
torch.cuda.synchronize()
fp16_time = time.monotonic() - t0
print(f"fp16: {fp16_time:.3f}s  ({50/fp16_time:.1f} iter/s)")

# --- fp32 conv benchmark ---
print("--- fp32 conv2d benchmark (50 iters) ---")
x32 = torch.randn(8, 4, 32, 32, device="cuda")
w32 = torch.randn(4, 4, 3, 3,  device="cuda")
torch.cuda.synchronize()
t1 = time.monotonic()
for _ in range(50):
    torch.nn.functional.conv2d(x32, w32, padding=1)
torch.cuda.synchronize()
fp32_time = time.monotonic() - t1
print(f"fp32: {fp32_time:.3f}s  ({50/fp32_time:.1f} iter/s)")
print(f"Speedup fp16/fp32: {fp32_time/fp16_time:.2f}x")

# --- Inspect loaded UNet dtype via service globals ---
print("\n--- MuseTalk UNet dtype check ---")
sys.path.insert(0, "/opt/musetalk")
os.chdir("/tmp/musetalk-service-ws")
try:
    import musetalk_service
    if musetalk_service._unet is not None:
        model = musetalk_service._unet.model
        dtypes = {p.dtype for p in model.parameters()}
        print(f"UNet param dtypes: {dtypes}")
    else:
        print("UNet not loaded in this process (expected — service runs separately)")
except Exception as e:
    print(f"Cannot inspect live service globals: {e}")

# --- Check cuDNN ---
print(f"\ncudnn.enabled        = {torch.backends.cudnn.enabled}")
print(f"cudnn.benchmark      = {torch.backends.cudnn.benchmark}")
print(f"cudnn.deterministic  = {torch.backends.cudnn.deterministic}")
print(f"cudnn.version        = {torch.backends.cudnn.version()}")
