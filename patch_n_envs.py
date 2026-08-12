import zipfile
import json
import os
import shutil
import glob

for path in glob.glob("models/sweep/*.zip") + glob.glob("models/prod_win/*.zip"):
    if not os.path.isfile(path):
        continue
    tmp = path + ".tmp"
    try:
        with zipfile.ZipFile(path, "r") as z:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
                for item in z.namelist():
                    data = z.read(item)
                    if item == "data":
                        d = json.loads(data)
                        if d.get("n_envs", 0) > 64:
                            print(f"{path}: {d['n_envs']} -> 64")
                            d["n_envs"] = 64
                            data = json.dumps(d).encode()
                        else:
                            print(f"{path}: skip (n_envs={d.get('n_envs', '?')})")
                            continue
                    out.writestr(item, data)
        shutil.move(tmp, path)
    except Exception as e:
        print(f"{path}: ERROR {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
