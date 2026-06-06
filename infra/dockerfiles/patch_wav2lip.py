from pathlib import Path
import re
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_wav2lip.py <inference.py path>")
        return 2

    target = Path(sys.argv[1])
    text = target.read_text(encoding="utf-8")

    new_load = """def _load(checkpoint_path):
\tif device == 'cuda':
\t\tcheckpoint = torch.load(checkpoint_path, weights_only=False)
\telse:
\t\tcheckpoint = torch.load(
\t\t\tcheckpoint_path,
\t\t\tmap_location='cpu',
\t\t\tweights_only=False,
\t\t)
\treturn checkpoint

"""

    new_model = """def load_model(path):
\tprint("Load checkpoint from: {}".format(path))
\tcheckpoint = _load(path)

\t# Newer distributed checkpoints can be TorchScript archives.
\tif isinstance(checkpoint, torch.jit.ScriptModule):
\t\tmodel = checkpoint
\t\tmodel = model.to(device)
\t\treturn model.eval()

\tmodel = Wav2Lip()
\ts = checkpoint["state_dict"] if isinstance(checkpoint, dict) and "state_dict" in checkpoint else checkpoint
\tnew_s = {}
\tfor k, v in s.items():
\t\tnew_s[k.replace('module.', '')] = v
\tmodel.load_state_dict(new_s)

\tmodel = model.to(device)
\treturn model.eval()

"""

    # Simple string replaces for robustness (whitespace-tolerant).
    # Simple string replaces for robustness (whitespace-tolerant).
    if "def _load(checkpoint_path):" in text:
        pattern = r"def _load\(checkpoint_path\):[^\n]*\n(?:.*?\n)*?\s*return checkpoint\n"
        text = re.sub(pattern, new_load, text, count=1)
        
    if "def load_model(path):" in text:
        pattern = r"def load_model\(path\):[^\n]*\n(?:.*?\n)*?\s*return model\.eval\(\)\n"
        text = re.sub(pattern, new_model, text, count=1)

    # Replace temp/result.avi with temp/result.mp4 and DIVX with mp4v.
    # This is done with simple string replacement for robustness.
    text = text.replace("'temp/result.avi'", "'temp/result.mp4'")
    text = text.replace("cv2.VideoWriter_fourcc(*'DIVX')", "cv2.VideoWriter_fourcc(*'mp4v')")

    target.write_text(text, encoding="utf-8")
    print(f"patched {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
