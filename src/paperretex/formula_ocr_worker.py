"""Executed by the isolated MinerU Python, not imported by the lightweight CLI."""

import json
import hashlib
import os
from pathlib import Path
import sys


def main():
    os.environ.setdefault("MINERU_MODEL_SOURCE", "modelscope")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import numpy as np
    import torch
    from PIL import Image
    from torch.utils.data import DataLoader
    from mineru.model.mfr.unimernet.Unimernet import UnimernetModel, MathDataset
    from mineru.utils.config_reader import get_device
    from mineru.utils.enum_class import ModelPath
    from mineru.utils.models_download_utils import auto_download_and_get_model_root_path

    root = Path(sys.argv[1]).resolve()
    formulas = json.loads((root / "formulas.json").read_text())
    if not formulas:
        (root / "formula-ocr.json").write_text("[]")
        return
    weights = (
        Path(auto_download_and_get_model_root_path(ModelPath.unimernet_small))
        / ModelPath.unimernet_small
    )
    model = UnimernetModel(str(weights), get_device())
    results = []
    # Bound image memory and inference batch sizes on laptops.
    for start in range(0, len(formulas), 8):
        batch = formulas[start : start + 8]
        images = []
        for f in batch:
            with Image.open(root / f["crop"]) as im:
                images.append(np.array(im.convert("RGB")))
        loader = DataLoader(
            MathDataset(images, model.model.transform), batch_size=8, num_workers=0
        )
        with torch.no_grad():
            for pixels in loader:
                values = model.model.generate({
                    "image": pixels.to(dtype=model.model.dtype, device=model.device)
                })["fixed_str"]
                if len(values) != len(batch):
                    raise RuntimeError("OCR returned incomplete batch")
                results.extend(
                    {
                        "id": f["id"],
                        "ocr_latex": value,
                        "model": "UniMERNet-small (MinerU 2.7.1)",
                        "crop_sha256": hashlib.sha256(
                            (root / f["crop"]).read_bytes()
                        ).hexdigest(),
                    }
                    for f, value in zip(batch, values)
                )
        print("Formula OCR: %d/%d" % (len(results), len(formulas)), flush=True)
    (root / "formula-ocr.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
